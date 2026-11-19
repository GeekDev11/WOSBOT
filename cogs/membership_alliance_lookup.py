"""Resolve an in-game alliance from its visible state and tag.

Only official WhiteoutStats API evidence is accepted. AID is never required
from an administrator. Every lookup uses Player Sync's shared quota.
"""
import asyncio
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import aiohttp

from . import alliance_api_sync as sync


@dataclass(frozen=True)
class ResolvedAlliance:
    state_id: int
    aid: int
    tag: str
    name: str


def validated_alliance(payload, state_id, tag):
    if not isinstance(payload, dict) or payload.get("ok") is False:
        raise ValueError("The API did not confirm that alliance.")
    alliance = payload.get("alliance")
    if not isinstance(alliance, dict):
        raise ValueError("The API did not return alliance details.")
    raw_aid = alliance.get("aid")
    raw_state = alliance.get("kid")
    actual_tag = alliance.get("abbr")
    if type(raw_aid) not in (int, str) or not str(raw_aid).isdigit() or int(raw_aid) <= 0:
        raise ValueError("The API did not provide a valid Alliance ID.")
    if type(raw_state) not in (int, str) or not str(raw_state).isdigit() or int(raw_state) != state_id:
        raise ValueError("The API returned a different state. Nothing was saved.")
    if not isinstance(actual_tag, str) or actual_tag != tag:
        raise ValueError("The API returned a different alliance tag. Check capitalization.")
    if payload.get("fresh") is False:
        raise ValueError("Alliance information is refreshing. Try again shortly.")
    return ResolvedAlliance(state_id, int(raw_aid), actual_tag,
                            str(alliance.get("name") or actual_tag)[:80])


def validated_ranked_alliance(payload, state_id, tag):
    """Accept an exact tag and positive AID from the official state-scoped board."""
    from .wos_leaderboards import leaderboard_rows

    if not isinstance(payload, dict) or payload.get("ok") is False:
        return None
    # Reject contradictory state information, if the response includes it.
    reported = payload.get("kid", payload.get("state_id"))
    if reported is not None and str(reported) != str(state_id):
        return None
    rows = leaderboard_rows(payload)
    if rows is None:
        return None
    matches = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("abbr") != tag:
            continue
        raw_aid = row.get("aid")
        if type(raw_aid) not in (int, str) or not str(raw_aid).isdigit() or int(raw_aid) <= 0:
            continue
        aid = int(raw_aid)
        matches[aid] = ResolvedAlliance(state_id, aid, tag, str(row.get("name") or tag)[:80])
    return next(iter(matches.values())) if len(matches) == 1 else None


async def fallback_ranked_alliance(cog, profile, headers, state_id, tag):
    """If the direct alliance lookup lacks data, try the official top-100 board."""
    if not await sync._take_slot(profile):
        return None, "Alliance lookup was inconclusive, and the API budget is exhausted before the state ranking check."
    url = f"https://api.whiteoutstats.com/v1/states/{state_id}/ranks"
    async with cog.session().get(
        url, params={"board": "alliance_power", "limit": 100},
        headers=headers, timeout=aiohttp.ClientTimeout(total=45)
    ) as response:
        if response.status == 429:
            return None, "WhiteoutStats is rate limiting the state ranking check. No alliance was saved."
        if response.status in (401, 403):
            return None, "WhiteoutStats rejected the configured API key."
        if response.status != 200:
            return None, f"WhiteoutStats could not verify the alliance through state rankings (HTTP {response.status}). No alliance was saved."
        payload = await response.json(content_type=None)
    match = validated_ranked_alliance(payload, state_id, tag)
    if match is None:
        return None, (
            f"WhiteoutStats could not verify [{tag}] in state {state_id} using the alliance lookup "
            "or top-100 alliance power ranking. This does not prove the alliance does not exist. "
            "Check the exact tag or retry when WhiteoutStats updates its data."
        )
    return match, None


async def resolve(bot, state_id, tag):
    """Return (ResolvedAlliance, None) or (None, friendly_error). Never save."""
    try:
        state_id = int(state_id)
        tag = str(tag).strip()
        if state_id <= 0 or not tag or len(tag) > 20 or "/" in tag or "?" in tag:
            return None, "Enter a valid state number and alliance tag."
        cog = bot.get_cog("AllianceApiSync")
        if cog is None:
            return None, "Player Sync is not loaded."
        profile, key = await asyncio.to_thread(sync.load_connection)
        base = profile.get("base_url") or ""
        parsed = urlsplit(base)
        if (parsed.scheme != "https" or parsed.hostname != "api.whiteoutstats.com"
                or not key):
            return None, "Configure your WhiteoutStats API connection and key in Player Sync first."
        if not await sync._take_slot(profile):
            return None, "Player Sync's API request budget is exhausted."
        header = profile.get("auth_header", "X-Api-Key")
        if header.lower() == "authorization":
            auth = key if key.lower().startswith("bearer ") else "Bearer " + key
        else:
            auth = key
        url = "https://api.whiteoutstats.com/v1/alliances/" + str(state_id) + "/" + quote(tag, safe="")
        headers = {header: auth, "Accept": "application/json"}
        async with cog.session().get(
            url, params={"include": "info"}, headers=headers,
            timeout=aiohttp.ClientTimeout(total=45)
        ) as response:
            if response.status == 404:
                return await fallback_ranked_alliance(cog, profile, headers, state_id, tag)
            if response.status == 202:
                return None, "Alliance data is being fetched by WhiteoutStats. Try again shortly."
            if response.status == 429:
                return None, "WhiteoutStats is rate limiting requests. Try again after the reset."
            if response.status in (401, 403):
                return None, "WhiteoutStats rejected the configured API key."
            if response.status != 200:
                return None, "WhiteoutStats returned HTTP " + str(response.status) + ". Nothing was saved."
            payload = await response.json(content_type=None)
        return validated_alliance(payload, state_id, tag), None
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None, "WhiteoutStats did not respond. No alliance was registered."
    except (ValueError, TypeError, KeyError) as exc:
        return None, str(exc)
