import asyncio
import contextlib
import io
import json
import os
import random
import sys
import traceback

import discord
from discord.ext import commands

from config import settings, emojis, ROOT
from utils.cache import cache, cache_manager
from utils.database import db, Base
from utils.functions import consoleLog, findClosestMatch, adminCheck, isBlacklistedUser, setRandomLyricStatus, errorLogBuffer

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
    @commands.command(name="pull")
    async def pull(self, ctx):
        msg = await ctx.respond(f"{emojis.loading} Pulling from GitHub...")
        proc = await asyncio.create_subprocess_exec(
            "git", "pull", "origin", "main", "--stat",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            cwd=ROOT,
        )
        out, _ = await proc.communicate()
        text = (out or b"").decode("utf-8", "replace").strip()
        tail = "\n".join(text.splitlines()[-8:])[-1500:]
        if proc.returncode != 0:
            return await msg.edit(content=f"{emojis.fail} Pull failed:\n```\n{tail}\n```")
        await msg.edit(content=f"```\n{tail}\n```\nRestarting...")
        consoleLog("SYSTEM", f"pull+restart initiated by {ctx.author}")
        os.execv(sys.executable, ["python"] + sys.argv)

    @adminCheck()
    @commands.command()
    async def w2(self, ctx):
        await ctx.reply("online.")

    def _valid_ext(self, cog_path):
        return cog_path.startswith("commands.") and ".." not in cog_path

    @adminCheck()
    @commands.command(name="reload")
    async def reload(self, ctx, cog_path: str):
        if cog_path.lower() != "all" and not self._valid_ext(cog_path):
            return await ctx.respond(f"{emojis.fail} Extension path must be under `commands.`", ephemeral=True)
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
        if not self._valid_ext(cog_path):
            return await ctx.respond(f"{emojis.fail} Extension path must be under `commands.`", ephemeral=True)
        try:
            self.bot.unload_extension(cog_path)
            await ctx.respond(f"{emojis.success} Disabled `{cog_path}`")
        except Exception as e:
            await ctx.respond(f"{emojis.fail} Failed to disable `{cog_path}`:\n```py\n{e}\n```")

    @adminCheck()
    @commands.command(name="enable")
    async def ce(self, ctx, cog_path: str):
        if not self._valid_ext(cog_path):
            return await ctx.respond(f"{emojis.fail} Extension path must be under `commands.`", ephemeral=True)
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
        token = self.bot.http.token or ""
        stdout = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout):
                exec("async def _eval_func():\n" + "\n".join(f"    {line}" for line in code.split("\n")), env)
                result = await env["_eval_func"]()
            output = stdout.getvalue().replace(token, "[redacted]")
            response = ""
            if output:
                response += f"```\n{output}\n```\n"
            if result is not None:
                response += f"```py\n{str(result).replace(token, '[redacted]')}\n```"
            await ctx.respond((response or "✅ Executed successfully ```No Output```")[:2000])
        except Exception as e:
            error = "".join(traceback.format_exception(type(e), e, e.__traceback__)).replace(token, "[redacted]")
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
    @commands.command(name="guilds")
    async def guilds(self, ctx):
        guilds = sorted(self.bot.guilds, key=lambda g: g.member_count or 0, reverse=True)
        if not guilds:
            return await ctx.respond(f"{emojis.fail} Bot is not in any guilds.")
        lines = []
        for g in guilds:
            prefix = db.getPrefix(g.id) or settings.prefix
            lines.append(f"**{g.name}** (`{g.id}`)\n-# {g.member_count or 0} members | prefix `{prefix}`")
        chunks, current = [], ""
        for line in lines:
            candidate = f"{current}\n\n{line}" if current else line
            if len(candidate) > 1800:
                chunks.append(current)
                current = line
            else:
                current = candidate
        chunks.append(current)
        header = f"{emojis.list} **{len(guilds)} guild{'s' if len(guilds) != 1 else ''}**\n\n"
        await ctx.respond(header + chunks[0])
        for chunk in chunks[1:]:
            await ctx.send(chunk)

    @adminCheck()
    @commands.command(name="guildinfo", aliases=["ginfo"])
    async def guildinfo(self, ctx, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return await ctx.respond(f"{emojis.fail} Not in a guild with ID `{guild_id}`.", ephemeral=True)
        owner = guild.owner or self.bot.get_user(guild.owner_id)
        owner_text = f"{owner} (`{guild.owner_id}`)" if owner else f"`{guild.owner_id}`"
        prefix = db.getPrefix(guild.id) or settings.prefix
        antiaudio = await db.isAntiaudioEnabled(guild.id)
        disabled = await db.disabledCommands(guild.id)
        lines = [
            f"## {guild.name}",
            f"**ID:** `{guild.id}`",
            f"**Owner:** {owner_text}",
            f"**Members:** {guild.member_count or 'N/A'}",
            f"**Channels:** {len(guild.channels)}",
            f"**Created:** {discord.utils.format_dt(guild.created_at, 'D')}",
            f"**Prefix:** `{prefix}`",
            f"**Anti-audio:** {'enabled' if antiaudio else 'disabled'}",
            f"**Disabled cmds:** {', '.join(f'`{c}`' for c in disabled) if disabled else 'none'}",
        ]
        await ctx.respond("\n".join(lines))

    @adminCheck()
    @commands.command(name="leave")
    async def leave(self, ctx, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return await ctx.respond(f"{emojis.fail} Not in a guild with ID `{guild_id}`.", ephemeral=True)
        name, gid = guild.name, guild.id
        await guild.leave()
        consoleLog("ADMIN", f"{ctx.author} forced leave from {name} ({gid})")
        await ctx.respond(f"{emojis.success} Left **{name}** (`{gid}`)")

    @adminCheck()
    @commands.command(name="guildinvite", aliases=["ginvite"])
    async def guildinvite(self, ctx, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return await ctx.respond(f"{emojis.fail} Not in a guild with ID `{guild_id}`.", ephemeral=True)
        usable = [c for c in guild.text_channels if c.permissions_for(guild.me).create_instant_invite]
        channel = guild.system_channel if guild.system_channel in usable else (usable[0] if usable else None)
        if channel is None:
            return await ctx.respond(f"{emojis.fail} No invite-capable channel found in **{guild.name}**.", ephemeral=True)
        try:
            invite = await channel.create_invite(max_age=3600, max_uses=1, reason=f"invite created for admin {ctx.author}")
        except discord.Forbidden:
            return await ctx.respond(f"{emojis.fail} Missing **Create Invite** permission in **{guild.name}**.", ephemeral=True)
        await ctx.respond(f"{emojis.success} Invite for **{guild.name}** ({invite.channel.name}): {invite.url}", ephemeral=True)

    @adminCheck()
    @commands.command(name="servercfg", aliases=["scfg"])
    async def servercfg(self, ctx, guild_id: int, action: str = "", *, value: str = ""):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return await ctx.respond(f"{emojis.fail} Not in a guild with ID `{guild_id}`.", ephemeral=True)
        if not action:
            prefix = db.getPrefix(guild.id) or settings.prefix
            antiaudio = await db.isAntiaudioEnabled(guild.id)
            disabled = await db.disabledCommands(guild.id)
            lines = [
                f"## Config: {guild.name}",
                f"**Prefix:** `{prefix}`",
                f"**Anti-audio:** {'enabled' if antiaudio else 'disabled'}",
                f"**Disabled cmds:** {', '.join(f'`{c}`' for c in disabled) if disabled else 'none'}",
                f"-# Actions: `servercfg {guild_id} prefix <value|clear>` | `antiaudio on|off` | `disable|enable <cmd>`",
            ]
            return await ctx.respond("\n".join(lines))
        action = action.lower()
        if action == "prefix":
            v = value.strip()
            if not v:
                return await ctx.respond(f"{emojis.fail} Usage: `servercfg {guild_id} prefix <value|clear>`", ephemeral=True)
            if v.lower() in ("clear", "reset"):
                await db.clearPrefix(guild.id)
                return await ctx.respond(f"{emojis.success} Prefix reset to `{settings.prefix}` in **{guild.name}**")
            if len(v) > 6:
                return await ctx.respond(f"{emojis.fail} Prefix must be 6 characters or fewer.", ephemeral=True)
            await db.setPrefix(guild.id, v)
            return await ctx.respond(f"{emojis.success} Prefix set to `{v}` in **{guild.name}**")
        if action == "antiaudio":
            v = value.strip().lower()
            if v not in ("on", "off", "enable", "disable"):
                return await ctx.respond(f"{emojis.fail} Usage: `servercfg {guild_id} antiaudio on|off`", ephemeral=True)
            enabled = v in ("on", "enable")
            await db.setAntiaudio(guild.id, enabled)
            return await ctx.respond(f"{emojis.success} Anti-audio **{'enabled' if enabled else 'disabled'}** in **{guild.name}**")
        if action in ("disable", "enable"):
            cmd_name = value.strip()
            if not cmd_name:
                return await ctx.respond(f"{emojis.fail} Usage: `servercfg {guild_id} {action} <command>`", ephemeral=True)
            cmd = self.bot.get_command(cmd_name)
            if not cmd:
                return await ctx.respond(f"{emojis.fail} Command `{cmd_name}` not found.", ephemeral=True)
            name = cmd.qualified_name
            if action == "disable":
                await db.disableCommand(guild.id, name)
            else:
                await db.enableCommand(guild.id, name)
            return await ctx.respond(f"{emojis.success} Command `{name}` **{action}d** in **{guild.name}**")
        await ctx.respond(f"{emojis.fail} Unknown action `{action}`. Use `prefix`, `antiaudio`, `disable`, or `enable`.", ephemeral=True)

    @adminCheck()
    @commands.command(name="errors")
    async def errors(self, ctx, amount: int = 10):
        if amount < 1 or amount > 50:
            return await ctx.respond(f"{emojis.fail} Amount must be between 1 and 50.", ephemeral=True)
        entries = list(errorLogBuffer)[-amount:]
        if not entries:
            return await ctx.respond(f"{emojis.success} No errors logged this session.")
        await ctx.respond(f"**Last {len(entries)} logged error{'s' if len(entries) != 1 else ''}**\n```\n{'\n'.join(entries)[-1800:]}\n```")

    @adminCheck()
    @commands.command(name="topguilds")
    async def topguilds(self, ctx):
        guilds = sorted(self.bot.guilds, key=lambda g: g.member_count or 0, reverse=True)[:10]
        if not guilds:
            return await ctx.respond(f"{emojis.fail} Bot is not in any guilds.")
        lines = [f"{i + 1}. **{g.name}** - {g.member_count or 0:,} members (`{g.id}`)" for i, g in enumerate(guilds)]
        await ctx.respond(f"{emojis.list} **Top servers by member count**\n" + "\n".join(lines))

def setup(bot):
    bot.add_cog(AdminCog(bot))
