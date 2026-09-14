import asyncio
import urllib.parse

import discord
from discord.ext import bridge, commands
from discord.ui import Section, TextDisplay, Thumbnail

from config import colors, emojis, settings
from utils.components import createContainer, createSimplePagination, createView
from utils.database import db
from utils.functions import consoleLog, getSession

LASTFM_API = "https://ws.audioscrobbler.com/2.0/"

PERIOD_CHOICES = [
    discord.OptionChoice(name="7 days", value="7day"),
    discord.OptionChoice(name="1 month", value="1month"),
    discord.OptionChoice(name="3 months", value="3month"),
    discord.OptionChoice(name="6 months", value="6month"),
    discord.OptionChoice(name="1 year", value="12month"),
    discord.OptionChoice(name="lifetime", value="overall"),
]

PERIOD_DISPLAY = {
    "7day": "last 7 days",
    "1month": "last month",
    "3month": "last 3 months",
    "6month": "last 6 months",
    "12month": "last year",
    "overall": "lifetime",
}


class LastFMCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def api(self, method, **params):
        params.update(method=method, api_key=settings.lastfm_api_key, format="json")
        session = await getSession()
        try:
            async with session.get(LASTFM_API, params=params, timeout=10) as r:
                if r.status == 200:
                    return await r.json()
        except Exception as e:
            consoleLog("LASTFM", f"{method} failed: {e}", type="error")
        return {}

    async def resolve_user(self, ctx, username):
        """Returns (lastfm_username, display_name, hidden) or None if not logged in."""
        if username is not None:
            return username, username, False
        udata = await db.getLastfm(ctx.author.id)
        if not udata:
            return None
        return udata[0], ctx.author.display_name, udata[1]

    @staticmethod
    def safe_name(lastfm_user, display_name, hidden):
        """Display name shown to the user when hidden, else the lastfm username."""
        return display_name if hidden else lastfm_user

    @bridge.bridge_group(name="lastfm", aliases=["lf", "fm"], invoke_without_command=True)
    async def lastfm(self, ctx):
        if ctx.invoked_subcommand is None:
            await self._nowplaying(ctx)

    @lastfm.command(name="login", description="Link your Last.fm account")
    @bridge.bridge_option(name="username", description="Your Last.fm username", required=True)
    @bridge.bridge_option(name="hidden", description="Hide your Last.fm profile link in whoknows lists", required=False)
    async def login(self, ctx, username: str, hidden: bool = False):
        await db.setLastfm(ctx.author.id, username, hidden)
        cont = createContainer(title=f"{emojis.success} Last.fm Linked", description=f"Last.fm username set to **{username}**.")
        await ctx.respond(view=createView(cont), ephemeral=True)

    @lastfm.command(name="toggle", aliases=["t", "hidden"], description="Toggle whether your Last.fm username shows on commands")
    async def toggle(self, ctx):
        udata = await db.getLastfm(ctx.author.id)
        if not udata:
            return await ctx.respond(f"{emojis.fail} Not logged in. Use `lastfm login <username>`.", ephemeral=True)
        hidden = not udata[1]
        await db.setLastfm(ctx.author.id, udata[0], hidden)
        state = "hidden" if hidden else "shown"
        cont = createContainer(title=f"{emojis.success} Privacy Toggled", description=f"Your Last.fm username is now **{state}** on commands.")
        await ctx.respond(view=createView(cont), ephemeral=True)

    @lastfm.command(name="nowplaying", aliases=["np", "playing"], description="See what you're currently playing on Last.fm")
    @bridge.bridge_option(name="username", description="Last.fm username to look up", required=False)
    async def nowplaying(self, ctx, username: str = None):
        await self._nowplaying(ctx, username)

    async def _nowplaying(self, ctx, username=None):
        await ctx.defer()
        resolved = await self.resolve_user(ctx, username)
        if not resolved:
            return await ctx.respond(f"{emojis.fail} Not logged in. Use `lastfm login <username>`.", ephemeral=True)
        lastfm_user, display_name, hidden = resolved

        data = await self.api("user.getrecenttracks", user=lastfm_user, limit=1)
        if "error" in data:
            return await ctx.respond(f"{emojis.fail} Last.fm error: {data.get('message', 'unknown error')}")
        tracks = data.get("recenttracks", {}).get("track", [])
        if not tracks:
            return await ctx.respond(f"{emojis.fail} No tracks found for `{self.safe_name(lastfm_user, display_name, hidden)}`.")

        track = tracks[0]
        artist = str(track["artist"]["#text"])
        name = str(track["name"])
        album = str(track.get("album", {}).get("#text", ""))
        now_playing = track.get("@attr", {}).get("nowplaying") == "true"
        images = track.get("image", [])
        image_url = images[-1].get("#text") if images else None

        reqs = [
            self.api("track.getinfo", artist=artist, track=name, username=lastfm_user),
            self.api("user.getinfo", user=lastfm_user),
        ]
        if album:
            reqs.append(self.api("album.getinfo", artist=artist, album=album, username=lastfm_user))
        responses = await asyncio.gather(*reqs)

        track_info = responses[0].get("track", {})
        track_plays = str(track_info.get("userplaycount", "0"))
        total_plays = str(responses[1].get("user", {}).get("playcount", "0"))
        album_info = responses[2] if album else None
        # ponytail: recenttracks album/image are empty for untagged scrobbles — fall back
        # to track.getinfo's album data, then fetch its play count (extra call, rare path)
        if not album:
            album = str(track_info.get("album", {}).get("title", ""))
            if album:
                album_info = await self.api("album.getinfo", artist=artist, album=album, username=lastfm_user)
        album_plays = str(album_info.get("album", {}).get("userplaycount", "0")) if album_info else "0"
        if not image_url:
            for src in (track_info.get("album", {}), (album_info or {}).get("album", {})):
                imgs = src.get("image") or []
                if imgs and imgs[-1].get("#text"):
                    image_url = imgs[-1]["#text"]
                    break

        safe_artist = urllib.parse.quote(artist)
        safe_name = urllib.parse.quote(name)

        cont = createContainer(color=colors.main)
        if hidden:
            cont.add_text(f"-# {display_name}")
        else:
            cont.add_text(f"-# [{display_name} ({lastfm_user})](https://www.last.fm/user/{lastfm_user})")
        track_text = (
            f"### [{name}](https://www.last.fm/music/{safe_artist}/_/{safe_name})\n"
            f"-# by [{artist}](https://www.last.fm/music/{safe_artist})\n\n"
            f"**Album:** {album or 'Unknown'}\n**Status:** {'Now Playing' if now_playing else 'Last Played'}"
        )
        if image_url:
            cont.add_item(Section(TextDisplay(track_text), accessory=Thumbnail(image_url)))
        else:
            cont.add_text(track_text)
        cont.add_text(f"-# Track plays: {track_plays} | Album plays: {album_plays} | Total: {total_plays}")

        msg = await ctx.respond(view=createView(cont))
        try:
            if isinstance(msg, discord.Interaction):
                msg = await msg.original_response()
            await msg.add_reaction(emojis.thumbsup)
            await msg.add_reaction(emojis.thumbsdown)
        except discord.HTTPException:
            pass

    # --- whoknows ---

    async def _whoknows(self, ctx, mode, query=None):
        if not ctx.guild:
            return await ctx.respond(f"{emojis.fail} Who Knows commands can only be used inside servers.", ephemeral=True)
        await ctx.defer()
        target_artist = target_track = target_album = None

        if query:
            if " - " in query:
                parts = query.split(" - ", 1)
                target_artist = parts[0].strip()
                if mode == "track":
                    target_track = parts[1].strip()
                elif mode == "album":
                    target_album = parts[1].strip()
            elif mode == "artist":
                target_artist = query
            else:
                d = await self.api(f"{mode}.search", **{mode: query}, limit=1)
                matches = d.get("results", {}).get(f"{mode}matches", {}).get(mode, [])
                if not matches:
                    return await ctx.respond(f"{emojis.fail} Could not find {mode}.")
                a = matches[0]["artist"]
                target_artist = a if isinstance(a, str) else a["name"]
                if mode == "track":
                    target_track = matches[0]["name"]
                else:
                    target_album = matches[0]["name"]
        else:
            udata = await db.getLastfm(ctx.author.id)
            if not udata:
                return await ctx.respond(f"{emojis.fail} Log in with `lastfm login` or provide a query.", ephemeral=True)
            d = await self.api("user.getrecenttracks", user=udata[0], limit=1)
            tracks = d.get("recenttracks", {}).get("track", [])
            if not tracks:
                return await ctx.respond(f"{emojis.fail} No recent tracks.")
            t = tracks[0]
            target_artist = t["artist"]["#text"]
            target_track = t["name"]
            target_album = t.get("album", {}).get("#text")

        if mode == "track" and not target_track:
            return await ctx.respond(f"{emojis.fail} Could not resolve track.")
        if mode == "album" and not target_album:
            return await ctx.respond(f"{emojis.fail} Could not resolve album.")

        user_map = await db.allLastfm()
        guild_users = [(m, *user_map[str(m.id)]) for m in ctx.guild.members if str(m.id) in user_map]
        if not guild_users:
            return await ctx.respond("No one in this server uses the bot.")

        listeners = []
        sem = asyncio.Semaphore(8)

        async def fetch(member, username, hidden):
            async with sem:
                params = {"username": username}
                if mode == "artist":
                    params["artist"] = target_artist
                elif mode == "album":
                    params.update(artist=target_artist, album=target_album)
                else:
                    params.update(artist=target_artist, track=target_track)
                d = await self.api(f"{mode}.getinfo", **params)
                if mode == "artist":
                    plays = int(d.get("artist", {}).get("stats", {}).get("userplaycount", 0) or 0)
                else:
                    plays = int(d.get(mode, {}).get("userplaycount", 0) or 0)
                if plays > 0:
                    listeners.append({"member": member, "username": username, "hidden": hidden, "plays": plays})

        await asyncio.gather(*(fetch(*u) for u in guild_users))

        item = target_artist if mode == "artist" else (target_album if mode == "album" else target_track)
        if not listeners:
            return await ctx.respond(f"No one knows **{item}**.")

        listeners.sort(key=lambda x: x["plays"], reverse=True)
        total_plays = sum(l["plays"] for l in listeners)
        lines = []
        for i, l in enumerate(listeners[:15], 1):
            name = l["member"].display_name
            if l["hidden"]:
                lines.append(f"{i}. **{name}** — {l['plays']:,} plays")
            else:
                lines.append(f"{i}. [**{name}**](https://last.fm/user/{l['username']}) — {l['plays']:,} plays")
        cont = createContainer(
            title=f"Who knows {item}?",
            description=["\n".join(lines), f"-# {mode.capitalize()} · {len(listeners)} listeners · {total_plays:,} plays"],
            heading="##",
        )
        await ctx.respond(view=createView(cont))

    @lastfm.command(name="whoknows", aliases=["wk"], description="See who in the server knows an artist")
    @bridge.bridge_option(name="query", description="Artist to look up (defaults to your current track)", required=False)
    async def whoknows_artist(self, ctx, *, query: str = None):
        await self._whoknows(ctx, "artist", query)

    @lastfm.command(name="whoknowsalbum", aliases=["wka"], description="See who in the server knows an album")
    @bridge.bridge_option(name="query", description="Album to look up (defaults to your current track)", required=False)
    async def whoknows_album(self, ctx, *, query: str = None):
        await self._whoknows(ctx, "album", query)

    @lastfm.command(name="whoknowstrack", aliases=["wkt"], description="See who in the server knows a track")
    @bridge.bridge_option(name="query", description="Track to look up (defaults to your current track)", required=False)
    async def whoknows_track(self, ctx, *, query: str = None):
        await self._whoknows(ctx, "track", query)

    # --- top ---

    async def _top(self, ctx, item_type, period, username):
        await ctx.defer()
        resolved = await self.resolve_user(ctx, username)
        if not resolved:
            return await ctx.respond(f"{emojis.fail} Not logged in. Use `lastfm login <username>`.", ephemeral=True)
        lastfm_user, display_name, hidden = resolved
        if hidden:
            display_name = ctx.author.display_name

        d = await self.api(f"user.gettop{item_type}", user=lastfm_user, period=period, limit=50)
        items = d.get(f"top{item_type}", {}).get(item_type[:-1], [])
        if not items:
            return await ctx.respond(f"{emojis.fail} No {item_type} found for `{self.safe_name(lastfm_user, display_name, hidden)}`.")

        def render(page_items, page, total_pages, user_id, extra_context):
            lines = []
            for idx, item in enumerate(page_items, start=page * 10 + 1):
                name, count, url = item["name"], item.get("playcount", "0"), item.get("url", "")
                if item_type == "tracks":
                    artist = item.get("artist", {}).get("name", "Unknown")
                    lines.append(f"{idx}. **[{name}]({url})** by {artist} ({count})")
                else:
                    lines.append(f"{idx}. **[{name}]({url})** ({count})")
            cont = createContainer(
                title=f"{display_name}'s Top {item_type.capitalize()} — {PERIOD_DISPLAY.get(period, period)}\n-# Page {page + 1}/{total_pages}",
                description="\n".join(lines),
                heading="##",
            )
            return cont, None

        await createSimplePagination(items, 10, ctx.author.id, f"lf_{item_type}", render).show(ctx)

    @lastfm.command(name="toptracks", aliases=["tt"], description="View your top tracks within a time period")
    @bridge.bridge_option(name="period", description="Time period", required=False, choices=PERIOD_CHOICES)
    @bridge.bridge_option(name="username", description="Last.fm username to look up", required=False)
    async def toptracks(self, ctx, username: str = None, period: str = "7day"):
        await self._top(ctx, "tracks", period, username)

    @lastfm.command(name="topartists", aliases=["ta"], description="View your top artists within a time period")
    @bridge.bridge_option(name="period", description="Time period", required=False, choices=PERIOD_CHOICES)
    @bridge.bridge_option(name="username", description="Last.fm username to look up", required=False)
    async def topartists(self, ctx, username: str = None, period: str = "7day"):
        await self._top(ctx, "artists", period, username)

    @lastfm.command(name="topalbums", aliases=["talb"], description="View your top albums within a time period")
    @bridge.bridge_option(name="period", description="Time period", required=False, choices=PERIOD_CHOICES)
    @bridge.bridge_option(name="username", description="Last.fm username to look up", required=False)
    async def topalbums(self, ctx, username: str = None, period: str = "7day"):
        await self._top(ctx, "albums", period, username)

    # --- latest ---

    @lastfm.command(name="latest", aliases=["recent", "rt"], description="View your latest tracks")
    @bridge.bridge_option(name="username", description="Last.fm username to look up", required=False)
    async def latest(self, ctx, username: str = None):
        await ctx.defer()
        resolved = await self.resolve_user(ctx, username)
        if not resolved:
            return await ctx.respond(f"{emojis.fail} Not logged in. Use `lastfm login <username>`.", ephemeral=True)
        lastfm_user, display_name, hidden = resolved
        if hidden:
            display_name = ctx.author.display_name

        d = await self.api("user.getrecenttracks", user=lastfm_user, limit=50)
        tracks = d.get("recenttracks", {}).get("track", [])
        if not tracks:
            return await ctx.respond(f"{emojis.fail} No tracks found for `{self.safe_name(lastfm_user, display_name, hidden)}`.")
        total_scrobbles = d.get("recenttracks", {}).get("@attr", {}).get("total", "0")

        def render(page_items, page, total_pages, user_id, extra_context):
            lines = []
            for t in page_items:
                name = str(t["name"])
                artist = str(t["artist"]["#text"])
                url = t.get("url", "")
                ts_str = f" <t:{t['date']['uts']}:R>" if "date" in t else " **(Now Playing)**"
                lines.append(f"**[{name}]({url})** by {artist}{ts_str}")
            cont = createContainer(
                title=f"Recent Tracks — {display_name}\n-# Page {page + 1}/{total_pages} | {total_scrobbles} total scrobbles",
                description="\n".join(lines),
                heading="##",
            )
            return cont, None

        await createSimplePagination(tracks, 10, ctx.author.id, "lf_latest", render).show(ctx)


def setup(bot):
    bot.add_cog(LastFMCog(bot))
