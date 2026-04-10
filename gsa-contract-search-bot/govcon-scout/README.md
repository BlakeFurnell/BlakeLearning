# GovCon Scout

A Flask web application that surfaces active federal contract opportunities from SAM.gov matched to your company's NAICS codes, set-aside eligibility, and business certifications — with optional AI fit scoring powered by Ollama.

## What it does

1. **Company lookup** — Enter your SAM.gov UEI; the app fetches your entity registration (NAICS codes, certifications, set-aside eligibility) from the SAM.gov Entity Management API.
2. **Opportunity search** — Queries the SAM.gov Opportunities API for active solicitations matching your primary (and up to two secondary) NAICS codes, filtered by set-aside type if applicable.
3. **AI fit analysis** (optional) — Sends each opportunity to an Ollama-hosted LLM which returns a 1–10 fit score, fit label, why it's a match, watch-outs, and a recommended action (Apply / Review Further / Skip).
4. **Ranked results** — Opportunities are sorted by fit score, filterable by label or action, with deadline urgency highlighted.

---

## Prerequisites

- **Python 3.11+**
- **SAM.gov API key** — free, see [Getting a SAM.gov API Key](#getting-a-samgov-api-key)
- **Ollama cloud API key** — required only for AI fit analysis

---

## Setup

```bash
# 1. Clone the repo
git clone <your-repo-url>
cd govcon-scout

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env            # if .env.example exists, otherwise edit .env directly
```

Edit `.env` and fill in your actual keys:

```env
SAM_API_KEY=your-sam-gov-api-key-here
OLLAMA_API_KEY=your-ollama-api-key-here
OLLAMA_BASE_URL=https://ollama.com/api
OLLAMA_MODEL=gemma4:31b
```

---

## Getting a SAM.gov API Key

1. Log in (or create an account) at [sam.gov](https://sam.gov)
2. Navigate to **Profile → API Key Management** — direct link: [sam.gov/profile/details](https://sam.gov/profile/details)
3. Generate a key under **Public APIs** (no approval required)
4. Paste the key into your `.env` as `SAM_API_KEY`

The public key is free and grants access to the Opportunities and Entity Management APIs used by this app.

---

## Running the App

```bash
# Make sure your venv is active
source .venv/bin/activate

python app.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser.

To verify the API integrations are configured:

```bash
curl http://localhost:5000/health
# → {"ollama": "configured", "sam_api": "configured", "status": "ok"}
```

---

## Testing individual service modules

```bash
# Entity lookup (no analysis, just profile)
python services/entity_lookup.py <YOUR_UEI>

# SAM.gov opportunity search (by NAICS, no UEI required)
python services/sam_client.py 541512 SBR
```

---

## Known Limitations

| Limitation | Detail |
|---|---|
| **SAM.gov rate limits** | The public API key allows approximately 1,000 requests/day. Searching 3 NAICS codes uses 3 requests per search. The 1-hour local cache (`sam_cache.json`) reduces repeat calls during development. |
| **Opportunity descriptions** | The SAM.gov Opportunities API v2 returns minimal description text in the search endpoint. Full solicitation text lives in attachments (PDFs), which this app does not currently download or parse. AI summaries will note when descriptions are thin. |
| **AI analysis time** | Ollama processes opportunities sequentially. A 31B-parameter model can take 30–90 seconds per opportunity depending on hardware. Searching 25 opportunities with analysis enabled may take 15–40 minutes on CPU. Use a smaller model or GPU-backed instance to speed this up. |
| **Entity API rate limits** | Entity lookups are cached in-memory per process. Restarting the server clears the cache, consuming one API call per UEI on the next lookup. |
| **Active registrations only** | The app rejects UEIs where `registrationStatus != "Active"`. Expired or pending registrations will show a clear error message. |
| **Set-aside mapping** | The business-type-to-set-aside-code mapping covers the most common socioeconomic categories. Niche programs (e.g., AbilityOne, Indian Incentive) are not currently mapped. |
