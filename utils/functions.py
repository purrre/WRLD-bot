import asyncio
import json
import logging
import os
import random
import re
import sys
import urllib.parse

import aiohttp
import discord
from discord.ext import commands
from discord.gateway import _log
from rapidfuzz import fuzz, process as rf_process

import config


# ==============================================================================
# logging
# ==============================================================================

infoFmt = "%(message)s"
errorFmt = "%(asctime)s - %(levelname)s - %(message)s"
dateFmt = "%Y-%m-%d %H:%M:%S"
levels = {
    "info": logging.INFO,
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "critical": logging.CRITICAL,
}


class LogFormatter(logging.Formatter):
    def format(self, record):
        fmt = infoFmt if record.levelno == logging.INFO else errorFmt
        return logging.Formatter(fmt, datefmt=dateFmt).format(record)


def getLogger(name):
    log = logging.getLogger(name)
    if not log.handlers:
        log.setLevel(logging.INFO)
        log.propagate = False
        handler = logging.StreamHandler()
        handler.setFormatter(LogFormatter())
        log.addHandler(handler)
    return log


def consoleLog(origin, message, type="info"):
    getLogger(origin).log(levels.get(type.lower(), logging.INFO), message)


# ==============================================================================
# json loading (cached)
# ==============================================================================

jsonCache = {}


def loadJsonFile(filePath, cacheKey=None, default=None, logErrors=True):
    key = cacheKey or filePath
    if key in jsonCache:
        return jsonCache[key]
    try:
        with open(filePath, "r", encoding="utf-8") as f:
            data = json.load(f)
            jsonCache[key] = data
            return data
    except FileNotFoundError:
        if logErrors:
            consoleLog("JSON", f"file not found: {filePath}", type="warning")
    except json.JSONDecodeError as e:
        if logErrors:
            consoleLog("JSON", f"decode error in {filePath}: {e}", type="error")
    except Exception as e:
        if logErrors:
            consoleLog("JSON", f"error loading {filePath}: {e}", type="error")
    jsonCache[key] = default
    return default


def getEraMap():
    return loadJsonFile(os.path.join(config.ROOT, "data", "mapping.json"), cacheKey="era_map", default={}, logErrors=False)


def getEmojiMap():
    return loadJsonFile(os.path.join(config.ROOT, "data", "emoji-mapping.json"), cacheKey="emoji_map", default={}, logErrors=False)


def getCommandInfo():
    return loadJsonFile(os.path.join(config.ROOT, "data", "command_info.json"), cacheKey="command_info", default={})


# ==============================================================================
# http session and api calls
# ==============================================================================

_session = None


async def getSession():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def closeSession():
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
        _session = None


async def httpcall(url, method="GET", paginate=False, expect_json=True, **kwargs):
    # lazy import keeps the ui module from being a hard dependency of this one
    from utils.components import getErrorEmbed
    session = await getSession()
    results = []
    current = url
    try:
        while current:
            async with session.request(method, current, **kwargs) as resp:
                if resp.status >= 400:
                    return False, getErrorEmbed("API Request Failed", "Failed to fetch data from the endpoint.", status=resp.status)
                if not expect_json:
                    return True, await resp.text()
                data = await resp.json()
                if paginate and isinstance(data, dict) and "results" in data:
                    results.extend(data["results"])
                    current = data.get("next")
                    if "params" in kwargs:
                        kwargs["params"] = None
                else:
                    return True, data
        return True, results
    except Exception as e:
        consoleLog("HTTP", str(e), type="error")
        return False, getErrorEmbed("Connection Error", "An unexpected error occurred while contacting the API.")


def browseUrl(query):
    return config.endpoints.browse + urllib.parse.quote(str(query))


def downloadUrl(path, *, plus=False):
    encoded = urllib.parse.quote_plus(str(path)) if plus else urllib.parse.quote(str(path))
    return config.endpoints.download + encoded


def apiHost(url):
    return urllib.parse.urlparse(str(url)).netloc


async def checkApiHealth(url, timeout=5):
    try:
        session = await getSession()
        async with session.get(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


async def fetchJson(url, timeout=10):
    try:
        session = await getSession()
        async with session.get(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception:
        return None


async def fetchBrowseItems(query, timeout=10):
    data = await fetchJson(browseUrl(query), timeout=timeout)
    if not isinstance(data, dict):
        return []
    items = data.get("items", []) or []
    return items if isinstance(items, list) else []


# ==============================================================================
# text processing and fuzzy matching
# ==============================================================================
def stripVersionMarkers(text):
    if not text:
        return ""
    cleaned = re.sub(r"\s*[\[(]\s*v(?:er(?:sion)?)?\s*\d+[a-z]?\s*[\])]\s*", " ", str(text), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()

def normalizeText(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())

def normalizeMatchingText(text):
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def tokenizeText(value):
    return [tok for tok in re.split(r"[^a-z0-9]+", normalizeText(value)) if len(tok) >= 3]


def getTitleKey(value):
    tokens = tokenizeText(str(value or ""))
    filtered = [tok for tok in tokens if tok not in {"and", "the", "a", "an", "with", "feat", "featuring", "ft", "x"}]
    return " ".join(filtered or tokens)

def extractTokens(text):
    if not text:
        return []
    return re.findall(r"[a-z0-9]+", str(text).lower())

def findClosestMatch(userInput, listAll=False):
    from utils.cache import cache
    if len(userInput) < 3:
        return None
    songEntries = cache.getTitles()
    if not songEntries:
        return None
    queryNormalized = normalizeMatchingText(userInput)
    queryTokens = extractTokens(userInput)
    if not queryTokens:
        return None
    canonicalBest = {}
    for entry in songEntries:
        aliases = entry.get("aliases", [])
        if not aliases:
            continue
        canonical = entry["canonical"]
        normCanonical = entry["normalized_canonical"]
        bestScore = -1
        bestAlias = None
        for alias in aliases:
            tokens = extractTokens(alias)
            if not all(q in tokens for q in queryTokens):
                continue
            tokenRatio = len(queryTokens) / len(tokens) if tokens else 0
            consecutive = False
            for i in range(len(tokens) - len(queryTokens) + 1):
                if tokens[i:i + len(queryTokens)] == queryTokens:
                    consecutive = True
                    break
            startsWith = tokens[:len(queryTokens)] == queryTokens
            exactMatch = tokens == queryTokens
            score = 0
            if exactMatch:
                score += 200
            if consecutive:
                score += 100
            if startsWith:
                score += 50
            score += tokenRatio * 30
            if (consecutive or tokenRatio > 0.5 or exactMatch) and score > bestScore:
                bestScore = score
                bestAlias = alias
        if bestScore > -1:
            canonicalBest[normCanonical] = (bestScore, canonical, bestAlias)
    sortedMatches = sorted(canonicalBest.values(), key=lambda x: x[0], reverse=True)
    strictMatches = [canonical for _, canonical, _ in sortedMatches]
    fuzzyMatches = []
    if not strictMatches:
        indexed = []
        for entry in songEntries:
            for alias in entry.get("aliases", []):
                indexed.append({
                    "normalized": normalizeMatchingText(alias),
                    "canonical": entry["canonical"],
                    "norm_canonical": entry["normalized_canonical"],
                })
        normalizedStrings = [item["normalized"] for item in indexed]
        results = rf_process.extract(queryNormalized, normalizedStrings, scorer=fuzz.WRatio, score_cutoff=75, limit=25)
        seen = set()
        for _, _, idx in results:
            normCanonical = indexed[idx]["norm_canonical"]
            if normCanonical not in seen:
                fuzzyMatches.append(indexed[idx]["canonical"])
                seen.add(normCanonical)
    matchedCanonicals = strictMatches or fuzzyMatches
    if not matchedCanonicals:
        return None
    allSongs = cache.getSongs()
    if not allSongs:
        return None
    matchedSongs = []
    seenIds = set()
    for canonical in matchedCanonicals:
        for song in allSongs:
            publicId = song.get("public_id")
            if publicId in seenIds:
                continue
            if song.get("name", "") == canonical or canonical in song.get("track_titles", []):
                matchedSongs.append(song)
                seenIds.add(publicId)
    if not matchedSongs:
        return None
    if listAll:
        return {"best": matchedSongs[0], "all": matchedSongs}
    if len(matchedSongs) > 1:
        return None
    return matchedSongs[0]

# ==============================================================================
# admin lookup and command checks
# ==============================================================================

def isAdminUser(userId):
    return str(userId) in config.settings.admin_ids

def getRandomLyric():
    from utils.cache import cache
    data = cache.getLyrics()
    if not data:
        return None
    valid_entries = [e for e in data.values() if isinstance(e, dict) and e.get("lyrics")]
    if not valid_entries:
        return None
    for _ in range(50):
        entry = random.choice(valid_entries)
        name = entry.get("name", "Unknown Track")
        lines = [
            line.strip().replace("\r", "")
            for line in entry.get("lyrics", "").split("\n")
        ]
        good = [
            line for line in lines
            if len(line) >= 18
            and not line.startswith(("[", "("))
            and not line.endswith(("]", ")"))
            and line.lower().count("ayy") <= 1
            and line.lower().count("yeah") <= 2
            and len(set(line.lower().split())) >= 3
        ]
        if good:
            return {"text": random.choice(good), "song": name}
    return None

async def setRandomLyricStatus(bot):
    lyric = getRandomLyric()
    if lyric:
        await bot.change_presence(activity=discord.CustomActivity(name="Custom Status", state=lyric["text"]))
        consoleLog("STATUS", f'set status: "{lyric["text"]}" — {lyric["song"]}')
        return lyric
    consoleLog("STATUS", "no lyrics available for status", type="warning")
    return None

def isBlacklistedUser(userId):
    from utils.database import db
    return db.getBanReason(userId) is not None

def fireAndForget(coro):
    try:
        asyncio.create_task(coro)
    except RuntimeError:
        pass

async def send(ctx, **kwargs):
    if hasattr(ctx, "respond"):
        await ctx.respond(**kwargs)
    else:
        await ctx.send(**{k: v for k, v in kwargs.items() if k != "ephemeral"})

def adminCheck():
    async def predicate(ctx):
        if isAdminUser(ctx.author.id):
            return True
        from utils.database import db
        reason = db.getBanReason(ctx.author.id)
        if reason is not None:
            embed = discord.Embed(description=f"{config.emojis.mod} You are blacklisted from using WRLD for `{reason}`")
            await send(ctx, embed=embed, ephemeral=True)
            raise commands.CheckFailure("blacklisted")
        return False
    return commands.check(predicate)


def blacklistCheck(bot):
    async def predicate(ctx):
        from utils.database import db
        reason = db.getBanReason(ctx.author.id)
        if reason is not None:
            embed = discord.Embed(description=f"{config.emojis.mod} You are blacklisted from using WRLD for `{reason}`")
            await send(ctx, embed=embed, ephemeral=True)
            raise commands.CheckFailure("blacklisted")
        stat = "slash_commands_run" if isinstance(ctx, discord.ApplicationContext) else "commands_run"
        fireAndForget(db.incrementStat(stat))
        return True
    return predicate

def disabledCommandsCheck(bot):
    exempt = {"disable", "enable", "disablecmd", "enablecmd", "disabledcmds"}

    async def predicate(ctx):
        if not ctx.guild:
            return True
        cmdName = ctx.command.qualified_name if ctx.command else None
        if not cmdName or cmdName in exempt:
            return True
        from utils.database import db
        if db.isCommandDisabled(ctx.guild.id, cmdName):
            msg = f"{config.emojis.fail} `{cmdName}` is disabled in this server."
            if hasattr(ctx, "respond"):
                await ctx.respond(msg, ephemeral=True, delete_after=7.0)
            else:
                sent = await ctx.send(msg)
                await asyncio.sleep(7)
                try:
                    await sent.delete()
                except Exception:
                    pass
            raise commands.CheckFailure("command_disabled")
        return True
    return predicate

# ==============================================================================
# android gateway identify patch
# ==============================================================================

async def mobileIdentify(self):
    payload = {
        "op": self.IDENTIFY,
        "d": {
            "token": self.token,
            "properties": {
                "$os": sys.platform,
                "$browser": "Discord Android",
                "$device": "Discord Android",
                "$referrer": "",
                "$referring_domain": "",
            },
            "compress": True,
            "large_threshold": 250,
            "v": 3,
        },
    }
    if self.shard_id is not None and self.shard_count is not None:
        payload["d"]["shard"] = [self.shard_id, self.shard_count]
    state = self._connection
    if state._activity is not None or state._status is not None:
        payload["d"]["presence"] = {
            "status": state._status,
            "game": state._activity,
            "since": 0,
            "afk": False,
        }
    if state._intents is not None:
        payload["d"]["intents"] = state._intents.value
    await self.call_hooks("before_identify", self.shard_id, initial=self._initial_identify)
    await self.send_as_json(payload)
    _log.info("Shard ID %s has sent the IDENTIFY payload.", self.shard_id)