from datetime import datetime

import discord
from discord.ext import bridge, commands
from discord.ui import Section, TextDisplay, Thumbnail

from config import emojis, endpoints
from utils.functions import getSession
from utils.components import createContainer, createView

class NowPlayingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @bridge.bridge_command(name="np", aliases=["nowplaying"], description="View what youre currently listening to on juicewrldapi's apps")
    @bridge.bridge_option(name="user", description="User to check now playing for", required=False)
    async def nowplaying(self, ctx, user: discord.User = None):
        target = user or ctx.author
        await ctx.defer()
        try:
            session = await getSession()
            async with session.get(f"{endpoints.now_playing}{target.id}", timeout=10) as resp:
                if resp.status != 200:
                    if resp.status == 404:
                        return await ctx.respond(
                            f"{emojis.fail} Discord account not linked to any user\n-# Link your account using the juicewrldapi or TPNE bot using `!link <code>`",
                            ephemeral=True,
                        )
                    return await ctx.respond(f"{emojis.fail} Failed to fetch now playing data.", ephemeral=True)
                data = await resp.json()
        except Exception as e:
            return await ctx.respond(f"{emojis.fail} Error fetching now playing data: {e}", ephemeral=True)
        now_playing = data.get("now_playing", {})
        user_data = data.get("user", {})
        if not now_playing:
            return await ctx.respond(f"{emojis.fail} No active playback data available.", ephemeral=True)
        duration = now_playing.get("duration", 0)
        updated_at = now_playing.get("updated_at", "")
        if duration > 0 and updated_at:
            try:
                timestamp = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                now = datetime.now(timestamp.tzinfo)
                if (now - timestamp).total_seconds() * 1000 >= (duration * 1.5):
                    return await ctx.respond(f"{emojis.fail} No active playback data available.", ephemeral=True)
            except Exception:
                pass
        track = now_playing.get("track", "Unknown")
        artist = now_playing.get("artist", "Unknown")
        album = now_playing.get("album", "Unknown")
        is_playing = now_playing.get("is_playing", False)
        duration = now_playing.get("duration", 0)
        position = now_playing.get("position", 0)
        if duration > 0:
            progress_pct = min(position / duration, 1.0)
            bar_length = 12
            filled_count = int(progress_pct * bar_length)
            bar_chars = []
            for i in range(bar_length):
                if i == 0:
                    bar_chars.append(emojis.w1 if filled_count > 0 else emojis.g1)
                elif i == bar_length - 1:
                    bar_chars.append(emojis.w3 if filled_count == bar_length else emojis.g3)
                else:
                    bar_chars.append(emojis.w2 if i < filled_count else emojis.g2)
            def fmt(ms):
                s = ms // 1000
                return f"{s // 60}:{s % 60:02d}"
            progress = f"{fmt(position)} {''.join(str(c) for c in bar_chars)} {fmt(duration)}"
        else:
            progress = "N/A"
        status_emoji = emojis.vinyl if is_playing else "⏸️"
        status_text = "Playing" if is_playing else "Paused"
        description = f"-# {artist}\n\n**Album:** {album}\n**Status:** {status_text}\n\n{progress}"
        album_art_url = now_playing.get("album_art_url")
        accessory = Thumbnail(album_art_url) if album_art_url else None
        header_text = ""
        if user is not None:
            display_name = getattr(target, "display_name", None) or target.name
            suffix = "'" if str(display_name).endswith("s") else "'s"
            header_text = f"-# {display_name}{suffix} Now Playing\n"
        title = f"{header_text}{status_emoji} {track}"
        if accessory:
            cont = createContainer(title=title, description=None)
            cont.add_item(Section(TextDisplay(description), accessory=accessory))
        else:
            cont = createContainer(title=title, description=description)
        message = await ctx.respond(view=createView(cont))
        try:
            if isinstance(message, discord.Interaction):
                message = await message.original_response()
            await message.add_reaction(emojis.thumbsup)
            await message.add_reaction(emojis.thumbsdown)
        except discord.HTTPException:
            pass


def setup(bot):
    bot.add_cog(NowPlayingCog(bot))
