"""
DKATANA DISCORD BOT V2.0
Bot Discord untuk Server Gang Roleplay FiveM
Versi: 2.0.0 - Optimized & Enhanced with Auto-Setup
Python: 3.11+
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import os
import logging
from datetime import datetime, timedelta
import pytz
from pymongo import MongoClient
from config import Config
import sys

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('DkatanaBot')

# Intents
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True
intents.guilds = True

class DkatanaBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=Config.PREFIX,
            intents=intents,
            help_command=None
        )
        self.start_time = datetime.now()
        self.spam_tracker = {}
        self.voice_tracker = {}
        self._startup_log_sent = False
        self._auto_setup_done = False

    async def setup_hook(self):
        """Load semua cogs semasa startup"""
        logger.info("🚀 Memuatkan cogs...")

        cogs = [
            'cogs.database',
            'cogs.attendance',
            'cogs.leave_system',
            'cogs.leaderboard',
            'cogs.admin_panel',
            'cogs.kutipan',
            'cogs.voice_manager',
            'cogs.anti_spam',
            'cogs.logger',
            'cogs.scheduler'
        ]

        for cog in cogs:
            try:
                await self.load_extension(cog)
                logger.info(f"✅ {cog} dimuatkan")
            except Exception as e:
                logger.error(f"❌ Gagal memuatkan {cog}: {e}")

        # Sync commands
        try:
            synced = await self.tree.sync()
            logger.info(f"✅ {len(synced)} command(s) disync")
        except Exception as e:
            logger.error(f"❌ Gagal sync commands: {e}")

    async def on_ready(self):
        """Event apabila bot sudah sedia"""
        logger.info(f"🤖 {self.user.name} sudah online!")
        logger.info(f"🆔 Bot ID: {self.user.id}")
        logger.info(f"🌐 Berada di {len(self.guilds)} server")

        # Set status
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="DKATANA Server | /help"
            ),
            status=discord.Status.online
        )

        # Start background tasks
        self.cleanup_old_data.start()

        # Auto setup messages on startup
        if not self._auto_setup_done and Config.AUTO_SETUP_ON_STARTUP:
            self._auto_setup_done = True
            await self.perform_auto_setup()

        # Log ke Admin Log sekali sahaja
        if not self._startup_log_sent:
            self._startup_log_sent = True
            try:
                log_cog = self.get_cog('LoggerCog')
                if log_cog:
                    uptime = datetime.now() - self.start_time
                    await log_cog.log_system(
                        "🚀 Bot Online",
                        (
                            f"Bot versi **2.0.0** sudah online!\n"
                            f"🆔 ID: `{self.user.id}`\n"
                            f"⏱️ Uptime: `{str(uptime).split('.')[0]}`\n"
                            f"🌐 Guilds: `{len(self.guilds)}`"
                        ),
                        color=Config.COLOR_SUCCESS,
                    )
            except Exception as e:
                logger.error(f"Gagal hantar startup log: {e}")

    async def perform_auto_setup(self):
        """Auto post setup messages on startup"""
        logger.info("📝 Melaksanakan auto-setup...")
        await asyncio.sleep(3)  # Wait for everything to be ready

        # Jika GUILD_ID tidak diset (0), guna guild pertama bot join.
        guild = self.get_guild(Config.GUILD_ID) if Config.GUILD_ID else (self.guilds[0] if self.guilds else None)
        if not guild:
            logger.error("❌ Guild tidak dijumpai untuk auto-setup! Sila set GUILD_ID dalam .env/config.")
            return

        # 1. Setup Attendance Message
        if Config.ATTENDANCE_CHANNEL_ID:
            await self._setup_attendance_channel(guild)

        # 2. Setup Admin Panel
        if Config.ADMIN_PANEL_CHANNEL_ID:
            await self._setup_admin_panel(guild)

        # 3. Setup Kutipan (leaderboard + stats) jika channel diset
        if getattr(Config, "KUTIPAN_LEADERBOARD_CHANNEL_ID", 0):
            await self._setup_kutipan_leaderboard(guild)
        # ✅ Statistik bulanan kutipan tidak lagi auto-post.
        # Admin boleh lihat statistik melalui Admin Panel sahaja.

        logger.info("✅ Auto-setup selesai!")

    async def _setup_kutipan_leaderboard(self, guild):
        """Auto setup/refresh leaderboard kutipan"""
        try:
            kutipan_cog = self.get_cog('KutipanCog')
            if not kutipan_cog:
                return
            # KutipanCog.update_leaderboard() tidak menerima parameter guild.
            # Jika pass guild, ia akan trigger TypeError dan leaderboard tak akan dipost.
            await kutipan_cog.update_leaderboard()
            logger.info("✅ Leaderboard kutipan dikemaskini (auto-setup)")
        except Exception as e:
            logger.error(f"❌ Error setup leaderboard kutipan: {e}")



    async def _setup_attendance_channel(self, guild):
        """Auto setup attendance message tanpa duplicate/spam semasa restart."""
        try:
            channel = guild.get_channel(Config.ATTENDANCE_CHANNEL_ID)
            if not channel:
                logger.error(f"❌ Attendance channel {Config.ATTENDANCE_CHANNEL_ID} tidak dijumpai!")
                return

            db_cog = self.get_cog('DatabaseCog')
            settings = await db_cog.get_guild_settings(guild.id) if db_cog else {}
            existing_msg = None
            msg_id = settings.get('attendance_setup_message_id') if settings else None
            if msg_id:
                try:
                    existing_msg = await channel.fetch_message(int(msg_id))
                except Exception:
                    existing_msg = None

            if existing_msg is None:
                async for msg in channel.history(limit=20):
                    if msg.author == self.user and msg.embeds and msg.embeds[0].title and 'SISTEM KEHADIRAN AHLI DKATANA' in msg.embeds[0].title:
                        existing_msg = msg
                        break

            # Import attendance cog to get the view
            from cogs.attendance import AttendanceView

            embed = discord.Embed(
                title="📅 SISTEM KEHADIRAN AHLI DKATANA",
                description=(
                    f"**CARA GUNA ATTENDANCE AUTO:**\n"
                    f"1️⃣ *Wajib! Masuk voice channel* **Verify Hadir**\n"
                    f"2️⃣ *Tunggu selama* **{Config.VOICE_REQUIRED_MINUTES} minit** *dalam VC masa anda hanya dikira semasa berada di dalam VC.*\n"
                    f"3️⃣ *Bot akan DM voice tracking progress bar kepada anda dan tunggu sehingga selesai.*\n"
                    f"4️⃣ *Tekan butang* **HADIR🟢** *selepas progress bar selesai.*\n\n"
                    f"**⛱️ CUTI RASMI:** *Jika admin set hari cuti rasmi, butang HADIR/APPLY CUTI akan ditolak*.\n"
                    f"**⏰ Waktu Kehadiran:** `{Config.ATTENDANCE_TIME} - {Config.ATTENDANCE_CUTOFF_TIME}`\n"
                    f"**🟡 APPLY CUTI:** *Boleh digunakan apabila anda hhendak bercuti*\n"
                    f"**🔴TIDAK HADIR:** *Akan auto ditanda selepas* `{Config.ATTENDANCE_CUTOFF_TIME}`\n\n"
                    f"**JIKA TERDAPAT SEBARANG MASALAH BOLEH BERURUSAN DENGAN PIHAK PENGURUSAN/TICKET**"
                ),
                color=0xFFD700
            )
            embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
            embed.set_footer(text="Ꭰ'Ҡᴀᴛᴀɴᴀ ⚡ ・ AttendanceSystem")

            view = AttendanceView(self.get_cog('AttendanceCog'))
            if existing_msg:
                try:
                    await existing_msg.edit(embed=embed, view=view)
                    if db_cog:
                        await db_cog.update_guild_settings(guild.id, {'attendance_setup_message_id': existing_msg.id})
                    logger.info(f"ℹ️ Attendance message sedia ada direfresh di {channel.name}")
                    return
                except Exception:
                    existing_msg = None

            msg = await channel.send(embed=embed, view=view)
            if db_cog:
                await db_cog.update_guild_settings(guild.id, {'attendance_setup_message_id': msg.id})
            logger.info(f"✅ Attendance message dihantar ke {channel.name}")

        except Exception as e:
            logger.error(f"❌ Error setup attendance channel: {e}")

    async def _setup_admin_panel(self, guild):
        """Auto setup admin panel"""
        try:
            channel = guild.get_channel(Config.ADMIN_PANEL_CHANNEL_ID)
            if not channel:
                logger.error(f"❌ Admin panel channel {Config.ADMIN_PANEL_CHANNEL_ID} tidak dijumpai!")
                return

            # Check if already has admin panel (look for specific embed title)
            panel_msg: discord.Message | None = None
            async for msg in channel.history(limit=20):
                if msg.author == self.user and msg.embeds and msg.embeds[0].title:
                    if "ADMIN PANEL DKATANA" in msg.embeds[0].title:
                        panel_msg = msg
                        break

            # If panel already exists and we're not clearing messages, refresh the view so it keeps working after restart
            if panel_msg and not Config.CLEAR_MESSAGES_ON_STARTUP:
                admin_cog = self.get_cog('AdminPanelCog')
                if admin_cog:
                    view = admin_cog.AdminView(admin_cog)
                    try:
                        await panel_msg.edit(view=view)
                        logger.info("ℹ️ Admin panel sudah wujud, view direfresh (tidak hantar mesej baru).")
                    except Exception as e:
                        logger.warning(f"⚠️ Gagal refresh admin panel view: {e}")
                else:
                    logger.error("❌ AdminPanelCog tidak dijumpai!")
                return
            # Clear existing messages if enabled
            if Config.CLEAR_MESSAGES_ON_STARTUP:
                deleted = 0
                async for msg in channel.history(limit=50):
                    if msg.author == self.user:
                        try:
                            await msg.delete()
                            deleted += 1
                            await asyncio.sleep(0.5)
                        except:
                            pass
                if deleted > 0:
                    logger.info(f"🗑️ {deleted} mesej lama dipadam dari admin panel channel")
                await asyncio.sleep(2)

            # Import admin panel cog to get the view
            from cogs.admin_panel import AdminPanelCog

            embed = discord.Embed(
                title="👑 ADMIN PANEL DKATANA 👑",
                description=(
                    "📋 **Selamat datang ke Panel Pentadbiran!**\n\n"
                    "Pilih tindakan dari **dropdown** di bawah untuk mengurus sistem.\n\n"
                    "***KATEGORI ATTENDANCE:***\n"
                    "⛱️ **Cuti** - `Set cuti rasmi`\n"
                    "⏱️ **Masa** - `Set masa voice dan kehadiran`\n"
                    "✏️ **Kehadiran** - `Edit kehadiran user`\n"
                    "❗ **Amaran** - `Hantar warning`\n"
                    "🗄️ **Database** - `Akses dan urus data`\n"
                    "👥 **Role** - `Tukar role leaderboard`\n"
                    "🔄 **Refresh** - `Update leaderboard`\n"
                    "📊 **Laporan** - `Lihat statistik dan laporan (7 hari kebelakang)`\n"
                    "🔒 **Voice** - `Kawal voice channel`\n\n"
                    "***KATEGORI KUTIPAN:***\n"
                    "🧹 **Data Lama** - `Kawal voice channel`\n"
                    "💰 **Kutipan** - `Edit leaderboard kutipan`\n"
                    "📈 **Statistik** - `Lihat statistik Kutipan mingguan`\n"
                    "📉 **Statistik** - `Lihat statistik Kutipan Bulanan`\n"
                    "📨 **Reminder** - `Hantar mesej peringatan kepada ahlli belum bayar`\n"
                    "🗒️ **Note** - `Tulis nota duit kutipan keluar masuk`\n"
                    "💵 **Jumlah** - `Tetapkan Jumlah Kutipan`\n"
                    "⚠️ **Penalty** - `Tetapkan jumllah duit denda`\n"
                    "👕 **Jersey** - `Edit jersey ahli`\n\n"
                    "📌 *Semua tindakan direkodkan*"
                ),
                color=0x9B59B6
            )
            embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
            embed.set_footer(text="Ꭰ'Ҡᴀᴛᴀɴᴀ ⚡ ・  Admin Panel |  Akses  Admin ")

            admin_cog = self.get_cog('AdminPanelCog')
            if admin_cog:
                view = admin_cog.AdminView(admin_cog)
                await channel.send(embed=embed, view=view)
                logger.info(f"✅ Admin panel dihantar ke {channel.name}")
            else:
                logger.error("❌ AdminPanelCog tidak dijumpai!")

        except Exception as e:
            logger.error(f"❌ Error setup admin panel: {e}")

    async def on_member_remove(self, member):
        """Event apabila member keluar server"""
        if member.bot:
            return

        db_cog = self.get_cog('DatabaseCog')
        if db_cog:
            await db_cog.mark_for_deletion(member.id, member.guild.id)
            logger.info(f"👤 {member.name} ditandakan untuk dipadam")

    async def on_voice_state_update(self, member, before, after):
        """Track perubahan voice channel"""
        if member.bot:
            return

        # Attendance tracking
        attendance_cog = self.get_cog('AttendanceCog')
        if attendance_cog:
            await attendance_cog.handle_voice_update(member, before, after)

        # Voice status tracking
        voice_cog = self.get_cog('VoiceManagerCog')
        if voice_cog:
            await voice_cog.update_voice_status(member, before, after)

    @tasks.loop(hours=24)
    async def cleanup_old_data(self):
        """Bersihkan data lama setiap hari"""
        logger.info("🧹 Membersihkan data lama...")
        db_cog = self.get_cog('DatabaseCog')
        if db_cog:
            deleted = await db_cog.cleanup_old_data()
            logger.info(f"🗑️ {deleted} data lama dipadam")

    @cleanup_old_data.before_loop
    async def before_cleanup(self):
        await self.wait_until_ready()

# Initialize bot
bot = DkatanaBot()

# Error handlers
@bot.event
async def on_command_error(ctx, error):
    """Handle command errors"""
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Anda tidak mempunyai kebenaran untuk menggunakan command ini!", delete_after=5)
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Argument diperlukan: {error.param.name}", delete_after=5)
    else:
        logger.error(f"Command error: {error}")

@bot.event
async def on_error(event, *args, **kwargs):
    """Handle general errors"""
    logger.error(f"Error dalam event {event}: {args} {kwargs}")

# Run bot
if __name__ == "__main__":
    try:
        bot.run(Config.TOKEN)
    except Exception as e:
        logger.critical(f"Bot crash: {e}")
        raise