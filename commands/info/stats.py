from discord.ext import bridge, commands

from config import emojis
from utils.database import db
from utils.components import createContainer, createView


class StatsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @bridge.bridge_command(name="stats", description="View bot usage statistics")
    async def wstats(self, ctx):
        row = await db.getStats()
        if not row:
            return await ctx.respond(f"{emojis.fail} No stats found.", ephemeral=True)
        track_searches = row.get("track_searches", 0)
        leaks_found = row.get("leaks_found", 0)
        session_zips_found = row.get("session_zips_found", 0)
        session_edits_found = row.get("session_edits_found", 0)
        snippets_sent = row.get("snippets_sent", 0)
        covers_found = row.get("covers_found", 0)
        total_finds = track_searches + leaks_found + session_zips_found + session_edits_found + snippets_sent + covers_found
        stats_text = (
            f"Commands Run: **{row.get('commands_run', 0):,}** (prefix) + **{row.get('slash_commands_run', 0):,}** (slash)\n\n"
            f"Total Finds: **{total_finds:,}**\n"
            f"-# <:reply2:1407148805287182348> Tracks Found: **{track_searches:,}**\n"
            f"-# <:reply2:1407148805287182348> Leaks Found: **{leaks_found:,}**\n"
            f"-# <:reply2:1407148805287182348> Session Zips Found: **{session_zips_found:,}**\n"
            f"-# <:reply2:1407148805287182348> Session Edits Found: **{session_edits_found:,}**\n"
            f"-# <:reply2:1407148805287182348> Snippets Found: **{snippets_sent:,}**\n"
            f"-# <:reply:1407148693802455120> Covers Found: **{covers_found:,}**\n\n"
            f"Random Songs Fetched: **{row.get('random_songs_found', 0):,}**\n"
            f"Lyrics Searched: **{row.get('lyrics_searched', 0):,}**\n"
            f"Random Lyrics Fetched: **{row.get('random_lyrics_found', 0):,}**"
        )
        cont = createContainer(
            title=f"{emojis.info} WRLD Usage Stats",
            description=stats_text + "\n\n-# Started Tracking <t:1780019830:R>",
        )
        await ctx.respond(view=createView(cont), ephemeral=True)


def setup(bot):
    bot.add_cog(StatsCog(bot))
