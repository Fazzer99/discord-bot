# bot/services/features.py
from __future__ import annotations
import json
from pathlib import Path

FEATURES_FILE = Path(__file__).resolve().parents[2] / "data" / "features.json"

def load_features():
    """
    Gibt Liste[List[str,str]] zurück: [[name, desc], ...]
    """
    if FEATURES_FILE.exists():
        with open(FEATURES_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                # defensive: nur erlaubte Formen durchlassen
                out = []
                for item in data:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        out.append([str(item[0]), str(item[1])])
                return out
            except Exception:
                return []
    return []

def save_features(features):
    FEATURES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(FEATURES_FILE, "w", encoding="utf-8") as f:
        json.dump(features, f, ensure_ascii=False, indent=4)