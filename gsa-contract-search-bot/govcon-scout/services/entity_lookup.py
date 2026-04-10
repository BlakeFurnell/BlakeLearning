"""
services/entity_lookup.py

SAM.gov Entity (vendor/contractor) registration lookup client.

Responsibilities:
- Query the SAM.gov Entity Management API by CAGE code
- Return registration status, business type, socioeconomic certifications,
  NAICS codes, and past-performance summary
- Identify incumbents and potential teaming partners from opportunity awardee data
- Cache results to avoid redundant API calls within a session

Base URL: https://api.sam.gov/entity-information/v3/entities
Docs: https://open.gsa.gov/api/entity-api/
"""

import sys
import json
from pathlib import Path

# Allow imports from the project root (config.py lives there)
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from config import Config

# ---------------------------------------------------------------------------
# Module-level cache: keyed by CAGE code → parsed profile dict
# Seeded at import time from entity_cache.json if it exists.
# ---------------------------------------------------------------------------
_ENTITY_CACHE_FILE = Path(__file__).parent.parent / "entity_cache.json"

def _load_entity_cache() -> dict[str, dict]:
    if _ENTITY_CACHE_FILE.exists():
        try:
            return json.loads(_ENTITY_CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}

_cache: dict[str, dict] = _load_entity_cache()

# SAM.gov Entity Management API endpoint
_BASE_URL = "https://api.sam.gov/entity-information/v3/entities"

# Map of business type descriptions → SAM.gov set-aside codes
_SET_ASIDE_MAP: dict[str, str] = {
    "Small Business": "SBR",
    "Woman Owned Small Business": "WOSB",
    "Economically Disadvantaged Woman-Owned Small Business": "EDWOSB",
    "Economically Disadvantaged WOSB": "EDWOSB",
    "Service-Disabled Veteran Owned Small Business": "SDVOSBC",
    "Service-Disabled Veteran Owned": "SDVOSBC",
    "Veteran Owned Small Business": "VOSBC",
    "HUBZone Small Business": "HZS",
    "HUBZone": "HZS",
    "8(a) Program Participant": "SBA",
    "8(a)": "SBA",
}


def get_company_profile(cage_code: str) -> dict:
    """
    Fetch and return a normalized company profile for the given CAGE code.

    Calls the SAM.gov Entity Management API v3 and returns a dict containing:
      - company_name
      - cage_code
      - uei              (returned by the API, stored for reference)
      - registration_status
      - naics_codes           list of {naicsCode, naicsDescription, isPrimary}
      - primary_naics         the entry flagged as primary (or None)
      - business_types        list of businessTypeDesc strings
      - set_aside_eligibility list of SAM.gov set-aside codes derived from business types

    Raises:
        ValueError: if the CAGE code is not found or the registration is not Active.
        requests.HTTPError: on non-2xx API responses.
    """
    cage_code = cage_code.strip().upper()

    if cage_code in _cache:
        return _cache[cage_code]

    params = {
        "cageCode": cage_code,
        "includeSections": "entityRegistration,coreData,assertions,repsAndCerts",
    }
    headers = {"X-Api-Key": Config.SAM_API_KEY}

    response = requests.get(_BASE_URL, params=params, headers=headers, timeout=15)
    response.raise_for_status()

    data = response.json()

    # The API returns {"entityData": [...], "totalRecords": N}
    entities = data.get("entityData", [])
    if not entities:
        raise ValueError(
            f"No entity found for CAGE code '{cage_code}'. "
            "Verify the code at sam.gov or check that the registration is active."
        )

    entity = entities[0]

    # ------------------------------------------------------------------ #
    # Registration status check
    # ------------------------------------------------------------------ #
    entity_reg = entity.get("entityRegistration", {})
    status = entity_reg.get("registrationStatus", "")
    if status != "Active":
        raise ValueError(
            f"Entity with CAGE '{cage_code}' registration is not Active "
            f"(current status: '{status}'). "
            "Only active registrations can be used for set-aside eligibility."
        )

    # ------------------------------------------------------------------ #
    # Company name and identifiers
    # ------------------------------------------------------------------ #
    core_data = entity.get("coreData", {})
    company_name = entity_reg.get("legalBusinessName", "") or \
                   core_data.get("entityInformation", {}).get("entityURL", "")

    # UEI is also returned by the API and useful to store for reference
    uei = entity_reg.get("ueiSAM", "")

    # ------------------------------------------------------------------ #
    # NAICS codes
    # SAM.gov Entity API v3 stores NAICS under assertions.goodsAndServices.naicsList.
    # Fall back to coreData.naicsCode if the assertions section is absent.
    # ------------------------------------------------------------------ #
    assertions = entity.get("assertions", {})
    raw_naics = (
        (assertions.get("goodsAndServices") or {}).get("naicsList")
        or (assertions.get("goodsAndServices") or {}).get("naicsCode")
        or core_data.get("naicsCode")
        or []
    )
    naics_codes = [
        {
            "naicsCode": n.get("naicsCode", ""),
            "naicsDescription": n.get("naicsDescription", ""),
            "isPrimary": str(n.get("naicsPrimaryIndicator", "N")).upper() in ("Y", "YES", "TRUE", "1"),
        }
        for n in raw_naics
    ]

    primary_naics = next((n for n in naics_codes if n["isPrimary"]), None)
    # If the API didn't flag any code as primary, treat the first one as primary
    if primary_naics is None and naics_codes:
        primary_naics = naics_codes[0]

    # ------------------------------------------------------------------ #
    # Business types
    # ------------------------------------------------------------------ #
    raw_biz_types = entity_reg.get("businessTypeList", []) or []
    business_types = [
        bt.get("businessTypeDesc", "")
        for bt in raw_biz_types
        if bt.get("businessTypeDesc")
    ]

    # ------------------------------------------------------------------ #
    # Set-aside eligibility — map known business type descriptions to codes
    # ------------------------------------------------------------------ #
    set_aside_eligibility = list(
        {
            _SET_ASIDE_MAP[bt]
            for bt in business_types
            if bt in _SET_ASIDE_MAP
        }
    )

    profile = {
        "company_name": company_name,
        "cage_code": cage_code,
        "uei": uei,
        "registration_status": status,
        "naics_codes": naics_codes,
        "primary_naics": primary_naics,
        "business_types": business_types,
        "set_aside_eligibility": set_aside_eligibility,
    }

    _cache[cage_code] = profile
    return profile


# ---------------------------------------------------------------------------
# EntityLookup class kept for future expansion
# ---------------------------------------------------------------------------

class EntityLookup:
    """Wrapper around the SAM.gov Entity Management API."""

    def lookup_by_cage(self, cage_code: str) -> dict:
        """Delegate to the module-level get_company_profile function."""
        return get_company_profile(cage_code)

    def lookup_by_name(self, company_name: str) -> list[dict]:
        """Search entities by company name and return matching records."""
        pass


# ---------------------------------------------------------------------------
# CLI test harness
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python entity_lookup.py <CAGE_CODE>")
        sys.exit(1)

    cage_arg = sys.argv[1]
    try:
        profile = get_company_profile(cage_arg)
        print(json.dumps(profile, indent=2))
    except ValueError as exc:
        print(f"[Error] {exc}", file=sys.stderr)
        sys.exit(1)
    except requests.HTTPError as exc:
        print(f"[HTTP Error] {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        sys.exit(1)
