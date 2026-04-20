"""
app.py

Main Flask application entry point for govcon-scout.

Responsibilities:
- Initialize the Flask app and wire in config
- Serve the landing page and form-driven routes
- Delegate to service layer: entity_lookup, sam_client, ollama_client
- Handle errors cleanly with user-facing error pages
"""

from flask import Flask, jsonify, render_template, request

from config import Config
from services.entity_lookup import get_company_profile
from services.sam_client import search_opportunities
from services.ollama_client import analyze_opportunities
from utils import format_currency, days_until

app = Flask(__name__)
app.config.from_object(Config)

# Register utility helpers so templates can call them directly
app.jinja_env.globals["format_currency"] = format_currency
app.jinja_env.globals["days_until"] = days_until


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Render the landing page with a CAGE code entry form."""
    return render_template("index.html")


@app.route("/lookup", methods=["POST"])
def lookup():
    """
    Accept a CAGE code, fetch the company profile from SAM.gov, and render it.
    On error re-renders index.html with a user-facing error message.
    """
    cage = request.form.get("cage", "").strip()
    if not cage:
        return render_template("index.html", error="Please enter a CAGE code.")

    try:
        profile = get_company_profile(cage)
    except ValueError as exc:
        return render_template("index.html", error=str(exc))
    except Exception as exc:
        app.logger.exception("Unexpected error during entity lookup for CAGE '%s'", cage)
        return render_template("index.html", error=f"Unexpected error: {exc}")

    return render_template("profile.html", profile=profile)


@app.route("/search", methods=["POST"])
def search():
    """
    Accept CAGE code + search params, run opportunity search, optionally run AI analysis.

    Form fields:
      cage             — SAM.gov CAGE code (required)
      days_back        — integer, how many days back to search (default 30)
      include_analysis — checkbox; if present, runs Ollama fit analysis
    """
    cage = request.form.get("cage", "").strip()
    if not cage:
        return render_template("index.html", error="Please enter a CAGE code.")

    try:
        days_back = int(request.form.get("days_back", 30))
    except ValueError:
        days_back = 30

    include_analysis = "include_analysis" in request.form

    try:
        profile = get_company_profile(cage)
    except ValueError as exc:
        return render_template("index.html", error=str(exc))
    except Exception as exc:
        app.logger.exception("Entity lookup failed for CAGE '%s'", cage)
        return render_template("index.html", error=f"Unexpected error: {exc}")

    try:
        opportunities = search_opportunities(profile, days_back=days_back)
    except ValueError as exc:
        # Includes rate-limit message from sam_client
        return render_template(
            "results.html",
            profile=profile,
            opportunities=[],
            error=str(exc),
            analysis_ran=False,
        )
    except Exception as exc:
        app.logger.exception("SAM.gov search failed for CAGE '%s'", cage)
        return render_template(
            "results.html",
            profile=profile,
            opportunities=[],
            error=f"SAM.gov search error: {exc}",
            analysis_ran=False,
        )

    analysis_ran = False
    if include_analysis and opportunities:
        try:
            opportunities = analyze_opportunities(profile, opportunities)
            analysis_ran = True
        except Exception as exc:
            app.logger.exception("Ollama analysis failed")
            return render_template(
                "results.html",
                profile=profile,
                opportunities=opportunities,
                error=f"AI analysis failed: {exc}. Showing unscored results.",
                analysis_ran=False,
            )

    return render_template(
        "results.html",
        profile=profile,
        opportunities=opportunities,
        error=None,
        analysis_ran=analysis_ran,
    )


@app.route("/health")
def health():
    """Return a JSON health check indicating which integrations are configured."""
    return jsonify({
        "status": "ok",
        "sam_api": "configured" if Config.SAM_API_KEY else "missing",
        "ollama": "configured" if Config.OLLAMA_API_KEY and Config.OLLAMA_BASE_URL else "missing",
    })


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(500)
def internal_error(exc):
    app.logger.exception("Unhandled 500 error")
    return render_template("error.html", error=str(exc)), 500


@app.errorhandler(404)
def not_found(_exc):
    return render_template("error.html", error="Page not found."), 404


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # SECURITY FIX: was hardcoded debug=True, now reads from Config.DEBUG
    app.run(host="0.0.0.0", port=8000, debug=Config.DEBUG)
