import asyncio
import logging
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, TextInput
import pytz

from config import Config

logger = logging.getLogger('DkatanaBot')


class AttendanceView(View):
    """View for attendance buttons (persistent)."""

    def __init__(self, cog: "AttendanceCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="HADIR 🟢", style=discord.ButtonStyle.success, custom_id="btn_hadir")
    async def hadir_btn(self, interaction: discord.Interaction, button: Button):
        await self.cog.handle_hadir(interaction)

    @discord.ui.button(label="APPLY CUTI 🟡", style=discord.ButtonStyle.primary, custom_id="btn_cuti")
    async def cuti_btn(self, interaction: discord.Interaction, button: Button):
        await self.cog.handle_cuti(interaction)


class LeaveModal(Modal):
    """Modal for leave application.

    Compatible with old calls:
      - LeaveModal(cog, jersey_number)
      - LeaveModal(cog, user, jersey_number)
    """

    def __init__(self, cog: "AttendanceCog", *args):
        super().__init__(title="📝 Borang Permohonan Cuti")
        self.cog = cog

        user = None
        jersey_number = 0

        if len(args) == 1:
            jersey_number = args[0]
        elif len(args) >= 2:
            user = args[0]
            jersey_number = args[1]

        self.user = user
        try:
            self.jersey_number = int(jersey_number)
        except Exception:
            self.jersey_number = 0

        default_name = user.display_name if user else ""

        self.nama = TextInput(label="NAMA", placeholder="Nama anda", default=default_name[:50], max_length=50, required=True)
        self.tarikh_cuti = TextInput(label="TARIKH MULA (DD/MM/YYYY)", placeholder="15/02/2026", max_length=10, required=True)
        self.tarikh_tamat = TextInput(label="TARIKH TAMAT (DD/MM/YYYY)", placeholder="17/02/2026", max_length=10, required=True)
        self.sebab = TextInput(label="SEBAB", placeholder="Sebab bercuti", max_length=200, style=discord.TextStyle.paragraph, required=True)
        self.no_jersei = TextInput(label="🎽 NOMBOR JERSI (AUTO)", default=str(self.jersey_number), max_length=5, required=True)

        self.add_item(self.nama)
        self.add_item(self.tarikh_cuti)
        self.add_item(self.tarikh_tamat)
        self.add_item(self.sebab)
        self.add_item(self.no_jersei)

    async def on_submit(self, interaction: discord.Interaction):
        # Abaikan input jersey jika user ubah - guna nombor dari sistem
        await self.cog.submit_cuti(
            interaction,
            self.nama.value,
            self.tarikh_cuti.value,
            self.tarikh_tamat.value,
            self.sebab.value,
            str(self.jersey_number),
        )


class AttendanceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # voice tracker per user
        # {
        #   user_id: {
        #      accumulated: float,  # seconds (dalam VC verify sahaja)
        #      join: datetime | None,
        #      in_voice: bool,
        #      msg_id: int | None,
        #      task: asyncio.Task | None,
        #   }
        # }
        self.voice_tracker: dict[int, dict] = {}

        # jersey DM temp (optional)
        self.jersey_dm_msgs: dict[int, int] = {}

        self.check_attendance.start()

    def cog_unload(self):
        self.check_attendance.cancel()

    # ---------- time helpers ----------
    def get_time(self) -> datetime:
        return datetime.now(pytz.timezone(Config.TIMEZONE))

    def get_attendance_date(self) -> str:
        """Get attendance date based on current time.

        Attendance window crosses midnight:
        - If time is 00:00 - cutoff (03:59), attendance is for yesterday.
        - Else, attendance is for today.
        """
        current = self.get_time()
        current_time = current.time()

        cutoff_h, cutoff_m = map(int, Config.ATTENDANCE_CUTOFF_TIME.split(':'))
        cutoff_time = current.replace(hour=cutoff_h, minute=cutoff_m, second=0, microsecond=0).time()

        if current_time <= cutoff_time:
            return (current - timedelta(days=1)).strftime("%Y-%m-%d")
        return current.strftime("%Y-%m-%d")

    def is_attendance_time(self) -> bool:
        """Check if current time within attendance window (start -> cutoff, crosses midnight)."""
        current = self.get_time()
        current_time = current.time()

        start_h, start_m = map(int, Config.ATTENDANCE_TIME.split(':'))
        start_time = current.replace(hour=start_h, minute=start_m, second=0, microsecond=0).time()

        cutoff_h, cutoff_m = map(int, Config.ATTENDANCE_CUTOFF_TIME.split(':'))
        cutoff_time = current.replace(hour=cutoff_h, minute=cutoff_m, second=0, microsecond=0).time()

        return (current_time >= start_time) or (current_time <= cutoff_time)

    def get_day_name(self, date_str: str) -> str:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        days = ['Isnin', 'Selasa', 'Rabu', 'Khamis', 'Jumaat', 'Sabtu', 'Ahad']
        return days[dt.weekday()]

    async def is_official_leave(self, date_str: str | None = None) -> bool:
        """Check if date is official leave day (set by admin)."""
        if date_str is None:
            date_str = self.get_attendance_date()
        dt = datetime.strptime(date_str, "%Y-%m-%d")

        db = self.bot.get_cog('DatabaseCog')
        if not db:
            return False
        settings = await db.get_guild_settings(Config.GUILD_ID)
        return dt.weekday() in (settings.get("official_leave_days", []) or [])

    # ---------- voice tracking ----------
    def _get_tracker(self, user_id: int) -> dict:
        if user_id not in self.voice_tracker:
            self.voice_tracker[user_id] = {
                'accumulated': 0.0,
                'join': None,
                'in_voice': False,
                'msg_id': None,
                'task': None,
            }
        return self.voice_tracker[user_id]

    def _voice_seconds(self, user_id: int) -> float:
        tr = self.voice_tracker.get(user_id)
        if not tr:
            return 0.0
        acc = float(tr.get('accumulated', 0.0) or 0.0)
        join = tr.get('join')
        in_voice = bool(tr.get('in_voice'))
        if in_voice and isinstance(join, datetime):
            acc += (self.get_time() - join).total_seconds()
        return max(0.0, acc)

    def get_voice_mins(self, user_id: int) -> int:
        return int(self._voice_seconds(user_id) // 60)

    def has_voice_time(self, user_id: int) -> bool:
        return self.get_voice_mins(user_id) >= int(getattr(Config, 'VOICE_REQUIRED_MINUTES', 0) or 0)

    async def _update_voice_embed(self, member: discord.Member, *, status: str, pct: int, bar: str, desc: str, color: int) -> None:
        """Create or edit voice tracking DM message."""
        user_id = member.id
        tr = self._get_tracker(user_id)

        try:
            dm = await member.create_dm()
        except Exception:
            return

        embed = discord.Embed(
            title="🎤 Voice Tracking - Kehadiran",
            description=desc,
            color=color,
        )
        embed.add_field(name="📊 Status", value=f"`{status}`", inline=True)
        embed.add_field(name="Progress", value=f"`{bar}` **{pct}%**", inline=False)
        embed.set_footer(text=f"Masa minimum: {Config.VOICE_REQUIRED_MINUTES} minit")

        msg_id = tr.get('msg_id')
        if msg_id:
            try:
                msg = await dm.fetch_message(int(msg_id))
                await msg.edit(embed=embed)
                return
            except discord.NotFound:
                tr['msg_id'] = None
            except Exception:
                # fallthrough to send new
                tr['msg_id'] = None

        try:
            msg = await dm.send(embed=embed)
            tr['msg_id'] = msg.id
        except Exception:
            return

    async def _delete_voice_dm(self, member: discord.Member) -> None:
        """Delete voice tracking DM message (lepas mark hadir)."""
        tr = self.voice_tracker.get(member.id)
        if not tr:
            return
        msg_id = tr.get('msg_id')
        if not msg_id:
            return
        try:
            dm = await member.create_dm()
            msg = await dm.fetch_message(int(msg_id))
            await msg.delete()
        except Exception:
            pass
        finally:
            tr['msg_id'] = None

    async def _start_or_resume_tracking(self, member: discord.Member) -> None:
        user_id = member.id
        tr = self._get_tracker(user_id)

        # cancel old task (kalau ada) untuk elak duplicate edit
        old_task = tr.get('task')
        if isinstance(old_task, asyncio.Task) and not old_task.done():
            old_task.cancel()

        tr['join'] = self.get_time()
        tr['in_voice'] = True

        tr['task'] = asyncio.create_task(self.track_progress(member))

    async def handle_voice_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Call this from on_voice_state_update in main."""
        if member.bot:
            return

        vc_id = int(getattr(Config, 'VOICE_ATTENDANCE_CHANNEL_ID', 0) or 0)
        if not vc_id:
            return

        before_id = before.channel.id if before.channel else None
        after_id = after.channel.id if after.channel else None

        # join / move into verify VC
        if after_id == vc_id and before_id != vc_id:
            # auto-assign jersey number (one time)
            await self._maybe_assign_jersey(member)

            await self._start_or_resume_tracking(member)
            return

        # leave / move out of verify VC
        if before_id == vc_id and after_id != vc_id:
            tr = self.voice_tracker.get(member.id)
            if tr:
                # add elapsed to accumulated and pause
                join = tr.get('join')
                if tr.get('in_voice') and isinstance(join, datetime):
                    tr['accumulated'] = float(tr.get('accumulated', 0.0) or 0.0) + (self.get_time() - join).total_seconds()
                tr['join'] = None
                tr['in_voice'] = False

                # cancel running task (kalau ada)
                task = tr.get('task')
                if isinstance(task, asyncio.Task) and not task.done():
                    task.cancel()
                tr['task'] = None

                # update DM (pause / complete)
                secs = self._voice_seconds(member.id)
                required = int(getattr(Config, 'VOICE_REQUIRED_MINUTES', 0) or 0) * 60
                pct = 0 if required <= 0 else min(100, int((secs / required) * 100))
                filled = pct // 10
                bar = "█" * filled + "░" * (10 - filled)
                mins = int(secs // 60)

                if required > 0 and secs >= required:
                    await self._update_voice_embed(
                        member,
                        status="✅ BOLEH MARK KEHADIRAN!",
                        pct=100,
                        bar="█" * 10,
                        desc=(
                            f"🎉 Anda sudah cukup **{Config.VOICE_REQUIRED_MINUTES} minit**!\n\n"
                            "Tekan butang **HADIR 🟢** di channel kehadiran."
                        ),
                        color=getattr(Config, 'COLOR_SUCCESS', 0x00FF00),
                    )
                else:
                    rem = max(0, required - secs)
                    rem_m, rem_s = int(rem // 60), int(rem % 60)
                    desc = (
                        "⏸️ Anda keluar dari voice channel. Masa **tidak dikira** semasa di luar VC.\n\n"
                        f"⏱️ Masa terkumpul: `{mins}m`\n"
                        f"⏳ Baki: `{rem_m}m {rem_s}s`"
                    )
                    await self._update_voice_embed(
                        member,
                        status="⏸️ PAUSE",
                        pct=pct,
                        bar=bar,
                        desc=desc,
                        color=getattr(Config, 'COLOR_WARNING', 0xFFA500),
                    )

            # jersey DM temp boleh dipadam bila keluar verify (optional)
            await self._delete_jersey_dm(member)
            return

    async def track_progress(self, member: discord.Member) -> None:
        """Update progress DM while user is inside verify VC."""
        user_id = member.id
        required_secs = int(getattr(Config, 'VOICE_REQUIRED_MINUTES', 0) or 0) * 60
        if required_secs <= 0:
            return

        try:
            while True:
                tr = self.voice_tracker.get(user_id)
                if not tr or not tr.get('in_voice'):
                    return

                elapsed = self._voice_seconds(user_id)
                pct = min(100, int((elapsed / required_secs) * 100))
                filled = pct // 10
                bar = "█" * filled + "░" * (10 - filled)

                mins = int(elapsed // 60)
                secs = int(elapsed % 60)
                remaining = max(0, required_secs - elapsed)
                rem_mins = int(remaining // 60)
                rem_secs = int(remaining % 60)

                if elapsed >= required_secs:
                    status = "✅ BOLEH MARK KEHADIRAN!"
                    desc = (
                        f"🎉 Anda sudah cukup **{Config.VOICE_REQUIRED_MINUTES} minit**!\n\n"
                        "Tekan butang **HADIR 🟢** di channel kehadiran."
                    )
                    color = getattr(Config, 'COLOR_SUCCESS', 0x00FF00)
                    await self._update_voice_embed(
                        member,
                        status=status,
                        pct=100,
                        bar="█" * 10,
                        desc=desc,
                        color=color,
                    )
                    return

                status = f"⏳ {mins}m {secs}s / {Config.VOICE_REQUIRED_MINUTES}m"
                desc = f"⏱️ Masa berbaki: `{rem_mins}m {rem_secs}s`"
                color = getattr(Config, 'COLOR_INFO', 0x3498DB)
                await self._update_voice_embed(
                    member,
                    status=status,
                    pct=pct,
                    bar=bar,
                    desc=desc,
                    color=color,
                )

                await asyncio.sleep(5)
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"Progress tracking error: {e}")

    # ---------- jersey DM (optional) ----------
    async def _maybe_assign_jersey(self, member: discord.Member) -> None:
        """Assign jersey number once and (optionally) DM user."""
        try:
            db_cog = self.bot.get_cog('DatabaseCog')
            if not db_cog:
                return

            user_doc = await db_cog.get_user(member.id)
            already_has = bool(user_doc and user_doc.get('jersey_number'))

            jersey_num = await db_cog.get_or_assign_jersey_number(
                user_id=member.id,
                guild_id=member.guild.id,
                username=member.name,
                display_name=member.display_name,
            )

            if jersey_num and not already_has:
                await self._send_or_edit_jersey_dm(member, int(jersey_num))
        except Exception as e:
            logger.warning(f"Error assigning jersey number: {e}")

    async def _send_or_edit_jersey_dm(self, member: discord.Member, jersey_num: int) -> None:
        """Send a temporary jersey DM (edit if exists)."""
        try:
            dm = await member.create_dm()
            embed = discord.Embed(
                title="🎽 NOMBOR JERSI ANDA",
                description=(
                    "Selamat datang ke DKATANA!\n\n"
                    f"Nombor jersi anda adalah: **#{jersey_num:02d}**"
                ),
                color=getattr(Config, 'COLOR_GOLD', 0xFFD700),
            )
            embed.add_field(
                name="📌 Perhatian",
                value=(
                    "Nombor ini adalah **tetap** dan kekal sepanjang hayat anda dalam DKATANA, "
                    "seperti nombor badan dalam sukan."
                ),
                inline=False,
            )
            embed.set_footer(text="DKATANA Jersey System | One Time Assignment")

            msg_id = self.jersey_dm_msgs.get(member.id)
            if msg_id:
                try:
                    msg = await dm.fetch_message(int(msg_id))
                    await msg.edit(embed=embed)
                    return
                except Exception:
                    self.jersey_dm_msgs.pop(member.id, None)

            msg = await dm.send(embed=embed)
            self.jersey_dm_msgs[member.id] = msg.id
        except Exception as e:
            logger.warning(f"Could not send/edit jersey DM to {member.display_name}: {e}")

    async def _delete_jersey_dm(self, member: discord.Member) -> None:
        msg_id = self.jersey_dm_msgs.pop(member.id, None)
        if not msg_id:
            return
        try:
            dm = await member.create_dm()
            msg = await dm.fetch_message(int(msg_id))
            await msg.delete()
        except Exception:
            pass

    # ---------- button handlers ----------
    async def _reply_official_leave(self, interaction: discord.Interaction, date_str: str) -> None:
        day_name = self.get_day_name(date_str)
        msg = (
            f"⛱️ Hari ini ialah hari **{day_name}**.\n"
            "Atasan DKATANA menetapkan hari ini sebagai **cuti rasmi DKATANA**, "
            "sila cuba hari lain. Terima kasih."
        )
        try:
            await interaction.followup.send(msg, ephemeral=True)
        except Exception:
            try:
                await interaction.response.send_message(msg, ephemeral=True)
            except Exception:
                pass

    async def _auto_move_after_hadir(self, member: discord.Member):
        """Pindahkan ahli ke VC seterusnya selepas HADIR berjaya, jika diaktifkan."""
        if not Config.AUTO_MOVE_AFTER_HADIR:
            return
        if not Config.AFTER_ATTENDANCE_VC_ID:
            return
        try:
            voice_state = getattr(member, 'voice', None)
            if not voice_state or not voice_state.channel:
                return
            if Config.VOICE_ATTENDANCE_CHANNEL_ID and voice_state.channel.id != Config.VOICE_ATTENDANCE_CHANNEL_ID:
                return
            target = member.guild.get_channel(Config.AFTER_ATTENDANCE_VC_ID)
            if target is None or not isinstance(target, discord.VoiceChannel):
                logger.warning('AUTO_MOVE_AFTER_HADIR aktif tetapi AFTER_ATTENDANCE_VC_ID tidak sah: %s', Config.AFTER_ATTENDANCE_VC_ID)
                return
            me = member.guild.me
            if me and not me.guild_permissions.move_members:
                logger.warning('Bot tiada permission Move Members untuk auto move selepas hadir.')
                return
            if voice_state.channel.id == target.id:
                return
            await member.move_to(target, reason='Attendance direkodkan - auto move selepas hadir')
        except Exception as e:
            logger.warning('Gagal auto move selepas hadir untuk %s: %s', member.id, e)

    async def handle_hadir(self, interaction: discord.Interaction):
        """Handle HADIR button."""
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return
        except discord.InteractionResponded:
            pass

        user = interaction.user
        guild = interaction.guild
        if not guild or not isinstance(user, discord.Member):
            await interaction.followup.send("❌ Tidak sah.", ephemeral=True)
            return

        date = self.get_attendance_date()

        # OFFICIAL LEAVE BLOCK
        if await self.is_official_leave(date):
            await self._reply_official_leave(interaction, date)
            return

        # Check role
        ahli_role = guild.get_role(Config.AHLI_DKATANA_ROLE_ID)
        if ahli_role and ahli_role not in user.roles:
            await interaction.followup.send("❌ Hanya ahli Dkatana boleh guna!", ephemeral=True)
            return

        # Check time window
        if not self.is_attendance_time():
            await interaction.followup.send(
                f"⏰ Butang HADIR hanya aktif dari **{Config.ATTENDANCE_TIME}** hingga **{Config.ATTENDANCE_CUTOFF_TIME}**!",
                ephemeral=True,
            )
            return

        db = self.bot.get_cog('DatabaseCog')
        if not db:
            await interaction.followup.send("❌ Database tidak tersedia.", ephemeral=True)
            return

        # already marked?
        existing = await db.get_attendance(user.id, date)
        if existing and existing.get('status') in ["hadir", "cuti", "cuti_rasmi", "cuti_am", "tidak_hadir"]:
            await interaction.followup.send("✅ Anda sudah mark kehadiran/cuti untuk hari ini!", ephemeral=True)
            return

        # voice time?
        if not self.has_voice_time(user.id):
            mins = self.get_voice_mins(user.id)
            await interaction.followup.send(
                f"⏳ Anda perlu tunggu **{Config.VOICE_REQUIRED_MINUTES} minit** dalam voice channel!\n"
                f"⏱️ Masa terkumpul sekarang: **{mins} minit**",
                ephemeral=True,
            )
            return

        # Mark hadir
        await db.mark_attendance(user.id, date, "hadir")
        await db.update_user_stats(user.id, "hadir")

        # Log
        log = self.bot.get_cog('LoggerCog')
        if log:
            try:
                await log.log_attendance(user, "hadir", Config.VOICE_REQUIRED_MINUTES)
            except Exception:
                pass

        # Update leaderboard
        lb = self.bot.get_cog('LeaderboardCog')
        if lb:
            lb.request_leaderboard_update(delay=0.2)

        # Auto move ke VC seterusnya jika diaktifkan
        try:
            await self._auto_move_after_hadir(user)
        except Exception:
            pass

        # DELETE voice tracking DM to avoid clutter
        try:
            await self._delete_voice_dm(user)
        except Exception:
            pass

        # cleanup tracker (stop further tracking)
        if user.id in self.voice_tracker:
            tr = self.voice_tracker.pop(user.id, None)
            if tr:
                task = tr.get('task')
                if isinstance(task, asyncio.Task) and not task.done():
                    task.cancel()

        await interaction.followup.send("✅ Kehadiran anda telah direkodkan.", ephemeral=True)

    async def handle_cuti(self, interaction: discord.Interaction):
        """Handle APPLY CUTI button."""
        user = interaction.user
        guild = interaction.guild
        if not guild or not isinstance(user, discord.Member):
            await interaction.response.send_message("❌ Tidak sah.", ephemeral=True)
            return

        date = self.get_attendance_date()

        # NOTE:
        # APPLY CUTI dibenarkan pada setiap hari termasuk hari cuti rasmi.
        # Hanya butang HADIR sahaja yang disekat pada hari cuti rasmi.

        ahli_role = guild.get_role(Config.AHLI_DKATANA_ROLE_ID)
        if ahli_role and ahli_role not in user.roles:
            await interaction.response.send_message("❌ Hanya ahli Dkatana boleh guna!", ephemeral=True)
            return

        db = self.bot.get_cog('DatabaseCog')
        if not db:
            await interaction.response.send_message("❌ Database tidak tersedia.", ephemeral=True)
            return

        # Check current status for attendance date
        existing = await db.get_attendance(user.id, date)
        if existing and existing.get('status'):
            status = existing['status']
            if status == 'hadir':
                await interaction.response.send_message(
                    "🟢 Anda sudah **HADIR** hari ini!\nTidak boleh apply cuti selepas mark hadir.",
                    ephemeral=True,
                )
                return
            if status == 'cuti':
                # Pastikan leaderboard sync walaupun status sudah wujud (kadang message belum sempat update)
                lb = self.bot.get_cog('LeaderboardCog')
                if lb:
                    lb.request_leaderboard_update()
                await interaction.response.send_message(
                    "🟡 Anda sudah **CUTI** hari ini!\nPermohonan cuti anda telah direkodkan.",
                    ephemeral=True,
                )
                return
            if status == 'cuti_rasmi':
                # Cuti rasmi tidak menghalang permohonan cuti.
                pass
            if status == 'tidak_hadir':
                await interaction.response.send_message(
                    "🔴 Anda telah **TIDAK HADIR** hari ini!\nWindow kehadiran telah tutup. Tidak boleh apply cuti.",
                    ephemeral=True,
                )
                return

        # Determine jersey number to prefill
        user_data = await db.get_user(user.id)
        number = (user_data.get('jersey_number') or user_data.get('leaderboard_number') or 'N/A') if user_data else 'N/A'

        await interaction.response.send_modal(LeaveModal(self, user, number))

    async def submit_cuti(self, interaction: discord.Interaction, nama: str, tarikh_cuti: str, tarikh_tamat: str, sebab: str, jersey_number: str):
        """Submit leave form."""
        await interaction.response.defer(ephemeral=True)

        def _parse_date(s: str):
            s = (s or "").strip()
            try:
                return datetime.strptime(s, "%d/%m/%Y").date()
            except Exception:
                return None

        d_start = _parse_date(tarikh_cuti)
        d_end = _parse_date(tarikh_tamat)

        if not d_start or not d_end:
            await interaction.followup.send(
                "❌ Tarikh tidak sah. Sila guna format **DD/MM/YYYY** (contoh: 15/02/2026).",
                ephemeral=True,
            )
            return

        if d_end < d_start:
            await interaction.followup.send(
                "❌ Tarikh tamat tidak boleh lebih awal daripada tarikh mula.",
                ephemeral=True,
            )
            return

        db = self.bot.get_cog('DatabaseCog')
        if not db:
            await interaction.followup.send("❌ Database tidak tersedia.", ephemeral=True)
            return

        user = interaction.user
        date = self.get_attendance_date()

        existing = await db.get_attendance(user.id, date)
        # Cuti rasmi tidak menghalang permohonan cuti.
        if existing and existing.get('status') in ["hadir", "cuti", "tidak_hadir"]:
            await interaction.followup.send(
                f"❌ Anda sudah **{existing['status'].upper()}** untuk hari ini!\nTidak boleh apply cuti lagi.",
                ephemeral=True,
            )
            return

        # jersey number fixed
        jersey_str = str(jersey_number)
        jersey_num = int(jersey_str) if jersey_str.isdigit() else 0

        leave = await db.create_leave_request(
            user.id,
            user.display_name,
            tarikh_cuti,
            tarikh_tamat,
            sebab,
            jersey_num,
        )

        # ping admin in log channel (optional)
        try:
            ch_id = getattr(Config, 'LOG_APPLY_CUTI_CHANNEL_ID', 0)
            admin_role_id = getattr(Config, 'ADMIN_ROLE_ID', 0)
            if ch_id:
                ch = self.bot.get_channel(int(ch_id))
                if ch:
                    ping = f"<@&{admin_role_id}>" if admin_role_id else ""
                    admin_embed = discord.Embed(
                        title="🟡 Permohonan Cuti Baru",
                        description=f"{ping} **{user.mention}** telah menghantar permohonan cuti.",
                        color=getattr(Config, 'COLOR_WARNING', 0xFFA500),
                    )
                    admin_embed.add_field(name="🎽 Jersey", value=f"`{jersey_num}`", inline=True)
                    admin_embed.add_field(name="📅 Tarikh", value=f"`{tarikh_cuti} - {tarikh_tamat}`", inline=False)
                    admin_embed.add_field(name="📝 Sebab", value=f"```{sebab[:900]}```", inline=False)
                    await ch.send(embed=admin_embed)
        except Exception as e:
            logger.warning(f"Gagal tag admin untuk cuti: {e}")

        # log
        log = self.bot.get_cog('LoggerCog')
        if log:
            try:
                await log.log_leave_request(user, leave)
            except Exception:
                pass

        embed = discord.Embed(
            title="📧 Permohonan Dihantar!",
            description="Permohonan cuti dihantar untuk kelulusan admin.",
            color=getattr(Config, 'COLOR_INFO', 0x3498DB),
        )
        embed.add_field(name="📅 Tarikh Cuti", value=f"`{tarikh_cuti} - {tarikh_tamat}`", inline=False)
        embed.add_field(name="📝 Sebab", value=f"```{sebab}```", inline=False)
        embed.set_footer(text="💡 Nota: Anda boleh apply cuti lagi jika belum diluluskan/ditolak.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------- background task ----------
    @tasks.loop(minutes=1)
    async def check_attendance(self):
        """Auto-mark tidak hadir selepas cutoff time (04:00)."""
        try:
            current = self.get_time()
            if current.hour != 4 or current.minute != 0:
                return

            db = self.bot.get_cog('DatabaseCog')
            if not db:
                return

            yesterday = (current - timedelta(days=1)).strftime("%Y-%m-%d")
            ahli_list = await db.get_all_ahli_dkatana()

            for ahli in ahli_list:
                uid = ahli.get('user_id')
                if not uid:
                    continue

                if await db.get_attendance(uid, yesterday):
                    continue

                if await db.is_on_leave(uid, yesterday):
                    await db.mark_attendance(uid, yesterday, 'cuti')
                elif await self.is_official_leave(yesterday):
                    await db.mark_attendance(uid, yesterday, 'cuti_rasmi')
                else:
                    await db.mark_attendance(uid, yesterday, 'tidak_hadir')

                    user = self.bot.get_user(uid)
                    if user:
                        log = self.bot.get_cog('LoggerCog')
                        if log:
                            try:
                                await log.log_attendance(user, 'tidak_hadir', 0, auto=True)
                            except Exception:
                                pass

            # Update leaderboard
            lb = self.bot.get_cog('LeaderboardCog')
            if lb:
                lb.request_leaderboard_update(delay=0.2)
        except Exception as e:
            logger.error(f"Check attendance error: {e}")

    @check_attendance.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    # ---------- commands ----------
    @commands.command(name="setup_attendance")
    @commands.has_permissions(administrator=True)
    async def setup_attendance_cmd(self, ctx):
        """Setup attendance message in channel."""
        try:
            await ctx.message.delete()
        except Exception:
            pass

        embed = discord.Embed(
            title="📅 SISTEM KEHADIRAN AHLI DKATANA",
            description=(
                "**Cara guna:**\n"
                "1️⃣ Wajib! Masuk voice channel **Verify Kehadiran**\n"
                f"2️⃣ Tunggu selama **{Config.VOICE_REQUIRED_MINUTES} minit** dalam VC (masa hanya dikira bila berada dalam VC).\n"
                "3️⃣ Bot akan DM progress bar kepada anda\n"
                "4️⃣ Tekan butang **HADIR 🟢** selepas progress bar selesai\n\n"
                "**⛱️ CUTI RASMI:** Jika admin set hari cuti rasmi, butang **HADIR 🟢** akan ditolak. **APPLY CUTI 🟡** masih boleh digunakan.\n"
                f"**⏰ Waktu Kehadiran:** {Config.ATTENDANCE_TIME} - {Config.ATTENDANCE_CUTOFF_TIME}\n"
                "**🟡 APPLY CUTI:** Boleh digunakan sebelum mark hadir\n"
                f"⚠️ Auto Mark **TIDAK HADIR🔴** selepas: {Config.ATTENDANCE_CUTOFF_TIME}"
            ),
            color=getattr(Config, 'COLOR_GOLD', 0xFFD700),
        )
        embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)
        embed.set_footer(text="🎤 Voice tracking akan dipadam selepas mark hadir")

        await ctx.send(embed=embed, view=AttendanceView(self))


async def setup(bot: commands.Bot):
    await bot.add_cog(AttendanceCog(bot))
