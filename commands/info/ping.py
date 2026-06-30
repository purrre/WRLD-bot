import random
import time

import aiohttp
import discord
from discord.ext import bridge, commands
from discord.ui import Container, Section, TextDisplay, Thumbnail

from config import colors, endpoints
from utils.functions import httpcall, getSession
from utils.components import loading, createView

LOADING_EMOJIS = [
    "<a:takeoff:1517041443175268477>",
    "<a:gravityflip:1517046487622615050>",
    "<a:sway:1517046344680738866>",
    "<a:radarping:1517046319317782549>",
    "<a:glitchwave:1517046212178477159>",
    "<a:hovering:1517046296416878663>",
]

# markers that suggest the response body is an error page rather than real api data.
# add or remove strings here; nothing cloudflare-specific is hardcoded.
ERROR_MARKERS = [
    "error",
    "unavailable",
    "bad gateway",
    "service unavailable",
    "gateway time",
    "ray id",
    "checking your browser",
    "please wait",
    "origin unreachable",
    "server error",
]

HOSTS = {
    "main": (endpoints.jwa, ["/"]),
    "api": (endpoints.jwa, ["/juicewrld/"]),
    "media (master)": (endpoints.media, ["/status/"]),
    "test": ("https://brick-hill.com", ["/"]),
}


async def ping_host(session, base_url, paths):
    start = time.perf_counter()
    for path in paths:
        try:
            async with session.get(f"{base_url}{path}", timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status >= 400:
                    continue
                text = (await resp.text()).lower()
                if any(marker in text for marker in ERROR_MARKERS):
                    continue
                return True, (time.perf_counter() - start) * 1000
        except Exception:
            continue
    return False, (time.perf_counter() - start) * 1000


class PingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.region = None
        self.country = None

    async def ensure_location(self):
        if self.country and self.country.lower() != "unknown":
            return
        ip = await httpcall("https://api.ipify.org", expect_json=False)
        r = await httpcall(f"https://ipinfo.io/{ip[1]}/json")
        self.country = r[1].get("country", "unknown")
        self.region = r[1].get("region", "")

    @bridge.bridge_command(usage="ping", description="Pings juicewrldapi to check the status", aliases=["p"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def ping(self, ctx):
        msg = await ctx.reply(random.choice(LOADING_EMOJIS))
        await self.ensure_location()
        session = await getSession()
        status_lines = []
        for name, (base_url, paths) in HOSTS.items():
            online, ping_ms = await ping_host(session, base_url, paths)
            if online:
                status_lines.append(f"<:status_online:1443125660552921142> {name.title()}")
                status_lines.append(f"-# {ping_ms:.2f}ms")
            else:
                status_lines.append(f"<:status_offline:1443125840501014572> {name.title()}")
                status_lines.append("-# down")
        cont = Container()
        header_lines = ["### juicewrldapi Status", *status_lines]
        accessory = Thumbnail("https://i.imgur.gg/AYK6hmG-hero-removebg-preview.png")
        cont.add_item(Section(TextDisplay("\n".join(header_lines)), accessory=accessory))
        cont.add_separator(divider=True)
        cont.add_text(f"-# Use `{ctx.prefix}jwa` for classic layout\n-# Use `{ctx.prefix}wrld` for bot ping")
        await msg.edit(content=None, embed=None, view=createView(cont))

    @bridge.bridge_command(name="jwa", usage="jwa", description="Pings juicewrldapi (classic embed layout)")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def jwa(self, ctx):
        msg = await ctx.reply(embed=await loading("PING"))
        await self.ensure_location()
        session = await getSession()
        embed = discord.Embed(title="juicewrldapi Status", url=endpoints.jwa, color=colors.main)
        for name, (base_url, paths) in HOSTS.items():
            online, ping_ms = await ping_host(session, base_url, paths)
            if online:
                embed.add_field(name=f"<:status_online:1443125660552921142> {name.title()}", value=f"Ping: {ping_ms:.2f} ms", inline=True)
            else:
                embed.add_field(name=f"<:status_offline:1443125840501014572> {name.title()}", value="Couldn't reach host :(", inline=True)
        embed.set_footer(text=f"{self.bot.user.name} | Pinged from {self.region}, {self.country}", icon_url=self.bot.user.avatar.url)
        await msg.edit(embed=embed)

    @bridge.bridge_command(usage="apistats", description="Shows basic juicewrldapi statistics")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def apistats(self, ctx):
        msg = await ctx.reply(embed=await loading("API"))
        success1, m_api = await httpcall(endpoints.media_status)
        success2, g_api = await httpcall(endpoints.plays_stats)
        if not success1 or not success2:
            return await ctx.reply(embed=m_api)
        size_gb = round(int(m_api.get("total_size_bytes", "0")) / 1073741824, 2)
        embed = discord.Embed(
            title="juicewrldapi Statistics",
            color=colors.main,
            description=f"Total Files: {m_api.get('total_files', 'N/A')} | Commits: {m_api.get('total_commits', 'N/A')} | Data Size: {size_gb} GB",
            url=f"{endpoints.jwa}/stats",
        )
        embed.set_thumbnail(url="https://raw.githubusercontent.com/HackinHood/juicewrldapi-desktop/refs/heads/master/assets/icon.png")
        embed.add_field(
            name="__Plays__",
            value=(
                f"- Total Plays: {g_api.get('total_plays', 'N/A')}\n"
                f"- Unique Songs Played: {g_api.get('total_songs_with_plays', 'N/A')}\n"
                f"- Unique Albums Played: {g_api.get('total_albums_with_plays', 'N/A')}\n"
                f"- Unique Eras Played: {g_api.get('total_eras_with_plays', 'N/A')}"
            ),
            inline=True,
        )
        top_categories = sorted(g_api.get("category_breakdown", []), key=lambda x: x["count"], reverse=True)[:5]
        embed.add_field(
            name="__Top Categories__",
            value="\n".join(f"- {c['category'].replace('_', ' ').title()}: {c['count']} plays" for c in top_categories),
            inline=True,
        )
        top_songs = sorted(g_api.get("top_songs", []), key=lambda x: x["play_count"], reverse=True)[:5]
        embed.add_field(
            name="__Top Songs__",
            value="\n".join(f"- {s['name']} ({s['era_name']}): **{s['play_count']}** plays" for s in top_songs),
            inline=False,
        )
        embed.set_footer(text=self.bot.user.name, icon_url=self.bot.user.avatar.url)
        await msg.edit(embed=embed)


def setup(bot):
    bot.add_cog(PingCog(bot))
