# bot/utils/timezones.py
from __future__ import annotations
from typing import Optional

_MIN_OFFSET_MIN = -12 * 60   # -720
_MAX_OFFSET_MIN =  14 * 60   #  840

def parse_utc_offset_to_minutes(value: object) -> Optional[int]:
    """
    Akzeptiert z.B.: 2, "2", "+2", "UTC+2", "+4.5", "-5,75", "utc-3.25"
    Liefert Minuten (int) oder None bei ungültig.
    Nur 15-Minuten-Schritte sind erlaubt.
    Range: -12:00 .. +14:00
    """
    if value is None:
        return None

    # floats/ints direkt
    if isinstance(value, (int, float)):
        minutes = round(float(value) * 60)
        return minutes if _validate_minutes(minutes) else None

    # strings flexibel parsen
    s = str(value).strip().upper()  # Groß/Klein egal
    if not s:
        return None
    # UTC-Präfix optional entfernen
    if s.startswith("UTC"):
        s = s[3:].strip()

    # Komma zu Punkt
    s = s.replace(",", ".")

    # Leeres/alleinstehendes +/- -> ungültig
    if s in {"+", "-"}:
        return None

    try:
        hours = float(s)
    except ValueError:
        # evtl. +2, -5.75 etc. ohne "UTC"
        try:
            hours = float(s.replace("UTC", ""))
        except Exception:
            return None

    minutes = round(hours * 60)
    return minutes if _validate_minutes(minutes) else None


def _validate_minutes(minutes: int) -> bool:
    if minutes < _MIN_OFFSET_MIN or minutes > _MAX_OFFSET_MIN:
        return False
    # Viertelstunden prüfen
    return (minutes % 15) == 0


def format_utc_offset(minutes: int) -> str:
    """
    120 -> 'UTC+2'
    -345 -> 'UTC-5.75'
    90 -> 'UTC+1.5'
    """
    sign = "+" if minutes >= 0 else "-"
    hours = abs(minutes) / 60.0
    # bis zu 2 Nachkommastellen, aber ohne überflüssige Nullen
    txt = f"{hours:.2f}".rstrip("0").rstrip(".")
    return f"UTC{sign}{txt}"