# bot/utils/replies.py
from __future__ import annotations
from typing import Optional, Iterable, Tuple
import discord
from .timeutil import translate_embed
from ..services.translation import translate_text_for_guild

# ----------------------------- Farb-/Style-Helfer -----------------------------

COLOR_MAP = {
    "success": discord.Color.green(),
    "error":   discord.Color.red(),
    "warning": discord.Color.gold(),
    "info":    discord.Color.blurple(),
}

def _pick_color(kind: Optional[str] = None, fallback: Optional[discord.Color] = None) -> discord.Color:
    """
    kind: "info" | "success" | "warning" | "error" | None
    """
    if isinstance(fallback, discord.Color):
        return fallback
    k = (kind or "info").lower()
    return COLOR_MAP.get(k, COLOR_MAP["info"])

def make_embed(
    *,
    description: Optional[str] = None,
    title: Optional[str] = None,
    kind: Optional[str] = "info",
    color: Optional[discord.Color] = None,
    footer: Optional[str] = None,
    author: Optional[Tuple[str, Optional[str]]] = None,  # (name, icon_url)
    fields: Optional[Iterable[Tuple[str, str, bool]]] = None,  # (name, value, inline)
) -> discord.Embed:
    """
    Baut ein einheitlich gestyltes Embed.
    - kind steuert die Farbe, color überschreibt kind.
    - author=(name, icon_url)
    - fields=[(name, value, inline)]
    """
    emb = discord.Embed(
        description=description or "",
        color=_pick_color(kind, color),
        title=title or None,
    )
    if footer:
        emb.set_footer(text=footer)
    if author:
        name, icon = author
        emb.set_author(name=name, icon_url=icon or discord.Embed.Empty)
    if fields:
        for n, v, inline in fields:
            emb.add_field(name=n, value=v, inline=inline)
    return emb

# ------------------------------ Ziel-/Send-Helfer -----------------------------

def _guild_id(target) -> Optional[int]:
    g = getattr(target, "guild", None) or getattr(getattr(target, "channel", None), "guild", None)
    return g.id if g else None

async def _send_interaction(inter: discord.Interaction, *, embed: discord.Embed, ephemeral: bool = False):
    """
    Schickt eine Nachricht zu einer Interaction.
    - Wenn noch NICHT geantwortet/deferred wurde -> response.send_message()
    - Wenn bereits geantwortet/deferred -> followup.send()
    - Fallback: wenn das Interaction-Token ungültig ist (NotFound), sende in den Kanal.
    """
    try:
        if not inter.response.is_done():
            return await inter.response.send_message(embed=embed, ephemeral=ephemeral)
        else:
            return await inter.followup.send(embed=embed, ephemeral=ephemeral)
    except discord.NotFound:
        # Interaction-Token bereits invalid -> versuche Kanal-Fallback
        try:
            if inter.channel:
                return await inter.channel.send(embed=embed)
        except Exception:
            pass
        return None

# --------------------------------- API: Replies --------------------------------

async def reply_text(
    target: discord.Interaction | discord.Message | discord.TextChannel | discord.Thread,
    text_de: str,
    *,
    title: Optional[str] = None,
    kind: Optional[str] = "info",
    color: Optional[discord.Color] = None,
    ephemeral: bool = False,
    footer: Optional[str] = None,
    author: Optional[Tuple[str, Optional[str]]] = None,
    fields: Optional[Iterable[Tuple[str, str, bool]]] = None,
    **kwargs
):
    """
    Sendet IMMER als Embed. Übersetzt automatisch abhängig von Guild-Sprache (DeepL).
    - kind: "info" | "success" | "warning" | "error" -> steuert Standardfarbe
    - color: überschreibt kind
    - title/footer/author/fields: optische Extras
    - ephemeral: nur bei Interactions relevant
    """
    gid = _guild_id(target)
    text = await translate_text_for_guild(gid, text_de) if gid else text_de

    embed = make_embed(
        description=text,
        title=title,
        kind=kind,
        color=color,
        footer=footer,
        author=author,
        fields=fields,
    )

    if isinstance(target, discord.Interaction):
        return await _send_interaction(target, embed=embed, ephemeral=ephemeral)
    elif isinstance(target, (discord.TextChannel, discord.Thread)):
        return await target.send(embed=embed, **kwargs)
    elif isinstance(target, discord.Message):
        return await target.reply(embed=embed, **kwargs)
    else:
        send = getattr(target, "send", None)
        if callable(send):
            return await send(embed=embed, **kwargs)
        raise TypeError("reply_text: unsupported target type")

async def send_embed(
    target: discord.Interaction | discord.Message | discord.TextChannel | discord.Thread,
    embed: discord.Embed,
    *,
    ephemeral: bool = False,
    kind: Optional[str] = None,
    **kwargs
):
    """
    Sendet einen vorhandenen Embed (optional Farbe via kind setzen + translate_embed-Hook).
    """
    if kind and not embed.color.value:
        embed.color = _pick_color(kind)

    gid = _guild_id(target)
    if gid:
        embed = await translate_embed(gid, embed)  # Platzhalter-Hook, falls du Titel/Felder übersetzen willst

    if isinstance(target, discord.Interaction):
        return await _send_interaction(target, embed=embed, ephemeral=ephemeral)
    elif isinstance(target, (discord.TextChannel, discord.Thread)):
        return await target.send(embed=embed, **kwargs)
    elif isinstance(target, discord.Message):
        return await target.reply(embed=embed, **kwargs)
    else:
        send = getattr(target, "send", None)
        if callable(send):
            return await send(embed=embed, **kwargs)
        raise TypeError("send_embed: unsupported target type")

# ------------------------------ Bequeme Kurzformen ------------------------------

async def reply_info(target, text_de: str, **kw):
    return await reply_text(target, text_de, kind="info", **kw)

async def reply_success(target, text_de: str, **kw):
    return await reply_text(target, text_de, kind="success", **kw)

async def reply_warning(target, text_de: str, **kw):
    return await reply_text(target, text_de, kind="warning", **kw)

async def reply_error(target, text_de: str, **kw):
    return await reply_text(target, text_de, kind="error", **kw)