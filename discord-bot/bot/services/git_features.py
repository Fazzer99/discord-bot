import base64
import json
import aiohttp
from ..config import settings

async def commit_features_json(features: list[tuple[str, str]]) -> bool:
    token = settings.github_token
    repo = settings.github_repo
    branch = settings.github_branch
    if not (token and repo and branch):
        return False

    content = json.dumps(features, ensure_ascii=False, indent=2)
    message = "Update features.json via bot"

    api = f"https://api.github.com/repos/{repo}/contents/data/features.json"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    async with aiohttp.ClientSession(headers=headers) as session:
        sha = None
        async with session.get(api, params={"ref": branch}) as r:
            if r.status == 200:
                data = await r.json()
                sha = data.get("sha")
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch
        }
        if sha:
            payload["sha"] = sha
        async with session.put(api, json=payload) as r:
            return r.status in (200, 201)
