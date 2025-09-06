# bot/utils/replies.py
from __future__ import annotations
from typing import Optional, Iterable, Tuple
import discord
from .timeutil import translate_embed
from ..services.translation import translate_text_for_guild

# ─── Usage-Logging (lokal, um Zirkular-Import zu vermeiden) ────────────────
from ..db import execute
from ..services.guild_config import get_guild_cfg

def _safe_len(s: Optional[str]) -> int:
    return len(s or "")

def _count_embed_chars(embed: discord.Embed) -> int:
    n = 0
    n += _safe_len(embed.title)
    n += _safe_len(embed.description)
    if embed.footer and getattr(embed.footer, "text", None):
        n += _safe_len(embed.footer.text)
    if embed.author and getattr(embed.author, "name", None):
        n += _safe_len(embed.author.name)
    for f in (embed.fields or []):
        n += _safe_len(f.name)
        n += _safe_len(f.value)
    return n

def _total_message_chars(content: Optional[str], embeds: Iterable[discord.Embed] | None) -> int:
    total = _safe_len(content)
    if embeds:
        for e in embeds:
            total += _count_embed_chars(e)
    return total

async def _guild_lang(guild_id: Optional[int]) -> str:
    if not guild_id:
        return "dm"
    try:
        cfg = await get_guild_cfg(guild_id)
        lang = str(cfg.get("lang") or "de").lower()
        return lang if lang in {"de", "en"} else "de"
    except Exception:
        return "de"

async def log_interaction_output(
    inter: discord.Interaction,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    embeds: Optional[list[discord.Embed]] = None,
    message_type: str = "ephemeral",
) -> None:
    """
    Ephemeral-/Interaction-Antwort protokollieren (wird nicht von on_message erfasst).
    Nur EIN Insert pro tatsächlichem Send.
    """
    try:
        if embeds is None and embed is not None:
            embeds = [embed]
        chars = _total_message_chars(content, embeds or [])
        if chars <= 0:
            return

        guild_id = inter.guild_id
        channel_id = inter.channel_id
        user_id = inter.user.id if inter.user else None
        lang = await _guild_lang(guild_id)

        await execute(
            """
            INSERT INTO public.output_usage
                (ts, guild_id, channel_id, user_id, message_type, chars, lang, is_dm, is_ephemeral)
            VALUES (now(), $1, $2, $3, $4, $5, $6, $7, $8)
            """,
            guild_id,
            channel_id,
            user_id,
            message_type,
            int(chars),
            lang,
            False,   # Interactions sind keine DMs
            True,    # hier: speziell ephemeral
        )
    except Exception:
        # Logging darf niemals das Antworten verhindern
        pass

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
    Fallbacks bei Fehlern/abgelaufenem Token:
      1) DM an den Nutzer
      2) Nachricht im aktuellen Kanal (nicht-ephemeral), mit Hinweis
    """
    try:
        if not inter.response.is_done():
            res = await inter.response.send_message(embed=embed, ephemeral=ephemeral)
        else:
            res = await inter.followup.send(embed=embed, ephemeral=ephemeral)

        # ✨ Ephemeral-Antwort protokollieren (sichtbare Replies loggt on_message)
        if ephemeral:
            try:
                await log_interaction_output(inter, embed=embed, message_type="ephemeral")
            except Exception:
                pass

        return res

    except discord.NotFound:
        # Interaction-Token bereits ungültig -> Fallbacks nutzen
        pass
    except discord.HTTPException as e:
        # 401/50027 = Invalid Webhook Token -> Fallbacks nutzen
        if not (e.status == 401 or getattr(e, "code", None) == 50027):
            raise

    # ── Fallback 1: DM ─────────────────────────────────────────────
    try:
        user = getattr(inter, "user", None)
        if user is not None:
            # Sichtbare DM → wird vom Usage-Logger erfasst
            return await user.send(embed=embed)
    except Exception:
        pass

    # ── Fallback 2: Kanal-Message ─────────────────────────────────
    try:
        ch = getattr(inter, "channel", None)
        if ch is not None:
            prefix = f"{inter.user.mention} " if getattr(inter, "user", None) else ""
            note = "(Hinweis: Ephemeral-Fallback nicht möglich – Interaction-Token abgelaufen.) "
            # Sichtbare Channel-Message → wird vom Usage-Logger erfasst
            return await ch.send(content=prefix + note, embed=embed)
    except Exception:
        pass

    return None

# --------------------------- Tracked Send (sichtbar) ---------------------------
# Nutze diese Funktion statt channel.send()/user.send() in deinem Code,
# damit alle sichtbaren Ausgaben konsistent über den Usage-Logger gezählt werden.
# WICHTIG: tracked_send selbst schreibt NICHT in die DB (kein Doppelzählen),
# das übernimmt der UsageLogger (on_message).

async def tracked_send(
    target: discord.abc.Messageable | discord.User | discord.Member | discord.Message,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    embeds: Optional[Iterable[discord.Embed]] = None,
    view: Optional[discord.ui.View] = None,
    **kwargs
):
    """
    Dünner Wrapper um .send() / .reply(), der NICHT selbst loggt.
    Sichtbare Nachrichten werden automatisch im UsageLogger.on_message gezählt.
    """
    # embeds vereinheitlichen
    embeds_list = list(embeds) if embeds is not None else None
    if embed is not None:
        if embeds_list is None:
            embeds_list = [embed]
        else:
            embeds_list = list(embeds_list) + [embed]

    # Ziel ermitteln und senden
    try:
        if isinstance(target, discord.Message):
            # Reply auf eine vorhandene Nachricht
            return await target.reply(content=content, embed=None if embeds_list else None,
                                      embeds=embeds_list, view=view, **kwargs)

        # Alles andere, was sendbar ist
        send_fn = getattr(target, "send", None)
        if callable(send_fn):
            return await send_fn(content=content, embed=None if embeds_list else None,
                                 embeds=embeds_list, view=view, **kwargs)

        # Fallback: versuche auf channel zu gehen, falls target sowas hat
        ch = getattr(target, "channel", None)
        if ch and hasattr(ch, "send"):
            return await ch.send(content=content, embed=None if embeds_list else None,
                                 embeds=embeds_list, view=view, **kwargs)
    except Exception:
        # Niemals die Bot-Laufzeit killen
        raise

    # Wenn gar kein send() verfügbar war:
    raise TypeError("tracked_send: unsupported target type")

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