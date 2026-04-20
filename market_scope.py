import time

import requests

import config


_DEFAULT_SPORT_CODES = frozenset(
    {
        "abb",
        "acn",
        "afc",
        "ahl",
        "arg",
        "atp",
        "aus",
        "bkaba",
        "bkarg",
        "bkbbl",
        "bkbsl",
        "bkcba",
        "bkcl",
        "bkfibaqaf",
        "bkfibaqam",
        "bkfibaqas",
        "bkfibaqeu",
        "bkfr1",
        "bkgr1",
        "bkjpn",
        "bkkbl",
        "bkligend",
        "bknbl",
        "bkseriea",
        "bkvtb",
        "bl2",
        "bol1",
        "bra",
        "bra2",
        "bun",
        "caf",
        "cbb",
        "cde",
        "cdr",
        "cehl",
        "cfb",
        "chess",
        "chi",
        "chi1",
        "codmw",
        "cof",
        "col",
        "col1",
        "con",
        "crafgwi20",
        "craus",
        "crban",
        "crbtnmlyhkg20",
        "creng",
        "cricbbl",
        "cricbpl",
        "criccpl",
        "criccsat20w",
        "crichkt20w",
        "cricilt20",
        "cricipl",
        "criclcl",
        "cricmlc",
        "cricnt20c",
        "cricpakt20cup",
        "cricps",
        "cricpsl",
        "cricsa20",
        "cricsm",
        "cricss",
        "crict20blast",
        "crict20lpl",
        "crict20plw",
        "crictbcl",
        "cricthunderbolt",
        "cricwncl",
        "crind",
        "crint",
        "crnew",
        "crpak",
        "crsou",
        "cru19wc",
        "cruae",
        "crwncl",
        "crwpl20",
        "crwt20wcgq",
        "cs2",
        "csa",
        "cwbb",
        "cze1",
        "dehl",
        "den",
        "dfb",
        "dota2",
        "efa",
        "efl",
        "egy1",
        "elc",
        "epl",
        "ere",
        "es2",
        "euroleague",
        "fif",
        "fifa",
        "fifwc",
        "fl1",
        "fr2",
        "hok",
        "ind",
        "ipl",
        "itc",
        "itsb",
        "j1100",
        "j2100",
        "ja2",
        "jap",
        "kbo",
        "khl",
        "kor",
        "lal",
        "lcs",
        "lib",
        "lol",
        "lpl",
        "mar1",
        "mex",
        "mlb",
        "mlbb",
        "mls",
        "mwoh",
        "nba",
        "ncaab",
        "nfl",
        "nhl",
        "nor",
        "odi",
        "ofc",
        "ow",
        "per1",
        "pll",
        "por",
        "powerslap",
        "psp",
        "pubg",
        "r6siege",
        "rl",
        "rou1",
        "ruchamp",
        "rueuchamp",
        "ruprem",
        "rus",
        "rusixnat",
        "rusrp",
        "rutopft",
        "ruurc",
        "sasa",
        "sc",
        "sc2",
        "sea",
        "she",
        "shl",
        "snhl",
        "spl",
        "ssc",
        "sud",
        "t20",
        "test",
        "testtesttest",
        "tur",
        "ucl",
        "uef",
        "uel",
        "ufc",
        "ukr1",
        "uwcl",
        "val",
        "wbc",
        "wildrift",
        "wll",
        "wnba",
        "wta",
        "wttmen",
        "wwoh",
        "zuffa",
    }
)
_DEFAULT_ESPORT_CODES = frozenset(
    {
        "codmw",
        "cs2",
        "dota2",
        "hok",
        "lcs",
        "lol",
        "lpl",
        "mlbb",
        "ow",
        "pubg",
        "r6siege",
        "rl",
        "sc2",
        "val",
        "wildrift",
    }
)
_catalog_cache = {"codes": _DEFAULT_SPORT_CODES, "fetched_at": 0.0}


def _normalize_slug(slug):
    return str(slug or "").strip().lower()


def _sport_code_from_slug(slug):
    normalized = _normalize_slug(slug)
    if not normalized:
        return ""
    return normalized.split("-", 1)[0]


def _fetch_catalog_codes():
    response = requests.get(f"{config.GAMMA_API_BASE}/sports", timeout=15)
    response.raise_for_status()
    rows = response.json()
    codes = {str(item.get("sport") or "").strip().lower() for item in rows if item.get("sport")}
    if not codes:
        raise ValueError("empty sports catalog")
    return frozenset(codes)


def get_sport_codes():
    now = time.time()
    if _catalog_cache["codes"] and now - _catalog_cache["fetched_at"] < config.MARKET_SCOPE_CACHE_SEC:
        return _catalog_cache["codes"]

    try:
        _catalog_cache["codes"] = _fetch_catalog_codes()
        _catalog_cache["fetched_at"] = now
    except Exception:
        _catalog_cache["fetched_at"] = now
        if not _catalog_cache["codes"]:
            _catalog_cache["codes"] = _DEFAULT_SPORT_CODES
    return _catalog_cache["codes"] or _DEFAULT_SPORT_CODES


def get_esports_codes():
    configured = {code for code in config.ESPORT_SPORT_CODES if code}
    return configured or set(_DEFAULT_ESPORT_CODES)


def classify_market_slug(slug):
    normalized_slug = _normalize_slug(slug)
    sport_code = _sport_code_from_slug(normalized_slug)
    sports_codes = get_sport_codes()
    esports_codes = get_esports_codes()

    if sport_code in esports_codes:
        bucket = "esports"
    elif sport_code in sports_codes:
        bucket = "sports"
    elif sport_code:
        bucket = "other"
    else:
        bucket = "unknown"

    return {
        "market_slug": normalized_slug,
        "sport_code": sport_code,
        "market_scope": bucket,
        "is_sports_universe": sport_code in sports_codes,
    }


def evaluate_trade_scope(trade):
    info = classify_market_slug(trade.get("market_slug") or trade.get("title", ""))
    allowed_scope = config.market_scope_set()

    if "all" in allowed_scope:
        info["allowed"] = True
        info["scope_reason"] = "all_markets_enabled"
        return info

    if info["market_scope"] == "unknown":
        info["allowed"] = False
        info["scope_reason"] = "missing_market_slug"
        return info

    if not info["is_sports_universe"]:
        info["allowed"] = False
        info["scope_reason"] = "outside_sports_universe"
        return info

    if info["market_scope"] == "esports":
        info["allowed"] = "esports" in allowed_scope
        info["scope_reason"] = "allowed" if info["allowed"] else "esports_disabled"
        return info

    info["allowed"] = "sports" in allowed_scope
    info["scope_reason"] = "allowed" if info["allowed"] else "sports_disabled"
    return info
