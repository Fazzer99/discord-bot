# bot/services/git_features.py
from __future__ import annotations
import base64
import json
import aiohttp
from typing import Tuple
from ..config import settings

PATH_IN_REPO = "discord-bot/data/features.json"  # <- WICHTIG: Subdirectory!

async def commit_features_json(features: list[tuple[str, str]]) -> Tuple[bool, str]:
    """
    Commitet die übergebene Feature-Liste als JSON nach
    https://api.github.com/repos/{repo}/contents/discord-bot/data/features.json

    Rückgabe: (success, message)
    """
    token  = settings.github_token
    repo   = settings.github_repo
    branch = settings.github_branch or "main"

    if not repo or not token:
        return False, "GitHub-Einstellungen fehlen (GITHUB_REPO/TOKEN/BRANCH)."

    content = json.dumps(features, ensure_ascii=False, indent=2)
    message = "Update features.json via bot"

    api = f"https://api.github.com/repos/{repo}/contents/{PATH_IN_REPO}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # SHA der bestehenden Datei besorgen (falls vorhanden)
            sha = None
            async with session.get(api, params={"ref": branch}) as r:
                if r.status == 200:
                    data = await r.json()
                    sha = data.get("sha")
                elif r.status not in (200, 404):
                    txt = await r.text()
                    return False, f"GET {r.status}: {txt}"

            payload = {
                "message": message,
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                "branch": branch,
            }
            if sha:
                payload["sha"] = sha  # Update statt Create

            async with session.put(api, json=payload) as r:
                if r.status in (200, 201):
                    return True, "Features erfolgreich zu GitHub gepusht."
                txt = await r.text()
                return False, f"PUT {r.status}: {txt}"

    except Exception as e:
        return False, f"Fehler beim GitHub-Commit: {e}"