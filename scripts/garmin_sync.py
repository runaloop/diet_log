#!/usr/bin/env python3
"""Fetch daily calories (total/active/BMR) from Garmin Connect.

Self-contained reimplementation of the Garmin Connect mobile auth flow
(no third-party dependencies, stdlib only). Flow reverse-engineered from
the public python-garminconnect library:

  1. login: POST sso.garmin.com/mobile/api/login (+ optional MFA code)
     -> CAS service ticket
  2. exchange: POST diauth.garmin.com/di-oauth2-service/oauth/token
     with the mobile app's public client id -> access + refresh tokens
  3. daily: refresh access token when needed, then
     GET connectapi.garmin.com/usersummary-service/usersummary/daily/...

Tokens live in ~/.config/diet-log/garmin_tokens.json (0600). The account
password is used once during `login` and never stored.

Usage:
  python3 scripts/garmin_sync.py login                   # interactive, one-time
  python3 scripts/garmin_sync.py fetch [YYYY-MM-DD]      # daily summary
  python3 scripts/garmin_sync.py activities [YYYY-MM-DD] # workouts of the day
  python3 scripts/garmin_sync.py base [YYYY-MM-DD]       # 4-week avg resting kcal
  (any command accepts --json for the raw API response)
"""

import base64
import getpass
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SSO = "https://sso.garmin.com"
DI_TOKEN_URL = "https://diauth.garmin.com/di-oauth2-service/oauth/token"
CONNECTAPI = "https://connectapi.garmin.com"

SSO_CLIENT_ID = "GCM_IOS_DARK"
SERVICE_URL = "https://mobile.integration.garmin.com/gcm/ios"
DI_GRANT_TYPE = (
    "https://connectapi.garmin.com/di-oauth2-service/oauth/grant/service_ticket"
)
# Public client ids embedded in the Garmin Connect mobile app; tried in order.
DI_CLIENT_IDS = (
    "GARMIN_CONNECT_MOBILE_ANDROID_DI_2025Q2",
    "GARMIN_CONNECT_MOBILE_ANDROID_DI_2024Q4",
    "GARMIN_CONNECT_MOBILE_ANDROID_DI",
    "GARMIN_CONNECT_MOBILE_IOS_DI",
)

LOGIN_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
)
NATIVE_HEADERS = {
    "User-Agent": "GCM-Android-5.23",
    "X-Garmin-User-Agent": (
        "com.garmin.android.apps.connectmobile/5.23; ; "
        "Google/sdk_gphone64_arm64/google; Android/33; Dalvik/2.1.0"
    ),
    "X-Garmin-Paired-App-Version": "10861",
    "X-Garmin-Client-Platform": "Android",
    "X-App-Ver": "10861",
    "X-Lang": "en",
    "X-GCExperience": "GC5",
    "Accept-Language": "en-US,en;q=0.9",
}

TOKEN_PATH = Path("~/.config/diet-log/garmin_tokens.json").expanduser()


class GarminError(Exception):
    pass


def _request(opener, method, url, params=None, headers=None,
             json_body=None, form_body=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = None
    hdrs = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode()
        hdrs["Content-Type"] = "application/json"
    elif form_body is not None:
        data = urllib.parse.urlencode(form_body).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with opener.open(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def _json_or_die(status, body, what):
    if status == 429:
        raise GarminError(f"{what}: HTTP 429 — Garmin rate-limited this IP, "
                          "retry in an hour")
    if status == 403:
        raise GarminError(f"{what}: HTTP 403 — Cloudflare bot challenge; "
                          "retry later (login is the only guarded step)")
    try:
        return json.loads(body)
    except ValueError:
        raise GarminError(f"{what}: HTTP {status}, non-JSON response "
                          f"(first 200 chars): {body[:200]}")


def _basic_auth(client_id):
    return "Basic " + base64.b64encode(f"{client_id}:".encode()).decode()


def _jwt_payload(token):
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


def save_tokens(tokens):
    TOKEN_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(tokens, f)
    TOKEN_PATH.chmod(0o600)


def load_tokens():
    if not TOKEN_PATH.exists():
        raise GarminError(f"no tokens at {TOKEN_PATH} — run: "
                          "python3 scripts/garmin_sync.py login")
    return json.loads(TOKEN_PATH.read_text())


# ---------------------------------------------------------------- login --

def login():
    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")

    # Cookie jar shared between login and MFA verify (CAS session lives there)
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    login_params = {
        "clientId": SSO_CLIENT_ID,
        "locale": "en-US",
        "service": SERVICE_URL,
    }
    login_headers = {
        "User-Agent": LOGIN_UA,
        "Accept": "application/json, text/plain, */*",
        "Origin": SSO,
    }
    status, body = _request(
        opener, "POST", f"{SSO}/mobile/api/login",
        params=login_params, headers=login_headers,
        json_body={"username": email, "password": password,
                   "rememberMe": True, "captchaToken": ""},
    )
    res = _json_or_die(status, body, "login")
    rtype = res.get("responseStatus", {}).get("type")

    if rtype == "INVALID_USERNAME_PASSWORD":
        raise GarminError("invalid username or password")

    if rtype == "MFA_REQUIRED":
        method = res.get("customerMfaInfo", {}).get("mfaLastMethodUsed", "email")
        code = input(f"MFA code ({method}): ").strip()
        status, body = _request(
            opener, "POST", f"{SSO}/mobile/api/mfa/verifyCode",
            params=login_params, headers=login_headers,
            json_body={"mfaMethod": method, "mfaVerificationCode": code,
                       "rememberMyBrowser": True, "reconsentList": [],
                       "mfaSetup": False},
        )
        res = _json_or_die(status, body, "MFA verify")
        rtype = res.get("responseStatus", {}).get("type")

    if rtype != "SUCCESSFUL":
        raise GarminError(f"login failed: {res}")

    tokens = exchange_ticket(opener, res["serviceTicketId"])
    save_tokens(tokens)
    print(f"OK, tokens saved to {TOKEN_PATH}")


def exchange_ticket(opener, ticket):
    for client_id in DI_CLIENT_IDS:
        status, body = _request(
            opener, "POST", DI_TOKEN_URL,
            headers={**NATIVE_HEADERS,
                     "Authorization": _basic_auth(client_id),
                     "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
                     "Cache-Control": "no-cache"},
            form_body={"client_id": client_id,
                       "service_ticket": ticket,
                       "grant_type": DI_GRANT_TYPE,
                       "service_url": SERVICE_URL},
        )
        if status == 429:
            raise GarminError("token exchange rate-limited, retry later")
        if status != 200:
            continue
        data = json.loads(body)
        client_id = _jwt_payload(data["access_token"]).get("client_id", client_id)
        return {"access_token": data["access_token"],
                "refresh_token": data.get("refresh_token"),
                "client_id": client_id}
    raise GarminError("service ticket exchange failed for all client ids")


# ---------------------------------------------------------------- fetch --

def refresh_if_needed(tokens, opener):
    exp = _jwt_payload(tokens["access_token"]).get("exp", 0)
    if time.time() < exp - 900:
        return tokens
    status, body = _request(
        opener, "POST", DI_TOKEN_URL,
        headers={**NATIVE_HEADERS,
                 "Authorization": _basic_auth(tokens["client_id"]),
                 "Accept": "application/json",
                 "Cache-Control": "no-cache"},
        form_body={"grant_type": "refresh_token",
                   "client_id": tokens["client_id"],
                   "refresh_token": tokens["refresh_token"]},
    )
    data = _json_or_die(status, body, "token refresh")
    if status != 200 or "access_token" not in data:
        raise GarminError(f"token refresh failed (HTTP {status}): {body[:200]} "
                          "— re-run: python3 scripts/garmin_sync.py login")
    tokens["access_token"] = data["access_token"]
    tokens["refresh_token"] = data.get("refresh_token", tokens["refresh_token"])
    save_tokens(tokens)
    return tokens


def api_get(opener, tokens, path, params=None):
    status, body = _request(
        opener, "GET", f"{CONNECTAPI}{path}", params=params,
        headers={**NATIVE_HEADERS,
                 "Authorization": f"Bearer {tokens['access_token']}",
                 "Accept": "application/json"},
    )
    if status == 401:
        raise GarminError("API rejected token (401) — re-run: "
                          "python3 scripts/garmin_sync.py login")
    return _json_or_die(status, body, path)


def _session():
    opener = urllib.request.build_opener()
    tokens = refresh_if_needed(load_tokens(), opener)
    if "display_name" not in tokens:
        prof = api_get(opener, tokens, "/userprofile-service/socialProfile")
        tokens["display_name"] = prof["displayName"]
        save_tokens(tokens)
    return opener, tokens


def get_summary(opener, tokens, date):
    summary = api_get(
        opener, tokens,
        f"/usersummary-service/usersummary/daily/{tokens['display_name']}",
        params={"calendarDate": date},
    )
    if summary.get("privacyProtected") is True:
        raise GarminError("API returned privacyProtected — token stale, "
                          "re-run login")
    return summary


def fetch(date, raw=False):
    opener, tokens = _session()
    summary = get_summary(opener, tokens, date)

    if raw:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    total = summary.get("totalKilocalories")
    active = summary.get("activeKilocalories")
    bmr = summary.get("bmrKilocalories")
    steps = summary.get("totalSteps")
    print(f"date:   {date}")
    print(f"total:  {round(total) if total is not None else '—'} kcal")
    print(f"active: {round(active) if active is not None else '—'} kcal")
    print(f"bmr:    {round(bmr) if bmr is not None else '—'} kcal")
    if steps is not None:
        print(f"steps:  {steps}")


def _zone_kcal(a, active_kcal):
    """Split a workout's active kcal across HR zones pro rata by time.

    Garmin gives seconds per HR zone (hrTimeInZone_1..5); kcal are not
    reported per zone, so time share is the estimate. Empty zones dropped.
    """
    times = [(z, a.get(f"hrTimeInZone_{z}") or 0.0) for z in range(1, 6)]
    total = sum(t for _, t in times)
    if not total or not active_kcal:
        return {}
    return {z: active_kcal * t / total for z, t in times if t > 0}


def activities(date, raw=False):
    opener, tokens = _session()
    acts = api_get(
        opener, tokens,
        "/activitylist-service/activities/search/activities",
        params={"startDate": date, "endDate": date,
                "start": "0", "limit": "50"},
    )
    if raw:
        print(json.dumps(acts, indent=2, ensure_ascii=False))
        return
    if not acts:
        print(f"no activities on {date}")
        return
    for a in sorted(acts, key=lambda a: a.get("startTimeLocal", "")):
        start = (a.get("startTimeLocal") or "")[11:16]
        type_key = (a.get("activityType") or {}).get("typeKey", "?")
        name = a.get("activityName") or type_key
        mins = round((a.get("duration") or 0) / 60)
        # Log active kcal only: device `calories` is total and includes the
        # BMR burned during the workout, which the diary's base expenditure
        # already covers — subtracting bmrCalories avoids double counting.
        kcal = round((a.get("calories") or 0) - (a.get("bmrCalories") or 0))
        zones = _zone_kcal(a, kcal)
        zone_s = ""
        if zones:
            dom = max(zones, key=zones.get)
            parts = " · ".join(f"Z{z} {round(v)}" for z, v in sorted(zones.items())
                               if round(v) > 0)
            zone_s = f" | Z{dom} ({parts} kcal)"
        hr = a.get("averageHR")
        hr_s = f" | avgHR {round(hr)}" if hr else ""
        print(f"{start} | {type_key} | {name} | {mins} min | {kcal} kcal{zone_s}{hr_s}")


def base(date, raw=False):
    opener, tokens = _session()
    days = []
    for i in range(1, 29):
        d = time.strftime(
            "%Y-%m-%d",
            time.localtime(time.mktime(time.strptime(date, "%Y-%m-%d")) - i * 86400),
        )
        bmr = get_summary(opener, tokens, d).get("bmrKilocalories")
        if bmr:
            days.append((d, bmr))
    if not days:
        raise GarminError("no BMR data in the last 28 days")
    avg = round(sum(b for _, b in days) / len(days))
    if raw:
        print(json.dumps({"average": avg, "days": dict(days)}, indent=2))
        return
    lo, hi = min(b for _, b in days), max(b for _, b in days)
    print(f"resting 4-week avg to {date}: {avg} kcal/day "
          f"({len(days)} days, range {round(lo)}-{round(hi)})")


def main():
    args = [a for a in sys.argv[1:] if a != "--json"]
    raw = "--json" in sys.argv
    if not args:
        print(__doc__.strip(), file=sys.stderr)
        sys.exit(2)
    cmd = args[0]
    try:
        if cmd == "login":
            login()
        elif cmd in ("fetch", "activities", "base"):
            date = args[1] if len(args) > 1 else time.strftime("%Y-%m-%d")
            {"fetch": fetch, "activities": activities, "base": base}[cmd](date, raw=raw)
        else:
            print(f"unknown command: {cmd}", file=sys.stderr)
            sys.exit(2)
    except GarminError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
