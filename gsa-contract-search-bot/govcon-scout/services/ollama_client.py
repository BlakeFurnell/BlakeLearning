"""
services/ollama_client.py

Ollama LLM client for AI-assisted analysis of contract opportunities.

Responsibilities:
- Connect to the Ollama API at OLLAMA_BASE_URL using OLLAMA_API_KEY
- Send contract descriptions and attachments to the configured model (OLLAMA_MODEL)
- Generate plain-language summaries of technical requirements
- Highlight win themes, incumbent information, and evaluation criteria
- Assess rough fit for a given company capability statement
- Stream or batch responses as appropriate for the UI
"""

import json
import re
import sys
from pathlib import Path

# Allow imports from the project root (config.py lives there)
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from config import Config

_CHAT_ENDPOINT = f"{Config.OLLAMA_BASE_URL.rstrip('/')}/v1/chat/completions"

_SYSTEM_PROMPT = (
    "You are a federal government contracting analyst. "
    "Analyze whether this contract opportunity is a good fit for the company. "
    "Be concise and practical."
)

_DEFAULT_ANALYSIS = {
    "fit_score": 0,
    "fit_label": "Unknown",
    "why_good_fit": "",
    "watch_outs": "",
    "recommended_action": "Review Further",
}


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_user_prompt(company_profile: dict, opportunity: dict) -> str:
    naics_codes = company_profile.get("naics_codes", [])
    naics_str = ", ".join(
        f"{n['naicsCode']} ({n.get('naicsDescription', '')})"
        for n in naics_codes
        if n.get("naicsCode")
    ) or "Not provided"

    business_types = ", ".join(company_profile.get("business_types", [])) or "None listed"
    set_asides = ", ".join(company_profile.get("set_aside_eligibility", [])) or "None"
    description = opportunity.get("description") or "Not provided"

    return (
        f"COMPANY PROFILE:\n"
        f"Name: {company_profile.get('company_name', 'Unknown')}\n"
        f"NAICS Codes: {naics_str}\n"
        f"Business Certifications: {business_types}\n"
        f"Set-Aside Eligibility: {set_asides}\n"
        f"\n"
        f"CONTRACT OPPORTUNITY:\n"
        f"Title: {opportunity.get('title', '')}\n"
        f"Agency: {opportunity.get('department', '')} / {opportunity.get('office', '')}\n"
        f"NAICS Code: {opportunity.get('naics_code', '')}\n"
        f"Set-Aside Type: {opportunity.get('set_aside_description', '')}\n"
        f"Posted: {opportunity.get('posted_date', '')}\n"
        f"Response Deadline: {opportunity.get('response_deadline', '')}\n"
        f"Description: {description}\n"
        f"\n"
        f"Respond ONLY with valid JSON in this exact format (no markdown, no preamble):\n"
        f"{{\n"
        f'  "fit_score": <integer 1-10>,\n'
        f'  "fit_label": "<Poor Fit | Fair Fit | Good Fit | Strong Fit>",\n'
        f'  "why_good_fit": "<2-3 sentences on why this is a match>",\n'
        f'  "watch_outs": "<1-2 sentences on risks or things to verify>",\n'
        f'  "recommended_action": "<Apply | Review Further | Skip>"\n'
        f"}}"
    )


# ---------------------------------------------------------------------------
# Single-opportunity analysis
# ---------------------------------------------------------------------------

def _analyze_one(company_profile: dict, opportunity: dict) -> dict:
    """
    Send one opportunity to Ollama and return the parsed analysis dict.
    Falls back to _DEFAULT_ANALYSIS on any failure.
    """
    payload = {
        "model": Config.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(company_profile, opportunity)},
        ],
        "stream": False,
    }

    headers = {
        "Authorization": f"Bearer {Config.OLLAMA_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            _CHAT_ENDPOINT,
            json=payload,
            headers=headers,
            timeout=120,  # LLMs can be slow; generous timeout
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        note = f"HTTP {exc.response.status_code} from Ollama: {exc.response.text[:200]}"
        print(f"[ollama_client] {note}", file=sys.stderr)
        return {**_DEFAULT_ANALYSIS, "watch_outs": note}
    except requests.RequestException as exc:
        note = f"Request error: {exc}"
        print(f"[ollama_client] {note}", file=sys.stderr)
        return {**_DEFAULT_ANALYSIS, "watch_outs": note}

    raw_content = (
        response.json()
        .get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
        .strip()
    )

    # Strip markdown code fences if the model wraps its output
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_content, flags=re.DOTALL).strip()

    try:
        analysis = json.loads(cleaned)
        # Ensure fit_score is an int and clamp to 1-10
        analysis["fit_score"] = max(1, min(10, int(analysis.get("fit_score", 0))))
        return analysis
    except (json.JSONDecodeError, ValueError) as exc:
        note = f"JSON parse error: {exc}. Raw response: {raw_content[:300]}"
        print(f"[ollama_client] {note}", file=sys.stderr)
        return {**_DEFAULT_ANALYSIS, "watch_outs": note}


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def analyze_opportunities(
    company_profile: dict,
    opportunities: list[dict],
) -> list[dict]:
    """
    Run AI fit analysis on each opportunity against a company profile.

    Calls Ollama sequentially (one request per opportunity) using the
    OpenAI-compatible /chat/completions endpoint. Merges the analysis result
    dict into each opportunity dict, then returns the list sorted by
    fit_score descending.

    Args:
        company_profile: dict from entity_lookup.get_company_profile()
        opportunities:   list of dicts from sam_client.search_opportunities()

    Returns:
        The same list with each dict extended by:
          fit_score, fit_label, why_good_fit, watch_outs, recommended_action
        Sorted by fit_score descending.
    """
    results: list[dict] = []

    for i, opp in enumerate(opportunities, start=1):
        title = opp.get("title", opp.get("notice_id", f"#{i}"))
        print(f"[ollama_client] Analyzing {i}/{len(opportunities)}: {title[:60]}", file=sys.stderr)

        analysis = _analyze_one(company_profile, opp)
        results.append({**opp, **analysis})

    results.sort(key=lambda o: o.get("fit_score", 0), reverse=True)
    return results


# ---------------------------------------------------------------------------
# Stub class kept for future expansion
# ---------------------------------------------------------------------------

class OllamaClient:
    """Client for Ollama-hosted LLM inference."""

    def analyze_opportunities(self, company_profile: dict, opportunities: list[dict]) -> list[dict]:
        """Delegate to the module-level analyze_opportunities function."""
        return analyze_opportunities(company_profile, opportunities)

    def summarize_opportunity(self, opportunity: dict) -> str:
        """Return an AI-generated plain-language summary of an opportunity."""
        pass

    def assess_fit(self, opportunity: dict, capability_statement: str) -> str:
        """Return an AI assessment of how well the opportunity fits a given company."""
        pass

    def chat(self, prompt: str, context: str = "") -> str:
        """Send a free-form prompt with optional context and return the model response."""
        pass
