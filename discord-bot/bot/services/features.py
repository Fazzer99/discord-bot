# bot/services/features.py
from __future__ import annotations
import json
from pathlib import Path
from typing import List

# Lokaler Pfad zur Datei im Repo:
#   discord-bot/data/features.json
FEATURES_FILE: Path = Path(__file__).resolve().parents[2] / "data" / "features.json"

# (Optional) Relativer Pfad IM REPO – nützlich für Logs/Debug
PATH_IN_REPO: str = "discord-bot/data/features.json"


def _normalize(features: list) -> List[List[str]]:
    """
    Defensive Normalisierung: Erlaube nur Sequenzen mit mind. 2 Einträgen,
    wandle alles in [name:str, desc:str] um.
    """
    out: List[List[str]] = []
    for item in features or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append([str(item[0]), str(item[1])])
    return out


def load_features() -> List[List[str]]:
    """
    Lädt die Features-Liste aus FEATURES_FILE.
    Rückgabeformat: [[name, desc], ...]
    """
    if not FEATURES_FILE.exists():
        return []
    try:
        data = json.loads(FEATURES_FILE.read_text(encoding="utf-8"))
        return _normalize(data)
    except Exception:
        # Korrupt/leer → leere Liste zurück
        return []


def save_features(features: List[List[str]]) -> None:
    """
    Speichert die Feature-Liste.
    Erwartet bereits normalisierte Struktur [[name, desc], ...].
    """
    FEATURES_FILE.parent.mkdir(parents=True, exist_ok=True)
    FEATURES_FILE.write_text(
        json.dumps(_normalize(features), ensure_ascii=False, indent=4),
        encoding="utf-8",
    )


# Bequeme Helfer (optional, aber praktisch)

def add_feature(name: str, description: str) -> List[List[str]]:
    """
    Fügt ein Feature hinzu (falls Name noch nicht existiert, case-insensitive).
    Gibt die aktuelle Liste zurück.
    """
    features = load_features()
    lower = name.strip().lower()
    if not any(f[0].strip().lower() == lower for f in features):
        features.append([name.strip(), description])
        save_features(features)
    return features


def remove_feature(name: str) -> List[List[str]]:
    """
    Entfernt ein Feature per Name (case-insensitive).
    Gibt die aktuelle Liste zurück.
    """
    features = load_features()
    lower = name.strip().lower()
    new_list = [f for f in features if f[0].strip().lower() != lower]
    if len(new_list) != len(features):
        save_features(new_list)
        return new_list
    return features