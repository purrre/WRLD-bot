# ported from TPNE for the Outsiders server

import discord
from discord.ext import commands

from config import emojis, settings
from utils.components import createContainer, createView
from utils.database import db

REACTION_KINDS = {
    "sobs": {"emoji": "😭", "label": "Sobs", "rep": 3},
    "skulls": {"emoji": "💀", "label": "Skulls", "rep": 1},
    "flames": {"emoji": "🔥", "label": "Flames", "rep": 1},
    "hearts": {"emoji": "❤️", "label": "Hearts", "rep": 2},
    "clowns": {"emoji": "🤡", "label": "Clowns", "rep": -3},
}
EMOJI_KINDS = {meta["emoji"]: kind for kind, meta in REACTION_KINDS.items()}

NEGATIVE_AURAS = [
    (-10000, -9001, "Illegal Aura"), (-9000, -8001, "Demonic Aura"), (-8000, -7001, "Infernal Aura"),
    (-7000, -6001, "Hellish Aura"), (-6000, -5001, "Fiendish Aura"), (-5000, -4001, "Sinful Aura"),
    (-4000, -3001, "Diabolical Aura"), (-3000, -2001, "Vile Aura"), (-2000, -1001, "Infamous Aura"),
    (-1000, -901, "Abyssal Aura"), (-900, -801, "Cursed Aura"), (-800, -701, "Malevolent Aura"),
    (-700, -601, "Evil Aura"), (-600, -501, "Twisted Aura"), (-500, -401, "Corrupt Aura"),
    (-400, -301, "Sinister Aura"), (-300, -201, "Ominous Aura"), (-200, -101, "Terrible Aura"),
    (-100, -51, "Dark Aura"), (-50, -1, "Negative Aura"),
]
POSITIVE_AURAS = [
    (0, 50, "Weak Aura"), (51, 100, "Strong Aura"), (101, 250, "Immense Aura"),
    (251, 350, "Insane Aura"), (351, 450, "Unfathomable Aura"), (451, 550, "Godlike Aura"),
    (551, 650, "Celestial Aura"), (651, 750, "Astral Aura"), (751, 850, "Cosmic Aura"),
    (851, 900, "Divine Aura"), (901, 950, "Omnipotent Aura"), (951, 1000, "Transcendent Aura"),
    (1001, 2000, "Ethereal Aura"), (2001, 3000, "Angelic Aura"), (3001, 4000, "Mythical Aura"),
    (4001, 5000, "Legendary Aura"), (5001, 6000, "Boundless Aura"), (6001, 7000, "Untold Aura"),
    (7001, 8000, "Eminent Aura"), (8001, 9000, "Eternal Aura"), (9001, 10000, "Infinite Aura"),
]


def calculateAura(score):
    if score <= -10001:
        return "Null Aura"
    if score == 0:
        return "No Aura"
    for low, high, level in NEGATIVE_AURAS + POSITIVE_AURAS:
        if low <= score <= high:
            return level
    return "Hes too powerful..."


class ReactionsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def gate(self, ctx):
        if not ctx.guild or ctx.guild.id not in settings.reaction_guild_ids:
            await ctx.respond(f"{emojis.fail} This command isn't enabled in this server.", ephemeral=True)
            return False
        return True

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.guild_id is None or payload.guild_id not in settings.reaction_guild_ids:
            return
        kind = EMOJI_KINDS.get(str(payload.emoji))
        if kind is None:
            return
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return
        if payload.member is not None and payload.member.bot:
            return
        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return
        await db.recordReaction(message.author.id, payload.user_id, kind, REACTION_KINDS[kind]["rep"])

    async def resolveName(self, userId, guild):
        uid = int(userId)
        user = (guild.get_member(uid) if guild else None) or self.bot.get_user(uid)
        if user is None:
            try:
                user = await self.bot.fetch_user(uid)
            except Exception:
                return "Unknown user"
        return user.display_name if user else "Unknown user"

    async def sendStats(self, ctx, kind, member):
        if not await self.gate(ctx):
            return
        member = member or ctx.author
        meta = REACTION_KINDS.get(kind, {"label": "Reputation", "emoji": "📈"})
        stats = await db.getReactionStats(member.id)
        if kind == "reputation":
            score = stats.get("reputation", 0)
            lines = f"**Reputation:** `{score:,}`"
        else:
            rx, tx = stats.get(f"{kind}_rx", 0), stats.get(f"{kind}_tx", 0)
            score = rx - tx
            lines = (
                f"**Received:** `{rx:,}`\n"
                f"**Given:** `{tx:,}`\n"
                f"**{meta['label'][:-1]}worth:** `{score:,}`\n"
                f"**Reputation:** `{stats.get('reputation', 0):,}`"
            )
        cont = createContainer(
            title=f"{meta['label']} {meta['emoji']}",
            heading="##",
            separator=False,
            color=None,
            description=f"{member.mention}\n-# {calculateAura(score)}",
        )
        cont.add_separator(divider=True)
        cont.add_text(lines)
        await ctx.respond(view=createView(cont))

    async def sendLeaderboard(self, ctx, kind):
        if not await self.gate(ctx):
            return
        meta = REACTION_KINDS[kind]
        if kind == "reputation":
            top = await db.reputationLeaderboard()
            bottom = await db.reputationLeaderboard(bottom=True)
        else:
            top = await db.reactionLeaderboard(kind)
            bottom = await db.reactionLeaderboard(kind, bottom=True)
        cont = createContainer(title=f"{meta['label']} Leaderboard", heading="##", color=None)
        for heading, rows, medal in (("Top 10", top, "👑"), ("Bottom 10", bottom, "💩")):
            lines = []
            for idx, (uid, count) in enumerate(rows):
                name = await self.resolveName(uid, ctx.guild)
                marker = medal if idx == 0 else f"`{idx + 1}.`"
                lines.append(f"{marker} **{name}** (`{count:,}`)")
            cont.add_text(f"### {heading}\n" + ("\n".join(lines) if lines else "No data"))
        await ctx.respond(view=createView(cont))


def _makeGroup(cog, kind, meta):
    @commands.group(name=kind, invoke_without_command=True, description=f"View a user's {meta['label']} reaction stats")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def grp(ctx, member: discord.Member = None):
        await cog.sendStats(ctx, kind, member)

    @grp.command(name="leaderboard", aliases=["lb"], description=f"Top and bottom 10 {meta['label']} received")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def lb(ctx):
        await cog.sendLeaderboard(ctx, kind)

    return grp


def setup(bot):
    cog = ReactionsCog(bot)
    bot.add_cog(cog)
    for kind, meta in REACTION_KINDS.items():
        bot.add_command(_makeGroup(cog, kind, meta))
    bot.add_command(_makeGroup(cog, "reputation", {"label": "Reputation", "emoji": "📈", "rep": 0}))
