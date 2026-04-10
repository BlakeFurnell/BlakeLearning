"""
utils.py

Shared utility helpers used by app.py (registered as Jinja2 globals)
and available for import in service modules.
"""

from datetime import datetime, timezone


def format_currency(amount) -> str:
    """
    Format a numeric dollar amount into a compact, human-readable string.

    Examples:
        1234          → "$1,234"
        950000        → "$950K"
        4500000       → "$4.5M"
        2000000000    → "$2.0B"
        None / ""     → "N/A"
    """
    if amount is None or amount == "":
        return "N/A"
    try:
        n = float(amount)
    except (TypeError, ValueError):
        return "N/A"

    if n >= 1_000_000_000:
        return f"${n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"${n:,.0f}"
    return f"${n:.0f}"


def days_until(date_str: str) -> int | None:
    """
    Return the number of calendar days between today (UTC) and the given date string.

    Accepts ISO 8601 strings ("2025-06-01T00:00:00-05:00") and common
    SAM.gov formats ("06/01/2025", "2025-06-01").

    Returns:
        Positive int  → days remaining
        0             → due today
        Negative int  → past deadline
        None          → unparseable date string
    """
    if not date_str:
        return None

    # Attempt several parse formats
    formats = [
        "%Y-%m-%dT%H:%M:%S%z",  # ISO with tz offset
        "%Y-%m-%dT%H:%M:%S",    # ISO no tz
        "%m/%d/%Y",              # SAM.gov short format
        "%Y-%m-%d",              # ISO date only
    ]

    deadline = None
    for fmt in formats:
        try:
            deadline = datetime.strptime(date_str[:len(fmt) + 6], fmt)
            break
        except ValueError:
            continue

    # Fallback: let datetime parse it directly (handles many ISO variants)
    if deadline is None:
        try:
            deadline = datetime.fromisoformat(date_str)
        except ValueError:
            return None

    # Normalize both sides to UTC-aware or naive
    now = datetime.now(timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)

    return (deadline.date() - now.date()).days
