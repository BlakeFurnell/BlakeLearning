"""
services/sam_client.py

SAM.gov Opportunities API client.

Responsibilities:
- Authenticate requests to the SAM.gov REST API using SAM_API_KEY
- Search contract opportunities by NAICS code, set-aside type, and date range
  derived from a company profile returned by entity_lookup.get_company_profile()
- Deduplicate results across multiple NAICS code queries
- Normalize API responses into clean dicts for the rest of the app
- Cache results to sam_cache.json for 1 hour to avoid redundant API calls
- Handle rate limits (HTTP 429) with a clear user-facing message

Base URL: https://api.sam.gov/prod/opportunities/v2/search
Docs: https://open.gsa.gov/api/get-opportunities-public-api/
"""

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow imports from the project root (config.py lives there)
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from config import Config

_BASE_URL = "https://api.sam.gov/prod/opportunities/v2/search"
_CACHE_FILE = Path(__file__).parent.parent / "sam_cache.json"
_CACHE_MAX_AGE_SECONDS = 3600  # 1 hour


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(naics_codes: list[str], set_aside: str | None, days_back: int) -> str:
    codes = ",".join(sorted(naics_codes))
    return f"{codes}|{set_aside or ''}|{days_back}"


def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    try:
        _CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except OSError as exc:
        print(f"[sam_client] Warning: could not write cache file: {exc}", file=sys.stderr)


def _cache_is_fresh(entry: dict) -> bool:
    cached_at = entry.get("cached_at", 0)
    return (time.time() - cached_at) < _CACHE_MAX_AGE_SECONDS


# ---------------------------------------------------------------------------
# Response normalizer
# ---------------------------------------------------------------------------

def _normalize(opp: dict) -> dict:
    """Map a raw SAM.gov opportunity record to a clean internal dict."""
    notice_id = opp.get("noticeId", "")
    award = opp.get("award") or {}

    return {
        "notice_id": notice_id,
        "title": opp.get("title", ""),
        "solicitation_number": opp.get("solicitationNumber", ""),
        "department": opp.get("fullParentPathName", ""),
        "office": opp.get("organizationLocationDTO", {}).get("city", ""),
        "posted_date": opp.get("postedDate", ""),
        "response_deadline": opp.get("responseDeadLine", ""),
        "naics_code": opp.get("naicsCode", ""),
        "set_aside_type": opp.get("typeOfSetAsideDescription", ""),
        "set_aside_description": opp.get("typeOfSetAside", ""),
        "description": opp.get("description", ""),
        "url": f"https://sam.gov/opp/{notice_id}/view" if notice_id else "",
        "estimated_value": award.get("amount") if award else None,
    }


# ---------------------------------------------------------------------------
# Single NAICS query
# ---------------------------------------------------------------------------

def _fetch_for_naics(
    naics_code: str,
    set_aside: str | None,
    posted_from: str,
    posted_to: str,
    max_results: int,
) -> list[dict]:
    """
    Call the SAM.gov Opportunities API for a single NAICS code.
    Returns a list of normalized opportunity dicts.
    Raises ValueError on HTTP 429 (rate limit).
    """
    params: dict = {
        "naicsCode": naics_code,
        "postedFrom": posted_from,
        "postedTo": posted_to,
        "limit": max_results,
        "active": "Yes",
    }
    if set_aside:
        params["typeOfSetAside"] = set_aside

    response = requests.get(
        _BASE_URL,
        params=params,
        headers={"X-Api-Key": Config.SAM_API_KEY},
        timeout=20,
    )

    if response.status_code == 429:
        raise ValueError(
            "SAM.gov rate limit reached (HTTP 429). "
            "The public API allows ~1,000 requests/day per key. "
            "Wait a few minutes or check your api.sam.gov usage dashboard."
        )

    response.raise_for_status()

    data = response.json()
    raw_opps = data.get("opportunitiesData", []) or []
    return [_normalize(o) for o in raw_opps]


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def search_opportunities(
    company_profile: dict,
    days_back: int = 30,
    max_results: int = 25,
) -> list[dict]:
    """
    Search SAM.gov for active contract opportunities matching a company profile.

    Queries by the company's NAICS codes (up to 3) and first eligible set-aside
    code. Deduplicates results across queries by noticeId. Returns cached results
    if sam_cache.json was written less than 1 hour ago for the same query params.

    Args:
        company_profile: dict returned by entity_lookup.get_company_profile()
        days_back:        how many calendar days back to search (default 30)
        max_results:      max records per NAICS query (default 25)

    Returns:
        List of normalized opportunity dicts, deduplicated by noticeId.

    Raises:
        ValueError: on HTTP 429 rate limit or missing primary NAICS.
        requests.HTTPError: on other non-2xx API responses.
    """
    # ------------------------------------------------------------------ #
    # Build query parameters
    # ------------------------------------------------------------------ #
    naics_list_full = company_profile.get("naics_codes", [])
    primary = company_profile.get("primary_naics")

    if not naics_list_full:
        raise ValueError(
            "Company profile has no NAICS codes. "
            "Update your SAM.gov registration to include at least one NAICS code."
        )

    # Build query list: primary first (if flagged), then fill up to 3 total.
    # If no primary is flagged, fall back to the first 3 codes in the list.
    seen_codes: set[str] = set()
    naics_to_query: list[str] = []

    candidates = ([primary] + naics_list_full) if primary else naics_list_full
    for entry in candidates:
        code = entry.get("naicsCode", "") if isinstance(entry, dict) else entry
        if code and code not in seen_codes:
            seen_codes.add(code)
            naics_to_query.append(code)
        if len(naics_to_query) == 3:
            break

    set_aside_codes = company_profile.get("set_aside_eligibility", [])
    set_aside = set_aside_codes[0] if set_aside_codes else None

    today = datetime.now(timezone.utc).date()
    posted_from = (today - timedelta(days=days_back)).strftime("%m/%d/%Y")
    posted_to = today.strftime("%m/%d/%Y")

    # ------------------------------------------------------------------ #
    # Cache check
    # ------------------------------------------------------------------ #
    cache = _load_cache()
    key = _cache_key(naics_to_query, set_aside, days_back)

    if key in cache and _cache_is_fresh(cache[key]):
        print(
            f"[sam_client] Returning cached results "
            f"(age: {int(time.time() - cache[key]['cached_at'])}s)",
            file=sys.stderr,
        )
        return cache[key]["results"]

    # ------------------------------------------------------------------ #
    # API calls — one per NAICS code, deduplicated by noticeId
    # ------------------------------------------------------------------ #
    seen_ids: set[str] = set()
    all_results: list[dict] = []

    for code in naics_to_query:
        print(f"[sam_client] Querying NAICS {code} | set-aside: {set_aside or 'none'}", file=sys.stderr)
        opps = _fetch_for_naics(code, set_aside, posted_from, posted_to, max_results)
        for opp in opps:
            if opp["notice_id"] not in seen_ids:
                seen_ids.add(opp["notice_id"])
                all_results.append(opp)

    # ------------------------------------------------------------------ #
    # Persist to cache
    # ------------------------------------------------------------------ #
    cache[key] = {
        "cached_at": time.time(),
        "results": all_results,
    }
    _save_cache(cache)

    return all_results


# ---------------------------------------------------------------------------
# Stub class kept for future expansion
# ---------------------------------------------------------------------------

class SAMClient:
    """Wrapper around the SAM.gov Opportunities v2 API."""

    def search(self, company_profile: dict, days_back: int = 30, max_results: int = 25) -> list[dict]:
        """Delegate to the module-level search_opportunities function."""
        return search_opportunities(company_profile, days_back, max_results)

    def get_opportunity(self, notice_id: str) -> dict:
        """Return full detail for a single opportunity by noticeId."""
        pass


# ---------------------------------------------------------------------------
# CLI test harness
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json as _json

    # Minimal fake profile for quick CLI testing without hitting entity API
    # Usage: python sam_client.py <naics_code> [set_aside_code]
    # Example: python sam_client.py 541512 SBR
    if len(sys.argv) < 2:
        print("Usage: python sam_client.py <naics_code> [set_aside_code]")
        sys.exit(1)

    naics_arg = sys.argv[1]
    set_aside_arg = sys.argv[2] if len(sys.argv) > 2 else None

    fake_profile = {
        "company_name": "Test Company",
        "cage_code": "TEST",
        "primary_naics": {"naicsCode": naics_arg, "naicsDescription": "", "isPrimary": True},
        "naics_codes": [{"naicsCode": naics_arg, "naicsDescription": "", "isPrimary": True}],
        "set_aside_eligibility": [set_aside_arg] if set_aside_arg else [],
    }

    try:
        results = search_opportunities(fake_profile)
        print(_json.dumps(results, indent=2))
        print(f"\n[{len(results)} opportunities found]", file=sys.stderr)
    except ValueError as exc:
        print(f"[Error] {exc}", file=sys.stderr)
        sys.exit(1)
    except requests.HTTPError as exc:
        print(f"[HTTP Error] {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        sys.exit(1)
