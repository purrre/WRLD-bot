import asyncio
import json
import os
import traceback

import discord
from discord.ext import bridge, commands
from discord.errors import ExtensionNotFound, NoEntryPointError
from discord.gateway import DiscordWebSocket

import config
from utils.functions import (
    consoleLog, closeSession, mobileIdentify, blacklistCheck, disabledCommandsCheck,
)
from utils.database import db
from utils.cache import ensureCache, startPoller, closeRedis

DiscordWebSocket.identify = mobileIdentify

def get_prefix(bot, message):
    if message.guild is None:
        return commands.when_mentioned_or(config.settings.prefix)(bot, message)
    prefix = db.getPrefix(message.guild.id) or config.settings.prefix
    return commands.when_mentioned_or(prefix)(bot, message)


class WRLD(bridge.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=get_prefix,
            intents=discord.Intents.all(),
            max_messages=100,
            cache_app_emojis=True,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=True, users=True, replied_user=False),
            help_command=None,
        )
        self.add_check(blacklistCheck(self))
        self.add_check(disabledCommandsCheck(self))
        self.db = db

    async def load_all_extensions(self, folder):
        consoleLog("SETUP", f"scanning {folder} for extensions...")
        for root, _, files in os.walk(folder):
            for filename in files:
                if filename.endswith(".py") and not filename.startswith("__"):
                    ext_path = os.path.join(root, filename).replace(os.path.sep, ".")[:-3]
                    try:
                        self.load_extension(ext_path)
                        consoleLog("COGS", f"loaded: {ext_path}")
                    except Exception as e:
                        if isinstance(e, (ExtensionNotFound, NoEntryPointError)):
                            consoleLog("COGS", f"skipped (no setup): {ext_path}")
                        else:
                            consoleLog("COGS", f"failed: {ext_path}", type="error")
                            traceback.print_exc()

    async def on_connect(self):
        if not hasattr(self, "_extensions_loaded"):
            await self.load_all_extensions("commands")
            self._extensions_loaded = True
            await self.sync_commands()

    async def on_ready(self):
        await config.emojis.load(self)
        consoleLog("INFO", f"logged in as {self.user} | {len(self.guilds)} guilds")
        await self.handleReboot()

    async def handleReboot(self):
        if not os.path.exists("reboot.json"):
            return
        try:
            with open("reboot.json", "r") as f:
                data = json.load(f)
            os.remove("reboot.json")
            channel = self.get_channel(data["channel_id"]) or await self.fetch_channel(data["channel_id"])
            if channel:
                try:
                    msg = await channel.fetch_message(data["message_id"])
                    await msg.edit("✅ Online. Rebooted successfully")
                except Exception:
                    await channel.send("✅ Online. Reboot complete")
        except Exception as e:
            consoleLog("SYSTEM", f"recovery error: {e}", type="error")


def pre_run_checks():
    if not config.settings.token:
        consoleLog("SETUP", "no bot token found, set BOT_TOKEN (or DEV_BOT_TOKEN in dev mode)", type="critical")
        raise SystemExit(1)


async def async_setup():
    await db.setup()
    await db.loadRuntime()
    await ensureCache()
    if config.settings.sync_enabled:
        startPoller(config.settings.sync_interval)


async def main():
    pre_run_checks()
    await async_setup()
    bot = WRLD()
    try:
        await bot.start(config.settings.token)
    finally:
        if not bot.is_closed():
            await bot.close()
        await closeSession()
        await closeRedis()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        consoleLog("SYSTEM", "shutdown")
