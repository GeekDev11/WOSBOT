"""Resolve an in-game alliance from its visible state and tag.

Only the official WhiteoutStats alliance endpoint is supported. AID is never
required from an administrator. Every lookup uses Player Sync's shared quota.
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
        async with cog.session().get(
            url, params={"include": "info"}, headers={header: auth, "Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=45)
        ) as response:
            if response.status == 404:
                return None, "No alliance was found for this state and tag. Check the spelling and capitalization."
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
