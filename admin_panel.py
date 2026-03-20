"""
Admin Panel Cog V3 - With Daily Report Date Selection & Historical View
"""
import discord
from discord.ext import commands
from discord.ui import View, Button, Select, Modal, TextInput, UserSelect
from discord import SelectOption
from datetime import datetime, timedelta
import pytz
import asyncio
import logging
from config import Config

logger = logging.getLogger('DkatanaBot')


class AdminPanelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # anti-duplicate bila admin klik dropdown bertubi-tubi
        self._select_cooldown: dict[tuple[int, str], datetime] = {}

        # 1 admin = 1 "hide message" yang boleh di-edit (elak spam banyak ephemeral message)
        # Key: admin user_id, Value: WebhookMessage/Message object
        self._admin_ui_msg: dict[int, object] = {}

        # Debounce edit untuk Admin Panel message (kurangkan 429 & bagi boleh pilih option sama berkali-kali)
        self._panel_reset_tasks: dict[int, asyncio.Task] = {}
        self._panel_reset_lock = asyncio.Lock()
        self._panel_last_reset_at: dict[int, datetime] = {}

    async def request_panel_reset(self, message: discord.Message | None):
        """Reset view panel secara debounce supaya dropdown boleh dipilih semula tanpa perlu pilih option lain."""
        if not message:
            return
        now = self.get_time()
        last = self._panel_last_reset_at.get(message.id)
        # Kurangkan spam PATCH edit message yang boleh trigger 429
        # (jangan terlalu lama supaya dropdown option sama masih boleh dipilih semula dengan cepat).
        if last and (now - last).total_seconds() < 0.6:
            return

        async with self._panel_reset_lock:
            old = self._panel_reset_tasks.get(message.id)
            if old and not old.done():
                old.cancel()

            async def _runner():
                try:
                    await asyncio.sleep(0.35)
                    # NOTE: AdminView ialah inner class, jadi kena akses melalui instance/class
                    await message.edit(view=self.AdminView(self))
                    self._panel_last_reset_at[message.id] = self.get_time()
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    logger.warning(f"panel_reset warning: {e}")

            self._panel_reset_tasks[message.id] = asyncio.create_task(_runner())

    async def ui_reply(self, interaction: discord.Interaction, *, content: str | None = None,
                       embed: discord.Embed | None = None, view: discord.ui.View | None = None):
        """Hantar/kemaskini 1 mesej ephemeral yang sama untuk admin ini.

        - Kali pertama: bot akan send hide message (ephemeral) dan simpan reference.
        - Kali seterusnya: bot akan edit mesej yang sama (tak jadi banyak hide message).

        **Nota:** Untuk guna `followup.send`, interaction mesti sudah `defer` dahulu.
        """
        uid = int(interaction.user.id)
        msg = self._admin_ui_msg.get(uid)

        # cuba edit mesej sedia ada
        if msg is not None:
            try:
                await msg.edit(content=content, embed=embed, view=view)
                return msg
            except Exception:
                self._admin_ui_msg.pop(uid, None)

        # jika tiada mesej, create baru
        try:
            new_msg = await interaction.followup.send(
                content=content,
                embed=embed,
                view=view,
                ephemeral=True,
                wait=True,
            )
        except Exception:
            # fallback: kalau belum respond, guna response.send_message
            if not interaction.response.is_done():
                await interaction.response.send_message(content=content, embed=embed, view=view, ephemeral=True)
                try:
                    new_msg = await interaction.original_response()
                except Exception:
                    new_msg = None
            else:
                new_msg = None

        if new_msg is not None:
            self._admin_ui_msg[uid] = new_msg
        return new_msg

    def _is_duplicate_select(self, user_id: int, action: str, window_sec: float = 0.35) -> bool:
        """Return True kalau action sama dipilih terlalu rapat (elak duplicate)."""
        try:
            now = self.get_time()
        except Exception:
            now = datetime.now(pytz.timezone(Config.TIMEZONE))

        key = (int(user_id), str(action))
        last = self._select_cooldown.get(key)
        if last and (now - last).total_seconds() < window_sec:
            return True
        self._select_cooldown[key] = now
        return False

    def get_time(self):
        return datetime.now(pytz.timezone(Config.TIMEZONE))

    def is_admin(self, user):
        admin_role = user.guild.get_role(Config.ADMIN_ROLE_ID)
        return admin_role in user.roles or user.guild_permissions.administrator

    def _parse_hhmm(self, hhmm: str, fallback=(0, 0)):
        """Parse 'HH:MM' -> (h,m)."""
        try:
            h, m = hhmm.split(":")
            h = int(h)
            m = int(m)
            if 0 <= h <= 23 and 0 <= m <= 59:
                return h, m
        except Exception:
            pass
        return fallback

    def _format_malay_day(self, dt: datetime):
        days = ['Isnin', 'Selasa', 'Rabu', 'Khamis', 'Jumaat', 'Sabtu', 'Ahad']
        return days[dt.weekday()]

    def _get_past_week_dates(self):
        """Get dates for the past week (today + 6 previous days)"""
        dates = []
        today = self.get_time()
        for i in range(7):  # Today + 6 days back
            date = today - timedelta(days=i)
            dates.append({
                'date_obj': date,
                'date_str': date.strftime("%Y-%m-%d"),
                'display': date.strftime("%d/%m/%Y"),
                'day_name': self._format_malay_day(date),
                'is_today': i == 0
            })
        return dates

    # ========== WARNING (MULTI USER DM) ==========
    async def send_warning_bulk(self, interaction: discord.Interaction, user_ids: list[int], message: str) -> tuple[int, int]:
        """Hantar amaran DM kepada ramai user (hanya role ahli DKATANA)."""
        guild = interaction.guild
        if not guild:
            return 0, len(user_ids or [])

        role = guild.get_role(Config.AHLI_DKATANA_ROLE_ID)
        if not role:
            # fallback: still allow, but will send to provided IDs
            pass

        ok = 0
        fail = 0
        message = (message or '').strip()
        if not message:
            return 0, len(user_ids or [])

        for uid in (user_ids or []):
            try:
                uid = int(uid)
            except Exception:
                fail += 1
                continue

            member = guild.get_member(uid)
            if role and member and role not in member.roles:
                fail += 1
                continue

            user = member or self.bot.get_user(uid)
            if user is None:
                try:
                    user = await self.bot.fetch_user(uid)
                except Exception:
                    user = None

            if user is None:
                fail += 1
                continue

            emb = discord.Embed(
                title="⚠️ WARNING MESSAGE DKATANA",
                description=f"```{message}```",
                color=getattr(Config, 'COLOR_WARNING', 0xFFA500),
            )
            emb.add_field(name="👤 Dihantar oleh", value=interaction.user.mention, inline=False)
            try:
                emb.add_field(name="🕒 Tarikh", value=self.get_time().strftime('%d/%m/%Y %H:%M') + ' (KL)', inline=False)
            except Exception:
                pass
            if guild.icon:
                try:
                    emb.set_thumbnail(url=guild.icon.url)
                except Exception:
                    pass

            try:
                await user.send(embed=emb)
                ok += 1
            except Exception:
                fail += 1

        # Log
        log = self.bot.get_cog('LoggerCog')
        if log:
            try:
                await log.log_admin_action(
                    interaction.user,
                    "Hantar Amaran (Bulk)",
                    f"Kepada: {len(user_ids or [])} user | Berjaya: {ok} | Gagal: {fail}"
                )
            except Exception:
                pass

        return ok, fail

    # ========== AUDIT DUIT (NOTE DUIT KELUAR) ==========
    def _audit_week_of_month(self, dt: datetime) -> int:
        """Kira minggu audit ikut format DKATANA (M1..M4).

        Minggu 1: 1-7
        Minggu 2: 8-14
        Minggu 3: 15-21
        Minggu 4: 22-akhir bulan
        """
        d = int(dt.day)
        if d <= 7:
            return 1
        if d <= 14:
            return 2
        if d <= 21:
            return 3
        return 4

    def _audit_week_key(self, dt: datetime, week_no: int | None = None) -> tuple[str, int]:
        """Return (key, week_no) untuk audit duit."""
        week_no = int(week_no or self._audit_week_of_month(dt))
        # clamp 1..4
        week_no = max(1, min(4, week_no))
        key = f"{dt.strftime('%Y-%m')}-M{week_no}"
        return key, week_no

    async def open_audit_duit_day_selector(self, interaction: discord.Interaction):
        """Paparkan dropdown pilihan minggu (1-4) + hari (Isnin-Ahad) untuk Audit Duit."""
        dt = self.get_time()
        _, auto_week_no = self._audit_week_key(dt)

        emb = discord.Embed(
            title="🧾 Message Audit Duit",
            description=(
                "Pilih **MINGGU** dan **HARI** untuk catatan audit duit keluar.\n\n"
                f"📅 Bulan: **{dt.strftime('%Y-%m')}** ({Config.TIMEZONE})\n"
                f"🗓️ Minggu (Auto): **MINGGU {auto_week_no}** (format M1–M4)\n\n"
                "📌 *Format minggu DKATANA:*\n"
                "M1 = 1–7, M2 = 8–14, M3 = 15–21, M4 = 22–akhir bulan."
            ),
            color=Config.COLOR_INFO if hasattr(Config, 'COLOR_INFO') else 0x5865F2,
        )
        view = AuditDuitSelectorView(self)
        await self.ui_reply(interaction, embed=emb, view=view)

    async def submit_audit_duit(self, interaction: discord.Interaction, day_upper: str, detail_text: str, week_no: int | None = None):
        """Hantar / edit mesej Audit Duit dalam channel log audit duit.

        - Message akan dihantar sebagai **embed** (cantik/tersusun)
        - Data disimpan dalam settings supaya boleh sambung/kemaskini hari seterusnya.
        """
        guild = interaction.guild
        if not guild:
            await interaction.followup.send("❌ Guild tidak sah.", ephemeral=True)
            return

        # Channel log audit duit
        ch_id = int(getattr(Config, 'AUDIT_DUIT_LOG_CHANNEL_ID', 0) or 0)
        if not ch_id:
            await interaction.followup.send(
                "❌ `AUDIT_DUIT_LOG_CHANNEL_ID` belum diset dalam .env / config.",
                ephemeral=True,
            )
            return

        channel = guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            try:
                channel = await guild.fetch_channel(ch_id)
            except Exception:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("❌ Channel log audit duit tidak dijumpai.", ephemeral=True)
            return

        # Settings (simpan message_id untuk setiap minggu)
        db_cog = self.bot.get_cog('DatabaseCog')
        if not db_cog:
            await interaction.followup.send("❌ DatabaseCog tidak dimuatkan.", ephemeral=True)
            return

        dt = self.get_time()
        week_key, week_no = self._audit_week_key(dt, week_no)

        settings = await db_cog.get_guild_settings(guild.id)

        # message id per minggu
        audit_map = settings.get('audit_duit_message_ids') or {}
        if not isinstance(audit_map, dict):
            audit_map = {}
        msg_id = audit_map.get(week_key)
        # backward compatibility: versi lama guna suffix -W
        old_week_key = f"{dt.strftime('%Y-%m')}-W{week_no}"
        if not msg_id and old_week_key in audit_map:
            msg_id = audit_map.get(old_week_key)

        # simpan data (day -> list[str]) supaya mudah rebuild embed tanpa parse
        audit_data = settings.get('audit_duit_data') or {}
        if not isinstance(audit_data, dict):
            audit_data = {}
        week_data = audit_data.get(week_key) or {}
        if (not week_data) and old_week_key in audit_data:
            # migrate in-memory (save later)
            week_data = audit_data.get(old_week_key) or {}
        if not isinstance(week_data, dict):
            week_data = {}

        # Migration fallback: kalau mapping data belum ada tapi message lama (format text) masih wujud,
        # cuba parse supaya data lama tak hilang bila tukar ke embed.
        if not week_data and msg_id:
            try:
                old_msg = await channel.fetch_message(int(msg_id))
                if old_msg and (old_msg.content or ''):
                    parsed: dict[str, list[str]] = {}
                    cur_day: str | None = None
                    for ln in (old_msg.content or '').splitlines():
                        up = ln.strip().upper()
                        if up.startswith('HARI:'):
                            cur_day = ln.split(':', 1)[1].strip().upper()
                            parsed[cur_day] = []
                            continue
                        if not cur_day:
                            continue
                        s = ln.strip()
                        if not s:
                            continue
                        if s.startswith('-'):
                            s = s.lstrip('-').strip()
                        parsed[cur_day].append(s)

                    for d, items in parsed.items():
                        if not items:
                            continue
                        numbered: list[str] = []
                        for i, it in enumerate(items):
                            it = str(it).strip()
                            if not it:
                                continue
                            # kalau dah ada numbering (contoh 1.) kekalkan
                            if len(it) >= 2 and it[0].isdigit() and it[1] == '.':
                                numbered.append(it)
                            else:
                                numbered.append(f"{i+1}. {it}")
                        if numbered:
                            week_data[d] = numbered

                    if week_data:
                        audit_data[week_key] = week_data
            except Exception:
                pass

        # Format detail lines
        day_upper = (day_upper or '').strip().upper()
        valid_days = ["ISNIN", "SELASA", "RABU", "KHAMIS", "JUMAAT", "SABTU", "AHAD"]
        if day_upper not in valid_days:
            # fallback: cuba normalize input
            day_upper = day_upper.replace('JUMAT', 'JUMAAT').replace('JUMMAT', 'JUMAAT')
            if day_upper not in valid_days:
                day_upper = valid_days[dt.weekday()]

        raw_lines = [ln.strip() for ln in (detail_text or '').splitlines() if ln.strip()]
        if not raw_lines:
            raw_lines = ["-"]

        # auto numbering
        detail_lines = [f"{i+1}. {ln}" for i, ln in enumerate(raw_lines)]
        week_data[day_upper] = detail_lines
        audit_data[week_key] = week_data

        # Build embed
        month_str = dt.strftime('%Y-%m')
        emb = discord.Embed(
            title=f"🧾 AUDIT DUIT MINGGUAN (MINGGU {week_no})",
            description=(
                f"📅 Bulan: **{month_str}** ({Config.TIMEZONE})\n"
                f"🗓️ Minggu: **{week_no}/4**\n"
                "💸 Catatan ini untuk **duit keluar** (audit)."
            ),
            color=Config.COLOR_INFO if hasattr(Config, 'COLOR_INFO') else 0x2ECC71,
        )

        for d in valid_days:
            if d not in week_data:
                continue
            lines = week_data.get(d) or []
            if not isinstance(lines, list):
                lines = [str(lines)]
            value = "\n".join([str(x) for x in lines if str(x).strip()])
            if not value:
                value = "-"
            if len(value) > 1024:
                value = value[:1010] + "…"
            emb.add_field(name=f"📌 Hari: {d}", value=value, inline=False)

        emb.set_footer(text=f"Dikemaskini: {dt.strftime('%d/%m/%Y %H:%M')} (KL) | Admin: {interaction.user.display_name}")

        # Send or edit
        msg_obj: discord.Message | None = None
        if msg_id:
            try:
                msg_obj = await channel.fetch_message(int(msg_id))
            except Exception:
                msg_obj = None

        if msg_obj is None:
            msg_obj = await channel.send(embed=emb)
        else:
            await msg_obj.edit(content=None, embed=emb)

        audit_map[week_key] = msg_obj.id
        await db_cog.update_guild_settings(guild.id, {
            'audit_duit_message_ids': audit_map,
            'audit_duit_data': audit_data,
        })

        await interaction.followup.send(
            f"✅ Audit Duit disimpan untuk **{day_upper}** (Minggu {week_no}).",
            ephemeral=True,
        )

    # ========== ADMIN VIEW ==========
    class AdminView(View):
        def __init__(self, cog):
            super().__init__(timeout=None)
            self.cog = cog

        @discord.ui.select(
            placeholder="⚙️ Pilih Tindakan Admin...",
            options=[
                SelectOption(label="⛱️ Set Cuti Rasmi", value="set_official_leave", description="Tetapkan hari cuti rasmi mingguan"),
                SelectOption(label="⏱️ Set Masa Voice", value="set_voice_time", description="Tetapkan masa minimum dalam voice channel"),
                SelectOption(label="🕐 Jam Open (Waktu Kehadiran)", value="set_attendance_time", description="Tetapkan waktu mula ambil kehadiran"),
                SelectOption(label="🕘 Jam Closed (Cutoff)", value="set_cutoff", description="Tetapkan waktu akhir untuk kehadiran"),
                SelectOption(label="✏️ Edit Kehadiran User", value="edit_attendance", description="Kemaskini status kehadiran ahli"),
                SelectOption(label="🟡 Pending Permohonan Cuti", value="leave_pending", description="Approve/Tolak/Padam permohonan cuti yang belum diproses"),
                SelectOption(label="⚠️ Hantar Amaran", value="send_warning", description="Hantar amaran DM (boleh pilih ramai)"),
                SelectOption(label="🗄️ Akses Database", value="access_database", description="Lihat, cari, padam data ahli"),
                SelectOption(label="👥 Role Leaderboard", value="change_role", description="Tukar role yang dipaparkan dalam leaderboard"),
                SelectOption(label="🔄 Refresh Leaderboard", value="refresh_lb", description="Update leaderboard dengan data terkini"),
                SelectOption(label="📊 Statistik Server", value="server_stats", description="Lihat statistik server dan kehadiran"),
                SelectOption(label="📋 Laporan Kehadiran Harian", value="daily_report", description="Ringkasan dan peratus kehadiran harian (pilih tarikh)"),
                SelectOption(label="🔒🔓 Lock/Unlock Voice", value="voice_lock_control", description="Kawal akses masuk Voice Channel Kehadiran"),
                SelectOption(label="🧹 Bersihkan Data Lama", value="cleanup_data", description="Padam data lama dan user tidak aktif"),

                # KUTIPAN
                SelectOption(label="💰 Edit Kutipan User", value="kutipan_panel", description="Panel kutipan duit mingguan (boleh pilih ramai)"),
                SelectOption(label="📅 Statistik Kutipan Mingguan", value="kutipan_stats_weekly", description="Lihat statistik kutipan minggu semasa"),
                SelectOption(label="📈 Statistik Kutipan Bulanan", value="kutipan_stats_monthly", description="Lihat statistik kutipan bulan ini"),
                SelectOption(label="📨 Reminder Kutipan", value="kutipan_reminder", description="DM semua ahli yang belum bayar kutipan"),
                SelectOption(label="🧾 Message Audit Duit", value="audit_duit", description="Nota audit duit keluar (pilih minggu & hari)"),
                SelectOption(label="💸 Edit Jumlah Kutipan", value="kutipan_edit_amount", description="Tetapkan jumlah kutipan yang perlu dibayar"),
                SelectOption(label="💸 Edit Jumlah Penalty Kutipan", value="kutipan_edit_penalty", description="Tetapkan jumlah denda jika gagal bayar"),

                # SISTEM
                SelectOption(label="🎽 Urus Jersey", value="jersey_management", description="Lihat statistik & pindah nombor jersi ahli"),
            ]
        )
        async def select(self, interaction: discord.Interaction, select: Select):
            action = (select.values[0] if select.values else "").strip()

            # elak duplicate bila admin klik laju
            if self.cog._is_duplicate_select(interaction.user.id, action):
                try:
                    await interaction.response.send_message("⏳ Sila tunggu...", ephemeral=True)
                except Exception:
                    pass
                return

            try:
                await self.cog.handle_select(interaction, action)
            finally:
                # Reset dropdown selection supaya boleh klik option yang sama berulang kali
                try:
                    await self.cog.request_panel_reset(interaction.message)
                except Exception:
                    pass

    async def handle_select(self, interaction: discord.Interaction, action: str):
        """Handle admin select dengan lebih tahan error."""
        if not self.is_admin(interaction.user):
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Hanya admin!", ephemeral=True)
            else:
                await self.ui_reply(interaction, content="❌ Hanya admin!", embed=None, view=None)
            return

        try:
            if action == "set_official_leave":
                await interaction.response.send_modal(OfficialLeaveModal(self))
                return
            if action == "set_voice_time":
                await interaction.response.send_modal(VoiceTimeModal(self))
                return
            if action == "set_attendance_time":
                await interaction.response.send_modal(AttendanceTimeModal(self))
                return
            if action == "edit_attendance":
                if not interaction.guild:
                    if not interaction.response.is_done():
                        await interaction.response.send_message("❌ Guild tidak dijumpai.", ephemeral=True)
                    else:
                        await self.ui_reply(interaction, content="❌ Guild tidak dijumpai.")
                    return
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True, thinking=False)
                view = AttendanceBulkEditView(self, interaction.user.id, interaction.guild)
                await self.ui_reply(interaction, embed=view.build_embed(), view=view)
                return
            if action == "set_cutoff":
                await interaction.response.send_modal(CutoffModal(self))
                return

            if action == "kutipan_reminder":
                kutipan = self.bot.get_cog("KutipanCog")
                if not kutipan:
                    if not interaction.response.is_done():
                        await interaction.response.send_message("❌ Sistem Kutipan belum dimuatkan.", ephemeral=True)
                    else:
                        await self.ui_reply(interaction, content="❌ Sistem Kutipan belum dimuatkan.")
                    return
                await kutipan.open_reminder_modal(interaction)
                return

            if action in {"leave_pending", "send_warning", "access_database", "change_role", "refresh_lb", "server_stats", "daily_report", "voice_lock_control", "cleanup_data", "audit_duit", "jersey_management", "kutipan_stats_monthly", "kutipan_stats_weekly"}:
                if not interaction.response.is_done():
                    await interaction.response.defer(ephemeral=True, thinking=False)

            if action == "leave_pending":
                await self.show_pending_leave_manager(interaction)
            elif action == "send_warning":
                if not interaction.guild:
                    await self.ui_reply(interaction, content="❌ Guild tidak dijumpai.")
                    return
                view = WarningMultiUserView(self, interaction.user.id, interaction.guild)
                await self.ui_reply(interaction, embed=view.build_embed(), view=view)
            elif action == "access_database":
                await self.show_database_menu(interaction)
                log = self.bot.get_cog('LoggerCog')
                if log:
                    await log.log_admin_action(interaction.user, "Akses Database", "Buka menu database")
            elif action == "change_role":
                await self.change_role(interaction)
            elif action == "refresh_lb":
                lb = self.bot.get_cog('LeaderboardCog')
                if lb:
                    await lb.update_leaderboard_channel()
                await self.ui_reply(interaction, content="✅ Leaderboard diupdate!", embed=None, view=None)
                log = self.bot.get_cog('LoggerCog')
                if log:
                    await log.log_admin_action(interaction.user, "Refresh Leaderboard", "Update leaderboard")
            elif action == "server_stats":
                await self.show_server_stats(interaction)
                log = self.bot.get_cog('LoggerCog')
                if log:
                    await log.log_admin_action(interaction.user, "Statistik Server", "Lihat statistik server")
            elif action == "daily_report":
                await self.show_daily_report_date_selector(interaction)
            elif action == "voice_lock_control":
                await self.show_voice_lock_control(interaction)
                log = self.bot.get_cog('LoggerCog')
                if log:
                    await log.log_admin_action(interaction.user, "Voice Lock Control", "Buka panel kawalan voice")
            elif action == "cleanup_data":
                await self.cleanup_data(interaction)
            elif action == "kutipan_panel":
                kutipan = self.bot.get_cog("KutipanCog")
                if not kutipan:
                    if not interaction.response.is_done():
                        await interaction.response.send_message("❌ Sistem Kutipan belum dimuatkan. Semak cogs/kutipan.py.", ephemeral=True)
                    else:
                        await self.ui_reply(interaction, content="❌ Sistem Kutipan belum dimuatkan. Semak cogs/kutipan.py.")
                    return
                await kutipan.open_admin_panel(interaction)
                log = self.bot.get_cog("LoggerCog")
                if log:
                    await log.log_admin_action(interaction.user, "Kutipan Panel", "Buka panel kutipan")
            elif action == "kutipan_stats_monthly":
                kutipan = self.bot.get_cog("KutipanCog")
                if not kutipan:
                    await self.ui_reply(interaction, content="❌ Sistem Kutipan belum dimuatkan.")
                    return
                await kutipan.send_monthly_stats(interaction)
                log = self.bot.get_cog("LoggerCog")
                if log:
                    await log.log_admin_action(interaction.user, "Statistik Kutipan Bulanan", "Lihat statistik bulanan (admin panel)")
            elif action == "kutipan_stats_weekly":
                kutipan = self.bot.get_cog("KutipanCog")
                if not kutipan:
                    await self.ui_reply(interaction, content="❌ Sistem Kutipan belum dimuatkan.")
                    return
                await kutipan.send_weekly_stats(interaction)
                log = self.bot.get_cog("LoggerCog")
                if log:
                    await log.log_admin_action(interaction.user, "Statistik Kutipan Mingguan", "Lihat statistik mingguan (admin panel)")
            elif action == "audit_duit":
                await self.open_audit_duit_day_selector(interaction)
                log = self.bot.get_cog('LoggerCog')
                if log:
                    await log.log_admin_action(interaction.user, "Audit Duit", "Buka pemilih hari audit duit")
            elif action == "kutipan_edit_amount":
                kutipan = self.bot.get_cog("KutipanCog")
                if not kutipan:
                    if not interaction.response.is_done():
                        await interaction.response.send_message("❌ Sistem Kutipan belum dimuatkan.", ephemeral=True)
                    else:
                        await self.ui_reply(interaction, content="❌ Sistem Kutipan belum dimuatkan.")
                    return
                await kutipan.open_amount_modal(interaction, kind='weekly')
            elif action == "kutipan_edit_penalty":
                kutipan = self.bot.get_cog("KutipanCog")
                if not kutipan:
                    if not interaction.response.is_done():
                        await interaction.response.send_message("❌ Sistem Kutipan belum dimuatkan.", ephemeral=True)
                    else:
                        await self.ui_reply(interaction, content="❌ Sistem Kutipan belum dimuatkan.")
                    return
                await kutipan.open_amount_modal(interaction, kind='penalty')
            elif action == "jersey_management":
                await self.show_jersey_menu(interaction)
                log = self.bot.get_cog('LoggerCog')
                if log:
                    await log.log_admin_action(interaction.user, "Akses Menu Jersey", "Buka menu pengurusan jersi")
            else:
                await self.ui_reply(interaction, content=f"❌ Tindakan admin tidak dikenali: `{action}`", embed=None, view=None)
        except Exception as e:
            logger.exception(f"admin_panel handle_select error ({action}): {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"❌ Ralat semasa buka menu **{action}**: {e}", ephemeral=True)
                else:
                    await self.ui_reply(interaction, content=f"❌ Ralat semasa buka menu **{action}**: {e}", embed=None, view=None)
            except Exception:
                pass


    async def show_daily_report_date_selector(self, interaction: discord.Interaction):
        """Show date selector for daily report (past week)"""
        dates = self._get_past_week_dates()

        embed = discord.Embed(
            title="📋 LAPORAN KEHADIRAN HARIAN",
            description="**Pilih tarikh untuk melihat laporan:**\n\nAnda boleh melihat laporan untuk **7 hari kebelakang** (seminggu).",
            color=0x3498DB
        )

        embed.add_field(
            name="📅 Tarikh Tersedia",
            value="\n".join([f"{'📍' if d['is_today'] else '📄'} {d['display']} ({d['day_name']})" for d in dates]),
            inline=False
        )

        if dates[0]['is_today']:
            embed.set_footer(text="📍 = Hari Ini")

        view = DailyReportDateSelectorView(self, dates)
        await self.ui_reply(interaction, embed=embed, view=view)

    async def show_daily_attendance_report(self, interaction: discord.Interaction, specific_date: str = None, date_display: str = None):
        """Papar laporan dan statistik kehadiran harian dengan embed menarik"""
        db = self.bot.get_cog('DatabaseCog')
        att_cog = self.bot.get_cog('AttendanceCog')

        now = self.get_time()

        # Use provided date or default to today
        if specific_date:
            date_str = specific_date
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            date_str = att_cog.get_attendance_date() if att_cog else now.strftime("%Y-%m-%d")
            dt = datetime.strptime(date_str, "%Y-%m-%d")

        day_name = self._format_malay_day(dt)
        display_date = date_display or dt.strftime("%d/%m/%Y")

        settings = await db.get_guild_settings(Config.GUILD_ID)
        official_days = settings.get("official_leave_days", [])

        ahli_list = await db.get_all_ahli_dkatana()
        total = len(ahli_list)

        # Determine if this is a future date
        is_future = dt.date() > now.date()

        # Determine if window is closed for this date
        cutoff_h, cutoff_m = self._parse_hhmm(settings.get("attendance_cutoff", Config.ATTENDANCE_CUTOFF_TIME))
        cutoff_dt = pytz.timezone(Config.TIMEZONE).localize(
            datetime(dt.year, dt.month, dt.day, cutoff_h, cutoff_m)
        ) + timedelta(days=1)

        # For past dates, window is always closed
        # For today, check current time
        # For future, window is not open yet
        if is_future:
            window_closed = False
            window_open = False
        elif dt.date() < now.date():
            window_closed = True
            window_open = False
        else:  # Today
            window_closed = now >= cutoff_dt
            window_open = not window_closed

        if dt.weekday() in official_days:
            hadir = 0
            cuti = 0
            cuti_rasmi = total
            cuti_am = 0
            tidak_hadir = 0
            belum = 0
            pct = 100.0 if total > 0 else 0.0
        else:
            hadir = 0
            cuti = 0
            cuti_rasmi = 0
            cuti_am = 0
            tidak_hadir = 0
            belum = 0

            for ahli in ahli_list:
                uid = ahli.get("user_id")
                att = await db.get_attendance(uid, date_str)
                if att and att.get("status"):
                    st = att["status"]
                    if st == "hadir":
                        hadir += 1
                    elif st == "cuti":
                        cuti += 1
                    elif st == "cuti_rasmi":
                        cuti_rasmi += 1
                    elif st == "cuti_am":
                        cuti_am += 1
                    elif st == "tidak_hadir":
                        tidak_hadir += 1
                    else:
                        if window_closed:
                            tidak_hadir += 1
                        else:
                            belum += 1
                else:
                    if window_closed:
                        tidak_hadir += 1
                    else:
                        belum += 1

            pct = (hadir / total * 100.0) if total > 0 else 0.0

        # Tentukan emoji dan ayat berdasarkan peratusan
        if is_future:
            status_emoji = "📅"
            status_text = "Tarikh akan datang"
            embed_color = 0x95A5A6  # Gray
        elif pct >= 90:
            status_emoji = "🌟"
            status_text = "Cemerlang! Kehadiran sangat baik! 🎉"
            embed_color = 0x00FF00
        elif pct >= 70:
            status_emoji = "✨"
            status_text = "Bagus! Teruskan usaha! 💪"
            embed_color = 0x3498DB
        elif pct >= 50:
            status_emoji = "⚡"
            status_text = "Sederhana. Perlu penambahbaikan. 📈"
            embed_color = 0xFFA500
        else:
            status_emoji = "📉"
            status_text = "Perlu perhatian segera! 🚨"
            embed_color = 0xFF0000

        # Window status text
        if dt.weekday() in official_days:
            window_text = "CUTI RASMI 🏖️"
        elif is_future:
            window_text = "BELUM MULA 📅"
        elif window_closed:
            window_text = "TUTUP 🔒"
        else:
            window_text = "SEDANG BERJALAN 🟢"

        # Create beautiful embed
        embed = discord.Embed(
            title=f"{status_emoji} LAPORAN KEHADIRAN HARIAN {status_emoji}",
            description=(
                f"📅 **Tarikh:** `{display_date}` (**{day_name}**)\n"
                f"⏱️ **Status Window:** `{window_text}`\n"
                f"📊 **Prestasi:** {status_text}\n\n"
                f"*Disiplin adalah kunci kejayaan!* 🔑"
            ),
            color=embed_color,
            timestamp=now
        )

        # Stats fields with emojis
        embed.add_field(
            name=f"{Config.EMOJI_PEOPLE} Total Ahli",
            value=f"**`{total}`** orang",
            inline=True
        )
        embed.add_field(
            name=f"{Config.EMOJI_HADIR} Hadir",
            value=f"**`{hadir}`** orang",
            inline=True
        )
        embed.add_field(
            name=f"{Config.EMOJI_CUTI} Cuti",
            value=f"**`{cuti}`** orang",
            inline=True
        )
        embed.add_field(
            name=f"{Config.EMOJI_CUTI_RASMI} Cuti Rasmi",
            value=f"**`{cuti_rasmi}`** orang",
            inline=True
        )
        embed.add_field(
            name="🏝️ Cuti Am",
            value=f"**`{cuti_am}`** orang",
            inline=True
        )
        embed.add_field(
            name=f"{Config.EMOJI_TIDAK_HADIR} Tidak Hadir",
            value=f"**`{tidak_hadir}`** orang",
            inline=True
        )
        embed.add_field(
            name=f"📈 Peratus Kehadiran",
            value=f"**`{pct:.1f}%`**",
            inline=True
        )

        # Progress bar
        filled = int(pct / 10)
        empty = 10 - filled
        progress_bar = "🟩" * filled + "⬜" * empty
        embed.add_field(
            name="📊 Bar Progress",
            value=f"{progress_bar} **{pct:.0f}%**",
            inline=False
        )

        # Additional info
        if belum > 0 and not is_future and dt.weekday() not in official_days and not window_closed:
            embed.add_field(
                name="⏳ Maklumat",
                value=f"`{belum}` ahli belum rekod kehadiran. Cutoff pada `{Config.ATTENDANCE_CUTOFF_TIME}`.",
                inline=False
            )

        if is_future:
            embed.add_field(
                name="📅 Nota",
                value="Laporan ini adalah untuk tarikh akan datang.",
                inline=False
            )

        embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else None)
        embed.set_footer(text=f"📌 Dijana pada {now.strftime('%H:%M:%S')} | Sistem Kehadiran DKATANA")

        await self.ui_reply(interaction, embed=embed, view=None)


    async def show_voice_lock_control(self, interaction: discord.Interaction):
        """Show voice channel lock/unlock control panel"""
        guild = interaction.guild
        channel = guild.get_channel(Config.VOICE_ATTENDANCE_CHANNEL_ID)
        ahli_role = guild.get_role(Config.AHLI_DKATANA_ROLE_ID)

        if not channel or not ahli_role:
            await self.ui_reply(interaction, content="❌ Voice channel atau role AHLI tidak dijumpaikan!", embed=None, view=None)
            return

        # Check current permission
        ow = channel.overwrites_for(ahli_role)
        current_status = "🔓 TERBUKA" if ow.connect != False else "🔒 TERKUNCI"

        embed = discord.Embed(
            title="🔐 KAWALAN VOICE CHANNEL KEHADIRAN",
            description=(
                f"📍 **Channel:** {channel.mention}\n"
                f"👥 **Role:** {ahli_role.mention}\n"
                f"🔒 **Status Semasa:** `{current_status}`\n\n"
                f"**Penerangan:**\n"
                f"🔒 **Lock** - Ahli tak boleh masuk voice channel\n"
                f"🔓 **Unlock** - Ahli boleh masuk untuk ambil kehadiran\n"
                f"👁️ **Channel masih boleh dilihat** oleh ahli\n\n"
                f"⚠️ *Tindakan ini akan direkodkan dalam log admin*"
            ),
            color=0x9B59B6
        )

        view = VoiceLockControlView(self, channel, ahli_role)
        await self.ui_reply(interaction, embed=embed, view=view)

    async def toggle_voice_lock(self, interaction: discord.Interaction, lock: bool, reason: str = None):
        """Toggle voice channel lock/unlock"""
        guild = interaction.guild
        channel = guild.get_channel(Config.VOICE_ATTENDANCE_CHANNEL_ID)
        ahli_role = guild.get_role(Config.AHLI_DKATANA_ROLE_ID)

        if not channel or not ahli_role:
            await interaction.followup.send("❌ Voice channel atau role tidak dijumpaikan!", ephemeral=True)
            return

        # Get current overwrite
        ow = channel.overwrites_for(ahli_role)

        # Set permissions - Lock = connect=False, Unlock = connect=True (or None)
        if lock:
            ow.connect = False
            action_text = "🔒 DIKUNCI"
            color = 0xFF0000
        else:
            ow.connect = True
            action_text = "🔓 DIBUKA"
            color = 0x00FF00

        try:
            await channel.set_permissions(ahli_role, overwrite=ow, reason=f"Admin {interaction.user.name}: {reason or 'Manual toggle'}")

            # Log to database
            db = self.bot.get_cog('DatabaseCog')
            if db:
                await db.log_voice_lock_action(
                    interaction.user.id,
                    interaction.user.display_name,
                    "lock" if lock else "unlock",
                    reason
                )

            # Log to Discord
            log = self.bot.get_cog('LoggerCog')
            if log:
                await log.log_voice_action(
                    interaction.user,
                    "LOCK" if lock else "UNLOCK",
                    channel,
                    reason or "Manual toggle via Admin Panel"
                )

            embed = discord.Embed(
                title=f"{action_text} - Voice Channel",
                description=f"📍 {channel.mention} telah **{action_text}**!",
                color=color
            )
            if reason:
                embed.add_field(name="📝 Sebab", value=f"```{reason}```", inline=False)
            embed.set_footer(text=f"Oleh: {interaction.user.display_name}")

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error toggling voice lock: {e}")
            await interaction.followup.send(f"❌ Ralat: {e}", ephemeral=True)

    # ========== DATABASE MENU ==========
    # ========== JERSEY MANAGEMENT ==========
    async def show_jersey_menu(self, interaction: discord.Interaction):
        """Show jersey management menu"""
        db = self.bot.get_cog('DatabaseCog')
        if not db:
            await self.ui_reply(interaction, content='❌ DatabaseCog tidak dijumpai.', embed=None, view=None)
            return
        stats = await db.get_jersey_number_stats()

        embed = discord.Embed(
            title="🎽 PENGURUSAN NOMBOR JERSI",
            description="Sistem Nombor Tetap (seperti nombor badan dalam sukan)",
            color=0xFFD700
        )

        embed.add_field(name="📊 Total Ahli", value=f"`{stats['total_assigned']}`", inline=True)
        embed.add_field(name="🔢 Nombor Tertinggi", value=f"`#{stats['highest_number']}`", inline=True)
        embed.add_field(name="➡️ Seterusnya", value=f"`#{stats['next_available']}`", inline=True)

        if stats['available_gaps']:
            gaps_text = ", ".join([f"#{g}" for g in stats['available_gaps'][:10]])
            embed.add_field(name="🔍 Nombor Available", value=f"`{gaps_text}`", inline=False)

        embed.set_footer(text="Pilih tindakan di bawah")

        view = JerseyMenuView(self)
        await self.ui_reply(interaction, embed=embed, view=view)

    async def show_jersey_stats(self, interaction: discord.Interaction):
        """Show detailed jersey statistics"""
        db = self.bot.get_cog('DatabaseCog')
        stats = await db.get_jersey_number_stats()
        users = await db.get_all_ahli_dkatana()

        embed = discord.Embed(
            title="🎽 STATISTIK LENGKAP NOMBOR JERSI",
            description=f"**Total Ahli:** `{len(users)}` | **Assigned:** `{stats['total_assigned']}`",
            color=0x3498DB
        )

        stats_text = f"Highest: `#{stats['highest_number']}`\nNext: `#{stats['next_available']}`\nGaps: `{len(stats['available_gaps'])}` slots"
        embed.add_field(name="📈 Statistik", value=stats_text, inline=False)

        if stats['available_gaps']:
            gaps = ", ".join([str(g) for g in stats['available_gaps'][:20]])
            embed.add_field(name="🔍 Nombor Kosong", value=f"```{gaps}```", inline=False)

        assigned_users = [u for u in users if u.get('jersey_number') or u.get('leaderboard_number')]
        assigned_users.sort(key=lambda x: x.get('jersey_number', x.get('leaderboard_number', 0)), reverse=True)

        recent = assigned_users[:10]
        if recent:
            text_lines = []
            for u in recent:
                j_num = u.get('jersey_number', u.get('leaderboard_number', 'N/A'))
                name = u.get('display_name', 'Unknown')
                text_lines.append(f"🎽 `#{j_num:02d}` - {name}")
            embed.add_field(name="👥 Ahli Terkini (Top 10)", value="\n".join(text_lines), inline=False)

        embed.set_footer(text="💡 Nombor jersi kekal sepanjang hayat ahli")
        await interaction.followup.send(embed=embed, ephemeral=True)

        log = self.bot.get_cog('LoggerCog')
        if log:
            await log.log_admin_action(interaction.user, "Lihat Statistik Jersey", f"Stats: {stats['total_assigned']} assigned")

    async def show_transfer_jersey_modal(self, interaction: discord.Interaction):
        """Show modal for transferring jersey"""
        modal = TransferJerseyModal(self)
        await interaction.response.send_modal(modal)

    async def transfer_jersey(self, interaction: discord.Interaction, from_user_id: int, to_user_id: int):
        """Transfer jersey number between users"""
        await interaction.response.defer(ephemeral=True)

        if from_user_id == to_user_id:
            await interaction.followup.send("❌ Tidak boleh pindahkan ke diri sendiri!", ephemeral=True)
            return

        guild = interaction.guild
        from_member = guild.get_member(from_user_id)
        to_member = guild.get_member(to_user_id)

        if not from_member or not to_member:
            await interaction.followup.send("❌ User tidak dijumpai dalam server!", ephemeral=True)
            return

        db = self.bot.get_cog('DatabaseCog')

        from_user_data = await db.get_user(from_user_id)
        to_user_data = await db.get_user(to_user_id)

        from_jersey = from_user_data.get('jersey_number') if from_user_data else None
        to_jersey = to_user_data.get('jersey_number') if to_user_data else None

        if not from_jersey:
            await interaction.followup.send(f"❌ {from_member.mention} tidak mempunyai nombor jersi!", ephemeral=True)
            return

        confirm_embed = discord.Embed(
            title="⚠️ Sahkan Pindahan Jersi",
            description=f"Anda akan memindahkan:\n\n🎽 `#{from_jersey}` dari {from_member.mention}\nke {to_member.mention}",
            color=0xE74C3C
        )

        if to_jersey:
            confirm_embed.add_field(name="⚠️ Amaran", value=f"{to_member.mention} sudah mempunyai jersi `#{to_jersey}`. Proses ini akan overwrite.", inline=False)

        confirm_embed.set_footer(text="Ini adalah tindakan kekal. Klik butang untuk sahkan.")

        view = ConfirmJerseyTransferView(self, from_user_id, to_user_id, from_jersey)
        await interaction.followup.send(embed=confirm_embed, view=view, ephemeral=True)

    async def execute_jersey_transfer(self, interaction: discord.Interaction, from_user_id: int, to_user_id: int, jersey_number: int):
        """Execute the actual jersey transfer"""
        await interaction.response.defer(ephemeral=True)

        db = self.bot.get_cog('DatabaseCog')
        success, message = await db.transfer_jersey_number(from_user_id, to_user_id, interaction.user.id)

        if success:
            embed = discord.Embed(
                title="✅ Pindahan Berjaya",
                description=message,
                color=0x2ECC71
            )

            guild = interaction.guild
            from_member = guild.get_member(from_user_id)
            to_member = guild.get_member(to_user_id)

            embed.add_field(name="Dari", value=from_member.mention if from_member else f"<@{from_user_id}>", inline=True)
            embed.add_field(name="Kepada", value=to_member.mention if to_member else f"<@{to_user_id}>", inline=True)
            embed.add_field(name="Nombor", value=f"🎽 `#{jersey_number}`", inline=True)

            await interaction.followup.send(embed=embed, ephemeral=True)

            log = self.bot.get_cog('LoggerCog')
            if log:
                await log.log_admin_action(interaction.user, "Pindahan Jersi", f"Jersi #{jersey_number} dari {from_user_id} ke {to_user_id}")
        else:
            await interaction.followup.send(f"❌ {message}", ephemeral=True)


    async def show_pending_leave_manager(self, interaction: discord.Interaction):
        """Papar senarai permohonan cuti yang masih **pending**.

        Keperluan:
        - Bila admin pilih dropdown **Pending Kehadiran**, bot akan balas (hide) sama ada ada/tidak rekod.
        - Jika ada, admin boleh approve/tolak/padam.
        - Guna **1** mesej hide yang sama (edit sahaja) supaya tak jadi banyak.
        """
        if not interaction.guild:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=False)
            await self.ui_reply(interaction, content="❌ Guild tidak sah.")
            return

        if not self.is_admin(interaction.user):
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=False)
            await self.ui_reply(interaction, content="❌ Anda tiada akses untuk guna panel ini.")
            return

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=False)

        db = self.bot.get_cog("DatabaseCog")
        if not db:
            await self.ui_reply(interaction, content="❌ DatabaseCog tidak dijumpai.")
            return

        try:
            pending = await db.get_pending_leave_requests()
        except Exception:
            pending = []

        if not pending:
            emb = discord.Embed(
                title="🟡 Pending Kehadiran / Cuti",
                description="✅ Tiada rekod permohonan cuti yang **pending**.",
                color=getattr(Config, 'COLOR_SUCCESS', 0x2ECC71),
            )
            emb.set_footer(text="Panel akan paparkan permohonan pending jika ada")
            await self.ui_reply(interaction, embed=emb, view=None)
            return

        view = PendingLeaveManageView(self, pending)
        await self.ui_reply(interaction, embed=view.build_embed(), view=view)


    async def show_database_menu(self, interaction: discord.Interaction):
        """Show database menu with buttons"""
        db = self.bot.get_cog('DatabaseCog')
        if not db:
            await self.ui_reply(interaction, content='❌ DatabaseCog tidak dijumpai.', embed=None, view=None)
            return
        users = await db.get_all_ahli_dkatana()
        pending = len(await db.get_pending_leave_requests())

        embed = discord.Embed(
            title="🗄️ AKSES DATABASE",
            description=f"**📊 Statistik:**\n👥 Total Ahli: `{len(users)}`\n⏳ Cuti Pending: `{pending}`",
            color=0x9B59B6
        )

        view = DatabaseMenuView(self)
        await self.ui_reply(interaction, embed=embed, view=view)

    async def show_all_users(self, interaction: discord.Interaction):
        """Show all users from database with jersey numbers"""
        db = self.bot.get_cog('DatabaseCog')
        users = await db.get_all_ahli_dkatana()

        chunk_size = 20
        chunks = [users[i:i+chunk_size] for i in range(0, len(users), chunk_size)]

        for idx, chunk in enumerate(chunks):
            embed = discord.Embed(
                title=f"📋 SENARAI AHLI (Bahagian {idx+1}/{len(chunks)})",
                description="🎽 **Nombor Jersi = Tetap (seperti nombor badan)**",
                color=0x3498DB
            )

            text = ""
            for user in chunk:
                jersey = user.get('jersey_number', user.get('leaderboard_number', 'N/A'))
                name = user.get('display_name', 'Unknown')
                text += f"🎽 `#{jersey:02d}` - {name}\n"

            embed.add_field(name="Ahli", value=text or "📭 Tiada", inline=False)
            embed.set_footer(text="💡 Nombor jersi kekal sama sepanjang masa")
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def change_role(self, interaction: discord.Interaction):
        """Change leaderboard role"""
        guild = interaction.guild
        options = []
        for role in guild.roles:
            if not role.is_default() and not role.managed:
                options.append(SelectOption(
                    label=role.name[:25],
                    value=str(role.id),
                    description=f"Members: {len(role.members)}"
                ))

        if len(options) > 25:
            options = options[:25]

        view = RoleSelectView(self, options)
        await self.ui_reply(interaction, content="Pilih role untuk leaderboard:", embed=None, view=view)

    async def show_server_stats(self, interaction: discord.Interaction):
        """Show server statistics"""
        db = self.bot.get_cog('DatabaseCog')
        if not db:
            await self.ui_reply(interaction, content='❌ DatabaseCog tidak dijumpai.', embed=None, view=None)
            return
        users = await db.get_all_ahli_dkatana()
        pending = len(await db.get_pending_leave_requests())

        guild = self.bot.get_guild(Config.GUILD_ID)
        total_members = len(guild.members) if guild else 0

        embed = discord.Embed(
            title="📊 STATISTIK SERVER",
            color=0x3498DB
        )
        embed.add_field(name="👥 Total Ahli Dkatana", value=f"`{len(users)}`", inline=True)
        embed.add_field(name="👥 Total Members", value=f"`{total_members}`", inline=True)
        embed.add_field(name="⏳ Cuti Pending", value=f"`{pending}`", inline=True)

        await self.ui_reply(interaction, embed=embed, view=None)

    async def cleanup_data(self, interaction: discord.Interaction):
        """Cleanup old data"""
        db = self.bot.get_cog('DatabaseCog')
        deleted = await db.cleanup_old_data()

        await self.ui_reply(interaction, content=f"🧹 {deleted} data lama telah dibersihkan!", embed=None, view=None)

        log = self.bot.get_cog('LoggerCog')
        if log:
            await log.log_admin_action(interaction.user, "Bersihkan Data", f"{deleted} item dipadam")

    # ========== COMMANDS ==========
    
    # ========== ARCHIVE MANAGEMENT COMMANDS ==========
    @commands.command(name="archive_stats")
    @commands.has_permissions(administrator=True)
    async def archive_stats_cmd(self, ctx):
        """📦 Lihat statistik archive data"""
        db = self.bot.get_cog('DatabaseCog')
        stats = await db.get_archive_stats()

        if not stats:
            await ctx.send("❌ Tidak dapat mengambil statistik archive.", delete_after=5)
            return

        embed = discord.Embed(
            title="📦 STATISTIK DATA ARCHIVE",
            description=f"Data retention: **{Config.DATA_RETENTION_DAYS} hari**",
            color=0x9B59B6
        )

        embed.add_field(
            name="📝 Attendance",
            value=f"Active: `{stats.get('attendance_active', 0)}`\nArchived: `{stats.get('attendance_archived', 0)}`\nTotal: `{stats.get('total_attendance', 0)}`",
            inline=True
        )

        embed.add_field(
            name="📋 Leave Requests",
            value=f"Active: `{stats.get('leave_active', 0)}`\nArchived: `{stats.get('leave_archived', 0)}`\nTotal: `{stats.get('total_leave', 0)}`",
            inline=True
        )

        embed.set_footer(text="💡 Data lebih 7 hari automatik di-archive setiap 04:30")

        await ctx.send(embed=embed, delete_after=30)

    @commands.command(name="archive_now")
    @commands.has_permissions(administrator=True)
    async def archive_now_cmd(self, ctx):
        """🗂️ Archive data lama SEKARANG (manual trigger)"""
        await ctx.send("📦 Memulakan proses archive...")

        db = self.bot.get_cog('DatabaseCog')

        archived_attendance = await db.archive_old_attendance(Config.DATA_RETENTION_DAYS)
        archived_leave = await db.archive_old_leave_requests(Config.DATA_RETENTION_DAYS)

        total = archived_attendance + archived_leave

        if total > 0:
            embed = discord.Embed(
                title="✅ Archive Selesai",
                description=f"Data lama telah di-archive:",
                color=Config.COLOR_SUCCESS
            )
            embed.add_field(name="📝 Attendance", value=f"`{archived_attendance}` rekod", inline=True)
            embed.add_field(name="📋 Leave Requests", value=f"`{archived_leave}` rekod", inline=True)
            embed.add_field(name="📅 Kriteria", value=f"Data > {Config.DATA_RETENTION_DAYS} hari", inline=False)

            await ctx.send(embed=embed)

            log = self.bot.get_cog('LoggerCog')
            if log:
                await log.log_admin_action(ctx.author, "Manual Archive", f"{archived_attendance} attendance, {archived_leave} leave")
        else:
            await ctx.send("📭 Tiada data lama untuk di-archive (kurang dari 7 hari).", delete_after=5)

    @commands.command(name="restore_data")
    @commands.has_permissions(administrator=True)
    async def restore_data_cmd(self, ctx, user_id: int = None, date: str = None):
        """🔄 Restore data dari archive"""
        await ctx.send("🔄 Memulakan proses restore...")

        db = self.bot.get_cog('DatabaseCog')
        restored = await db.restore_from_archive(user_id, date)

        if restored > 0:
            await ctx.send(f"✅ Berjaya restore `{restored}` rekod dari archive.")

            log = self.bot.get_cog('LoggerCog')
            if log:
                await log.log_admin_action(ctx.author, "Restore Data", f"Restored {restored} records")
        else:
            await ctx.send("❌ Tiada data untuk di-restore.", delete_after=5)

    @commands.command(name="cleanup_permanent")
    @commands.has_permissions(administrator=True)
    async def cleanup_permanent_cmd(self, ctx, days: int = 30):
        """⚠️ Padam PERMANEN data archive (hard delete)"""
        if days < 30:
            await ctx.send("❌ Minimum 30 hari untuk keamanan data.", delete_after=5)
            return

        # Confirmation required
        embed = discord.Embed(
            title="⚠️ AMARAN: Padam Data Permanent",
            description=f"Anda akan memadam data archive lebih {days} hari SECARA PERMANEN!\n\nTaip `CONFIRM` dalam 10 saat untuk teruskan.",
            color=Config.COLOR_ERROR
        )
        await ctx.send(embed=embed)

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content == "CONFIRM"

        try:
            await self.bot.wait_for('message', check=check, timeout=10.0)
        except:
            await ctx.send("❌ Dibatalkan.", delete_after=3)
            return

        db = self.bot.get_cog('DatabaseCog')
        cutoff = datetime.now() - timedelta(days=days)

        # Hard delete from archive collections
        att_result = db.db.attendance_archive.delete_many({"archived_at": {"$lt": cutoff}})
        leave_result = db.db.leave_archive.delete_many({"archived_at": {"$lt": cutoff}})

        total = att_result.deleted_count + leave_result.deleted_count

        if total > 0:
            await ctx.send(f"🗑️ Berjaya padam `{total}` rekod secara permanent.")

            log = self.bot.get_cog('LoggerCog')
            if log:
                await log.log_admin_action(ctx.author, "Hard Delete Archive", f"Deleted {total} records older than {days} days")
        else:
            await ctx.send("📭 Tiada data untuk dipadam.", delete_after=5)

    
    # ========== JERSEY NUMBER MANAGEMENT ==========
    @commands.command(name="jersey_stats")
    @commands.has_permissions(administrator=True)
    async def jersey_stats_cmd(self, ctx):
        """🎽 Lihat statistik nombor jersi"""
        db = self.bot.get_cog('DatabaseCog')
        stats = await db.get_jersey_number_stats()

        embed = discord.Embed(
            title="🎽 STATISTIK NOMBOR JERSI",
            description="Sistem Nombor Tetap (seperti nombor badan)",
            color=0xFFD700
        )

        embed.add_field(name="📊 Total Assigned", value=f"`{stats['total_assigned']}`", inline=True)
        embed.add_field(name="🔢 Highest Number", value=f"`#{stats['highest_number']}`", inline=True)
        embed.add_field(name="➡️ Next Available", value=f"`#{stats['next_available']}`", inline=True)

        if stats['available_gaps']:
            gaps_text = ", ".join([f"#{g}" for g in stats['available_gaps'][:5]])
            embed.add_field(name="🔍 Available Gaps", value=f"`{gaps_text}`", inline=False)

        embed.set_footer(text="💡 Setiap ahli mempunyai nombor jersi tetap sepanjang hayat")
        await ctx.send(embed=embed, delete_after=30)

    @commands.command(name="my_jersey")
    async def my_jersey_cmd(self, ctx):
        """🎽 Lihat nombor jersi sendiri"""
        db = self.bot.get_cog('DatabaseCog')
        user = await db.get_user(ctx.author.id)

        if not user or not user.get('jersey_number'):
            await ctx.send("❌ Anda tidak mempunyai nombor jersi lagi. Sila masuk voice channel untuk daftar.", delete_after=5)
            return

        jersey_num = user['jersey_number']
        assigned_at = user.get('jersey_assigned_at', 'Unknown')
        if isinstance(assigned_at, datetime):
            assigned_at = assigned_at.strftime("%d/%m/%Y")

        embed = discord.Embed(
            title="🎽 NOMBOR JERSI ANDA",
            color=0xFFD700
        )
        embed.add_field(name="Nombor", value=f"`#{jersey_num:02d}`", inline=True)
        embed.add_field(name="Diamalkan Sejak", value=f"`{assigned_at}`", inline=True)
        embed.set_footer(text="💡 Nombor ini tetap dan tidak akan berubah")

        await ctx.send(embed=embed, delete_after=30)

    @commands.command(name="find_by_jersey")
    async def find_by_jersey_cmd(self, ctx, jersey_number: int):
        """🔍 Cari ahli mengikut nombor jersi"""
        db = self.bot.get_cog('DatabaseCog')
        user = await db.get_user_by_jersey_number(jersey_number)

        if not user:
            await ctx.send(f"❌ Tiada ahli dengan nombor jersi `#{jersey_number:02d}`.", delete_after=5)
            return

        member = ctx.guild.get_member(user['user_id'])
        name = member.mention if member else user.get('display_name', 'Unknown')

        embed = discord.Embed(
            title=f"🔍 NOMBOR JERSI #{jersey_number:02d}",
            color=0x3498DB
        )
        embed.add_field(name="👤 Ahli", value=name, inline=True)
        embed.add_field(name="📝 Nama", value=f"`{user.get('display_name', 'Unknown')}`", inline=True)

        await ctx.send(embed=embed, delete_after=30)

    @commands.command(name="transfer_jersey")
    @commands.has_permissions(administrator=True)
    async def transfer_jersey_cmd(self, ctx, from_user: discord.Member, to_user: discord.Member):
        """🔄 Pindahkan nombor jersi (KEMAS KINIAN: Rare case sahaja)"""
        if from_user.id == to_user.id:
            await ctx.send("❌ Tidak boleh pindahkan ke diri sendiri!", delete_after=3)
            return

        confirm_msg = await ctx.send(
            f"⚠️ **AMARAN:** Anda akan memindahkan nombor jersi dari {from_user.mention} ke {to_user.mention}.\n"
            f"Ini adalah tindakan **KEKAL** dan jarang digunakan.\n\n"
            f"Taip `CONFIRM JERSEY` dalam 15 saat untuk teruskan."
        )

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content == "CONFIRM JERSEY"

        try:
            await self.bot.wait_for('message', check=check, timeout=15.0)
        except:
            await confirm_msg.edit(content="❌ Pindahan nombor jersi dibatalkan.", delete_after=3)
            return

        db = self.bot.get_cog('DatabaseCog')
        success, message = await db.transfer_jersey_number(from_user.id, to_user.id, ctx.author.id)

        if success:
            embed = discord.Embed(
                title="✅ Pindahan Berjaya",
                description=message,
                color=Config.COLOR_SUCCESS
            )
            await ctx.send(embed=embed)

            log = self.bot.get_cog('LoggerCog')
            if log:
                await log.log_admin_action(ctx.author, "Transfer Jersey", f"From {from_user.name} to {to_user.name}")
        else:
            await ctx.send(f"❌ {message}", delete_after=5)

    @commands.command(name="setup_admin_panel")
    @commands.has_permissions(administrator=True)
    async def setup_admin_panel_cmd(self, ctx):
        """Setup admin panel"""
        await ctx.message.delete()

        # NOTE: Ini hanyalah penerangan untuk admin. Tindakan sebenar dibuat melalui dropdown (AdminView).
        embed = discord.Embed(
            title="👑 ADMIN PANEL DKATANA 👑",
            description=(
                "📋 **Selamat datang ke Panel Pentadbiran!**\n\n"
                "Pilih tindakan dari **dropdown** di bawah untuk mengurus sistem.\n\n"
                "**⚙️ Kategori Tindakan:**\n"
                "⛱️ **Cuti** - Set cuti rasmi\n"
                "⏱️ **Masa** - Set masa voice dan kehadiran\n"
                "✏️ **Edit** - Edit kehadiran user\n"
                "⚠️ **Amaran** - Hantar warning\n"
                "🗄️ **Database** - Akses dan urus data\n"
                "👥 **Role** - Tukar role leaderboard\n"
                "🔄 **Refresh** - Update leaderboard\n"
                "📊 **Laporan** - Lihat statistik dan laporan (7 hari kebelakang)\n"
                "🔒 **Voice** - Kawal voice channel\n\n"
                "📌 *Semua tindakan direkodkan*"
            ),
            color=0x9B59B6
        )

        embed.add_field(
            name="📊 KEHADIRAN",
            value=(
                "⛱️ **Set Cuti Rasmi** — Tetapkan hari cuti rasmi (auto skip kehadiran).\n"
                "⏱️ **Set Masa VC** — Tetapkan masa minimum VC untuk dikira hadir.\n"
                "🕐 **Jam Open** — Masa mula ambil kehadiran.\n"
                "🕘 **Jam Closed** — Masa akhir/cutoff kehadiran.\n"
                "✏️ **Edit Kehadiran User** — Kemaskini status hadir ahli secara manual.\n"
                "🟡 **Pending Permohonan Cuti** — Urus permohonan cuti (Approve/Tolak/Padam) jika admin terlepas atau bot restart.\n"
                "📋 **Laporan Kehadiran Harian** — Pilih tarikh (7 hari) untuk lihat ringkasan & peratus.\n"
                "🔒🔓 **Lock/Unlock VC** — Kawal akses Voice Channel kehadiran.\n"
                "🔄 **Refresh Leaderboard Kehadiran** — Update paparan leaderboard terkini.\n"
                "📊 **Statistik Server** — Papar statistik ringkas (server/kehadiran)."
            ),
            inline=False
        )

        embed.add_field(
            name="💰 KUTIPAN",
            value=(
                "💰 **Edit Kutipan User** — Panel bendahari untuk kemaskini status kutipan (boleh pilih ramai sekali).\n"
                "📅 **Statistik Kutipan Mingguan** — Ringkasan kutipan minggu semasa.\n"
                "📈 **Statistik Kutipan Bulanan** — Ringkasan kutipan bulan semasa.\n\n"
                "📨 **Reminder Kutipan** — Hantar DM kepada semua ahli yang belum bayar (mesej boleh set).\n"
                "🧾 **Message Audit Duit** — Nota audit duit keluar (pilih minggu & hari).\n"
                "💸 **Edit Jumlah Kutipan** — Tetapkan jumlah yang perlu dibayar oleh ahli (update DB + .env).\n"
                "💸 **Edit Jumlah Penalty Kutipan** — Tetapkan jumlah denda jika gagal bayar (update DB + .env).\n\n"
                "🟩 **Simbol leaderboard:** ✅=Bayar  |  ⬜=Belum Bayar  |  🟨=Cuti  |  ⚠️=Denda"
            ),
            inline=False
        )

        embed.add_field(
            name="🧾 CMD ADMIN",
            value=(
                "`!setup_admin_panel` — Post message admin panel\n"
                "`!setup_leaderboard` — Post message leaderboard kehadiran\n"
                "`!setup_attendance` — Post message button kehadiran\n"
                "✅ Auto-Restart aktif (mengikut jadual dalam config/.env)"
            ),
            inline=False
        )

        embed.add_field(
            name="⚙️ SISTEM SERVER",
            value=(
                "⚠️ **Hantar Amaran** — Hantar DM amaran kepada ramai ahli (pilih user).\n"
                "🗄️ **Akses Database** — Cari/lihat/padam data ahli (ikut menu).\n"
                "👥 **Role Leaderboard** — Tukar role untuk leaderboard (kehadiran/kutipan).\n"
                "🎽 **Urus Jersey** — Semak/pindah nombor jersi ahli.\n"
                "🧹 **Bersihkan Data Lama** — Padam data lama / user tidak aktif (ikut tetapan)."
            ),
            inline=False
        )

        embed.add_field(
            name="🧭 Cara guna (ringkas)",
            value=(
                "1) Pilih tindakan dalam dropdown.\n"
                "2) Isi panel/modal (jika keluar).\n"
                "3) Tekan **Simpan/Update**.\n"
                "4) Jika berjaya, bot akan bagi notis (kadang-kadang *ephemeral*)."
            ),
            inline=False
        )
        embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)
        embed.set_footer(text="⚡ DKATANA Admin Panel | Akses Terhad • Gunakan dropdown di bawah")

        view = self.AdminView(self)
        await ctx.send(embed=embed, view=view)


# ========== VOICE LOCK CONTROL VIEW ==========

class DailyReportDateSelectorView(View):
    """View for selecting date for daily report (past week)"""
    def __init__(self, cog, dates):
        super().__init__(timeout=120)
        self.cog = cog
        self.dates = dates

        # Create select options for past week
        options = []
        for d in dates:
            label = f"{'📍 ' if d['is_today'] else ''}{d['display']} ({d['day_name']})"
            options.append(SelectOption(
                label=label[:100],  # Discord limit
                value=d['date_str'],
                description=f"Lihat laporan {d['display']}"
            ))

        select = Select(
            placeholder="📅 Pilih tarikh laporan...",
            options=options,
            min_values=1,
            max_values=1
        )
        select.callback = self.on_date_select
        self.add_item(select)

    async def on_date_select(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        selected_date = interaction.data["values"][0]
        date_info = next((d for d in self.dates if d['date_str'] == selected_date), None)

        if date_info:
            await self.cog.show_daily_attendance_report(
                interaction, 
                specific_date=selected_date,
                date_display=date_info['display']
            )

            # Log admin action
            log = interaction.client.get_cog('LoggerCog')
            if log:
                await log.log_admin_action(
                    interaction.user, 
                    "Laporan Harian", 
                    f"Lihat laporan kehadiran untuk tarikh {date_info['display']}"
                )


class VoiceLockControlView(View):
    """View for voice channel lock/unlock control"""
    def __init__(self, cog, channel, role):
        super().__init__(timeout=120)
        self.cog = cog
        self.channel = channel
        self.role = role

    @discord.ui.button(label="🔒 LOCK (Kunci)", style=discord.ButtonStyle.danger, row=0)
    async def lock_btn(self, interaction: discord.Interaction, button: Button):
        modal = VoiceLockReasonModal(self.cog, lock=True)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🔓 UNLOCK (Buka)", style=discord.ButtonStyle.success, row=0)
    async def unlock_btn(self, interaction: discord.Interaction, button: Button):
        modal = VoiceLockReasonModal(self.cog, lock=False)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="📋 Log Tindakan", style=discord.ButtonStyle.secondary, row=1)
    async def log_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        db = interaction.client.get_cog('DatabaseCog')
        if db:
            logs = await db.get_recent_voice_lock_actions(5)
            if logs:
                text = "**📋 Sejarah Lock/Unlock Terkini:**\n\n"
                for log in logs:
                    action_emoji = "🔒" if log.get("action") == "lock" else "🔓"
                    time_str = log.get("timestamp").strftime("%d/%m %H:%M") if log.get("timestamp") else "Unknown"
                    text += f"{action_emoji} `{time_str}` - **{log.get('admin_name', 'Unknown')}**"
                    if log.get("reason"):
                        text += f" *({log.get('reason')})*"
                    text += "\n"
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.followup.send("📭 Tiada rekod tindakan.", ephemeral=True)


class VoiceLockReasonModal(Modal):
    """Modal for entering reason when locking/unlocking"""
    reason = TextInput(
        label="Sebab (Optional)",
        placeholder="Masukkan sebab tindakan ini...",
        max_length=100,
        required=False
    )

    def __init__(self, cog, lock: bool):
        super().__init__(title="🔒 Lock Voice Channel" if lock else "🔓 Unlock Voice Channel")
        self.cog = cog
        self.lock = lock

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.cog.toggle_voice_lock(interaction, self.lock, self.reason.value or None)


# ========== DATABASE MENU VIEW ==========
class DatabaseMenuView(View):
    """View for database menu with buttons"""
    def __init__(self, cog):
        super().__init__(timeout=120)
        self.cog = cog

    @discord.ui.button(label="📋 Lihat Semua", style=discord.ButtonStyle.primary, row=0)
    async def view_all(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        await self.cog.show_all_users(interaction)

    @discord.ui.button(label="🔍 Cari User", style=discord.ButtonStyle.secondary, row=0)
    async def search_user(self, interaction: discord.Interaction, button: Button):
        modal = SearchUserModal(self.cog)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🗑️ Padam User", style=discord.ButtonStyle.danger, row=0)
    async def delete_user(self, interaction: discord.Interaction, button: Button):
        modal = DeleteUserModal(self.cog)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="➕ Tambah Cuti", style=discord.ButtonStyle.success, row=1)
    async def add_leave(self, interaction: discord.Interaction, button: Button):
        modal = AddLeaveModal(self.cog)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="⚙️ Settings", style=discord.ButtonStyle.secondary, row=1)
    async def view_settings(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        db = interaction.client.get_cog('DatabaseCog')
        settings = await db.get_guild_settings(Config.GUILD_ID)

        embed = discord.Embed(title="⚙️ Settings", color=0x3498DB)
        embed.add_field(name="⏱️ Voice Mins", value=f"`{settings.get('voice_required_minutes', 10)}`", inline=True)
        embed.add_field(name="🕐 Attendance Time", value=f"`{settings.get('attendance_time', '21:30')}`", inline=True)
        embed.add_field(name="⏰ Cutoff Time", value=f"`{settings.get('attendance_cutoff', '03:59')}`", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)


class RoleSelectView(View):
    def __init__(self, cog, options):
        super().__init__(timeout=60)
        self.cog = cog

        select = Select(placeholder="Pilih role...", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=False)

        role_id = int(interaction.data["values"][0])
        db = interaction.client.get_cog('DatabaseCog')
        await db.update_guild_settings(Config.GUILD_ID, {"leaderboard_role_id": role_id})

        role = interaction.guild.get_role(role_id)
        try:
            lb = interaction.client.get_cog('LeaderboardCog')
            if lb:
                await lb.update_leaderboard_channel()
        except Exception:
            logger.exception('role select refresh leaderboard failed')

        await self.cog.ui_reply(interaction, content=f"✅ Role leaderboard ditetapkan kepada **{role.name if role else 'Unknown'}**", embed=None, view=None)

        log = interaction.client.get_cog('LoggerCog')
        if log:
            await log.log_admin_action(interaction.user, "Tukar Role", role.name if role else 'Unknown')


# =============================
# AUDIT DUIT UI (WEEK + DAY + MODAL)
# =============================


class AuditDuitWeekSelect(Select):
    """Pilih minggu audit (Auto / Minggu 1-4)."""

    def __init__(self, cog: AdminPanelCog):
        self.cog = cog
        options = [
            SelectOption(label="Auto (ikut tarikh hari ini)", value="auto"),
            SelectOption(label="Minggu 1", value="1"),
            SelectOption(label="Minggu 2", value="2"),
            SelectOption(label="Minggu 3", value="3"),
            SelectOption(label="Minggu 4", value="4"),
        ]
        super().__init__(placeholder="Pilih Minggu (1-4)…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        v = self.values[0] if self.values else "auto"
        try:
            if hasattr(self.view, 'selected_week'):
                self.view.selected_week = v
        except Exception:
            pass
        # kecilkan friction - hanya acknowledge
        await interaction.response.defer(ephemeral=True)


class AuditDuitDaySelect(Select):
    def __init__(self, cog: AdminPanelCog):
        self.cog = cog
        options = [
            SelectOption(label="Isnin", value="ISNIN"),
            SelectOption(label="Selasa", value="SELASA"),
            SelectOption(label="Rabu", value="RABU"),
            SelectOption(label="Khamis", value="KHAMIS"),
            SelectOption(label="Jumaat", value="JUMAAT"),
            SelectOption(label="Sabtu", value="SABTU"),
            SelectOption(label="Ahad", value="AHAD"),
        ]
        super().__init__(placeholder="Pilih Hari (Isnin - Ahad)…", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        day_upper = (self.values[0] if self.values else "").strip().upper()

        # read week from view (default: auto)
        week_value = getattr(self.view, 'selected_week', 'auto')
        dt = self.cog.get_time()
        if str(week_value) == 'auto':
            _, week_no = self.cog._audit_week_key(dt)
        else:
            try:
                week_no = int(week_value)
            except Exception:
                _, week_no = self.cog._audit_week_key(dt)

        await interaction.response.send_modal(AuditDuitModal(self.cog, day_upper, week_no))


class AuditDuitSelectorView(View):
    def __init__(self, cog: AdminPanelCog):
        super().__init__(timeout=180)
        self.selected_week = 'auto'
        self.add_item(AuditDuitWeekSelect(cog))
        self.add_item(AuditDuitDaySelect(cog))


class AuditDuitModal(Modal):
    def __init__(self, cog: AdminPanelCog, day_upper: str, week_no: int):
        super().__init__(title=f"🧾 AUDIT DUIT — {day_upper} (M{week_no})")
        self.cog = cog
        self.day_upper = (day_upper or '').strip().upper()
        try:
            self.week_no = int(week_no)
        except Exception:
            self.week_no = 1

        self.detail = TextInput(
            label="Butiran duit keluar",
            placeholder="Contoh: RM50 beli air / RM120 topup / dll",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
        )
        self.add_item(self.detail)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.cog.submit_audit_duit(interaction, self.day_upper, self.detail.value, self.week_no)


# ========== MODALS ==========
class OfficialLeaveModal(Modal):
    days = TextInput(label="Hari (0-6, pisah koma)", placeholder="0,1 = Isnin,Selasa", max_length=20)

    def __init__(self, cog):
        super().__init__(title="⛱️ Set Cuti Rasmi")
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        day_map = {"0": "Isnin", "1": "Selasa", "2": "Rabu", "3": "Khamis", "4": "Jumaat", "5": "Sabtu", "6": "Ahad"}
        selected = [int(d.strip()) for d in self.days.value.split(",") if d.strip().isdigit()]
        selected = [d for d in selected if 0 <= d <= 6]

        db = interaction.client.get_cog('DatabaseCog')
        await db.update_guild_settings(Config.GUILD_ID, {"official_leave_days": selected})

        names = [day_map.get(str(d), str(d)) for d in selected]
        await interaction.followup.send(f"✅ Cuti rasmi: {', '.join(names) if names else 'Tiada'}", ephemeral=True)

        log = interaction.client.get_cog('LoggerCog')
        if log:
            await log.log_admin_action(interaction.user, "Set Cuti Rasmi", f"Hari: {', '.join(names) if names else 'Tiada'}")


class VoiceTimeModal(Modal):
    mins = TextInput(label="Masa (minit)", placeholder="10", max_length=3)

    def __init__(self, cog):
        super().__init__(title="⏱️ Set Masa Voice")
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            minutes = int(self.mins.value)
            db = interaction.client.get_cog('DatabaseCog')
            await db.update_guild_settings(Config.GUILD_ID, {"voice_required_minutes": minutes})
            await interaction.followup.send(f"✅ Masa voice: {minutes} minit", ephemeral=True)

            log = interaction.client.get_cog('LoggerCog')
            if log:
                await log.log_admin_action(interaction.user, "Set Masa Voice", f"{minutes} minit")
        except:
            await interaction.followup.send("❌ Nombor tidak sah!", ephemeral=True)


class AttendanceTimeModal(Modal):
    time = TextInput(label="Waktu (HH:MM)", placeholder="21:30", max_length=5)

    def __init__(self, cog):
        super().__init__(title="🕐 Set Waktu Kehadiran")
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        db = interaction.client.get_cog('DatabaseCog')
        await db.update_guild_settings(Config.GUILD_ID, {"attendance_time": self.time.value})
        await interaction.followup.send(f"✅ Waktu kehadiran: {self.time.value}", ephemeral=True)

        log = interaction.client.get_cog('LoggerCog')
        if log:
            await log.log_admin_action(interaction.user, "Set Waktu Kehadiran", self.time.value)


def _date_range_inclusive(start_dt: datetime, end_dt: datetime) -> list[str]:
    dates: list[str] = []
    cursor = start_dt
    while cursor <= end_dt:
        dates.append(cursor.strftime("%Y-%m-%d"))
        cursor += timedelta(days=1)
    return dates


class AttendanceEditUserSelect(Select):
    def __init__(self, parent_view: "AttendanceBulkEditView"):
        self.parent_view = parent_view
        options = parent_view.get_member_options_for_current_page()
        disabled = False
        if not options:
            options = [SelectOption(label="Tiada ahli DKATANA dijumpai", value="0", description="Semak AHLI_DKATANA_ROLE_ID")]
            disabled = True
        super().__init__(
            placeholder=f"Pilih ahli DKATANA (Page {parent_view.member_page + 1}/{max(1, parent_view.total_member_pages())})",
            min_values=1,
            max_values=max(1, min(25, len(options))),
            options=options,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.set_selected_from_values(self.values)
        await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)


class AttendancePageButton(Button):
    def __init__(self, parent_view: "AttendanceBulkEditView", direction: int):
        label = "⬅️ Sebelum" if direction < 0 else "Seterusnya ➡️"
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=1)
        self.parent_view = parent_view
        self.direction = direction

    async def callback(self, interaction: discord.Interaction):
        total_pages = self.parent_view.total_member_pages()
        if total_pages <= 1:
            await interaction.response.send_message("ℹ️ Senarai ahli hanya 1 page.", ephemeral=True)
            return
        self.parent_view.member_page = (self.parent_view.member_page + self.direction) % total_pages
        self.parent_view.rebuild_member_select()
        await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)


class AttendanceStatusSelect(Select):
    def __init__(self, parent_view: "AttendanceBulkEditView"):
        self.parent_view = parent_view
        super().__init__(
            placeholder="Pilih status kehadiran (🟢🔴🟡⚪)...",
            min_values=1,
            max_values=1,
            options=[
                SelectOption(label="HADIR 🟢", value="hadir", description="Set ahli sebagai hadir"),
                SelectOption(label="TIDAK HADIR 🔴", value="tidak_hadir", description="Set ahli sebagai tidak hadir"),
                SelectOption(label="CUTI 🟡", value="cuti", description="Set cuti ikut tarikh mula/tamat"),
                SelectOption(label="BELUM ⚪", value="belum_hadir", description="Reset status jadi belum"),
            ],
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_status = self.values[0]
        await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)


class AttendanceApplyButton(Button):
    def __init__(self, parent_view: "AttendanceBulkEditView"):
        super().__init__(label="📅 Setkan Tarikh Status", style=discord.ButtonStyle.success, row=3)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if not self.parent_view.selected_user_ids:
            await interaction.response.send_message("⚠️ Sila pilih sekurang-kurangnya 1 ahli.", ephemeral=True)
            return
        if not self.parent_view.selected_status:
            await interaction.response.send_message("⚠️ Sila pilih status dahulu.", ephemeral=True)
            return
        await interaction.response.send_modal(AttendanceDateStatusModal(self.parent_view))


class AttendanceOfficialLeaveButton(Button):
    def __init__(self, parent_view: "AttendanceBulkEditView"):
        super().__init__(label="🏝️ CUTI AM", style=discord.ButtonStyle.primary, row=3)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AttendanceOfficialLeaveModal(self.parent_view))


class AttendanceBulkEditCancelButton(Button):
    def __init__(self, parent_view: "AttendanceBulkEditView"):
        super().__init__(label="Batal", style=discord.ButtonStyle.secondary, row=3)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="❌ Edit kehadiran dibatalkan.", embed=None, view=None)


class AttendanceBulkEditView(View):
    def __init__(self, cog: "AdminPanelCog", author_id: int, guild: discord.Guild):
        super().__init__(timeout=300)
        self.cog = cog
        self.author_id = int(author_id)
        self.guild = guild
        self.selected_user_ids: list[int] = []
        self.selected_status: str | None = None
        self.member_page: int = 0
        self.page_size: int = 25
        self.role_member_ids: list[int] = self._get_role_member_ids()

        self.member_select = AttendanceEditUserSelect(self)
        self.add_item(self.member_select)
        self.add_item(AttendancePageButton(self, direction=-1))
        self.add_item(AttendancePageButton(self, direction=1))
        self.add_item(AttendanceStatusSelect(self))
        self.add_item(AttendanceApplyButton(self))
        self.add_item(AttendanceOfficialLeaveButton(self))
        self.add_item(AttendanceBulkEditCancelButton(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Panel ini bukan untuk anda.", ephemeral=True)
            return False
        if not self.cog.is_admin(interaction.user):
            await interaction.response.send_message("❌ Hanya admin!", ephemeral=True)
            return False
        return True

    def _get_role_member_ids(self) -> list[int]:
        role = self.guild.get_role(Config.AHLI_DKATANA_ROLE_ID)
        if not role:
            return []
        members = [m for m in role.members if not m.bot]
        members.sort(key=lambda x: x.display_name.lower())
        return [m.id for m in members]

    def total_member_pages(self) -> int:
        if not self.role_member_ids:
            return 1
        return max(1, (len(self.role_member_ids) + self.page_size - 1) // self.page_size)

    def get_member_options_for_current_page(self) -> list[SelectOption]:
        start = self.member_page * self.page_size
        end = start + self.page_size
        subset = self.role_member_ids[start:end]
        options: list[SelectOption] = []
        for uid in subset:
            m = self.guild.get_member(uid)
            if not m:
                continue
            options.append(
                SelectOption(
                    label=(m.display_name[:90] if m.display_name else str(uid)),
                    value=str(uid),
                    description=f"ID: {uid}"
                )
            )
        return options

    def rebuild_member_select(self):
        options = self.get_member_options_for_current_page()
        self.member_select.options = options or [SelectOption(label="Tiada ahli DKATANA dijumpai", value="0")]
        self.member_select.max_values = max(1, min(25, len(self.member_select.options)))
        self.member_select.placeholder = f"Pilih ahli DKATANA (Page {self.member_page + 1}/{max(1, self.total_member_pages())})"
        self.member_select.disabled = len(options) == 0

    def set_selected_from_values(self, values):
        chosen = []
        for val in values:
            try:
                uid = int(val)
            except Exception:
                continue
            if uid in self.role_member_ids:
                chosen.append(uid)
        self.selected_user_ids = list(dict.fromkeys(chosen))

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="✏️ Edit Kehadiran User (Multi Pilihan)",
            description=(
                "1) Pilih ahli DKATANA.\n"
                "2) Pilih status melalui dropdown.\n"
                "3) Klik butang **Setkan Tarikh Status** untuk kemaskini ahli dipilih.\n"
                "4) Atau klik **🏝️ CUTI AM** untuk set cuti am semua ahli DKATANA.\n"
                "5) Bot akan update leaderboard + database."
            ),
            color=Config.COLOR_INFO,
        )

        if self.selected_user_ids:
            preview = ", ".join(f"<@{uid}>" for uid in self.selected_user_ids[:10])
            more = "" if len(self.selected_user_ids) <= 10 else f" (+{len(self.selected_user_ids)-10} lagi)"
            embed.add_field(name=f"👥 Ahli dipilih ({len(self.selected_user_ids)})", value=f"{preview}{more}", inline=False)
        else:
            embed.add_field(name="👥 Ahli dipilih", value="Belum ada pilihan.", inline=False)

        embed.add_field(
            name="📄 Page Ahli",
            value=f"`{self.member_page + 1}/{self.total_member_pages()}` • Total ahli DKATANA: `{len(self.role_member_ids)}`",
            inline=False
        )

        status_label = {
            "hadir": "HADIR",
            "cuti": "CUTI",
            "belum_hadir": "BELUM HADIR",
            "tidak_hadir": "TIDAK HADIR",
            None: "Belum dipilih"
        }
        embed.add_field(name="📌 Status dipilih", value=f"`{status_label.get(self.selected_status, 'Belum dipilih')}`", inline=False)

        embed.set_footer(text="Admin Panel • Edit Kehadiran")
        return embed

    async def _apply_to_users(self, user_ids: list[int], dates: list[str], status_key: str) -> int:
        db = self.cog.bot.get_cog('DatabaseCog')
        lb = self.cog.bot.get_cog('LeaderboardCog')
        log = self.cog.bot.get_cog('LoggerCog')
        if not db:
            return 0

        changed = 0
        for uid in user_ids:
            try:
                if status_key == "belum_hadir":
                    await asyncio.to_thread(
                        db.db.attendance.delete_many,
                        {"user_id": int(uid), "date": {"$in": dates}}
                    )
                elif len(dates) == 1:
                    await db.mark_attendance(int(uid), dates[0], status_key)
                else:
                    await db.bulk_mark_attendance(int(uid), dates, status_key)
                changed += 1
            except Exception as e:
                logger.warning(f"Gagal update attendance uid={uid}: {e}")

        if lb and changed > 0:
            # Force refresh terus selepas admin edit attendance
            # supaya leaderboard channel terus menunjukkan data terkini.
            try:
                await lb.update_leaderboard_channel(force_refresh=True)
            except Exception as e:
                logger.warning(f"Leaderboard immediate update gagal, fallback debounce: {e}")
                try:
                    lb.request_leaderboard_update(delay=0.2)
                except Exception:
                    pass

        if log and changed > 0:
            users = ", ".join(str(x) for x in user_ids[:30])
            detail = f"Status={status_key}, Tarikh={dates[0]}..{dates[-1]}, Users=[{users}]"
            admin_member = self.guild.get_member(self.author_id)
            if admin_member:
                await log.log_admin_action(admin_member, "Edit Kehadiran (Multi)", detail)

        return changed


class AttendanceDateStatusModal(Modal):
    start_date = TextInput(label="Tarikh Mula (DD/MM/YYYY)", placeholder="20/03/2026", max_length=10)
    end_date = TextInput(label="Tarikh Tamat (DD/MM/YYYY) - Optional", placeholder="25/03/2026", required=False, max_length=10)

    def __init__(self, parent_view: AttendanceBulkEditView):
        super().__init__(title="📅 Kemaskini Tarikh Status")
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        status_key = self.parent_view.selected_status
        if not status_key:
            await interaction.followup.send("❌ Status belum dipilih.", ephemeral=True)
            return

        try:
            start_dt = datetime.strptime(self.start_date.value.strip(), "%d/%m/%Y")
            end_raw = (self.end_date.value or "").strip()
            end_dt = datetime.strptime(end_raw, "%d/%m/%Y") if end_raw else start_dt
        except Exception:
            await interaction.followup.send("❌ Format tarikh tidak sah. Guna DD/MM/YYYY.", ephemeral=True)
            return

        if end_dt < start_dt:
            await interaction.followup.send("❌ Tarikh tamat cuti tidak boleh lebih awal daripada tarikh mula.", ephemeral=True)
            return

        dates = _date_range_inclusive(start_dt, end_dt)
        changed = await self.parent_view._apply_to_users(self.parent_view.selected_user_ids, dates, status_key)
        status_label = {
            "hadir": "HADIR",
            "cuti": "CUTI",
            "belum_hadir": "BELUM HADIR",
            "tidak_hadir": "TIDAK HADIR",
        }.get(status_key, status_key.upper())
        await interaction.followup.send(
            f"✅ Kemaskini selesai: **{changed}** ahli ditanda **{status_label}** dari `{dates[0]}` hingga `{dates[-1]}`.",
            ephemeral=True
        )


class AttendanceOfficialLeaveModal(Modal):
    start_date = TextInput(label="Tarikh Mula CUTI AM (DD/MM/YYYY)", placeholder="20/03/2026", max_length=10)
    end_date = TextInput(label="Tarikh Tamat CUTI AM (DD/MM/YYYY)", placeholder="22/03/2026", max_length=10)

    def __init__(self, parent_view: AttendanceBulkEditView):
        super().__init__(title="🏝️ Set CUTI AM Semua Ahli DKATANA")
        self.parent_view = parent_view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            start_dt = datetime.strptime(self.start_date.value.strip(), "%d/%m/%Y")
            end_dt = datetime.strptime(self.end_date.value.strip(), "%d/%m/%Y")
        except Exception:
            await interaction.followup.send("❌ Format tarikh tidak sah. Guna DD/MM/YYYY.", ephemeral=True)
            return

        if end_dt < start_dt:
            await interaction.followup.send("❌ Tarikh tamat tidak boleh lebih awal daripada tarikh mula.", ephemeral=True)
            return

        role_user_ids = self.parent_view._get_role_member_ids()
        if not role_user_ids:
            await interaction.followup.send("❌ Tiada ahli DKATANA dijumpai untuk dikemaskini.", ephemeral=True)
            return

        dates = _date_range_inclusive(start_dt, end_dt)
        changed = await self.parent_view._apply_to_users(role_user_ids, dates, "cuti_am")
        await interaction.followup.send(
            f"✅ CUTI AM berjaya ditetapkan untuk **{changed}** ahli DKATANA dari `{dates[0]}` hingga `{dates[-1]}`.",
            ephemeral=True
        )


# ==================== WARNING (MULTI USER) ====================

class WarningUserSelect(UserSelect):
    def __init__(self, parent_view: "WarningMultiUserView"):
        super().__init__(
            placeholder="Pilih ahli DKATANA (boleh pilih ramai)",
            min_values=1,
            max_values=25,
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        # Filter hanya ahli dalam role DKATANA
        self.parent_view.set_selected_from_values(interaction)
        await interaction.response.edit_message(embed=self.parent_view.build_embed(), view=self.parent_view)


class WarningComposeButton(Button):
    def __init__(self, parent_view: "WarningMultiUserView"):
        super().__init__(label="✍️ Tulis Amaran", style=discord.ButtonStyle.danger)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        if not self.parent_view.selected_user_ids:
            return await interaction.response.send_message("⚠️ Sila pilih sekurang-kurangnya 1 ahli.", ephemeral=True)
        await interaction.response.send_modal(WarningMessageModal(self.parent_view.cog, self.parent_view.selected_user_ids))


class WarningCancelButton(Button):
    def __init__(self, parent_view: "WarningMultiUserView"):
        super().__init__(label="Batal", style=discord.ButtonStyle.secondary)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="❌ Dibatalkan.", embed=None, view=None)


class WarningMultiUserView(View):
    def __init__(self, cog: "AdminPanelCog", author_id: int, guild: discord.Guild):
        super().__init__(timeout=180)
        self.cog = cog
        self.author_id = int(author_id)
        self.guild = guild
        self.selected_user_ids: list[int] = []
        self.filtered_out: list[str] = []

        self.add_item(WarningUserSelect(self))
        self.add_item(WarningComposeButton(self))
        self.add_item(WarningCancelButton(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Elak orang lain guna view ini
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ Panel ini bukan untuk anda.", ephemeral=True)
            return False
        # Extra safety: admin sahaja
        if not self.cog.is_admin(interaction.user):
            await interaction.response.send_message("❌ Hanya admin!", ephemeral=True)
            return False
        return True

    def set_selected_from_values(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(Config.AHLI_DKATANA_ROLE_ID) if interaction.guild else None
        self.filtered_out = []
        chosen: list[int] = []

        # self.children[0] ialah UserSelect
        select: WarningUserSelect | None = None
        for ch in self.children:
            if isinstance(ch, WarningUserSelect):
                select = ch
                break

        values = getattr(select, 'values', []) if select else []
        for u in values:
            uid = int(getattr(u, 'id', 0) or 0)
            if not uid:
                continue
            m = interaction.guild.get_member(uid) if interaction.guild else None
            if role and m and role not in m.roles:
                self.filtered_out.append(m.display_name)
                continue
            chosen.append(uid)

        # unique preserve order
        seen = set()
        self.selected_user_ids = []
        for uid in chosen:
            if uid not in seen:
                self.selected_user_ids.append(uid)
                seen.add(uid)

    def build_embed(self) -> discord.Embed:
        emb = discord.Embed(
            title="⚠️ Hantar Amaran (Multi User)",
            description=(
                "Pilih ahli DKATANA yang ingin dihantar amaran, kemudian tekan **Tulis Amaran**.\n\n"
                "📌 Amaran akan dihantar ke **DM** ahli yang dipilih dalam bentuk embed."
            ),
            color=0xFFA500,
        )
        if self.selected_user_ids:
            preview = ", ".join(f"<@{uid}>" for uid in self.selected_user_ids[:10])
            more = "" if len(self.selected_user_ids) <= 10 else f" (+{len(self.selected_user_ids)-10} lagi)"
            emb.add_field(name="✅ Dipilih", value=preview + more, inline=False)
        else:
            emb.add_field(name="ℹ️ Dipilih", value="Belum ada ahli dipilih.", inline=False)

        if self.filtered_out:
            cut = ", ".join(self.filtered_out[:10])
            more = "" if len(self.filtered_out) <= 10 else f" (+{len(self.filtered_out)-10} lagi)"
            emb.add_field(
                name="🚫 Dibuang (bukan role ahli)",
                value=cut + more,
                inline=False,
            )
        return emb


class WarningMessageModal(Modal):
    reason = TextInput(
        label="Mesej Amaran",
        placeholder="Tulis amaran untuk ahli DKATANA di sini...",
        max_length=1200,
        style=discord.TextStyle.paragraph,
        required=True,
    )

    def __init__(self, cog: "AdminPanelCog", target_user_ids: list[int]):
        super().__init__(title="⚠️ WARNING MESSAGE DKATANA")
        self.cog = cog
        self.target_user_ids = [int(x) for x in (target_user_ids or [])]

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        ok, fail = await self.cog.send_warning_bulk(interaction, self.target_user_ids, str(self.reason.value))
        msg = f"✅ Amaran dihantar. Berjaya: **{ok}** | Gagal: **{fail}**"
        await interaction.followup.send(msg, ephemeral=True)


class WarningModal(Modal):
    user_id = TextInput(label="User ID", placeholder="123456789", max_length=20)
    reason = TextInput(label="Sebab Amaran", placeholder="Sebab amaran", max_length=200, style=discord.TextStyle.paragraph)

    def __init__(self, cog):
        super().__init__(title="⚠️ Hantar Amaran")
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            uid = int(self.user_id.value)
            user = interaction.client.get_user(uid)

            if user:
                embed = discord.Embed(
                    title="⚠️ AMARAN RASMI",
                    description=f"Anda menerima amaran:\n```{self.reason.value}```",
                    color=0xFFA500
                )
                embed.add_field(name="👤 Dihantar oleh", value=interaction.user.mention)
                await user.send(embed=embed)
                await interaction.followup.send(f"✅ Amaran dihantar kepada {user.mention}", ephemeral=True)

                log = interaction.client.get_cog('LoggerCog')
                if log:
                    await log.log_admin_action(interaction.user, "Hantar Amaran", f"Kepada: {user.display_name}, Sebab: {self.reason.value}")
            else:
                await interaction.followup.send("❌ User tidak dijumpaikan!", ephemeral=True)
        except:
            await interaction.followup.send("❌ User ID tidak sah!", ephemeral=True)


class CutoffModal(Modal):
    time = TextInput(label="Waktu Cutoff (HH:MM)", placeholder="03:59", max_length=5)

    def __init__(self, cog):
        super().__init__(title="⏰ Set Masa Cutoff")
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        db = interaction.client.get_cog('DatabaseCog')
        await db.update_guild_settings(Config.GUILD_ID, {"attendance_cutoff": self.time.value})
        await interaction.followup.send(f"✅ Waktu cutoff: {self.time.value}", ephemeral=True)

        log = interaction.client.get_cog('LoggerCog')
        if log:
            await log.log_admin_action(interaction.user, "Set Cutoff Time", self.time.value)


class SearchUserModal(Modal):
    user_id = TextInput(label="User ID atau Nama", placeholder="123456789 atau nama user", max_length=50)

    def __init__(self, cog):
        super().__init__(title="🔍 Cari User")
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            db = interaction.client.get_cog('DatabaseCog')

            if self.user_id.value.isdigit():
                user_data = await db.get_user(int(self.user_id.value))
            else:
                users = await db.get_all_ahli_dkatana()
                user_data = None
                for u in users:
                    if self.user_id.value.lower() in u.get('display_name', '').lower():
                        user_data = u
                        break

            if user_data:
                embed = discord.Embed(title="🔍 Maklumat User", color=0x3498DB)

                # Get jersey number (fixed)
                jersey = user_data.get('jersey_number', user_data.get('leaderboard_number', 'N/A'))

                embed.add_field(name="🎽 Nombor Jersi", value=f"`#{jersey:02d}`", inline=True)
                embed.add_field(name="User ID", value=f"`{user_data['user_id']}`", inline=True)
                embed.add_field(name="Username", value=f"`{user_data.get('username', 'N/A')}`", inline=True)
                embed.add_field(name="Role Ahli", value=f"`{'Ya' if user_data.get('role_ahli') else 'Tidak'}`", inline=True)
                embed.set_footer(text="🎽 Nombor Jersi = Tetap (seperti nombor badan)")
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send("❌ User tidak dijumpai!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Ralat: {e}", ephemeral=True)


class DeleteUserModal(Modal):
    user_id = TextInput(label="User ID", placeholder="123456789", max_length=20)
    confirm = TextInput(label="Taip 'CONFIRM' untuk padam", placeholder="CONFIRM", max_length=10)

    def __init__(self, cog):
        super().__init__(title="🗑️ Padam User")
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if self.confirm.value != "CONFIRM":
            await interaction.followup.send("❌ Pengesahan tidak betul!", ephemeral=True)
            return

        try:
            uid = int(self.user_id.value)
            db = interaction.client.get_cog('DatabaseCog')
            await db.delete_user(uid)
            await interaction.followup.send(f"✅ User {uid} telah dipadam!", ephemeral=True)

            log = interaction.client.get_cog('LoggerCog')
            if log:
                await log.log_admin_action(interaction.user, "Padam User", f"User ID: {uid}")
        except Exception as e:
            await interaction.followup.send(f"❌ Ralat: {e}", ephemeral=True)


class AddLeaveModal(Modal):
    user_id = TextInput(label="User ID", placeholder="123456789", max_length=20)
    tarikh_cuti = TextInput(label="TARIKH CUTI (DD/MM/YYYY)", placeholder="15/02/2026", max_length=10)
    tarikh_tamat = TextInput(label="TARIKH TAMAT (DD/MM/YYYY)", placeholder="17/02/2026", max_length=10)
    sebab = TextInput(label="SEBAB", placeholder="Sebab cuti", max_length=200)

    def __init__(self, cog):
        super().__init__(title="➕ Tambah Cuti")
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            uid = int(self.user_id.value)
            db = interaction.client.get_cog('DatabaseCog')
            user = await db.get_user(uid)

            if not user:
                await interaction.followup.send("❌ User tidak dijumpai!", ephemeral=True)
                return

            leave = await db.create_leave_request(
                uid, user.get('display_name', 'Unknown'),
                self.tarikh_cuti.value, self.tarikh_tamat.value, self.sebab.value,
                user.get('leaderboard_number', 0)
            )

            await db.update_leave_request(leave['_id'], "approved", interaction.user.id)

            await interaction.followup.send(f"✅ Cuti ditambah untuk user {uid}!")

            log = interaction.client.get_cog('LoggerCog')
            if log:
                await log.log_admin_action(interaction.user, "Tambah Cuti", f"User: {uid}, Tarikh: {self.tarikh_cuti.value} - {self.tarikh_tamat.value}")
        except Exception as e:
            await interaction.followup.send(f"❌ Ralat: {e}", ephemeral=True)




# ========== JERSEY MANAGEMENT VIEWS ==========
class JerseyMenuView(View):
    """View for jersey management menu"""
    def __init__(self, cog):
        super().__init__(timeout=120)
        self.cog = cog

    @discord.ui.button(label="📊 Statistik", style=discord.ButtonStyle.primary, row=0)
    async def stats_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        await self.cog.show_jersey_stats(interaction)

    @discord.ui.button(label="🔄 Pindah Jersi", style=discord.ButtonStyle.success, row=0)
    async def transfer_btn(self, interaction: discord.Interaction, button: Button):
        await self.cog.show_transfer_jersey_modal(interaction)

    @discord.ui.button(label="📋 Senarai Ahli", style=discord.ButtonStyle.secondary, row=1)
    async def list_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        await self.cog.show_all_users(interaction)

    @discord.ui.button(label="❌ Tutup", style=discord.ButtonStyle.danger, row=1)
    async def close_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content="🔒 Menu ditutup.", embed=None, view=None)


class ConfirmJerseyTransferView(View):
    """View for confirming jersey transfer"""
    def __init__(self, cog, from_user_id: int, to_user_id: int, jersey_number: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.from_user_id = from_user_id
        self.to_user_id = to_user_id
        self.jersey_number = jersey_number

    @discord.ui.button(label="✅ Sahkan Pindahan", style=discord.ButtonStyle.success)
    async def confirm_btn(self, interaction: discord.Interaction, button: Button):
        await self.cog.execute_jersey_transfer(interaction, self.from_user_id, self.to_user_id, self.jersey_number)
        self.stop()

    @discord.ui.button(label="❌ Batal", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content="❌ Pindahan jersi dibatalkan.", embed=None, view=None)
        self.stop()


class TransferJerseyModal(Modal, title="🔄 Pindah Nombor Jersi"):
    """Modal for transferring jersey number"""
    from_user_id = TextInput(label="ID User Asal", placeholder="123456789", max_length=20)
    to_user_id = TextInput(label="ID User Baru", placeholder="987654321", max_length=20)
    confirm = TextInput(label="Taip 'TRANSFER' untuk sahkan", placeholder="TRANSFER", max_length=10)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.upper() != "TRANSFER":
            await interaction.response.send_message("❌ Pengesahan tidak betul! Taip 'TRANSFER' untuk sahkan.", ephemeral=True)
            return

        try:
            from_id = int(self.from_user_id.value)
            to_id = int(self.to_user_id.value)
            await self.cog.transfer_jersey(interaction, from_id, to_id)
        except ValueError:
            await interaction.response.send_message("❌ ID User mesti nombor!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Ralat: {str(e)}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdminPanelCog(bot))

class PendingLeaveManageView(discord.ui.View):
    def __init__(self, admin_panel_cog: AdminPanelCog, pending_requests: list[dict]):
        super().__init__(timeout=300)
        self.cog = admin_panel_cog
        self.pending_requests = pending_requests
        self.selected_req: dict | None = None

        options = []
        for req in pending_requests[:25]:
            jersey = req.get('jersey_number')
            uid = req.get('user_id')
            start = req.get('start_date')
            end = req.get('end_date')
            label = f"#{int(jersey):02d} | {uid}" if jersey is not None else str(uid)
            desc = f"{start} → {end}" if start and end else "Permohonan cuti"
            options.append(discord.SelectOption(label=label[:100], value=str(req.get('request_id')), description=desc[:100]))

        self.sel = discord.ui.Select(
            placeholder='🟡 Pilih permohonan cuti (Pending)...',
            options=options,
            min_values=1,
            max_values=1,
        )
        self.sel.callback = self._on_select
        self.add_item(self.sel)

        self.btn_approve = discord.ui.Button(label='✅ Approve', style=discord.ButtonStyle.success, disabled=True)
        self.btn_reject = discord.ui.Button(label='❌ Tolak', style=discord.ButtonStyle.danger, disabled=True)
        self.btn_delete = discord.ui.Button(label='🗑️ Padam', style=discord.ButtonStyle.secondary, disabled=True)

        self.btn_approve.callback = self._on_approve
        self.btn_reject.callback = self._on_reject
        self.btn_delete.callback = self._on_delete

        self.add_item(self.btn_approve)
        self.add_item(self.btn_reject)
        self.add_item(self.btn_delete)

    def build_embed(self) -> discord.Embed:
        e = discord.Embed(title='🟡 Pending Permohonan Cuti', color=0xF1C40F)
        if self.selected_req:
            r = self.selected_req
            e.description = (
                f"👤 **User:** <@{r.get('user_id')}>\n"
                f"🎽 **Jersey:** {r.get('jersey_number', '-')}\n"
                f"📅 **Tarikh:** {r.get('start_date')} → {r.get('end_date')}\n"
                f"📝 **Sebab:** {r.get('reason') or '-'}\n"
                f"🆔 **Request ID:** `{r.get('request_id')}`"
            )
        else:
            e.description = 'Pilih permohonan di dropdown untuk lihat detail dan buat tindakan.'
        e.set_footer(text=f'Jumlah pending: {len(self.pending_requests)} | Timeout: 5 min')
        return e

    async def _reload(self):
        db = self.cog.bot.get_cog('DatabaseCog')
        if not db:
            return
        self.pending_requests = await db.get_pending_leave_requests()
        opts=[]
        for req in self.pending_requests[:25]:
            jersey = req.get('jersey_number')
            uid = req.get('user_id')
            start = req.get('start_date')
            end = req.get('end_date')
            label = f"#{int(jersey):02d} | {uid}" if jersey is not None else str(uid)
            desc = f"{start} → {end}" if start and end else 'Permohonan cuti'
            opts.append(discord.SelectOption(label=label[:100], value=str(req.get('request_id')), description=desc[:100]))
        if not opts:
            self.sel.options=[discord.SelectOption(label='(Tiada lagi pending)', value='none')]
            self.sel.disabled=True
            self.btn_approve.disabled=True
            self.btn_reject.disabled=True
            self.btn_delete.disabled=True
            self.selected_req=None
        else:
            self.sel.options=opts

    async def _on_select(self, interaction: discord.Interaction):
        rid = self.sel.values[0]
        self.selected_req = next((r for r in self.pending_requests if str(r.get('request_id')) == rid), None)
        en = self.selected_req is not None
        self.btn_approve.disabled = not en
        self.btn_reject.disabled = not en
        self.btn_delete.disabled = not en
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_approve(self, interaction: discord.Interaction):
        if not self.selected_req:
            return await interaction.response.send_message('❌ Sila pilih permohonan dahulu.', ephemeral=True)
        db = self.cog.bot.get_cog('DatabaseCog')
        if not db:
            return await interaction.response.send_message('❌ DatabaseCog tidak dijumpai.', ephemeral=True)
        ok_doc = await db.set_leave_status_if_pending(self.selected_req.get('request_id'), 'approved', interaction.user.id)
        ok = ok_doc is not None
        if ok:
            await self._reload()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
            await interaction.followup.send('✅ Permohonan cuti telah **DILULUSKAN**.', ephemeral=True)
        else:
            await interaction.response.send_message('❌ Gagal approve (mungkin sudah diproses).', ephemeral=True)

    async def _on_reject(self, interaction: discord.Interaction):
        if not self.selected_req:
            return await interaction.response.send_message('❌ Sila pilih permohonan dahulu.', ephemeral=True)
        db = self.cog.bot.get_cog('DatabaseCog')
        if not db:
            return await interaction.response.send_message('❌ DatabaseCog tidak dijumpai.', ephemeral=True)
        ok_doc = await db.set_leave_status_if_pending(self.selected_req.get('request_id'), 'rejected', interaction.user.id)
        ok = ok_doc is not None
        if ok:
            await self._reload()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
            await interaction.followup.send('✅ Permohonan cuti telah **DITOLAK**.', ephemeral=True)
        else:
            await interaction.response.send_message('❌ Gagal tolak (mungkin sudah diproses).', ephemeral=True)

    async def _on_delete(self, interaction: discord.Interaction):
        if not self.selected_req:
            return await interaction.response.send_message('❌ Sila pilih permohonan dahulu.', ephemeral=True)
        db = self.cog.bot.get_cog('DatabaseCog')
        if not db:
            return await interaction.response.send_message('❌ DatabaseCog tidak dijumpai.', ephemeral=True)
        ok = await db.delete_leave_request(self.selected_req.get('request_id'))
        if ok:
            await self._reload()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
            await interaction.followup.send('✅ Permohonan cuti telah **DIPADAM**.', ephemeral=True)
        else:
            await interaction.response.send_message('❌ Gagal padam (mungkin sudah diproses).', ephemeral=True)
