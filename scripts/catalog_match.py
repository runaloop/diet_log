"""Fuzzy catalog lookup — shared by `db.py` and `plan_ration.find_canonical`.

Resolution order never changes: exact name/alias, then substring, and only
then this module. It exists because the substring stage breaks on two things
users do constantly — typos (`эксненту`) and word order (`овсянка быстров`
against «Овсянка по-новому Быстров»).

Matching is per token, not per string: the query is split into words and a
candidate qualifies only when *every* query word resembles some word of one
of its strings (its canonical name or one alias). One mechanism covers both
failure modes — word order stops mattering, and each word tolerates a typo.

Thresholds are measured, not guessed (see tests/test_catalog_match.py):

    worst required match   бострв ~ быстров      0.769
                           эксненту ~ экспонента 0.778
    closest false match    грени ~ греча         0.600
                           смит ~ сметана        0.545

TOKEN_THRESHOLD sits in that gap. Ambiguity stays an error: a silently wrong
product is worse than a refusal, so a winner must lead the runner-up by
MARGIN or the caller is handed every tie.
"""
import re
from difflib import SequenceMatcher

TOKEN_THRESHOLD = 0.75
MARGIN = 0.05
# Similarity alone cannot separate «Экспонента» from «Экспонента Био Скир»:
# the matched word is identical. Coverage — how much of the candidate the
# query actually accounts for — breaks that tie, and only that tie.
COVERAGE_MARGIN = 0.05
# Below this length a typo is indistinguishable from a different word, so
# short tokens must match exactly ('смит', 'чиа', '2%').
MIN_FUZZY_LEN = 4

_TOKEN_RE = re.compile(r'\w+', re.UNICODE)
_DIGIT_RE = re.compile(r'\d')


def tokenize(text):
    """'Exponenta High-Pro 20г' -> ['exponenta', 'high', 'pro', '20г'].

    Lowercasing mirrors the `ulower` SQLite function registered in db.py.
    """
    return _TOKEN_RE.findall((text or '').lower())


def token_score(a, b):
    """Similarity of two words, 0..1. Exact-only for short or numeric ones."""
    if a == b:
        return 1.0
    if _DIGIT_RE.search(a) or _DIGIT_RE.search(b):
        return 0.0          # '20г' must not pass for '25г'
    if len(a) < MIN_FUZZY_LEN or len(b) < MIN_FUZZY_LEN:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def string_score(q_tokens, cand_tokens):
    """(similarity, coverage), or None when some query word matched nothing.

    similarity — mean of each query word's best match, the value the
    threshold is measured against. coverage — the share of the candidate's
    own words the query accounts for, so a query naming a product in full
    outranks one clipping a word off a longer name.
    """
    if not q_tokens or not cand_tokens:
        return None
    total = 0.0
    for q in q_tokens:
        best = max(token_score(q, c) for c in cand_tokens)
        if best < TOKEN_THRESHOLD:
            return None
        total += best
    coverage = min(1.0, len(q_tokens) / len(cand_tokens))
    return total / len(q_tokens), coverage


def rank(query, entries):
    """[(similarity, coverage, name)] best first, over [(name, [strings...])].

    A candidate is scored against each of its strings separately — its own
    name and each alias — and keeps the best. Pooling every alias into one
    bag would let a product with many aliases satisfy a query word by word
    from unrelated aliases.
    """
    q_tokens = tokenize(query)
    if not q_tokens:
        return []
    scored = []
    for name, strings in entries:
        best = None
        for s in strings:
            sc = string_score(q_tokens, tokenize(s))
            if sc is not None and (best is None or sc > best):
                best = sc
        if best is not None:
            scored.append((best[0], best[1], name))
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    return scored


def best(query, entries):
    """(name, ties). Exactly one of them is meaningful.

    Returns (name, []) on a clear winner, (None, [tied names]) when the lead
    is under MARGIN, and (None, []) when nothing matched at all.
    """
    scored = rank(query, entries)
    if not scored:
        return None, []
    top_sim = scored[0][0]
    near = [t for t in scored if top_sim - t[0] < MARGIN]
    if len(near) == 1:
        return near[0][2], []
    top_cov = near[0][1]
    ties = [n for _, cov, n in near if top_cov - cov < COVERAGE_MARGIN]
    if len(ties) > 1:
        return None, sorted(ties)
    return near[0][2], []
