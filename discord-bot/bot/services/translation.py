import aiohttp
import os

DEEPL_KEY = os.environ.get("DEEPL_API_KEY", "")
_cache: dict[str, str] = {}

async def translate_de_to_en(text_de: str) -> str:
    if not text_de or not text_de.strip():
        return text_de
    if text_de in _cache:
        return _cache[text_de]
    if not DEEPL_KEY:
        return text_de  # Fallback

    url = "https://api-free.deepl.com/v2/translate" if "free" in DEEPL_KEY.lower() else "https://api.deepl.com/v2/translate"
    data = {"auth_key": DEEPL_KEY, "text": text_de, "source_lang": "DE", "target_lang": "EN"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, timeout=20) as resp:
                js = await resp.json()
        translated = js.get("translations", [{}])[0].get("text", text_de)
        _cache[text_de] = translated
        return translated
    except Exception:
        return text_de
