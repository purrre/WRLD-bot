import asyncio
import contextlib
import io
import json
import os
import random
import sys
import traceback
import urllib.parse

import discord
from discord.ext import commands
from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from config import settings, emojis
from utils.cache import cache, cache_manager
from utils.database import db, Base
from utils.functions import consoleLog, findClosestMatch, adminCheck, isBlacklistedUser, setRandomLyricStatus


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @adminCheck()
    @commands.command(name="restart", aliases=["reboot"])
    async def restart(self, ctx):
        await ctx.respond("Restarting bot...")
        consoleLog("SYSTEM", f"restart initiated by {ctx.author}")
        os.execv(sys.executable, ["python"] + sys.argv)

    @adminCheck()
    @commands.command()
    async def w2(self, ctx):
        await ctx.reply("online.")

    @adminCheck()
    @commands.command(name="reload")
    async def reload(self, ctx, cog_path: str):
        try:
            if cog_path.lower() == "all":
                reloaded = []
                for ext in list(self.bot.extensions):
                    self.bot.reload_extension(ext)
                    reloaded.append(ext)
                return await ctx.respond(f"{emojis.success} Reloaded {len(reloaded)} extensions")
            self.bot.reload_extension(cog_path)
            await ctx.respond(f"{emojis.success} Successfully reloaded `{cog_path}`")
        except Exception as e:
            await ctx.respond(f"{emojis.fail} Failed to reload `{cog_path}`:\n```py\n{e}\n```")

    @adminCheck()
    @commands.command(name="disable")
    async def cd(self, ctx, cog_path: str):
        try:
            self.bot.unload_extension(cog_path)
            await ctx.respond(f"{emojis.success} Disabled `{cog_path}`")
        except Exception as e:
            await ctx.respond(f"{emojis.fail} Failed to disable `{cog_path}`:\n```py\n{e}\n```")

    @adminCheck()
    @commands.command(name="enable")
    async def ce(self, ctx, cog_path: str):
        try:
            self.bot.load_extension(cog_path)
            await ctx.respond(f"{emojis.success} Enabled `{cog_path}`")
        except Exception as e:
            await ctx.respond(f"{emojis.fail} Failed to enable `{cog_path}`:\n```py\n{e}\n```")

    @adminCheck()
    @commands.command(name="setstatus", aliases=["ss"])
    async def setstatus(self, ctx, typ: str, *desc):
        if not typ or not desc:
            return await ctx.respond(f"{emojis.fail} Missing status type or text")
        status_text = " ".join(desc)
        if typ.lower() == "custom":
            await self.bot.change_presence(activity=discord.CustomActivity(name="Custom Status", state=status_text))
        else:
            activity_type = getattr(discord.ActivityType, typ.lower(), None)
            if activity_type is None:
                return await ctx.respond(f"{emojis.fail} Invalid status type")
            await self.bot.change_presence(activity=discord.Activity(type=activity_type, name=status_text))
        await ctx.respond(f"{emojis.success} Updated status")

    @adminCheck()
    @commands.command(name="refreshstatus", aliases=["rstatus"])
    async def refreshstatus(self, ctx):
        lyric = await setRandomLyricStatus(self.bot)
        if lyric:
            await ctx.respond(f'{emojis.success} Status refreshed:\n> "{lyric["text"]}"\n-# *{lyric["song"]}*')
        else:
            await ctx.respond(f"{emojis.fail} No lyrics available to set status.")

    @adminCheck()
    @commands.command(name="eval")
    async def eval(self, ctx, *, code: str):
        if code.startswith("```") and code.endswith("```"):
            code = code[3:-3]
            if code.startswith("python\n"):
                code = code[7:]
            elif code.startswith("py\n"):
                code = code[3:]
        env = {
            "bot": self.bot, "ctx": ctx, "channel": ctx.channel, "author": ctx.author,
            "guild": ctx.guild, "message": ctx.message, "discord": discord,
            "commands": commands, "asyncio": asyncio, "cache": cache,
            "cache_manager": cache_manager, "db": self.bot.db,
        }
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                exec("async def _eval_func():\n" + "\n".join(f"    {line}" for line in code.split("\n")), env)
                result = await env["_eval_func"]()
            output = stdout.getvalue()
            response = ""
            if output:
                response += f"```\n{output}\n```\n"
            if result is not None:
                response += f"```py\n{result}\n```"
            await ctx.respond((response or "✅ Executed successfully ```No Output```")[:2000])
        except Exception as e:
            error = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            await ctx.respond(f"```py\n{error[:1900]}\n```")

    @adminCheck()
    @commands.command(name="recache", aliases=["sync", "resync"])
    async def recache(self, ctx):
        msg = await ctx.respond(f"{emojis.loading} Refreshing all caches...")
        try:
            start = asyncio.get_running_loop().time()
            await cache_manager.syncAll()
            elapsed = asyncio.get_running_loop().time() - start
            await msg.edit(f"{emojis.success} Cache refresh complete in {elapsed:.2f}s")
        except Exception as e:
            await ctx.respond(f"{emojis.fail} Cache refresh failed:\n```py\n{e}\n```")

    @adminCheck()
    @commands.command(name="json")
    async def json(self, ctx, *, song: str):
        results = findClosestMatch(song, listAll=True)
        if not results:
            return await ctx.respond(f"{emojis.fail} No song found for `{song}`.", ephemeral=True)
        best = results["best"]
        raw = json.dumps(best, indent=2, default=str)
        filename = f"{best.get('name', 'song')}.json"
        if len(raw) > 1900:
            buffer = io.BytesIO(raw.encode("utf-8"))
            await ctx.respond(file=discord.File(fp=buffer, filename=filename), ephemeral=True)
        else:
            await ctx.respond(f"```json\n{raw}\n```", ephemeral=True)

    @adminCheck()
    @commands.command()
    async def sban(self, ctx, user: discord.User):
        # rofl
        msg = await ctx.reply(f"{emojis.loading} Working on it.. (1 EXCLUSION)")
        delay = random.randint(3, 7)
        await asyncio.sleep(delay)
        elapsed_ms = round(delay * 1000 + random.randint(0, 999) + random.uniform(0.01, 0.99), 2)
        await msg.edit(
            content=(
                f"BANNED {user.mention} from the following services in {elapsed_ms}ms:\n"
                "```\n"
                "juicewrldapi.com.web\n"
                "juicewrldapi.com.api\n"
                "juicewrldapi.desktop\n"
                "juicewrldapi.android\n"
                "juicewrldapi.ios\n"
                "juicewrldapi.services.bot\n"
                "juicewrldapi.services.dj\n"
                "juicewrldapi.master\n"
            )
        )

    @adminCheck()
    @commands.command(name="botban")
    async def botban(self, ctx, user: discord.User, *, reason: str = "No reason provided"):
        if user.id == ctx.author.id:
            return await ctx.respond(f"{emojis.fail} You cannot ban yourself.", ephemeral=True)
        if isBlacklistedUser(user.id):
            return await ctx.respond(f"{emojis.fail} {user.mention} is already banned.", ephemeral=True)
        await self.bot.db.addBan(user.id, reason)
        await ctx.respond(f"{emojis.success} Banned {user.mention} for: `{reason}`")
        consoleLog("ADMIN", f"{ctx.author} banned {user} for: {reason}")

    @adminCheck()
    @commands.command(name="botunban")
    async def botunban(self, ctx, user: discord.User):
        if not isBlacklistedUser(user.id):
            return await ctx.respond(f"{emojis.fail} {user.mention} is not banned.", ephemeral=True)
        await self.bot.db.removeBan(user.id)
        await ctx.respond(f"{emojis.success} Unbanned {user.mention}")
        consoleLog("ADMIN", f"{ctx.author} unbanned {user}")

    @adminCheck()
    @commands.command(name="portdb")
    async def portdb(self, ctx):
        if not settings.db_host or not settings.db_name:
            return await ctx.respond(f"{emojis.fail} No MySQL credentials configured in `.env`.")

        msg = await ctx.reply(f"{emojis.loading} Connecting to MySQL at `{settings.db_host}`...")

        mysql_url = (
            f"mysql+pymysql://{urllib.parse.quote_plus(settings.db_user)}:{urllib.parse.quote_plus(settings.db_password)}"
            f"@{settings.db_host}:{settings.db_port}/{settings.db_name}?charset=utf8mb4"
        )

        try:
            mysql_engine = create_engine(mysql_url, echo=False, pool_pre_ping=True)
            with mysql_engine.connect() as conn:
                mysql_tables = conn.execute(
                    sql_text("SELECT TABLE_NAME FROM information_schema.tables WHERE TABLE_SCHEMA = :db"),
                    {"db": settings.db_name},
                ).scalars().all()
        except Exception as e:
            await msg.edit(content=f"{emojis.fail} Failed to connect to MySQL:\n```py\n{e}\n```")
            return

        if not mysql_tables:
            await msg.edit(content=f"{emojis.fail} No tables found in `{settings.db_name}`.")
            return

        local_tables = set(Base.metadata.tables.keys())
        results = []

        for table_name in mysql_tables:
            if table_name not in local_tables:
                results.append(f"**{table_name}**: skipped (not in local schema)")
                continue

            try:
                with mysql_engine.connect() as conn:
                    rows = conn.execute(sql_text(f"SELECT * FROM `{table_name}`")).mappings().all()
                    row_dicts = [dict(r) for r in rows]

                if not row_dicts:
                    results.append(f"**{table_name}**: 0 rows (empty)")
                    continue

                table = Base.metadata.tables[table_name]
                pk_cols = [c.name for c in table.primary_key.columns]

                await msg.edit(content=f"{emojis.loading} Porting `{table_name}` ({len(row_dicts)} rows)...")

                async with db.session() as s:
                    await s.execute(table.delete())
                    for row in row_dicts:
                        clean = {k: v for k, v in row.items() if k in table.c}
                        if pk_cols:
                            stmt = sqlite_insert(table).values(**clean)
                            set_dict = {c: getattr(table.c, c) for c in clean if c not in pk_cols}
                            if set_dict:
                                stmt = stmt.on_conflict_do_update(index_elements=pk_cols, set_=set_dict)
                            else:
                                stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)
                            await s.execute(stmt, clean)
                        else:
                            await s.execute(table.insert().values(**clean))
                    await s.commit()

                results.append(f"**{table_name}**: {len(row_dicts)} rows ported")
                consoleLog("PORTDB", f"ported {table_name}: {len(row_dicts)} rows")
            except Exception as e:
                results.append(f"**{table_name}**: error — {e}")
                consoleLog("PORTDB", f"error porting {table_name}: {e}", type="error")

        mysql_engine.dispose()

        await db.loadRuntime()

        summary = "\n".join(results)
        if len(summary) > 1900:
            fp = io.BytesIO(summary.encode("utf-8"))
            await msg.edit(content=f"{emojis.success} Port complete. See attached log.")
            await ctx.send(file=discord.File(fp, filename="portdb_log.txt"), ephemeral=True)
        else:
            await msg.edit(content=f"{emojis.success} Port complete:\n```\n{summary}\n```")
        consoleLog("ADMIN", f"portdb completed by {ctx.author}")


def setup(bot):
    bot.add_cog(AdminCog(bot))
