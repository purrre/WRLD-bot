import json

import discord
from discord import ButtonStyle
from discord.ext import commands
from discord.ui import ActionRow, Button, Select

from config import emojis, NOT_YOURS
from utils.components import createContainer, TimeoutView
from utils.database import db, REACTION_KINDS_DB, statColumns
from utils.functions import adminCheck

REACTION_COLUMNS = ["reputation"] + [f"{k}_{d}" for k in REACTION_KINDS_DB for d in ("rx", "tx")]


def reaction_label(col):
    if col == "reputation":
        return "Reputation"
    parts = col.rsplit("_", 1)
    if len(parts) == 2:
        kind, direction = parts
        return f"{kind.title()} ({'received' if direction == 'rx' else 'given'})"
    return col



async def execute_undo(undo_type, undo_data_str):
    if undo_type is None or undo_data_str is None:
        raise ValueError("no undo info stored")
    data = json.loads(undo_data_str)
    if undo_type == "reaction_set":
        await db.upsertReactionColumn(data["uid"], data["col"], data["old"])
    elif undo_type == "reaction_add":
        await db.addReactionColumn(data["uid"], data["col"], -data["delta"])
    elif undo_type == "reaction_clear":
        for col in REACTION_COLUMNS:
            await db.upsertReactionColumn(data["uid"], col, data["old_stats"].get(col, 0))
    elif undo_type == "grail_add":
        await db.removeGrail(data["uid"], data["song"])
    elif undo_type == "grail_remove":
        await db.addGrail(data["uid"], data["song"])
    elif undo_type == "grail_clear":
        for song in data["old_grails"]:
            await db.addGrail(data["uid"], song)
    elif undo_type == "botstat_set":
        await db.setBotStatDirect(data["col"], data["old"])
    elif undo_type == "botstat_add":
        await db.incrementStat(data["col"], -data["delta"])
    elif undo_type == "ban":
        await db.removeBan(data["uid"])
    elif undo_type == "unban":
        await db.addBan(data["uid"], data.get("reason", ""))
    else:
        raise ValueError(f"unknown undo type: {undo_type}")



class UserIdModal(discord.ui.Modal):
    def __init__(self, title, callback_fn):
        super().__init__(title=title[:45])
        self.callback_fn = callback_fn
        self.add_item(discord.ui.InputText(label="User ID", placeholder="123456789012345678", required=True))

    async def callback(self, interaction):
        await self.callback_fn(interaction, self.children[0].value.strip())


class AmountModal(discord.ui.Modal):
    def __init__(self, title, label, callback_fn):
        super().__init__(title=title[:45])
        self.callback_fn = callback_fn
        self.add_item(discord.ui.InputText(label=label[:45], required=True))

    async def callback(self, interaction):
        await self.callback_fn(interaction, self.children[0].value.strip())


class TextModal(discord.ui.Modal):
    def __init__(self, title, label, callback_fn, placeholder=""):
        super().__init__(title=title[:45])
        self.callback_fn = callback_fn
        self.add_item(discord.ui.InputText(label=label[:45], placeholder=placeholder, required=True))

    async def callback(self, interaction):
        await self.callback_fn(interaction, self.children[0].value.strip())


class CommitIdModal(discord.ui.Modal):
    def __init__(self, callback_fn):
        super().__init__(title="Undo Commit")
        self.callback_fn = callback_fn
        self.add_item(discord.ui.InputText(label="Commit ID", placeholder="e.g. 1", required=True))

    async def callback(self, interaction):
        await self.callback_fn(interaction, self.children[0].value.strip())


def section_select(panel):
    select = Select(
        placeholder="Select a section to edit...",
        options=[
            discord.SelectOption(label="Reactions", value="reactions", emoji="😭", description="Edit reaction stats"),
            discord.SelectOption(label="Grails", value="grails", emoji="🏆", description="Edit user grails"),
            discord.SelectOption(label="Bot Stats", value="botstats", emoji="📊", description="Edit bot statistics"),
            discord.SelectOption(label="Bans", value="bans", emoji="🚫", description="Ban/unban users"),
        ],
    )
    select.callback = panel._on_section
    return select


def undo_btn(panel):
    btn = Button(label="Undo", style=ButtonStyle.danger)
    btn.callback = panel._on_undo
    return btn


async def commit_history_container():
    commits = await db.getDbCommits()
    cont = createContainer(title="Commit History", heading="##", color=None)
    if not commits:
        cont.add_text("-# No actions yet.")
        return cont
    lines = []
    for e in commits:
        text = f"`#{e['id']}` {e['description']}"
        lines.append(f"~~{text}~~" if e["undone"] else text)
    cont.add_text("\n".join(lines))
    return cont


async def main_container():
    table_lines = []
    for table in db.tables():
        try:
            count = await db.countRows(table)
        except Exception:
            count = "?"
        table_lines.append(f"- `{table}` - {count}")
    cont = createContainer(title="Database Admin", heading="##", color=None)
    cont.add_text("\n".join(table_lines))
    try:
        totals = await db.reactionTotals()
        cont.add_separator(divider=True)
        cont.add_text("### Reaction Totals\n" + "\n".join(f"- {k}: {v:,}" for k, v in totals.items()))
    except Exception:
        pass
    try:
        stats = await db.getStats()
        cont.add_separator(divider=True)
        cont.add_text("### Bot Stats\n" + "\n".join(f"- {k}: {v:,}" for k, v in stats.items()))
    except Exception:
        pass
    cont.add_separator(divider=True)
    commits = await db.getDbCommits()
    if commits:
        commit_lines = []
        for e in commits:
            text = f"`#{e['id']}` {e['description']}"
            commit_lines.append(f"~~{text}~~" if e["undone"] else text)
        cont.add_text("### Commit History\n" + "\n".join(commit_lines))
    else:
        cont.add_text("### Commit History\n-# No actions yet.")
    return cont



class DbPanel(TimeoutView):
    def __init__(self, bot, user_id):
        super().__init__(timeout=600)
        self.bot = bot
        self.user_id = user_id
        self.edit_user_id = None
        self.edit_column = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(NOT_YOURS, ephemeral=True)
            return False
        return True

    def _btn(self, label, callback, *, style=ButtonStyle.gray, disabled=False):
        btn = Button(label=label, style=style, disabled=disabled)
        btn.callback = callback
        return btn

    async def _render(self, interaction, container, *rows):
        self.clear_items()
        self.add_item(container)
        for row in rows:
            self.add_item(row)
        if interaction.response.is_done():
            await interaction.edit_original_response(view=self)
        else:
            await interaction.response.edit_message(view=self)

    async def _show_main(self, interaction):
        container = await main_container()
        await self._render(interaction, container, ActionRow(section_select(self)), ActionRow(undo_btn(self)))

    async def _show_commits(self, interaction):
        container = await commit_history_container()
        await self._render(interaction, container, ActionRow(section_select(self)), ActionRow(undo_btn(self)))

    async def _on_section(self, interaction):
        section = interaction.data["values"][0]
        if section == "reactions":
            await interaction.response.send_modal(UserIdModal("Reactions Editor", self._rx_load))
        elif section == "grails":
            await interaction.response.send_modal(UserIdModal("Grails Editor", self._grail_load))
        elif section == "botstats":
            self.edit_column = None
            await self._bs_render(interaction)
        elif section == "bans":
            await interaction.response.send_modal(UserIdModal("Ban Editor", self._ban_load))

    async def _on_undo(self, interaction):
        await interaction.response.send_modal(CommitIdModal(self._undo_exec))

    async def _undo_exec(self, interaction, id_str):
        try:
            commit_id = int(id_str)
        except ValueError:
            return await interaction.response.send_message(f"{emojis.fail} Invalid commit ID.", ephemeral=True)
        commit = await db.getDbCommit(commit_id)
        if commit is None:
            return await interaction.response.send_message(f"{emojis.fail} Commit #{commit_id} not found.", ephemeral=True)
        if commit["undone"]:
            return await interaction.response.send_message(f"{emojis.fail} Commit #{commit_id} already undone.", ephemeral=True)
        if not commit["undo_type"]:
            return await interaction.response.send_message(f"{emojis.fail} Commit #{commit_id} cannot be undone.", ephemeral=True)
        try:
            await execute_undo(commit["undo_type"], commit["undo_data"])
            await db.markDbCommitUndone(commit_id)
        except Exception as e:
            return await interaction.response.send_message(f"{emojis.fail} Undo failed: `{e}`", ephemeral=True)
        await self._show_commits(interaction)


    async def _rx_load(self, interaction, uid_str):
        if not uid_str.isdigit():
            return await interaction.response.send_message(f"{emojis.fail} Invalid user ID.", ephemeral=True)
        self.edit_user_id = uid_str
        self.edit_column = None
        await self._rx_render(interaction)

    async def _rx_render(self, interaction):
        stats = await db.getReactionStats(self.edit_user_id)
        cont = createContainer(
            title=f"Reactions - User {self.edit_user_id}",
            heading="##",
            color=None,
            description="\n".join(f"- **{reaction_label(c)}**: {stats.get(c, 0):,}" for c in REACTION_COLUMNS),
        )
        select = Select(
            placeholder="Select reaction type...",
            options=[discord.SelectOption(label=reaction_label(c), value=c, description=f"Current: {stats.get(c, 0):,}") for c in REACTION_COLUMNS],
        )
        select.callback = self._rx_select
        row_btns = ActionRow(
            self._btn("Set", self._rx_set, style=ButtonStyle.primary),
            self._btn("Add", self._rx_add, style=ButtonStyle.success),
            self._btn("Remove", self._rx_remove, style=ButtonStyle.danger),
            self._btn("Clear User", self._rx_clear, style=ButtonStyle.danger),
            self._btn("Back", self._show_commits),
        )
        await self._render(interaction, cont, ActionRow(select), row_btns)

    async def _rx_select(self, interaction):
        self.edit_column = interaction.data["values"][0]
        await interaction.response.defer()

    async def _rx_set(self, interaction):
        if not self.edit_column:
            return await interaction.response.send_message(f"{emojis.fail} Select a reaction type first.", ephemeral=True)
        await interaction.response.send_modal(AmountModal("Set Value", "Set to", self._rx_do_set))

    async def _rx_do_set(self, interaction, val_str):
        try:
            val = int(val_str)
        except ValueError:
            return await interaction.response.send_message(f"{emojis.fail} Invalid number.", ephemeral=True)
        col, uid = self.edit_column, self.edit_user_id
        old = (await db.getReactionStats(uid)).get(col, 0)
        await db.upsertReactionColumn(uid, col, val)
        await db.addDbCommit(
            f"Set {reaction_label(col)} to {val:,} for user {uid}",
            undo_type="reaction_set",
            undo_data=json.dumps({"uid": uid, "col": col, "old": old}),
        )
        await self._show_commits(interaction)

    async def _rx_add(self, interaction):
        if not self.edit_column:
            return await interaction.response.send_message(f"{emojis.fail} Select a reaction type first.", ephemeral=True)
        await interaction.response.send_modal(AmountModal("Add Amount", "Add how many", self._rx_do_add))

    async def _rx_do_add(self, interaction, val_str):
        try:
            val = int(val_str)
        except ValueError:
            return await interaction.response.send_message(f"{emojis.fail} Invalid number.", ephemeral=True)
        col, uid = self.edit_column, self.edit_user_id
        await db.addReactionColumn(uid, col, val)
        await db.addDbCommit(
            f"Added {val:,} {reaction_label(col)} to user {uid}",
            undo_type="reaction_add",
            undo_data=json.dumps({"uid": uid, "col": col, "delta": val}),
        )
        await self._show_commits(interaction)

    async def _rx_remove(self, interaction):
        if not self.edit_column:
            return await interaction.response.send_message(f"{emojis.fail} Select a reaction type first.", ephemeral=True)
        await interaction.response.send_modal(AmountModal("Remove Amount", "Remove how many", self._rx_do_remove))

    async def _rx_do_remove(self, interaction, val_str):
        try:
            val = int(val_str)
        except ValueError:
            return await interaction.response.send_message(f"{emojis.fail} Invalid number.", ephemeral=True)
        col, uid = self.edit_column, self.edit_user_id
        await db.addReactionColumn(uid, col, -val)
        await db.addDbCommit(
            f"Removed {val:,} {reaction_label(col)} from user {uid}",
            undo_type="reaction_add",
            undo_data=json.dumps({"uid": uid, "col": col, "delta": -val}),
        )
        await self._show_commits(interaction)

    async def _rx_clear(self, interaction):
        uid = self.edit_user_id
        old_stats = await db.getReactionStats(uid)
        await db.clearReactionStats(uid)
        await db.addDbCommit(
            f"Cleared all reactions for user {uid}",
            undo_type="reaction_clear",
            undo_data=json.dumps({"uid": uid, "old_stats": old_stats}),
        )
        await self._show_commits(interaction)


    async def _grail_load(self, interaction, uid_str):
        if not uid_str.isdigit():
            return await interaction.response.send_message(f"{emojis.fail} Invalid user ID.", ephemeral=True)
        self.edit_user_id = uid_str
        await self._grail_render(interaction)

    async def _grail_render(self, interaction):
        grails = await db.getGrails(self.edit_user_id)
        desc = "\n".join(f"- {g}" for g in grails[:20]) if grails else "-# No grails."
        cont = createContainer(title=f"Grails - User {self.edit_user_id}", heading="##", color=None, description=desc)
        if len(grails) > 20:
            cont.add_text(f"-# ...and {len(grails) - 20} more")
        row_btns = ActionRow(
            self._btn("Add Grail", self._grail_add, style=ButtonStyle.success),
            self._btn("Remove Grail", self._grail_remove, style=ButtonStyle.danger),
            self._btn("Clear All", self._grail_clear, style=ButtonStyle.danger),
            self._btn("Back", self._show_commits),
        )
        await self._render(interaction, cont, row_btns)

    async def _grail_add(self, interaction):
        await interaction.response.send_modal(TextModal("Add Grail", "Song Name", self._grail_do_add))

    async def _grail_do_add(self, interaction, song):
        uid = self.edit_user_id
        await db.addGrail(uid, song)
        await db.addDbCommit(
            f"Added grail '{song}' to user {uid}",
            undo_type="grail_add",
            undo_data=json.dumps({"uid": uid, "song": song}),
        )
        await self._show_commits(interaction)

    async def _grail_remove(self, interaction):
        await interaction.response.send_modal(TextModal("Remove Grail", "Song Name", self._grail_do_remove))

    async def _grail_do_remove(self, interaction, song):
        uid = self.edit_user_id
        await db.removeGrail(uid, song)
        await db.addDbCommit(
            f"Removed grail '{song}' from user {uid}",
            undo_type="grail_remove",
            undo_data=json.dumps({"uid": uid, "song": song}),
        )
        await self._show_commits(interaction)

    async def _grail_clear(self, interaction):
        uid = self.edit_user_id
        old_grails = await db.getGrails(uid)
        await db.clearGrails(uid)
        await db.addDbCommit(
            f"Cleared all grails for user {uid}",
            undo_type="grail_clear",
            undo_data=json.dumps({"uid": uid, "old_grails": old_grails}),
        )
        await self._show_commits(interaction)


    async def _bs_render(self, interaction):
        stats = await db.getStats()
        cont = createContainer(
            title="Bot Stats Editor",
            heading="##",
            color=None,
            description="\n".join(f"- **{k}**: {v:,}" for k, v in stats.items()),
        )
        select = Select(
            placeholder="Select stat to edit...",
            options=[discord.SelectOption(label=c, value=c, description=f"Current: {stats.get(c, 0):,}") for c in statColumns],
        )
        select.callback = self._bs_select
        row_btns = ActionRow(
            self._btn("Set", self._bs_set, style=ButtonStyle.primary),
            self._btn("Add", self._bs_add, style=ButtonStyle.success),
            self._btn("Back", self._show_commits),
        )
        await self._render(interaction, cont, ActionRow(select), row_btns)

    async def _bs_select(self, interaction):
        self.edit_column = interaction.data["values"][0]
        await interaction.response.defer()

    async def _bs_set(self, interaction):
        if not self.edit_column:
            return await interaction.response.send_message(f"{emojis.fail} Select a stat first.", ephemeral=True)
        await interaction.response.send_modal(AmountModal("Set Stat", f"Set {self.edit_column} to", self._bs_do_set))

    async def _bs_do_set(self, interaction, val_str):
        try:
            val = int(val_str)
        except ValueError:
            return await interaction.response.send_message(f"{emojis.fail} Invalid number.", ephemeral=True)
        col = self.edit_column
        old = (await db.getStats()).get(col, 0)
        await db.setBotStatDirect(col, val)
        await db.addDbCommit(
            f"Set {col} to {val:,}",
            undo_type="botstat_set",
            undo_data=json.dumps({"col": col, "old": old}),
        )
        await self._show_commits(interaction)

    async def _bs_add(self, interaction):
        if not self.edit_column:
            return await interaction.response.send_message(f"{emojis.fail} Select a stat first.", ephemeral=True)
        await interaction.response.send_modal(AmountModal("Add to Stat", f"Add to {self.edit_column}", self._bs_do_add))

    async def _bs_do_add(self, interaction, val_str):
        try:
            val = int(val_str)
        except ValueError:
            return await interaction.response.send_message(f"{emojis.fail} Invalid number.", ephemeral=True)
        col = self.edit_column
        await db.incrementStat(col, val)
        await db.addDbCommit(
            f"Added {val:,} to {col}",
            undo_type="botstat_add",
            undo_data=json.dumps({"col": col, "delta": val}),
        )
        await self._show_commits(interaction)


    async def _ban_load(self, interaction, uid_str):
        if not uid_str.isdigit():
            return await interaction.response.send_message(f"{emojis.fail} Invalid user ID.", ephemeral=True)
        self.edit_user_id = uid_str
        await self._ban_render(interaction)

    async def _ban_render(self, interaction):
        is_banned = db.isBanned(self.edit_user_id)
        reason = db.getBanReason(self.edit_user_id) or ""
        if is_banned:
            desc = f"🚫 **Banned**\n**Reason:** {reason or 'No reason provided'}"
            btn = self._btn("Unban", self._ban_unban, style=ButtonStyle.success)
        else:
            desc = "✅ **Not banned**"
            btn = self._btn("Ban", self._ban_ban, style=ButtonStyle.danger)
        cont = createContainer(title=f"Bans - User {self.edit_user_id}", heading="##", color=None, description=desc)
        row_btns = ActionRow(btn, self._btn("Back", self._show_commits))
        await self._render(interaction, cont, row_btns)

    async def _ban_ban(self, interaction):
        await interaction.response.send_modal(TextModal("Ban User", "Reason", self._ban_do_ban, placeholder="No reason provided"))

    async def _ban_do_ban(self, interaction, reason):
        uid = self.edit_user_id
        await db.addBan(uid, reason)
        await db.addDbCommit(
            f"Banned user {uid} ({reason})",
            undo_type="ban",
            undo_data=json.dumps({"uid": uid}),
        )
        await self._show_commits(interaction)

    async def _ban_unban(self, interaction):
        uid = self.edit_user_id
        reason = db.getBanReason(uid) or ""
        await db.removeBan(uid)
        await db.addDbCommit(
            f"Unbanned user {uid}",
            undo_type="unban",
            undo_data=json.dumps({"uid": uid, "reason": reason}),
        )
        await self._show_commits(interaction)



class DbAdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @adminCheck()
    @commands.group(name="dbadmin", invoke_without_command=True, aliases=["db"])
    async def dbadmin(self, ctx):
        panel = DbPanel(self.bot, ctx.author.id)
        container = await main_container()
        panel.clear_items()
        panel.add_item(container)
        panel.add_item(ActionRow(section_select(panel)))
        panel.add_item(ActionRow(undo_btn(panel)))
        await ctx.respond(view=panel)

    @adminCheck()
    @dbadmin.command(name="c", aliases=["commits", "history", "log"])
    async def dbadmin_commits(self, ctx):
        panel = DbPanel(self.bot, ctx.author.id)
        container = await commit_history_container()
        panel.clear_items()
        panel.add_item(container)
        panel.add_item(ActionRow(section_select(panel)))
        panel.add_item(ActionRow(undo_btn(panel)))
        await ctx.respond(view=panel)


def setup(bot):
    bot.add_cog(DbAdminCog(bot))
