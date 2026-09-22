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
  python3 scripts/garmin_sync.py weight [YYYY-MM-DD]     # latest weigh-in up to date
  python3 scripts/garmin_sync.py apply [YYYY-MM-DD] [--back N] [--base] [--dry-run]
                                                         # sync the diary with the watch
  (fetch/activities/base/weight accept --json for the raw API response)

`apply` is the whole «синк гармин» in one call: logs the day's workouts
that are not in the diary yet (zone in the name — from power zones for
cycling, from HR otherwise; split rows when a Z1–2 and a Z3+ part both
carry ≥150 kcal, `other` skipped), writes or updates
the NEAT top-up row «Прочая активность (Garmin)» (skipped when marked
«ручной фикс» / «не синкать», updated only when off by >20 kcal),
appends a new weigh-in with body composition to config/user.md, and with
--base compares the 4-week resting average with the constant there
(>2% off → constant and effective base updated). --back N repeats the
workout/NEAT sync for the N previous days. No token file → exits 0
silently (a machine without Garmin). --fixture FILE feeds the API
answers from JSON instead (tests, offline).
"""

import base64
import re
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


def _power_times(a):
    """Seconds per HR-scale zone from Garmin's 7 power zones, or {} if absent.

    Power zones 6–7 (anaerobic, neuromuscular) fold into Z5: the diary only
    ever asks «low or high», and nothing above Z5 exists on the HR scale.
    """
    times = {}
    for pz in range(1, 8):
        t = a.get(f"powerTimeInZone_{pz}") or 0.0
        if t:
            times[min(pz, 5)] = times.get(min(pz, 5), 0.0) + t
    return times


def _zone_kcal(a, active_kcal, type_key=None):
    """Split a workout's active kcal across zones pro rata by time.

    Cycling with a power meter is read from powerTimeInZone_1..7: on a long
    ride HR drifts upward at constant effort, so heart rate alone labels a
    steady Z2 as intervals. Everything else — and cycling without power —
    falls back to hrTimeInZone_1..5. Kcal are not reported per zone, so time
    share is the estimate. Empty zones dropped.
    """
    times = _power_times(a) if type_key in POWER_ZONE_TYPES else {}
    if not times:
        times = {z: a.get(f"hrTimeInZone_{z}") or 0.0 for z in range(1, 6)}
    total = sum(times.values())
    if not total or not active_kcal:
        return {}
    return {z: active_kcal * t / total for z, t in times.items() if t > 0}


def get_activities(opener, tokens, date):
    return api_get(
        opener, tokens,
        "/activitylist-service/activities/search/activities",
        params={"startDate": date, "endDate": date,
                "start": "0", "limit": "50"},
    ) or []


def get_weight(opener, tokens, date):
    return api_get(
        opener, tokens, "/weight-service/weight/latest",
        params={"date": date, "ignorePriority": "true"},
    ) or {}


def base_days(opener, tokens, date):
    """[(day, bmr)] for the 28 days before `date`."""
    days = []
    for i in range(1, 29):
        d = time.strftime(
            "%Y-%m-%d",
            time.localtime(time.mktime(time.strptime(date, "%Y-%m-%d")) - i * 86400),
        )
        bmr = get_summary(opener, tokens, d).get("bmrKilocalories")
        if bmr:
            days.append((d, bmr))
    return days


def activities(date, raw=False):
    opener, tokens = _session()
    acts = get_activities(opener, tokens, date)
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
        zones = _zone_kcal(a, kcal, type_key)
        zone_s = ""
        if zones:
            # A workout counts as steady (Z1/Z2) only if ≥90% of its kcal live
            # there; otherwise the Z3+ share is meaningful → treat it as an
            # interval/hard session so day_load buckets it as high, not mid.
            total_z = sum(zones.values())
            low = zones.get(1, 0.0) + zones.get(2, 0.0)
            low_share = low / total_z if total_z else 0.0
            parts = " · ".join(f"Z{z} {round(v)}" for z, v in sorted(zones.items())
                               if round(v) > 0)
            if low_share >= 0.90:
                dom = max((z for z in zones if z <= 2), key=zones.get)
                label = f"Z{dom}"
            else:
                hi_dom = max((z for z in zones if z >= 3), key=zones.get, default=3)
                label = f"интервалы Z{hi_dom}"
            zone_s = f" | {label} · low {round(low_share * 100)}% ({parts} kcal)"
        hr = a.get("averageHR")
        hr_s = f" | avgHR {round(hr)}" if hr else ""
        print(f"{start} | {type_key} | {name} | {mins} min | {kcal} kcal{zone_s}{hr_s}")


def base(date, raw=False):
    opener, tokens = _session()
    days = base_days(opener, tokens, date)
    if not days:
        raise GarminError("no BMR data in the last 28 days")
    avg = round(sum(b for _, b in days) / len(days))
    if raw:
        print(json.dumps({"average": avg, "days": dict(days)}, indent=2))
        return
    lo, hi = min(b for _, b in days), max(b for _, b in days)
    print(f"resting 4-week avg to {date}: {avg} kcal/day "
          f"({len(days)} days, range {round(lo)}-{round(hi)})")


def weight(date, raw=False):
    opener, tokens = _session()
    res = get_weight(opener, tokens, date)
    if raw:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return
    grams = (res or {}).get("weight")
    if not grams:
        print(f"no weigh-ins up to {date}")
        return
    kg = round(grams / 1000, 1)
    when = res.get("calendarDate") or date
    source = res.get("sourceType") or "?"
    print(f"weight: {kg} kg | date: {when} | source: {source}")

    fat = res.get("bodyFat")
    muscle_g = res.get("muscleMass")
    bone_g = res.get("boneMass")
    water = res.get("bodyWater")
    bmi = res.get("bmi")
    extras = []
    if fat is not None:
        extras.append(f"fat: {round(fat, 1)}%")
        if muscle_g:
            extras.append(f"muscle: {round(muscle_g / 1000, 1)} kg")
        if bone_g:
            extras.append(f"bone: {round(bone_g / 1000, 2)} kg")
        if water is not None:
            extras.append(f"water: {round(water, 1)}%")
        if bmi is not None:
            extras.append(f"bmi: {round(bmi, 1)}")
    if extras:
        print("body: " + " | ".join(extras))



# ---------------------------------------------------------------- apply --
# The whole «синк гармин» as one call (AGENTS.md «Синхронизация с Garmin»).

TYPE_RU = {
    "walking": "Прогулка", "hiking": "Хайкинг",
    "running": "Бег", "trail_running": "Бег", "treadmill_running": "Бег (дорожка)",
    "cycling": "Вело", "indoor_cycling": "Вело", "virtual_ride": "Вело",
    "road_biking": "Вело", "mountain_biking": "Вело", "gravel_cycling": "Вело",
    "lap_swimming": "Плавание", "open_water_swimming": "Плавание",
    "strength_training": "Силовая", "elliptical": "Эллипс",
    "indoor_rowing": "Гребля", "rowing": "Гребля", "indoor_cardio": "Кардио",
    "hiit": "Интервалы", "yoga": "Йога", "pilates": "Пилатес",
}
SKIP_TYPES = {"other"}                       # contrast shower & co: NEAT covers them
NO_ZONE_TYPES = {"walking", "hiking", "strength_training", "yoga", "pilates"}
POWER_ZONE_TYPES = {"cycling", "indoor_cycling", "virtual_ride", "road_biking",
                    "mountain_biking", "gravel_cycling"}  # zones from power, HR is the fallback
SPLIT_MIN_KCAL = 150                         # Z1–2 part and Z3+ part both ≥ this → two rows
NEAT_NAME = "Прочая активность (Garmin)"
NEAT_MATCH = "Прочая активность"           # the row may carry a suffix («…, ручной фикс»)
NEAT_TIME = "23:59"
NEAT_TOLERANCE = 20                          # kcal; smaller drift leaves the row alone
MANUAL_RE = re.compile(r"ручной фикс|не синкать", re.IGNORECASE)
BASE_DRIFT = 0.02
ROW_RE = re.compile(r"^\|\s*(\d{2}:\d{2})\s*\|\s*([^|]*?)\s*\|\s*(-?[\d.]+)\s*\|")
WEIGHT_ROW_RE = re.compile(
    r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([\d.]+)\s*\|\s*([\d.]*)\s*\|\s*([\d.]*)\s*\|\s*([\d.]*)\s*\|")
BASE_LINE_RE = re.compile(
    r"^(- Базовый расход \(Garmin API, 4-нед среднее покоя, )(\d{4}-\d{2}-\d{2})(\): )(\d+)( ккал.*)$",
    re.MULTILINE)
CORRECTION_RE = re.compile(r"Поправка базового расхода:\s*([−-]?\d+)")
EFFECTIVE_RE = re.compile(r"(\*\*Эффективный базовый расход\*\* = сырой \+ поправка = )(\d+)( ккал)")


class Live:
    """API-backed data source for apply()."""

    def __init__(self):
        self.opener, self.tokens = _session()

    def summary(self, d):
        return get_summary(self.opener, self.tokens, d)

    def activities(self, d):
        return get_activities(self.opener, self.tokens, d)

    def weight(self, d):
        return get_weight(self.opener, self.tokens, d)

    def base_avg(self, d):
        days = base_days(self.opener, self.tokens, d)
        return round(sum(b for _, b in days) / len(days)) if days else None


class Fixture:
    """JSON-backed data source: {"summary": {...}, "activities": [...],
    "weight": {...}, "base": N} (per-day keys may be dicts keyed by date)."""

    def __init__(self, path):
        self.data = json.loads(Path(path).read_text(encoding="utf-8"))

    def _pick(self, key, d, default):
        v = self.data.get(key, default)
        return v.get(d, default) if isinstance(v, dict) and d in v else v

    def summary(self, d):
        return self._pick("summary", d, {})

    def activities(self, d):
        return self._pick("activities", d, [])

    def weight(self, d):
        return self._pick("weight", d, {})

    def base_avg(self, d):
        return self.data.get("base")


def workout_rows(a):
    """Diary rows for one Garmin activity: [(name, kcal)]; [] when skipped."""
    type_key = (a.get("activityType") or {}).get("typeKey", "?")
    if type_key in SKIP_TYPES:
        return []
    kcal = round((a.get("calories") or 0) - (a.get("bmrCalories") or 0))
    if kcal <= 0:
        return []
    mins = round((a.get("duration") or 0) / 60)
    ru = TYPE_RU.get(type_key) or (a.get("activityName") or type_key)
    if type_key in NO_ZONE_TYPES:
        return [(f"{ru} {mins} мин", kcal)]
    zones = _zone_kcal(a, kcal, type_key)
    if not zones:
        return [(f"{ru} Z2 {mins} мин", kcal)]   # no HR data: unknown zone counts as Z2
    low = zones.get(1, 0.0) + zones.get(2, 0.0)
    high = sum(v for z, v in zones.items() if z >= 3)
    if low >= SPLIT_MIN_KCAL and high >= SPLIT_MIN_KCAL:
        lo_z = max((z for z in zones if z <= 2), key=zones.get)
        hi_z = max((z for z in zones if z >= 3), key=zones.get)
        lo_min = round(mins * low / (low + high))
        return [(f"{ru} Z{lo_z} (часть) {lo_min} мин", round(low)),
                (f"{ru} Z{hi_z} (часть) {mins - lo_min} мин", round(high))]
    low_share = low / (low + high) if (low + high) else 0.0
    if low_share >= 0.90:
        dom = max((z for z in zones if z <= 2), key=zones.get)
        label = f"Z{dom}"
    else:
        hi_dom = max((z for z in zones if z >= 3), key=zones.get, default=3)
        label = f"интервалы Z{hi_dom}"
    return [(f"{ru} {label} {mins} мин", kcal)]


def training_rows(lines):
    """[(index, time, name, kcal>0)] for the diary's training rows (К<0)."""
    out = []
    for i, l in enumerate(lines):
        m = ROW_RE.match(l.strip())
        if m and float(m.group(3)) < 0:
            out.append((i, m.group(1), m.group(2), -float(m.group(3))))
    return out


def sync_diary(d, src, path, dry_run):
    """Workouts + NEAT row for one day. Returns (report lines, changed)."""
    import recalc_plan
    from format_tables import format_file
    from log import activity_row, insert_rows
    from validate_diary import validate

    day = d.isoformat()
    lines = path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    if not any("Продукт/Активность" in l for l in lines):
        lines = recalc_plan.apply(lines, recalc_plan.compute(lines, d))
    report, changed = [], False

    logged_times = {t for _, t, n, _ in training_rows(lines) if NEAT_MATCH not in n}
    new_rows = []
    for a in sorted(src.activities(day), key=lambda a: a.get("startTimeLocal", "")):
        start = (a.get("startTimeLocal") or "")[11:16]
        if start in logged_times:
            continue
        rows = workout_rows(a)
        if not rows:
            continue
        for name, kcal in rows:
            report.append(f"{day}: + {name} −{kcal} ({start})")
        new_rows.append((start, [activity_row(start, n, k) for n, k in rows]))
    for start, rows in new_rows:
        lines = insert_rows(lines, rows, start)
        changed = True

    active = (src.summary(day) or {}).get("activeKilocalories")
    if active is not None:
        rows = training_rows(lines)
        neat = [(i, n, k) for i, _, n, k in rows if NEAT_MATCH in n]
        spent = sum(k for _, _, n, k in rows if NEAT_MATCH not in n)
        diff = round(active - spent)
        if neat:
            i, name, current = neat[0]
            if MANUAL_RE.search(name):
                report.append(f"{day}: {name} −{current:.0f} — ручной фикс, не трогаю")
            elif abs(diff - current) > NEAT_TOLERANCE:
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                cells[2] = f"-{diff}"
                lines[i] = "| " + " | ".join(cells) + " |"
                report.append(f"{day}: {NEAT_NAME} −{diff} (было −{current:.0f})")
                changed = True
        elif diff > 0:
            lines = insert_rows(lines, [activity_row(NEAT_TIME, NEAT_NAME, diff)], NEAT_TIME)
            report.append(f"{day}: + {NEAT_NAME} −{diff}")
            changed = True

    if changed and not dry_run:
        c = recalc_plan.compute(lines, d)
        lines = recalc_plan.apply(lines, c)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        format_file(path)
        errs = validate(path)
        report += [f"   ✗ {e}" for e in errs]
    return report, changed, lines


def sync_weight(d, src, user_path, dry_run):
    """Append a new weigh-in (with body composition) to the user.md table."""
    res = src.weight(d.isoformat())
    grams = (res or {}).get("weight")
    if not grams:
        return []
    kg = round(grams / 1000, 1)
    when = res.get("calendarDate") or d.isoformat()
    fat = res.get("bodyFat")
    muscle = round(res["muscleMass"] / 1000, 1) if res.get("muscleMass") else None
    bone = round(res["boneMass"] / 1000, 2) if res.get("boneMass") else None
    text = user_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    rows = [(i, m) for i, l in enumerate(lines) if (m := WEIGHT_ROW_RE.match(l))]
    if rows:
        last_i, last = max(rows, key=lambda r: r[1].group(1))
        last_date, last_kg = last.group(1), float(last.group(2))
        last_fat = float(last.group(3)) if last.group(3) else None
        last_muscle = float(last.group(4)) if last.group(4) else None
        if when <= last_date:
            return []
        same = kg == last_kg
        moved = ((fat is not None and last_fat is not None and abs(fat - last_fat) >= 0.5) or
                 (muscle is not None and last_muscle is not None and abs(muscle - last_muscle) >= 0.5))
        if same and not moved:
            return []
        insert_at = max(i for i, _ in rows) + 1
    else:
        insert_at = len(lines)
    fmt = lambda v, nd: ("" if v is None else f"{v:.{nd}f}")
    row = f"| {when} | {kg} | {fmt(fat, 1)} | {fmt(muscle, 1)} | {fmt(bone, 2)} | Garmin |"
    body = " · ".join(s for s in (f"жир {fat:.1f}%" if fat is not None else "",
                                   f"мышцы {muscle}" if muscle else "",
                                   f"кость {bone}" if bone else "") if s)
    if not dry_run:
        lines.insert(insert_at, row)
        user_path.write_text("\n".join(lines), encoding="utf-8")
        from format_tables import format_file
        format_file(user_path)
    return [f"вес: {kg} кг ({when}) → {user_path.name}" + (f"; {body}" if body else "")]


def sync_base(d, src, user_path, dry_run):
    """Compare the 4-week resting average with the user.md constant."""
    avg = src.base_avg(d.isoformat())
    if not avg:
        return ["база: Garmin не дал данных за 28 дней"]
    text = user_path.read_text(encoding="utf-8")
    m = BASE_LINE_RE.search(text)
    if not m:
        return [f"база: Garmin {avg} ккал; строка константы в {user_path.name} не найдена — сверить руками"]
    raw = int(m.group(4))
    drift = (avg - raw) / raw
    if abs(drift) <= BASE_DRIFT:
        return [f"база: Garmin {avg} vs константа {raw} ({drift:+.1%}) — без изменений"]
    corr = CORRECTION_RE.search(text)
    correction = int(corr.group(1).replace("−", "-")) if corr else 0
    effective = avg + correction
    if not dry_run:
        text = BASE_LINE_RE.sub(lambda mm: f"{mm.group(1)}{d.isoformat()}{mm.group(3)}{avg}{mm.group(5)}", text, count=1)
        text = EFFECTIVE_RE.sub(lambda mm: f"{mm.group(1)}{effective}{mm.group(3)}", text, count=1)
        user_path.write_text(text, encoding="utf-8")
    return [f"база: Garmin {avg} vs константа {raw} ({drift:+.1%}) → константа {avg}, "
            f"эффективный {effective} (поправка {correction:+d}); новый дневник возьмёт его из {user_path.name}"]


def apply(date_s, back=0, with_base=False, dry_run=False, fixture=None,
          diary=None, user=None):
    from datetime import date as _date, timedelta
    import recalc_plan
    from paths import USER, diary_path

    if fixture:
        src = Fixture(fixture)
    else:
        if not TOKEN_PATH.exists():
            return 0                       # a machine without Garmin: stay silent
        src = Live()
    user_path = Path(user) if user else USER
    ref = _date.fromisoformat(date_s)
    days = [ref] if diary else [ref - timedelta(days=i) for i in range(back, -1, -1)]
    report, last_lines = [], None
    for d in days:
        path = Path(diary).resolve() if diary else diary_path(d)
        if not path.exists():
            report.append(f"{d}: дневника нет — пропуск")
            continue
        rep, changed, lines = sync_diary(d, src, path, dry_run)
        report += rep or [f"{d}: тренировки и докрутка на месте"]
        if d == ref:
            last_lines = lines
    report += sync_weight(ref, src, user_path, dry_run)
    if with_base:
        report += sync_base(ref, src, user_path, dry_run)
    if dry_run:
        report = ["(dry-run, ничего не записано)"] + report
    print("\n".join(report))
    if last_lines is not None and not dry_run:
        c = recalc_plan.compute(last_lines, ref)
        print()
        print("\n".join(recalc_plan.status_block(c, ref, last_lines)))
    return 0


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
        elif cmd == "apply":
            opts, pos = {}, []
            rest = args[1:]
            i = 0
            while i < len(rest):
                a = rest[i]
                if a in ("--back", "--fixture", "--diary", "--user"):
                    i += 1
                    if i >= len(rest):
                        raise GarminError(f"{a}: требуется значение")
                    opts[a[2:]] = rest[i]
                elif a in ("--base", "--dry-run"):
                    opts[a[2:].replace("-", "_")] = True
                elif a.startswith("--"):
                    raise GarminError(f"неизвестный флаг {a}")
                else:
                    pos.append(a)
                i += 1
            date = pos[0] if pos else time.strftime("%Y-%m-%d")
            sys.exit(apply(date, back=int(opts.get("back", 0)),
                           with_base=opts.get("base", False),
                           dry_run=opts.get("dry_run", False),
                           fixture=opts.get("fixture"), diary=opts.get("diary"),
                           user=opts.get("user")))
        elif cmd in ("fetch", "activities", "base", "weight"):
            date = args[1] if len(args) > 1 else time.strftime("%Y-%m-%d")
            {"fetch": fetch, "activities": activities,
             "base": base, "weight": weight}[cmd](date, raw=raw)
        else:
            print(f"unknown command: {cmd}", file=sys.stderr)
            sys.exit(2)
    except GarminError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
