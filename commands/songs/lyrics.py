import discord
from discord.ext import bridge, commands
from discord import ButtonStyle
from discord.ui import ActionRow, Button
from rapidfuzz import fuzz

from config import emojis, NOT_YOURS
from utils.cache import cache
from utils.database import db
from utils.components import PersistentSongView, createContainer, createSongDropdown, createView
from utils.functions import getRandomLyric
from utils.songs import build_song_container, song_matches

class LyricsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @staticmethod
    def valid(lyrics):
        return bool(lyrics) and lyrics.lower() not in ("none", "n/a", "", "null")

    @staticmethod
    def name(song):
        return (song.get("track_titles") or [song.get("name", "Unknown")])[0]

    def find_lyrics(self, song):
        pid = song.get("public_id")
        if pid is not None:
            entry = cache.getLyrics(pid)
            if isinstance(entry, dict):
                return entry.get("lyrics")
        data = cache.getLyrics()
        if not data:
            return None
        name = str(song.get("name", "")).lower()
        for entry in data.values():
            if isinstance(entry, dict) and entry.get("name", "").lower() == name:
                return entry.get("lyrics")
        return None

    def count(self):
        data = cache.getLyrics()
        if not data:
            return 0
        return sum(1 for e in data.values() if isinstance(e, dict) and self.valid(e.get("lyrics", "")))

    def get_lyrics_views(self, song):
        lyrics = (self.find_lyrics(song) or song.get("lyrics", "") or "").strip()
        if not self.valid(lyrics):
            return None, f"{emojis.fail} No lyrics available for this song."
        song_name = self.name(song)
        parts, current = [], ""
        for line in lyrics.split("\n"):
            if len(current) + len(line) + 1 > 3800:
                parts.append(current)
                current = line + "\n"
            else:
                current += line + "\n"
        if current:
            parts.append(current)
        total = len(parts)
        views = []
        for i, part in enumerate(parts, start=1):
            title = f"Lyrics\n-# {song_name}" if total == 1 else f"Lyrics: {song_name} (Part {i}/{total})"
            heading = "###" if total == 1 else "##"
            views.append(createView(createContainer(title=title, description=part, heading=heading)))
        return views, None

    async def display_lyrics(self, interaction, song):
        views, error = self.get_lyrics_views(song)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        await interaction.response.send_message(view=views[0], ephemeral=True)
        for view in views[1:]:
            await interaction.followup.send(view=view, ephemeral=True)

    def lyrics_button(self, song):
        btn = discord.ui.Button(label="View Lyrics", style=discord.ButtonStyle.secondary)
        async def callback(interaction):
            await self.display_lyrics(interaction, song)
        btn.callback = callback
        return btn

    def build_lyrics_view(self, song, user_id, matches=None):
        cont = createContainer()
        cont.add_item(build_song_container(song, mode="info"))
        cont.add_separator(divider=True)
        cont.add_item(ActionRow(self.lyrics_button(song)))
        view = createView(cont, view_class=PersistentSongView)
        if matches and len(matches) > 1:
            async def on_select(interaction, song_id):
                await interaction.response.defer()
                selected = next((m for m in matches if str(m.get("public_id")) == str(song_id)), None)
                if selected:
                    await interaction.edit_original_response(view=self.build_lyrics_view(selected, user_id, matches))
            view.add_item(ActionRow(createSongDropdown(matches, user_id, "Choose a song...", on_select)))
        return view

    @bridge.bridge_command(name="lyrics", aliases=["ly"], description="Get lyrics for a song")
    @bridge.bridge_option(name="song", description="Song name to get lyrics for", required=True)
    async def lyrics(self, ctx, *, song: str):
        matches = song_matches(song, "info")
        if not matches:
            return await ctx.respond(f"{emojis.fail} No song found matching **{song}**.", ephemeral=True)
        lyric_matches = [m for m in matches if self.valid(self.find_lyrics(m))]
        if not lyric_matches:
            count = self.count()
            return await ctx.respond(
                f"{emojis.fail} No lyrics available for songs matching **{song}**.\n-# The database currently contains **{count}** songs with lyrics.",
                ephemeral=True,
            )
        if len(lyric_matches) == 1:
            return await ctx.respond(view=self.build_lyrics_view(lyric_matches[0], ctx.author.id, lyric_matches))
        count = self.count()
        cont = createContainer(
            title="Lyrics",
            description=(
                f"Found **{len(lyric_matches)}** songs matching **{song}** with lyrics available.\n\n"
                f"-# The database currently contains **{count}** songs with lyrics."
            ),
        )
        view = createView(cont, view_class=PersistentSongView)

        async def on_select(interaction, song_id):
            if interaction.user.id != ctx.author.id:
                return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
            selected = next((s for s in lyric_matches if str(s.get("public_id")) == str(song_id)), None)
            if selected:
                await self.display_lyrics(interaction, selected)

        dropdown = createSongDropdown(lyric_matches, ctx.author.id, "Choose a song...", on_select)
        view.add_item(ActionRow(dropdown))
        await ctx.respond(view=view)

    @bridge.bridge_command(name="randomlyric", aliases=["lyric", "rl"], description="Get a random lyric line")
    async def randomlyric(self, ctx):
        await ctx.defer()

        async def send_lyric(target, is_edit=False):
            lyric = getRandomLyric()
            if not lyric:
                msg = f"{emojis.fail} No bars found."
                return await target.edit_original_response(content=msg, view=None) if is_edit else await target.respond(msg, ephemeral=True)
            await db.incrementStat("random_lyrics_found")

            next_btn = Button(label="Next Lyric", style=ButtonStyle.gray)

            async def next_callback(interaction):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
                await interaction.response.defer()
                await send_lyric(interaction, is_edit=True)

            next_btn.callback = next_callback
            cont = createContainer(title=f'"{lyric["text"]}"', description=f"-# Lyric from *{lyric['song']}*", heading="##")
            view = createView(cont, ActionRow(next_btn), timeout=300)
            await target.edit_original_response(view=view) if is_edit else await target.respond(view=view)

        await send_lyric(ctx)

    @bridge.bridge_command(aliases=["searchlyrics", "lyricsearch", "sl", "searchlyric", "ls"], description="Search for songs by lyrics")
    @bridge.bridge_option(name="lyrics", description="Lyrics text to search for", required=True)
    async def lyricssearch(self, ctx, *, lyrics: str):
        async with ctx.typing():
            data = cache.getLyrics()
            if not data:
                return await ctx.respond(view=createView(createContainer(title="Lyrics Search", description=f"{emojis.fail} Lyrics cache is not available.")))
            query = lyrics.lower().strip().replace("'", "").replace("'", "")
            if not [w for w in query.split() if len(w) >= 2]:
                return await ctx.respond(f"{emojis.fail} Please provide a longer lyrics query.", ephemeral=True)
            songs = cache.getSongs() or []
            matches = []
            for pid, entry in data.items():
                if not isinstance(entry, dict):
                    continue
                text = str(entry.get("lyrics", "")).lower().replace("'", "").replace("'", "")
                if query in text:
                    song = next((s for s in songs if str(s.get("public_id")) == str(pid)), None)
                    if song:
                        matches.append((song, 100))
                    continue
                best = max((fuzz.WRatio(query, line.strip()) for line in text.split("\n") if len(line.strip()) >= len(query) // 2), default=0)
                if best >= 88:
                    song = next((s for s in songs if str(s.get("public_id")) == str(pid)), None)
                    if song:
                        matches.append((song, best))
            matches.sort(key=lambda x: x[1], reverse=True)
            matches = [s for s, _ in matches]
            count = self.count()
            if not matches:
                return await ctx.respond(f"{emojis.fail} No songs found with lyrics matching **{lyrics}**.\n-# The database currently contains **{count}** songs with lyrics.", ephemeral=True)
            if len(matches) == 1:
                song = matches[0]
                cont = createContainer(title="Lyrics Search", description=f"Found **{self.name(song)}** with matching lyrics.\n\n-# The database currently contains **{count}** songs with lyrics.")
                view = createView(cont, view_class=PersistentSongView)
                view.add_item(ActionRow(self.lyrics_button(song)))
                return await ctx.respond(view=view)
            cont = createContainer(title="Lyrics Search", description=f"Found **{len(matches)}** songs with matching lyrics.\n\n-# The database currently contains **{count}** songs with lyrics.")
            view = createView(cont, view_class=PersistentSongView)

            async def on_select(interaction, song_id):
                if interaction.user.id != ctx.author.id:
                    return await interaction.response.send_message(NOT_YOURS, ephemeral=True)
                selected = next((s for s in matches if str(s.get("public_id")) == str(song_id)), None)
                if selected:
                    await self.display_lyrics(interaction, selected)

            view.add_item(ActionRow(createSongDropdown(matches, ctx.author.id, placeholder="Choose a song to view lyrics...", callback_func=on_select)))
            await ctx.respond(view=view)


def setup(bot):
    bot.add_cog(LyricsCog(bot))
