# bot/utils/timezones.py
from __future__ import annotations
from zoneinfo import ZoneInfo, available_timezones

ALL_TZS = sorted(available_timezones())

def guess_tz_from_locale(locale: str | None) -> str:
    """Sehr simple Heuristik: de* -> Europe/Berlin, sonst UTC."""
    loc = (locale or "").lower()
    if loc.startswith("de"):
        return "Europe/Berlin"
    return "UTC"

def validate_tz(name: str | None) -> str | None:
    """Gültige IANA-Zeitzone zurückgeben, sonst None."""
    try:
        if not name:
            return None
        ZoneInfo(name)  # wirft bei Ungültigkeit
        return name
    except Exception:
        return None

def search_timezones(query: str) -> list[str]:
    """Einfache Suche (case-insensitiv, Teilstring) – max. 25 Ergebnisse."""
    q = (query or "").lower()
    if not q:
        # ein paar beliebte Defaults zuerst
        seeds = ["Europe/Berlin", "UTC", "Europe/Vienna", "Europe/Zurich", "Europe/London", "America/New_York"]
        return [tz for tz in seeds if tz in ALL_TZS][:25]
    out = [tz for tz in ALL_TZS if q in tz.lower()]
    return out[:25]