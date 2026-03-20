
"""
Kutipan System - Admin Panel Only (No Commands)
- 4 minggu per bulan (1-7, 8-14, 15-21, 22-akhir)
- Leaderboard kutipan + nombor jersey (fixed)
- Target bulanan auto (bil ahli role dkatana * 4 * amaun minggu)
- Reminder DM 3 hari sebelum tamat minggu
- Penalti auto selepas tamat minggu
- Statistik graf (matplotlib)
- Backup JSON manual melalui Admin Panel
Timezone: Asia/Kuala_Lumpur
"""

from __future__ import annotations

import os
import sys
import json
import logging
import re
import asyncio
from datetime import datetime, timedelta
import pytz

import discord
from discord.ext import commands, tasks
from discord.ui import View, Select, UserSelect, Button
from discord import SelectOption

from config import Config

logger = logging.getLogger('DkatanaBot')


def _safe_int(x, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _now_str_kl() -> str:
    dt = now_kl()
    return dt.strftime('%d/%m/%Y %H:%M:%S')


def _update_env_var(env_path: str, key: str, value: str) -> None:
    """Update / append KEY=VALUE in .env."""
    lines: list[str] = []
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()

    out: list[str] = []
    found = False
    prefix = f"{key}="
    for ln in lines:
        if ln.strip().startswith(prefix):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{key}={value}")

    with open(env_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(out) + "\n")


def now_kl() -> datetime:
    return datetime.now(pytz.timezone(Config.TIMEZONE))


def month_key(dt: datetime | None = None) -> str:
    dt = dt or now_kl()
    return dt.strftime("%Y-%m")



def sanitize_name_for_table(name: str) -> str:
    """Buang emoji / simbol pelik supaya alignment dalam codeblock jadi kemas."""
    cleaned = re.sub(r"[^0-9A-Za-z _\-\.\|]", "", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "user"


def _truncate_with_ellipsis(s: str, width: int) -> str:
    """Truncate string supaya muat dalam column monospaced."""
    if width <= 0:
        return ""
    s = s or ""
    if len(s) <= width:
        return s
    if width <= 3:
        return s[:width]
    return s[: max(0, width - 3)] + "..."


def week_index(dt: datetime | None = None) -> int:
    """0..3 based on day-of-month blocks: 1-7, 8-14, 15-21, 22-last."""
    dt = dt or now_kl()
    d = dt.day
    if d <= 7:
        return 0
    if d <= 14:
        return 1
    if d <= 21:
        return 2
    return 3


def week_end_day_for_index(dt: datetime, idx: int) -> int:
    """Return day-of-month for end of the specified week index in dt's month."""
    # last day of month
    if dt.month == 12:
        nxt = datetime(dt.year + 1, 1, 1, tzinfo=dt.tzinfo)
    else:
        nxt = datetime(dt.year, dt.month + 1, 1, tzinfo=dt.tzinfo)
    last_day = (nxt - timedelta(days=1)).day
    return [7, 14, 21, last_day][idx]


def emoji_week(v: int) -> str:
    # Data lama:
    #   1 = bayar
    #   0 = kosong / belum direkod
    #  -1 = belum bayar
    # Baru:
    #   2 = cuti
    #  -2 = denda
    if v == 1:
        return "✅"
    if v == 2:
        return "🟨"
    if v == -2:
        return "⚠️"
    return "⬜"


class KutipanReminderModal(discord.ui.Modal):
    def __init__(self, cog: "KutipanCog"):
        super().__init__(title="📨 Reminder Kutipan")
        self.cog = cog
        self.msg = discord.ui.TextInput(
            label="Mesej reminder (akan dihantar DM)",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1200,
            default=(
                "Asalamualaikum untuk anak dkatana anda diminta untuk membayar duit kutipan markas kepada bendahari dkatana.\n"
                "Terima Kasih."
            )
        )
        self.add_item(self.msg)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("⚠️ Guild tidak dijumpai.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        sent, failed = await self.cog.send_kutipan_reminders(interaction.guild, str(self.msg.value))
        await interaction.followup.send(
            f"✅ Reminder dihantar. Berjaya: **{sent}** | Gagal: **{failed}**",
            ephemeral=True,
        )


class KutipanAmountModal(discord.ui.Modal):
    def __init__(self, cog: "KutipanCog", kind: str, current: int):
        title = "💸 Edit Jumlah Kutipan" if kind == 'weekly' else "💸 Edit Jumlah Penalty Kutipan"
        super().__init__(title=title)
        self.cog = cog
        self.kind = kind
        label = "Jumlah kutipan mingguan (contoh: 6000)" if kind == 'weekly' else "Jumlah penalty (contoh: 12000)"
        self.amount = discord.ui.TextInput(
            label=label,
            required=True,
            max_length=12,
            default=str(current or ""),
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.amount.value).strip().replace(",", "")
        if not raw.isdigit():
            return await interaction.response.send_message("⚠️ Sila masukkan nombor sahaja.", ephemeral=True)
        amt = int(raw)
        await interaction.response.defer(ephemeral=True)
        await self.cog.set_kutipan_amount(self.kind, amt)
        what = "Kutipan" if self.kind == 'weekly' else "Penalty"
        await interaction.followup.send(f"✅ Jumlah **{what}** berjaya ditetapkan kepada **{amt}**.", ephemeral=True)

class KutipanAdminView(View):
    def __init__(self, cog: "KutipanCog", guild: discord.Guild):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild = guild

        self.selected_user_ids: list[int] = []
        self.selected_week: str | None = None
        self.selected_status: str | None = None
        
        # Pengunci Anti-Spam / Anti-Duplicate
        self._is_processing = False

        self.add_item(KutipanUserPicker())
        self.add_item(KutipanWeekPicker())
        self.add_item(KutipanStatusPicker())
        self.add_item(KutipanSaveButton())
        self.add_item(KutipanSyncButton())
        self.add_item(KutipanRefreshButton())
        self.add_item(KutipanBackupButton())

    def summary_text(self) -> str:
        s_map = {
            "paid": "✅ Bayar",
            "unpaid": "⬜ Belum bayar",
            "cuti": "🟨 Cuti",
            "penalty": "⚠️ Denda",
            "clear": "⬜ Kosongkan",
        }
        w_show = f"Minggu {self.selected_week}" if self.selected_week and self.selected_week != "auto" else "Auto (minggu semasa)"
        s_show = s_map.get(self.selected_status, "—") if self.selected_status else "—"
        ready = bool(self.selected_user_ids) and bool(self.selected_week) and bool(self.selected_status)
        ready_show = "✅ Lengkap (boleh simpan)" if ready else "⚠️ Kosongkan"

        if self.selected_user_ids:
            ahli_info = f"<@{self.selected_user_ids[0]}> (+{len(self.selected_user_ids)-1} orang)" if len(self.selected_user_ids) > 1 else f"<@{self.selected_user_ids[0]}>"
        else:
            ahli_info = "—"

        status_proses = "⏳ **SEDANG DIPROSES...**\n\n" if self._is_processing else ""

        return (
            f"{status_proses}💰 **Panel Kutipan (Bendahari)**\n"
            "Pilih ahli + minggu + status, kemudian tekan **Simpan/Update**.\n\n"
            f"• Ahli: {ahli_info}\n"
            f"• Minggu: **{w_show}**\n"
            f"• Status: **{s_show}**\n"
            f"• Keadaan: {ready_show}\n"
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        try:
            if not isinstance(interaction.user, discord.Member):
                return False
            return self.cog.is_bendahari_or_admin(interaction.user)
        except Exception:
            return False


class KutipanUserPicker(UserSelect):
    def __init__(self):
        super().__init__(placeholder="Pilih ahli (boleh pilih ramai)…", min_values=1, max_values=25)

    async def callback(self, interaction: discord.Interaction):
        view: KutipanAdminView = self.view  # type: ignore
        if getattr(view, '_is_processing', False):
            return await interaction.response.defer()

        members = list(self.values)  # type: ignore
        role = interaction.guild.get_role(Config.AHLI_DKATANA_ROLE_ID) if interaction.guild else None
        
        if role:
            members = [m for m in members if m in role.members]

        if not members:
            view.selected_user_ids = []
            return await interaction.response.edit_message(
                content=view.summary_text() + "\n⚠️ Tiada ahli DKATANA dipilih.", view=view
            )

        view.selected_user_ids = [m.id for m in members]
        await interaction.response.edit_message(content=view.summary_text(), view=view)


class KutipanWeekPicker(Select):
    def __init__(self):
        options = [
            SelectOption(label="Minggu 1 (1-7)", value="0"),
            SelectOption(label="Minggu 2 (8-14)", value="1"),
            SelectOption(label="Minggu 3 (15-21)", value="2"),
            SelectOption(label="Minggu 4 (22-akhir)", value="3"),
            SelectOption(label="Auto (ikut tarikh sekarang)", value="auto"),
        ]
        super().__init__(placeholder="Pilih minggu…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        view: KutipanAdminView = self.view  # type: ignore
        if getattr(view, '_is_processing', False):
            return await interaction.response.defer()

        view.selected_week = self.values[0]
        await interaction.response.edit_message(content=view.summary_text(), view=view)


class KutipanStatusPicker(Select):
    def __init__(self):
        options = [
            SelectOption(label="✅ Bayar", value="paid"),
            SelectOption(label="⬜ Belum bayar", value="unpaid"),
            SelectOption(label="🟨 Cuti", value="cuti"),
            SelectOption(label="⚠️ Denda", value="penalty"),
            SelectOption(label="⬜ Kosongkan (reset minggu ini)", value="clear"),
        ]
        super().__init__(placeholder="Pilih status…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        view: KutipanAdminView = self.view  # type: ignore
        if getattr(view, '_is_processing', False):
            return await interaction.response.defer()

        view.selected_status = self.values[0]
        await interaction.response.edit_message(content=view.summary_text(), view=view)


class KutipanSaveButton(Button):
    def __init__(self):
        super().__init__(label="Simpan/Update", style=discord.ButtonStyle.blurple)

    async def callback(self, interaction: discord.Interaction):
        view: KutipanAdminView = self.view  # type: ignore

        if getattr(view, '_is_processing', False):
            return await interaction.response.defer()

        if not view.selected_user_ids or not view.selected_week or not view.selected_status:
            return await interaction.response.send_message("⚠️ Sila lengkapkan pilihan (ahli + minggu + status).", ephemeral=True)

        view._is_processing = True
        saved_user_ids = list(view.selected_user_ids) # Simpan untuk rujukan log

        try:
            # Kemaskini UI menunjukkan sedang proses
            await interaction.response.edit_message(content=view.summary_text(), view=view)

            wk = view.cog._get_current_week_idx() if view.selected_week == "auto" else int(view.selected_week)
            status = view.selected_status

            ok_count = 0
            fail_msgs = []

            for uid in saved_user_ids:
                ok, msg = await view.cog.set_payment_status(
                    guild=interaction.guild,
                    user_id=uid,
                    week_idx=wk,
                    status=status,
                    actor=interaction.user
                )
                if ok:
                    ok_count += 1
                else:
                    fail_msgs.append(msg)

            await view.cog.update_leaderboard()

            # Kosongkan senarai user selepas update supaya admin tak tersilap double save
            view.selected_user_ids = []

            text = f"✅ Selesai update: **{ok_count}** ahli dikemaskini (Minggu {wk+1}, Status: {status})."
            if fail_msgs:
                text += "\n⚠️ Gagal sebahagian: " + " | ".join(fail_msgs[:5])

            await interaction.followup.send(text, ephemeral=True)

            await view.cog.log_kutipan_update(
                guild=interaction.guild,
                actor=interaction.user,
                user_ids=saved_user_ids,
                week_idx=wk,
                status=status,
            )
        finally:
            view._is_processing = False
            # Kembalikan UI normal
            try:
                await interaction.message.edit(content=view.summary_text(), view=view)
            except Exception:
                pass


class KutipanSyncButton(Button):
    def __init__(self):
        super().__init__(label="Sync ahli role", style=discord.ButtonStyle.green)

    async def callback(self, interaction: discord.Interaction):
        view: KutipanAdminView = self.view  # type: ignore
        if getattr(view, '_is_processing', False):
            return await interaction.response.defer()

        view._is_processing = True
        try:
            await interaction.response.edit_message(content=view.summary_text(), view=view)
            await view.cog.sync_members(interaction.guild)
            await view.cog.update_leaderboard()
            await interaction.followup.send("✅ Sync siap & leaderboard dikemas kini.", ephemeral=True)
        finally:
            view._is_processing = False
            try:
                await interaction.message.edit(content=view.summary_text(), view=view)
            except Exception:
                pass


class KutipanRefreshButton(Button):
    def __init__(self):
        super().__init__(label="Refresh leaderboard", style=discord.ButtonStyle.gray)

    async def callback(self, interaction: discord.Interaction):
        view: KutipanAdminView = self.view  # type: ignore
        if getattr(view, '_is_processing', False):
            return await interaction.response.defer()

        view._is_processing = True
        try:
            await interaction.response.edit_message(content=view.summary_text(), view=view)
            await view.cog.update_leaderboard()
            await interaction.followup.send("🔄 Refresh siap.", ephemeral=True)
        finally:
            view._is_processing = False
            try:
                await interaction.message.edit(content=view.summary_text(), view=view)
            except Exception:
                pass


class KutipanBackupButton(Button):
    def __init__(self):
        super().__init__(label="Backup sekarang", style=discord.ButtonStyle.gray)

    async def callback(self, interaction: discord.Interaction):
        view: KutipanAdminView = self.view  # type: ignore
        if getattr(view, '_is_processing', False):
            return await interaction.response.defer()

        view._is_processing = True
        try:
            await interaction.response.edit_message(content=view.summary_text(), view=view)
            await view.cog.backup_now(post_to_channel=True)
            await interaction.followup.send("🗄️ Backup dijana & dihantar ke channel backup.", ephemeral=True)
        finally:
            view._is_processing = False
            try:
                await interaction.message.edit(content=view.summary_text(), view=view)
            except Exception:
                pass


class KutipanCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._started = False
        self._lb_lock = asyncio.Lock()

    @commands.Cog.listener()
    async def on_ready(self):
        # Jalankan sekali sahaja lepas bot ready.
        if self._started:
            return
        self._started = True

        # Pastikan dokumen ahli wujud untuk bulan semasa, dan update leaderboard.
        try:
            guild = self.bot.get_guild(Config.GUILD_ID)
            if guild:
                await self.sync_members(guild)
                await self.update_leaderboard()
            logger.info("Leaderboard kutipan dikemaskini (startup)")
        except Exception:
            logger.exception("Startup kutipan gagal")

        # background jobs (reminder/penalty/backup)
        try:
            if not self.daily_reminder.is_running():
                self.daily_reminder.start()
            if not self.daily_penalty.is_running():
                self.daily_penalty.start()
            # Backup kutipan kini manual sahaja melalui Admin Panel (elak spam channel backup).
        except Exception:
            logger.exception("Gagal start background jobs kutipan")

    # ---------- Access helpers ----------
    def is_bendahari_or_admin(self, member: discord.Member) -> bool:
        bendahari_id = getattr(Config, "BENDAHARI_ROLE_ID", 0)
        bendahari_role = member.guild.get_role(bendahari_id) if bendahari_id else None
        admin_role = member.guild.get_role(Config.ADMIN_ROLE_ID) if Config.ADMIN_ROLE_ID else None
        return (
            (bendahari_role and bendahari_role in member.roles) or
            (admin_role and admin_role in member.roles) or
            member.guild_permissions.administrator
        )

    def _db(self):
        db_cog = self.bot.get_cog('DatabaseCog')
        if not db_cog:
            raise RuntimeError("DatabaseCog belum loaded.")
        return db_cog.db

    async def _db_call(self, fn, *args, **kwargs):
        """Jalankan operasi pymongo (sync) dalam thread supaya event loop tak block."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def _get_leaderboard_role(self, guild: discord.Guild) -> discord.Role | None:
        """Role yang digunakan untuk leaderboard kutipan (ikut setting role leaderboard)."""
        role_id = int(getattr(Config, 'AHLI_DKATANA_ROLE_ID', 0) or 0)
        db_cog = self.bot.get_cog('DatabaseCog')
        if db_cog:
            try:
                settings = await db_cog.get_guild_settings(guild.id)
                rid = int(settings.get('leaderboard_role_id') or 0)
                if rid:
                    role_id = rid
            except Exception:
                pass
        return guild.get_role(role_id) if role_id else None

    async def open_admin_panel(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Panel ini hanya untuk server.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not self.is_bendahari_or_admin(interaction.user):
            await interaction.response.send_message("❌ Hanya admin/bendahari.", ephemeral=True)
            return

        view = KutipanAdminView(self, interaction.guild)
        await interaction.response.send_message(content=view.summary_text(), view=view, ephemeral=True)

    async def open_reminder_modal(self, interaction: discord.Interaction):
        await interaction.response.send_modal(KutipanReminderModal(self))

    async def open_amount_modal(self, interaction: discord.Interaction, kind: str):
        # kind: 'weekly' | 'penalty'
        key = 'KUTIPAN_WEEKLY_AMOUNT' if kind == 'weekly' else 'KUTIPAN_PENALTY_AMOUNT'
        current = int(getattr(Config, key, 0) or 0)
        await interaction.response.send_modal(KutipanAmountModal(self, kind=kind, current=current))

    def _get_current_week_idx(self) -> int:
        d = now_kl().day
        if d <= 7:
            return 0
        if d <= 14:
            return 1
        if d <= 21:
            return 2
        return 3

    async def send_kutipan_reminders(self, guild: discord.Guild, message: str) -> tuple[int, int]:
        """DM semua ahli yang belum bayar utk minggu semasa. Return (sent, failed)."""
        week_idx = self._get_current_week_idx()
        db = self._db()
        col = db['kutipan_members']

        cur_month = month_key()
        role = guild.get_role(Config.AHLI_DKATANA_ROLE_ID)
        member_ids = [m.id for m in role.members if not m.bot] if role else []
        q = {"month": cur_month}
        if member_ids:
            q["user_id"] = {"$in": member_ids}

        docs = await self._db_call(lambda: list(col.find(q)))

        # Build embed sekali sahaja (lebih laju & konsisten)
        embed = discord.Embed(
            title="PERINGATAN PEMBAYARAN DUIT MARKAS MINGGUAN",
            description=(str(message or "").strip() or "-"),
            color=getattr(Config, 'COLOR_WARNING', 0xFFA500),
        )
        embed.set_footer(text="Ꭰ'Ҡᴀᴛᴀɴᴀ ⚡ ・MessageReminder")
        sent = 0
        failed = 0
        for doc in docs:
            uid = int(doc.get('user_id', 0) or 0)
            if not uid:
                continue
            weeks = doc.get('weeks') or [0, 0, 0, 0]
            wv = _safe_int(weeks[week_idx], 0)
            if wv in (1, 2):
                continue
            try:
                user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                await user.send(embed=embed)
                sent += 1
                await asyncio.sleep(0.6)
            except Exception:
                failed += 1
        return sent, failed

    async def set_kutipan_amount(self, kind: str, amount: int) -> None:
        """Update jumlah kutipan/penalty di DB + .env + runtime Config."""
        amount = int(amount)
        key = 'KUTIPAN_WEEKLY_AMOUNT' if kind == 'weekly' else 'KUTIPAN_PENALTY_AMOUNT'

        db = self._db()
        db['kutipan_config'].update_one({'_id': 'amounts'}, {'$set': {key: amount, 'updated_at': now_kl()}}, upsert=True)
        setattr(Config, key, amount)

        env_path = os.path.join(os.getcwd(), '.env')
        try:
            _update_env_var(env_path, key, str(amount))
        except Exception:
            logger.exception('Gagal update .env untuk %s', key)

    # ---------- Data ops ----------
    async def sync_members(self, guild: discord.Guild):
        db = self._db()
        role = guild.get_role(Config.AHLI_DKATANA_ROLE_ID)
        if role is None:
            logger.warning("AHLI_DKATANA_ROLE_ID tak jumpa.")
            return

        cur_month = month_key()
        col = db.kutipan_members

        for m in role.members:
            doc = await self._db_call(col.find_one, {"user_id": m.id, "month": cur_month})
            # ensure jersey exists (permanent)
            db_cog = self.bot.get_cog('DatabaseCog')
            if db_cog:
                try:
                    await db_cog.get_or_assign_jersey_number(m.id, guild.id, m.name, m.display_name)
                except Exception:
                    pass

            if not doc:
                await self._db_call(col.insert_one, {
                    "user_id": m.id,
                    "guild_id": guild.id,
                    "month": cur_month,
                    "weeks": [0, 0, 0, 0],
                    "total_paid": 0,
                    "penalty": 0,
                    "updated_at": now_kl(),
                })
            else:
                # keep weeks if exists
                await self._db_call(
                    col.update_one,
                    {"_id": doc["_id"]},
                    {"$set": {"updated_at": now_kl()}}
                )

    async def set_payment_status(self, guild: discord.Guild, user_id: int, week_idx: int, status: str, actor: discord.Member) -> tuple[bool, str]:
        if week_idx not in (0, 1, 2, 3):
            return False, "Minggu tidak sah."
        if status not in ("paid", "unpaid", "cuti", "penalty", "clear"):
            return False, "Status tidak sah."

        db = self._db()
        col = db.kutipan_members
        cur_month = month_key()

        doc = await self._db_call(col.find_one, {"user_id": user_id, "month": cur_month})
        if not doc:
            # create if missing
            await self._db_call(col.insert_one, {
                "user_id": user_id,
                "guild_id": guild.id,
                "month": cur_month,
                "weeks": [0, 0, 0, 0],
                "total_paid": 0,
                "penalty": 0,
                "updated_at": now_kl(),
            })
            doc = await self._db_call(col.find_one, {"user_id": user_id, "month": cur_month})

        weeks = (doc.get("weeks") or [0, 0, 0, 0])[:4]
        while len(weeks) < 4:
            weeks.append(0)

        prev = int(weeks[week_idx] or 0)
        total_paid = int(doc.get("total_paid", 0) or 0)
        penalty_total = int(doc.get("penalty", 0) or 0)

        weekly_amount = int(getattr(Config, "KUTIPAN_WEEKLY_AMOUNT", 6000) or 0)
        penalty_amount = int(getattr(Config, "KUTIPAN_PENALTY_AMOUNT", 0) or 0)

        # Mapping:
        # 1=bayar, 0=kosong, -1=belum bayar, 2=cuti, -2=denda
        new_code = {
            "paid": 1,
            "unpaid": -1,
            "clear": 0,
            "cuti": 2,
            "penalty": -2,
        }[status]

        # Kemas kini total_paid (ikut status BAYAR sahaja)
        if prev == 1 and new_code != 1:
            total_paid = max(0, total_paid - weekly_amount)
        elif prev != 1 and new_code == 1:
            total_paid = total_paid + weekly_amount

        # Kemas kini penalty (ikut status DENDA sahaja)
        if prev == -2 and new_code != -2:
            penalty_total = max(0, penalty_total - penalty_amount)
        elif prev != -2 and new_code == -2:
            penalty_total = penalty_total + penalty_amount

        weeks[week_idx] = new_code

        member = guild.get_member(user_id)
        username = member.display_name if member else str(doc.get("username") or user_id)

        await self._db_call(
            col.update_one,
            {"_id": doc["_id"]},
            {"$set": {
                "weeks": weeks,
                "total_paid": int(total_paid),
                "penalty": int(penalty_total),
                "username": username,
                "updated_at": now_kl(),
                "updated_by": actor.id,
            }}
        )
        return True, f"Disimpan untuk <@{user_id}> Minggu {week_idx+1} = {emoji_week(weeks[week_idx])}"

    async def log_kutipan_update(
        self,
        guild: discord.Guild | None,
        actor: discord.abc.User,
        user_ids: list[int],
        week_idx: int,
        status: str,
    ) -> None:
        """Hantar log kemaskini kutipan (1 mesej ringkas)."""
        if not guild:
            return
        ch_id = getattr(Config, "KUTIPAN_LOG_CHANNEL_ID", 0)
        if not ch_id:
            return
        channel = guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            try:
                channel = await guild.fetch_channel(ch_id)
            except Exception:
                return
        if not isinstance(channel, discord.TextChannel):
            return

        users_preview = ", ".join([f"<@{uid}>" for uid in user_ids[:10]])
        more = f" (+{len(user_ids)-10} lagi)" if len(user_ids) > 10 else ""

        s_map = {
            "paid": "✅ Bayar",
            "unpaid": "⬜ Belum bayar",
            "cuti": "🟨 Cuti",
            "penalty": "⚠️ Denda",
            "clear": "⬜ Kosongkan",
        }
        status_txt = s_map.get(status, status)
        msg = (
            f"🧾 **Kutipan dikemaskini**\n"
            f"👮 Admin: {actor.mention}\n"
            f"📅 Masa: `{_now_str_kl()}`\n"
            f"🗓️ Minggu: **{week_idx+1}**\n"
            f"✅ Status: **{status_txt}**\n"
            f"👥 Ahli: {users_preview}{more}"
        )
        try:
            await channel.send(msg)
        except Exception:
            logger.exception("Gagal hantar log kutipan")

    # ---------- Leaderboard & Stats ----------
    async def _monthly_target(self, guild: discord.Guild) -> tuple[int, int, float]:
        role = await self._get_leaderboard_role(guild)
        member_ids = [m.id for m in role.members if not m.bot] if role else []
        count = len(member_ids)

        weekly_amount = int(getattr(Config, "KUTIPAN_WEEKLY_AMOUNT", 0) or 0)
        target = count * 4 * weekly_amount

        db = self._db()
        col = db.kutipan_members
        cur_month = month_key()
        collected = 0

        q = {"month": cur_month}
        if member_ids:
            q["user_id"] = {"$in": member_ids}
        for doc in col.find(q):
            collected += int(doc.get("total_paid", 0) or 0)
        pct = (collected / target * 100.0) if target > 0 else 0.0
        return target, collected, pct

    async def _weekly_totals(self, guild: discord.Guild) -> tuple[list[int], list[int]]:
        db = self._db()
        col = db.kutipan_members
        cur_month = month_key()

        weekly_amount = int(getattr(Config, "KUTIPAN_WEEKLY_AMOUNT", 6000))
        penalty_amount = int(getattr(Config, "KUTIPAN_PENALTY_AMOUNT", 12000))

        paid_counts = [0, 0, 0, 0]
        unpaid_counts = [0, 0, 0, 0]

        role = await self._get_leaderboard_role(guild)
        member_ids = [m.id for m in role.members if not m.bot] if role else []
        q = {"month": cur_month}
        if member_ids:
            q["user_id"] = {"$in": member_ids}

        for doc in col.find(q):
            w = doc.get("weeks", [0, 0, 0, 0])
            for i in range(4):
                if w[i] == 1:
                    paid_counts[i] += 1
                elif w[i] == -2:
                    unpaid_counts[i] += 1

        kutipan = [c * weekly_amount for c in paid_counts]
        penalti = [c * penalty_amount for c in unpaid_counts]
        return kutipan, penalti

    async def build_leaderboard_embeds(self, guild: discord.Guild) -> list[discord.Embed]:
        """Bina embed leaderboard duit kutipan mingguan (bulan semasa).

        Permintaan:
        - Nama jadi mention @user (ikut role yang ditetapkan)
        - Kotak kosong guna ⬜
        - Petunjuk status ikut format baharu
        """

        role = await self._get_leaderboard_role(guild)
        members = [m for m in (role.members if role else []) if not m.bot]
        member_by_id = {m.id: m for m in members}
        member_ids = list(member_by_id.keys())

        db = self._db()
        col = db.kutipan_members
        cur_month = month_key()

        # Ambil dokumen kutipan bulan semasa (untuk ahli role sahaja)
        docs_by_id: dict[int, dict] = {}
        if member_ids:
            try:
                for doc in col.find({"month": cur_month, "user_id": {"$in": member_ids}}):
                    uid = int(doc.get("user_id") or 0)
                    if uid:
                        docs_by_id[uid] = doc
            except Exception:
                docs_by_id = {}

        # Ambil nombor jersi secara bulk (lebih laju berbanding fetch satu-satu)
        jersey_by_id: dict[int, int] = {}
        db_cog = self.bot.get_cog("DatabaseCog")
        if db_cog and member_ids:
            try:
                for udoc in db_cog.db.users.find(
                    {"user_id": {"$in": member_ids}},
                    {"user_id": 1, "jersey_number": 1}
                ):
                    uid = int(udoc.get("user_id") or 0)
                    j = udoc.get("jersey_number")
                    if uid and j:
                        jersey_by_id[uid] = int(j)
            except Exception:
                jersey_by_id = {}

        # rows: (sort_key, jersey_txt, name_txt, weeks_txt, total_paid, penalty)
        rows: list[tuple[int, str, str, str, int, int]] = []
        for uid in member_ids:
            doc = docs_by_id.get(uid) or {}
            weeks = (doc.get("weeks") or [0, 0, 0, 0])[:4]
            while len(weeks) < 4:
                weeks.append(0)
            weeks_txt = "".join(emoji_week(int(x or 0)) for x in weeks)

            total_paid = int(doc.get("total_paid", 0) or 0)
            penalty = int(doc.get("penalty", 0) or 0)

            jersey = jersey_by_id.get(uid, 0)
            jersey_txt = f"#{jersey:02d}" if jersey else "#--"
            if uid in member_by_id:
                disp = sanitize_name_for_table(member_by_id[uid].display_name)
            else:
                disp = f"user-{uid}"
            # Gaya "@user" untuk nampak macam mention, tapi kekal kemas dalam codeblock.
            name_txt = "@" + disp

            sort_key = jersey if jersey else 9999
            rows.append((sort_key, jersey_txt, name_txt, weeks_txt, total_paid, penalty))

        rows.sort(key=lambda r: r[0])

        # Header info (lebih laju - guna data yang dah ada)
        weekly_amount = int(getattr(Config, 'KUTIPAN_WEEKLY_AMOUNT', 0) or 0)
        target = len(member_ids) * 4 * weekly_amount
        collected = sum(int((docs_by_id.get(uid) or {}).get('total_paid', 0) or 0) for uid in member_ids)
        pct = (collected / target * 100.0) if target > 0 else 0.0

        embeds: list[discord.Embed] = []
        per_page = 25
        total_pages = max(1, (len(rows) + per_page - 1) // per_page)

        # Global width supaya alignment konsisten dari atas ke bawah (dan antara page)
        max_total_all = max([int(r[4] or 0) for r in rows], default=0)
        max_pen_all = max([int(r[5] or 0) for r in rows], default=0)
        w_total = max(5, len(str(max_total_all)))
        w_pen = max(3, len(str(max_pen_all)))
        w_name = 18

        for page_idx in range(total_pages):
            start = page_idx * per_page
            end = start + per_page
            chunk = rows[start:end]

            title = "💰🏆 LEADERBOARD KUTIPAN DKATANA (BULAN INI) 🏆💰"
            if total_pages > 1:
                title += f" — {page_idx + 1}/{total_pages}"

            penalty_amount = int(getattr(Config, 'KUTIPAN_PENALTY_AMOUNT', 0) or 0)
            header = (
                f"📅 **Bulan:** `{cur_month}` ({Config.TIMEZONE})\n"
                f"🎯 **Target Bulanan:** `{target:,}`\n"
                f"💰 **Kutipan Semasa:** `{collected:,}` (**{pct:.1f}%**)\n"
                f"🧾 **Kutipan/Minggu:** `{weekly_amount:,}`  |  ⚠️ **Penalty:** `{penalty_amount:,}`\n\n"
                "```\n"
                f"{'JRSY':<4} │ {'NAMA':<{w_name}} │ M1M2M3M4 │ {'TOTAL':>{w_total}} │ {'PEN':>{w_pen}}\n"
            )

            lines: list[str] = []
            for _, jersey_txt, name_txt, weeks_txt, total_paid, penalty in chunk:
                name_cell = _truncate_with_ellipsis(name_txt, w_name).ljust(w_name)
                # Full monospaced table row (align sebaris)
                lines.append(
                    f"{jersey_txt:<4} │ {name_cell} │ {weeks_txt} │ {str(total_paid).rjust(w_total)} │ {str(penalty).rjust(w_pen)}"
                )

            if not lines:
                lines = ["📭 Tiada data."]

            desc = header + "\n" + "\n".join(lines) + "\n```"
            if len(desc) > 4096:
                # trim baris kalau terlebih
                trimmed = []
                cur_len = len(header) + 1
                for ln in lines:
                    if cur_len + len(ln) + 1 > 4050:
                        trimmed.append("…")
                        break
                    trimmed.append(ln)
                    cur_len += len(ln) + 1
                desc = header + "\n" + "\n".join(trimmed) + "\n```"

            emb = discord.Embed(title=title, description=desc, color=0x5865F2)
            emb.set_footer(text=f"🕒 Dikemaskini: {now_kl().strftime('%d/%m/%Y %H:%M')} (KL)")
            embeds.append(emb)

        legend = discord.Embed(
            title="📖 PETUNJUK",
            description=(
                "✅ = *Bayar*\n"
                "⬜ = *Belum Bayar*\n"
                "🟨 = *Cuti*\n"
                "⚠️ = *Denda*\n"
                "🎽 = *nomnor Jersey*\n\n"
                "**M1->M4** = *minggu 1 -> minggu 4*"
            ),
            color=0x2B2D31,
        )
        legend.set_footer(text="🛠️ Status boleh dikemaskini melalui Admin Panel")
        embeds.append(legend)
        return embeds

    async def update_leaderboard(self):
        """Update/refresh mesej leaderboard kutipan di channel yang ditetapkan."""
        # Elak duplicate / race condition bila banyak update serentak
        async with self._lb_lock:
            channel_id = int(getattr(Config, "KUTIPAN_LEADERBOARD_CHANNEL_ID", 0))
            if channel_id == 0:
                return
            channel = self.bot.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                return
            guild = channel.guild

            db = self._db()
            settings = db.settings.find_one({"guild_id": guild.id}) or {}

            # Backward compatibility: old single message id -> list
            msg_ids = settings.get("kutipan_leaderboard_message_ids")
            if not isinstance(msg_ids, list):
                old_one = settings.get("kutipan_leaderboard_message_id")
                msg_ids = [old_one] if old_one else []

            embeds = await self.build_leaderboard_embeds(guild)
            allowed = discord.AllowedMentions(users=False, roles=False, everyone=False)

            new_ids: list[int] = []

            # Edit existing messages or send new ones
            for i, emb in enumerate(embeds):
                msg = None
                if i < len(msg_ids) and msg_ids[i]:
                    try:
                        msg = await channel.fetch_message(int(msg_ids[i]))
                        await msg.edit(content=None, embed=emb, allowed_mentions=allowed)
                    except Exception:
                        msg = None
                if msg is None:
                    msg = await channel.send(embed=emb, allowed_mentions=allowed)
                new_ids.append(msg.id)

            # Delete any extra old messages
            for j in range(len(embeds), len(msg_ids)):
                try:
                    if msg_ids[j]:
                        m = await channel.fetch_message(int(msg_ids[j]))
                        await m.delete()
                except Exception:
                    pass

            db.settings.update_one(
                {"guild_id": guild.id},
                {"$set": {
                    "kutipan_leaderboard_message_ids": new_ids,
                    # keep legacy field in sync (first message)
                    "kutipan_leaderboard_message_id": new_ids[0] if new_ids else None
                }},
                upsert=True
            )

    async def update_stats(self):
        channel_id = int(getattr(Config, "KUTIPAN_STATS_CHANNEL_ID", 0))
        if channel_id == 0:
            return
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        guild = channel.guild
    
        db = self._db()
        settings = db.settings.find_one({"guild_id": guild.id}) or {}
        msg_id = settings.get("kutipan_stats_message_id")
    
        target, collected, pct = await self._monthly_target(guild)
        kutipan, penalti = await self._weekly_totals(guild)
    
        emb = discord.Embed(
            title="📈 STATISTIK KUTIPAN (BULAN INI)",
            color=0x00D4AA
        )
        emb.add_field(name="🗓 Bulan", value=f"**{month_key()}** (Asia/Kuala_Lumpur)", inline=False)
        emb.add_field(name="🎯 Target Bulanan", value=f"**{target}**", inline=True)
        emb.add_field(name="💰 Kutipan Semasa", value=f"**{collected}** ({pct:.1f}%)", inline=True)
        emb.add_field(name="🧾 Kutipan Mingguan", value=f"`{kutipan}`", inline=False)
        emb.add_field(name="⚠️ Penalti Mingguan", value=f"`{penalti}`", inline=False)
        emb.set_footer(text=f"Updated: {now_kl().strftime('%Y-%m-%d %H:%M:%S')} (KL)")
    
        if msg_id:
            try:
                msg = await channel.fetch_message(int(msg_id))
                await msg.edit(content=None, embed=emb, attachments=[])
            except Exception:
                msg = await channel.send(embed=emb)
                db.settings.update_one({"guild_id": guild.id}, {"$set": {"kutipan_stats_message_id": msg.id}}, upsert=True)
        else:
            msg = await channel.send(embed=emb)
            db.settings.update_one({"guild_id": guild.id}, {"$set": {"kutipan_stats_message_id": msg.id}}, upsert=True)

    
    async def send_monthly_stats(self, interaction: discord.Interaction):
        """Hantar statistik kutipan bulanan (ephemeral, tanpa graf)."""
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ Guild tidak sah.", ephemeral=True)
            return

        target, collected, pct = await self._monthly_target(guild)
        kutipan, penalti = await self._weekly_totals(guild)

        emb = discord.Embed(
            title="📈 Statistik Kutipan (Bulan Ini)",
            color=0x00D4AA
        )
        emb.add_field(name="🗓 Bulan", value=f"**{month_key()}** (Asia/Kuala_Lumpur)", inline=False)
        emb.add_field(name="🎯 Target Bulanan", value=f"**{target}**", inline=True)
        emb.add_field(name="💰 Kutipan Semasa", value=f"**{collected}** ({pct:.1f}%)", inline=True)
        emb.add_field(name="🧾 Kutipan Mingguan", value=f"`{kutipan}`", inline=False)
        emb.add_field(name="⚠️ Penalti Mingguan", value=f"`{penalti}`", inline=False)
        emb.set_footer(text=f"Updated: {now_kl().strftime('%Y-%m-%d %H:%M:%S')} (KL)")

        await interaction.followup.send(embed=emb, ephemeral=True)

    async def send_weekly_stats(self, interaction: discord.Interaction):
        """Hantar statistik kutipan minggu semasa (ephemeral, tanpa graf)."""
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ Guild tidak sah.", ephemeral=True)
            return

        idx = week_index(now_kl())
        cur_month = month_key()

        db = self._db()
        col = db.kutipan_members

        role = guild.get_role(Config.AHLI_DKATANA_ROLE_ID)
        members = [m for m in (role.members if role else []) if not m.bot]
        total_members = len(members)

        paid = 0
        belum_bayar = 0
        cuti = 0
        denda = 0
        penalty_total = 0

        for m in members:
            doc = col.find_one({"user_id": m.id, "guild_id": guild.id, "month": cur_month}) or {}
            weeks = doc.get("weeks", [0, 0, 0, 0])
            st = weeks[idx] if idx < len(weeks) else 0
            if st == 1:
                paid += 1
            elif st == 2:
                cuti += 1
            elif st == -2:
                denda += 1
            else:
                belum_bayar += 1
            penalty_total += int(doc.get("penalty", 0) or 0)

        weekly_amount = int(getattr(Config, "KUTIPAN_WEEKLY_AMOUNT", 10))
        collected_this_week = paid * weekly_amount
        # Target minggu ini: exclude CUTI (jika digunakan sebagai exempt)
        due_members = max(0, total_members - cuti)
        target_this_week = due_members * weekly_amount
        pct = (collected_this_week / target_this_week * 100.0) if target_this_week else 0.0

        emb = discord.Embed(
            title="📅 Statistik Kutipan (Minggu Ini)",
            color=0x00D4AA
        )
        emb.add_field(name="🗓 Bulan", value=f"**{cur_month}** (Asia/Kuala_Lumpur)", inline=False)
        emb.add_field(name="🧩 Minggu", value=f"**M{idx+1}**", inline=True)
        emb.add_field(name="🎯 Target Mingguan", value=f"**{target_this_week}**", inline=True)
        emb.add_field(name="💰 Kutipan Minggu Ini", value=f"**{collected_this_week}** ({pct:.1f}%)", inline=False)
        emb.add_field(name="✅ Bayar", value=str(paid), inline=True)
        emb.add_field(name="⬜ Belum Bayar", value=str(belum_bayar), inline=True)
        emb.add_field(name="🟨 Cuti", value=str(cuti), inline=True)
        emb.add_field(name="⚠️ Denda", value=str(denda), inline=True)
        emb.add_field(name="⚠️ Jumlah Penalti (Bulan Ini)", value=f"**{penalty_total}**", inline=False)
        emb.set_footer(text=f"Updated: {now_kl().strftime('%Y-%m-%d %H:%M:%S')} (KL)")

        await interaction.followup.send(embed=emb, ephemeral=True)

    # ---------- Backup ----------
    async def backup_now(self, post_to_channel: bool = True) -> str:
        db = self._db()
        col = db.kutipan_members
        cur_month = month_key()
        data = list(col.find({"month": cur_month}, {"_id": 0}))

        os.makedirs("backups", exist_ok=True)
        stamp = now_kl().strftime("%Y-%m-%d_%H%M%S")
        path = f"backups/kutipan_backup_{cur_month}_{stamp}.json"

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        if post_to_channel:
            channel_id = int(getattr(Config, "KUTIPAN_BACKUP_CHANNEL_ID", 0))
            if channel_id:
                channel = self.bot.get_channel(channel_id)
                if isinstance(channel, discord.TextChannel):
                    try:
                        await channel.send("🧷 Backup kutipan:", file=discord.File(path))
                    except Exception:
                        pass

        return path

    # ---------- Scheduled jobs ----------
    @tasks.loop(hours=24)
    async def daily_backup(self):
        """Disabled auto backup; kekal wujud untuk compatibility sahaja."""
        await self.bot.wait_until_ready()
        return

    @daily_backup.before_loop
    async def before_daily_backup(self):
        await self.bot.wait_until_ready()
        # Align ke 02:00 Asia/Kuala_Lumpur
        dt = now_kl()
        target = dt.replace(hour=2, minute=0, second=0, microsecond=0)
        if target <= dt:
            target = target + timedelta(days=1)
        await discord.utils.sleep_until(target)
    @tasks.loop(hours=24)
    async def daily_reminder(self):
        """Run daily; if today is reminder day for any week, DM all unpaid."""
        await self.bot.wait_until_ready()
        try:
            guild = self.bot.get_guild(Config.GUILD_ID)
            if not guild:
                return

            dt = now_kl()
            # reminder day = end_day - 3
            for idx in range(4):
                end_day = week_end_day_for_index(dt, idx)
                reminder_day = max(1, end_day - 3)
                if dt.day == reminder_day:
                    await self._send_week_reminders(guild, idx)
                    break
        except Exception as e:
            logger.warning(f"Reminder loop error: {e}")

    async def _send_week_reminders(self, guild: discord.Guild, wk: int):
        db = self._db()
        col = db.kutipan_members
        cur_month = month_key()

        role = guild.get_role(Config.AHLI_DKATANA_ROLE_ID)
        member_ids = [m.id for m in role.members if not m.bot] if role else []
        q = {"month": cur_month}
        if member_ids:
            q["user_id"] = {"$in": member_ids}

        for doc in col.find(q):
            w = (doc.get("weeks") or [0, 0, 0, 0])[:4]
            while len(w) < 4:
                w.append(0)

            st = int(w[wk] or 0)
            # skip jika bayar (✅) atau cuti (🟨)
            if st in (1, 2):
                continue

            user = self.bot.get_user(int(doc["user_id"]))
            if user:
                try:
                    embed = discord.Embed(
                        title="⚠️ PERINGATAN PEMBAYARAN DUIT MARKAS MINGGUAN",
                        description=(
                            "Assalamualaikum anak DKATANA,\n\n"
                            f"Anda masih **belum bayar** kutipan untuk **Minggu {wk+1}**.\n"
                            "Sila buat pembayaran kepada bendahari sebelum tamat minggu ini.\n\n"
                            "Terima kasih atas kerjasama anda."
                        ),
                        color=0xF1C40F,
                    )
                    embed.add_field(name="📅 Minggu", value=f"Minggu {wk+1}", inline=True)
                    embed.add_field(name="🕒 Zon Masa", value="Asia/Kuala_Lumpur", inline=True)
                    embed.set_footer(text="Ꭰ'Ҡᴀᴛᴀɴᴀ ⚡ ・ MessageReminder")
                    await user.send(embed=embed)
                except Exception:
                    pass

    @tasks.loop(hours=24)
    async def daily_penalty(self):
        """Run daily near end; if today is end-day of any week, enforce penalty."""
        await self.bot.wait_until_ready()
        try:
            guild = self.bot.get_guild(Config.GUILD_ID)
            if not guild:
                return

            dt = now_kl()
            for idx in range(4):
                if dt.day == week_end_day_for_index(dt, idx):
                    await self._apply_week_penalty(guild, idx)
                    break
        except Exception as e:
            logger.warning(f"Penalty loop error: {e}")

    async def _apply_week_penalty(self, guild: discord.Guild, wk: int):
        db = self._db()
        col = db.kutipan_members
        cur_month = month_key()

        penalty_amount = int(getattr(Config, "KUTIPAN_PENALTY_AMOUNT", 0) or 0)

        role = guild.get_role(Config.AHLI_DKATANA_ROLE_ID)
        member_ids = [m.id for m in role.members if not m.bot] if role else []
        q = {"month": cur_month}
        if member_ids:
            q["user_id"] = {"$in": member_ids}

        # Apply denda untuk ahli yang belum bayar (⬜) sahaja.
        # Tak apply jika status = bayar (✅) / cuti (🟨) / sudah denda (⚠️).
        for doc in col.find(q):
            weeks = (doc.get("weeks") or [0, 0, 0, 0])[:4]
            while len(weeks) < 4:
                weeks.append(0)

            st = int(weeks[wk] or 0)
            if st in (1, 2, -2):
                continue

            weeks[wk] = -2
            new_pen = int(doc.get("penalty", 0) or 0) + penalty_amount

            col.update_one(
                {"user_id": doc["user_id"], "month": cur_month},
                {"$set": {"weeks": weeks, "penalty": int(new_pen), "updated_at": now_kl()}}
            )

            user = self.bot.get_user(int(doc["user_id"]))
            if user and penalty_amount > 0:
                try:
                    await user.send(
                        f"❌ Kutipan Minggu {wk+1} Tamat\n"
                        f"Anda tidak membuat bayaran dalam tempoh minggu ini.\n"
                        f"⚠️ Penalti dikenakan: **{penalty_amount}**.\n"
                        f"Sila hubungi bendahari jika ada isu."
                    )
                except Exception:
                    pass

        await self.update_leaderboard()


async def setup(bot: commands.Bot):
    await bot.add_cog(KutipanCog(bot))
