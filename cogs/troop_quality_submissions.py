"""Channel-based troop quality screenshot submissions.

Regular members do not use commands. Their linked Discord ID and Chief ID must
match an existing alliance member before any database update can be made.
"""
import asyncio
import logging
import re

import discord
from discord.ext import commands

from . import troop_quality as quality

logger = logging.getLogger("alliance")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
MAX_BYTES = 8 * 1024 * 1024
ID_RE = re.compile(r"(?<!\d)\d{6,16}(?!\d)")
TIER_RE = re.compile(
    r"\b(exalted|helios|apex)\s*(infantry|lancer|marksman)\b", re.I
)
TIER_NAMES = {"exalted": 12, "helios": 11, "apex": 10}


def parse_tiers(text):
    """Parse only explicit troop names; never use troop counts as evidence."""
    found = {}
    for tier, kind in TIER_RE.findall(text or ""):
        troop = kind.lower()
        found[troop] = max(found.get(troop, 0), TIER_NAMES[tier.lower()])
    return found


def parse_chief_id(text):
    matches = ID_RE.findall(text or "")
    return int(matches[0]) if len(matches) == 1 else None


def owner_matches(fid, alliance_id, user_id, path=quality.DB_PATH):
    """Fail closed when a Chief ID is not linked to the message author."""
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(path, timeout=30)) as db:
        row = db.execute(
            "SELECT discord_id FROM users WHERE fid=? AND alliance=?",
            (int(fid), str(alliance_id)),
        ).fetchone()
    return bool(row and row[0] is not None and int(row[0]) == int(user_id))


def submission_changes(tiers, skills, existing=None):
    """Do not overwrite unseen troop types or downgrade saved records."""
    existing = existing or {}
    changes = {}
    for troop, tier in tiers.items():
        if troop not in quality.TROOPS or tier not in (10, 11, 12):
            continue
        old_tier = existing.get(troop, (None, None))[0]
        if old_tier is not None and old_tier > tier:
            continue
        if tier == 12:
            level = skills.get(troop)
            if type(level) is not int or not 0 <= level <= 3:
                raise ValueError(f"Select a T12 skill level for {troop}.")
            changes[troop] = (12, level)
        else:
            changes[troop] = (tier, None)
    return changes


class SubmissionView(discord.ui.View):
    def __init__(self, cog, fid, alliance_id, submitter_id, tiers):
        super().__init__(timeout=600)
        self.cog = cog
        self.fid = fid
        self.alliance_id = alliance_id
        self.submitter_id = submitter_id
        self.tiers = tiers
        self.skills = {}
        self._build()

    def _build(self):
        self.clear_items()
        for row, troop in enumerate(t for t in quality.TROOPS if self.tiers.get(t) == 12):
            select = discord.ui.Select(
                placeholder=f"{troop.title()} T12 skill level (0–3)",
                min_values=1, max_values=1, row=row,
                options=[discord.SelectOption(label=f"Level {i}", value=str(i),
                         default=self.skills.get(troop) == i) for i in range(4)],
            )
            async def selected(interaction, t=troop, widget=select):
                self.skills[t] = int(widget.values[0])
                self._build()
                await interaction.response.edit_message(embed=self.embed(), view=self)
            select.callback = selected
            self.add_item(select)
        confirm = discord.ui.Button(label="Confirm Registration", emoji="✅",
                                    style=discord.ButtonStyle.success, row=3)
        confirm.disabled = any(t not in self.skills for t, v in self.tiers.items() if v == 12)
        confirm.callback = self.confirm
        self.add_item(confirm)
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary, row=3)
        cancel.callback = self.cancel
        self.add_item(cancel)

    def embed(self):
        lines = [
            f"**Chief ID:** \`{self.fid}\`",
            "Please verify your screenshot results before saving:",
            "",
        ]
        for troop in quality.TROOPS:
            tier = self.tiers.get(troop)
            if tier == 12:
                level = self.skills.get(troop)
                result = f"T12 · Skill {level}/3" if level is not None else "T12 · Select skill 0–3"
            elif tier in (10, 11):
                result = f"T{tier}"
            else:
                result = "Not detected (will not change)"
            lines.append(f"**{troop.title()}:** {result}")
        lines.append("\nOnly clearly detected troop types will be updated. Counts are ignored.")
        return discord.Embed(title="🛡️ Troop Quality · Screenshot Registration",
                             description="\n".join(lines))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.submitter_id:
            await interaction.response.send_message("This submission belongs to another member.", ephemeral=True)
            return False
        if not await asyncio.to_thread(owner_matches, self.fid, self.alliance_id, interaction.user.id):
            await interaction.response.send_message(
                "Your Chief ID is not linked to your Discord account in this alliance.", ephemeral=True
            )
            return False
        return True

    async def confirm(self, interaction):
        await interaction.response.defer()
        try:
            existing = await asyncio.to_thread(quality.read_quality, [self.fid])
            changes = submission_changes(self.tiers, self.skills, existing.get(self.fid))
            if not changes:
                await interaction.followup.send("No new troop information was detected.", ephemeral=True)
                return
            changed = await asyncio.to_thread(
                quality.save_quality, self.alliance_id, [self.fid], changes, interaction.user.id
            )
        except (ValueError, OSError, Exception) as exc:
            logger.exception("Troop Quality: submission save failed")
            await interaction.followup.send("Registration failed. Please ask an administrator to review it.",
                                            ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        embed = self.embed()
        embed.title = "✅ Troop Quality Registration Saved"
        embed.set_footer(text=f"{changed} troop type(s) changed · Saved with UTC timestamp")
        await interaction.edit_original_response(embed=embed, view=self)
        self.stop()

    async def cancel(self, interaction):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="Troop registration cancelled.",
                                                embed=None, view=self)
        self.stop()


class TroopQualitySubmissions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return
        alliance_id = await asyncio.to_thread(
            quality.submission_alliance, message.guild.id, message.channel.id
        )
        if alliance_id is None or not message.attachments:
            return
        fid = parse_chief_id(message.content)
        if fid is None:
            await message.reply("Include exactly one Chief ID with your troop screenshot.", mention_author=False)
            return
        if not await asyncio.to_thread(owner_matches, fid, alliance_id, message.author.id):
            await message.reply(
                "This Chief ID isn't linked to your Discord account in this alliance. "
                "Please complete ID registration or contact an alliance administrator.",
                mention_author=False,
            )
            return
        attachments = [
            a for a in message.attachments
            if a.filename.lower().endswith(IMAGE_EXTS)
            and a.size <= MAX_BYTES
        ][:3]
        if not attachments:
            await message.reply("Attach a PNG, JPG, or WebP troop preview screenshot (max 8 MB).",
                                mention_author=False)
            return
        try:
            from .bear_track import ocr_bytes, DEFAULT_OCR_LANG
            results = []
            for attachment in attachments:
                raw = await attachment.read()
                results.append(await ocr_bytes(raw, lang=DEFAULT_OCR_LANG))
            tiers = {}
            for output in results:
                for troop, tier in parse_tiers(output).items():
                    tiers[troop] = max(tiers.get(troop, 0), tier)
        except Exception:
            logger.exception("Troop Quality: screenshot OCR failed")
            await message.reply("I couldn't process that screenshot. Try a clearer image or ask an admin.",
                                mention_author=False)
            return
        if not tiers:
            await message.reply(
                "I couldn't identify Apex, Helios, or Exalted troop names. "
                "Upload a clear Troops Preview screenshot.",
                mention_author=False,
            )
            return
        view = SubmissionView(self, fid, alliance_id, message.author.id, tiers)
        await message.reply(embed=view.embed(), view=view, mention_author=False)


async def setup(bot):
    await bot.add_cog(TroopQualitySubmissions(bot))
