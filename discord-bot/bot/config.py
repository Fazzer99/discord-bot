import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    token: str = os.environ.get("DISCORD_TOKEN", "")
    database_url: str = os.environ.get("DATABASE_URL", "")  # leer = DB optional aus
    default_tz: str = os.environ.get("DEFAULT_TZ", "Europe/Berlin")
    owner_id: int = int(os.environ.get("BOT_OWNER_ID", "0") or 0)

    # Services
    deepl_key: str = os.environ.get("DEEPL_API_KEY", "")

    # GitHub (optional)
    github_token: str = os.environ.get("GITHUB_TOKEN", "")
    github_repo: str = os.environ.get("GITHUB_REPO", "")
    github_branch: str = os.environ.get("GITHUB_BRANCH", "main")
    github_token_expiration: str = os.environ.get("GITHUB_TOKEN_EXPIRATION", "")

settings = Settings()