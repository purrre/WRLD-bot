import os

from sqlalchemy import (
    Column, String, Integer, Text, DateTime, UniqueConstraint, func, select, delete,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

import config
from utils.functions import consoleLog


class Base(DeclarativeBase):
    pass

# me when typescript

class Prefix(Base):
    __tablename__ = "prefixes"
    guild_id = Column(String(25), primary_key=True)
    prefix = Column(String(10), nullable=False)

class BannedGuild(Base):
    __tablename__ = "banned_guilds"
    guild_id = Column(String(25), primary_key=True)
    reason = Column(Text)
    timestamp = Column(DateTime, server_default=func.now())

class BannedUser(Base):
    __tablename__ = "banned_users"
    user_id = Column(String(25), primary_key=True)
    reason = Column(Text)
    timestamp = Column(DateTime, server_default=func.now())

class DisabledCommand(Base):
    __tablename__ = "disabled_commands"
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(String(25))
    command_name = Column(String(50))
    __table_args__ = (UniqueConstraint("guild_id", "command_name"),)

class Grail(Base):
    __tablename__ = "grail_list"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(25))
    song_name = Column(String(255))
    added_at = Column(DateTime, server_default=func.now())

class TopSearch(Base):
    __tablename__ = "top_searches"
    search = Column(String(255), primary_key=True)
    search_count = Column(Integer, server_default="1")

class SeenUser(Base):
    __tablename__ = "seen_users"
    user_id = Column(String(25), primary_key=True)
    seen_at = Column(DateTime, server_default=func.now())

statColumns = (
    "commands_run", "slash_commands_run", "track_searches", "random_songs_found",
    "lyrics_searched", "random_lyrics_found", "leaks_found", "session_zips_found",
    "session_edits_found", "snippets_sent", "covers_found", "jwa_requests",
)

class BotStats(Base):
    __tablename__ = "bot_stats"
    id = Column(Integer, primary_key=True)
    for _name in statColumns:
        locals()[_name] = Column(Integer, server_default="0", nullable=False)
    del _name


def normalizeSqliteUrl(url):
    prefix = "sqlite+aiosqlite:///"
    if url.startswith(prefix):
        path = url[len(prefix):]
        if path and not os.path.isabs(path):
            path = os.path.join(config.ROOT, path)
            return prefix + path.replace("\\", "/")
    return url


class Database:
    def __init__(self):
        self._engine = None
        self._sessionMaker = None
        self.prefixes = {}
        self.banned = {}
        self.disabled = {}

    @property
    def engine(self):
        if self._engine is None:
            url = normalizeSqliteUrl(config.settings.database_url)
            if url.startswith("sqlite") and ":///" in url:
                dbPath = url.split(":///", 1)[1]
                os.makedirs(os.path.dirname(dbPath), exist_ok=True)
            self._engine = create_async_engine(url, echo=False)
            self._sessionMaker = async_sessionmaker(self._engine, expire_on_commit=False, class_=AsyncSession)
            consoleLog("DB", "engine ready")
        return self._engine

    def session(self):
        self.engine  # ensure sm exists
        return self._sessionMaker()

    async def close(self):
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionMaker = None
            consoleLog("DB", "engine disposed")

    async def setup(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                sqlite_insert(BotStats.__table__).values(id=1).on_conflict_do_nothing(index_elements=["id"])
            )
        consoleLog("DB", "schema ready")

    async def loadRuntime(self):
        async with self.session() as s:
            self.prefixes = {row.guild_id: row.prefix for row in (await s.execute(select(Prefix))).scalars()}
            self.banned = {row.user_id: (row.reason or "") for row in (await s.execute(select(BannedUser))).scalars()}
            self.disabled = {}
            for row in (await s.execute(select(DisabledCommand))).scalars():
                self.disabled.setdefault(str(row.guild_id), set()).add(row.command_name)
        consoleLog(
            "DB",
            f"loaded {len(self.prefixes)} prefixes, {len(self.banned)} bans, "
            f"{sum(len(v) for v in self.disabled.values())} disabled cmds",
        )

    # ---- stats ----
    async def incrementStat(self, column, amount=1):
        if column not in statColumns:
            raise ValueError(f"unknown stat column: {column!r}")
        async with self.session() as s:
            stmt = sqlite_insert(BotStats.__table__).values(id=1, **{column: amount})
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={column: getattr(BotStats.__table__.c, column) + amount},
            )
            await s.execute(stmt)
            await s.commit()

    async def getStats(self):
        async with self.session() as s:
            row = (await s.execute(select(BotStats).where(BotStats.id == 1))).scalar_one_or_none()
            if row is None:
                return {}
            return {name: getattr(row, name, 0) or 0 for name in statColumns}

    # ---- prefixes ----
    def getPrefix(self, guildId):
        return self.prefixes.get(str(guildId)) if guildId is not None else None

    async def setPrefix(self, guildId, prefix):
        gid = str(guildId)
        async with self.session() as s:
            stmt = sqlite_insert(Prefix.__table__).values(guild_id=gid, prefix=prefix)
            stmt = stmt.on_conflict_do_update(index_elements=["guild_id"], set_={"prefix": prefix})
            await s.execute(stmt)
            await s.commit()
        self.prefixes[gid] = prefix

    async def clearPrefix(self, guildId):
        gid = str(guildId)
        async with self.session() as s:
            await s.execute(delete(Prefix).where(Prefix.guild_id == gid))
            await s.commit()
        self.prefixes.pop(gid, None)

    # ---- bans ----
    def getBanReason(self, userId):
        return self.banned.get(str(userId)) if userId is not None else None

    def isBanned(self, userId):
        return str(userId) in self.banned

    async def addBan(self, userId, reason):
        uid = str(userId)
        async with self.session() as s:
            stmt = sqlite_insert(BannedUser.__table__).values(user_id=uid, reason=reason)
            stmt = stmt.on_conflict_do_update(index_elements=["user_id"], set_={"reason": reason})
            await s.execute(stmt)
            await s.commit()
        self.banned[uid] = reason

    async def removeBan(self, userId):
        uid = str(userId)
        async with self.session() as s:
            await s.execute(delete(BannedUser).where(BannedUser.user_id == uid))
            await s.commit()
        self.banned.pop(uid, None)

    # ---- disabled commands ----
    def isCommandDisabled(self, guildId, commandName):
        if guildId is None or commandName is None:
            return False
        return commandName in self.disabled.get(str(guildId), set())

    async def disableCommand(self, guildId, commandName):
        gid = str(guildId)
        async with self.session() as s:
            stmt = sqlite_insert(DisabledCommand.__table__).values(guild_id=gid, command_name=commandName)
            stmt = stmt.on_conflict_do_nothing(index_elements=["guild_id", "command_name"])
            await s.execute(stmt)
            await s.commit()
        self.disabled.setdefault(gid, set()).add(commandName)

    async def enableCommand(self, guildId, commandName):
        gid = str(guildId)
        async with self.session() as s:
            await s.execute(
                delete(DisabledCommand).where(
                    DisabledCommand.guild_id == gid, DisabledCommand.command_name == commandName
                )
            )
            await s.commit()
        self.disabled.get(gid, set()).discard(commandName)

    async def disabledCommands(self, guildId):
        gid = str(guildId)
        async with self.session() as s:
            rows = (await s.execute(select(DisabledCommand.command_name).where(DisabledCommand.guild_id == gid))).scalars()
            return list(rows)

    # ---- grails ----
    async def getGrails(self, userId):
        async with self.session() as s:
            rows = (await s.execute(select(Grail.song_name).where(Grail.user_id == str(userId)).order_by(Grail.added_at))).scalars()
            return list(rows)

    async def grailExists(self, userId, songName):
        async with self.session() as s:
            row = (await s.execute(
                select(Grail.song_name).where(
                    Grail.user_id == str(userId), func.lower(Grail.song_name) == songName.lower()
                )
            )).scalar_one_or_none()
            return row

    async def addGrail(self, userId, songName):
        async with self.session() as s:
            s.add(Grail(user_id=str(userId), song_name=songName))
            await s.commit()

    async def removeGrail(self, userId, songName):
        async with self.session() as s:
            await s.execute(
                delete(Grail).where(
                    Grail.user_id == str(userId), func.lower(Grail.song_name) == songName.lower()
                )
            )
            await s.commit()

    async def clearGrails(self, userId):
        async with self.session() as s:
            await s.execute(delete(Grail).where(Grail.user_id == str(userId)))
            await s.commit()

    # ---- generic table access ----
    def tables(self):
        return sorted(Base.metadata.tables.keys())

    def table(self, name):
        return Base.metadata.tables[name]

    def columns(self, name):
        return [c.name for c in self.table(name).columns]

    def primaryKeys(self, name):
        return [c.name for c in self.table(name).primary_key.columns]

    def editableColumns(self, name):
        out = []
        for c in self.table(name).columns:
            if c.primary_key and c.autoincrement is True:
                continue
            out.append(c.name)
        return out

    async def countRows(self, name):
        async with self.session() as s:
            return (await s.execute(select(func.count()).select_from(self.table(name)))).scalar_one()

    async def fetchRows(self, name, limit=5, offset=0):
        t = self.table(name)
        async with self.session() as s:
            rows = (await s.execute(select(t).limit(limit).offset(offset))).mappings().all()
            return [dict(r) for r in rows]

    async def allRows(self, name):
        t = self.table(name)
        async with self.session() as s:
            rows = (await s.execute(select(t))).mappings().all()
            return [dict(r) for r in rows]

    async def insertRow(self, name, values):
        t = self.table(name)
        async with self.session() as s:
            await s.execute(t.insert().values(**values))
            await s.commit()

    async def updateRow(self, name, pkValues, values):
        t = self.table(name)
        async with self.session() as s:
            stmt = t.update()
            for col, val in pkValues.items():
                stmt = stmt.where(t.c[col] == val)
            await s.execute(stmt.values(**values))
            await s.commit()

    async def deleteRow(self, name, pkValues):
        t = self.table(name)
        async with self.session() as s:
            stmt = t.delete()
            for col, val in pkValues.items():
                stmt = stmt.where(t.c[col] == val)
            await s.execute(stmt)
            await s.commit()


db = Database()
