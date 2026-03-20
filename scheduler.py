"""
Scheduler Cog - Auto Restart dan Weekly Reset
Compatible dengan Bot-Hosting.net / Pterodactyl
"""
import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz
import asyncio
import sys
import os
import sys
import shutil
import logging
from config import Config

logger = logging.getLogger('DkatanaBot')

class SchedulerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.restart_scheduled = False
        self.weekly_reset_done = False

        self.check_schedules.start()
        self.update_leaderboard_loop.start()
        self.archive_old_data.start()

    def cog_unload(self):
        self.check_schedules.cancel()
        self.update_leaderboard_loop.cancel()

    def get_current_time(self):
        tz = pytz.timezone(Config.TIMEZONE)
        return datetime.now(tz)

    def get_week_range(self):
        tz = pytz.timezone(Config.TIMEZONE)
        today = datetime.now(tz)
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")

    # ==================== BACKGROUND TASKS ====================

    @tasks.loop(minutes=1)
    async def check_schedules(self):
        try:
            current = self.get_current_time()

            if (current.hour == Config.AUTO_RESTART_HOUR and 
                current.minute == Config.AUTO_RESTART_MINUTE and 
                not self.restart_scheduled and
                getattr(Config, 'AUTO_RESTART_ENABLED', True)):

                self.restart_scheduled = True
                await self.perform_restart()

            if current.minute != Config.AUTO_RESTART_MINUTE:
                self.restart_scheduled = False

            if (current.weekday() == Config.WEEKLY_RESET_DAY and 
                current.hour == 0 and 
                current.minute == 0 and 
                not self.weekly_reset_done):

                self.weekly_reset_done = True
                await self.perform_weekly_reset()

            if current.minute != 0:
                self.weekly_reset_done = False

        except Exception as e:
            logger.error(f"Error dalam check_schedules: {e}")

    @tasks.loop(minutes=5)
    async def update_leaderboard_loop(self):
        try:
            leaderboard_cog = self.bot.get_cog('LeaderboardCog')
            if leaderboard_cog:
                await leaderboard_cog.update_leaderboard_message()
        except Exception as e:
            logger.error(f"Error update leaderboard loop: {e}")

    @check_schedules.before_loop
    async def before_check_schedules(self):
        await self.bot.wait_until_ready()

    @update_leaderboard_loop.before_loop
    async def before_update_leaderboard(self):
        await self.bot.wait_until_ready()

    # ==================== RESTART (BOT-HOSTING.NET FIX) ====================

    async def perform_restart(self):
        """
        Restart bot - Compatible dengan Bot-Hosting.net / Pterodactyl
        Strategy: Exit dengan code 0 supaya panel auto-restart process
        """
        logger.info("🔄 Memulakan proses restart...")

        log_cog = self.bot.get_cog('LoggerCog')
        if log_cog:
            await log_cog.log_restart()

        channel = None
        if log_cog:
            channel = log_cog.get_admin_log_channel()
        if not channel:
            channel = self.bot.get_channel(Config.LOG_ADMIN_CHANNEL_ID) or self.bot.get_channel(Config.LOG_CHANNEL_ID)

        if channel:
            embed = discord.Embed(
                title=f"{Config.EMOJI_REFRESH} Auto Restart",
                description="Bot akan direstart dalam **30 saat**...\n\n⚠️ **NOTA:** Jika bot tidak kembali online, sila tekan butang **Start** di panel.",
                color=Config.COLOR_WARNING
            )
            try:
                await channel.send(embed=embed)
            except:
                pass

        await asyncio.sleep(30)
        logger.info("🔄 Restarting bot (Bot-Hosting.net mode)...")

        # Tutup connection dengan elok
        try:
            await self.bot.close()
        except:
            pass

        # Untuk Bot-Hosting.net / Pterodactyl:
        # Exit dengan code 0 akan trigger auto-restart jika di-enable di panel
        # Exit dengan code 1 juga akan trigger restart (crash detection)
        logger.info("👋 Exiting process untuk restart...")

        # Cuba restart process sendiri dahulu (jika hosting support), fallback exit.
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception:
            os._exit(0)  # Fallback: panel auto-restart process

    # ==================== WEEKLY RESET ====================

    @tasks.loop(hours=24)
    async def archive_old_data(self):
        """Archive data (soft delete) & Hard Delete - runs daily at 04:30"""
        try:
            current = self.get_current_time()

            # Run at 04:30 daily (30 minutes after attendance check)
            if current.hour != 4 or current.minute != 30:
                return

            if not Config.AUTO_ARCHIVE_OLD_DATA:
                return

            logger.info("📦 Memulakan proses archive & hard delete data lama...")

            db = self.bot.get_cog('DatabaseCog')
            if not db:
                return

            # 1. Archive attendance & leave (older than 7 days)
            archived_attendance = await db.archive_old_attendance(Config.DATA_RETENTION_DAYS)
            archived_leave = await db.archive_old_leave_requests(Config.DATA_RETENTION_DAYS)
            
            # 2. Hard Delete archives (older than 90 days)
            del_att, del_leave = await db.hard_delete_old_archives(days=90)

            total_archived = archived_attendance + archived_leave
            total_deleted = del_att + del_leave

            if total_archived > 0 or total_deleted > 0:
                # Log to Discord
                log_cog = self.bot.get_cog('LoggerCog')
                if log_cog:
                    await log_cog.log_system(
                        "📦 Pembersihan Database Harian",
                        f"**Telah Diarkib (Soft Delete):**\n"
                        f"📝 Attendance: `{archived_attendance}` rekod\n"
                        f"📋 Leave Requests: `{archived_leave}` rekod\n\n"
                        f"**Telah Dipadam Selamanya (Hard Delete > 90 hari):**\n"
                        f"🗑️ Attendance: `{del_att}` rekod\n"
                        f"🗑️ Leave Requests: `{del_leave}` rekod",
                        Config.COLOR_INFO
                    )
            else:
                logger.info("📦 Tiada data untuk diarkib/dipadam hari ini.")

        except Exception as e:
            logger.error(f"Error dalam archive_old_data: {e}")

    @archive_old_data.before_loop
    async def before_archive(self):
        await self.bot.wait_until_ready()

    async def perform_weekly_reset(self):
        logger.info("🔄 Memulakan weekly reset...")

        log_cog = self.bot.get_cog('LoggerCog')
        if log_cog:
            await log_cog.log_weekly_reset()

        logger.info("✅ Weekly reset selesai!")

    # ==================== COMMANDS ====================

    @commands.command(name="restart")
    @commands.has_permissions(administrator=True)
    async def manual_restart(self, ctx):
        """Restart bot - Bot-Hosting.net compatible"""
        if not getattr(Config, 'ENABLE_MANUAL_RESTART', False):
            return await ctx.send("❌ Manual restart dimatikan. Auto-restart bot sedang aktif.", delete_after=15)

        await ctx.send("🔄 Bot akan restart sekarang (soft restart).\nℹ️ Kalau hosting tak support, panel akan restart automatik.")

        log_cog = self.bot.get_cog('LoggerCog')
        if log_cog:
            await log_cog.log_admin_action(ctx.author, "Manual Restart", "Bot direstart manual")

        # Buang cache yang tidak digunakan
        try:
            for root, dirs, files in os.walk(os.getcwd()):
                if '__pycache__' in dirs:
                    shutil.rmtree(os.path.join(root, '__pycache__'), ignore_errors=True)
        except Exception:
            logger.exception("Gagal padam __pycache__")

        try:
            await self.bot.close()
        finally:
            # Cuba restart proses tanpa perlu panel, fallback exit jika gagal.
            try:
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception:
                os._exit(0)

    @commands.command(name="schedule_info")
    @commands.has_permissions(administrator=True)
    async def schedule_info(self, ctx):
        """Info schedule"""
        current = self.get_current_time()

        embed = discord.Embed(title=f"{Config.EMOJI_CLOCK} Schedule Info", color=Config.COLOR_INFO)

        next_restart = current.replace(hour=Config.AUTO_RESTART_HOUR, minute=Config.AUTO_RESTART_MINUTE, second=0)
        if next_restart < current:
            next_restart += timedelta(days=1)

        embed.add_field(
            name="🔄 Auto Restart",
            value=f"Masa: `{Config.AUTO_RESTART_HOUR:02d}:{Config.AUTO_RESTART_MINUTE:02d}`\nSeterusnya: `{next_restart.strftime('%d/%m %H:%M')}`",
            inline=False
        )

        week_start, week_end = self.get_week_range()
        embed.add_field(name="📊 Minggu Semasa", value=f"`{week_start}` → `{week_end}`", inline=False)

        await ctx.send(embed=embed, delete_after=30)

    @commands.command(name="reconnect")
    @commands.has_permissions(administrator=True)
    async def reconnect_cmd(self, ctx):
        """Reconnect tanpa restart process"""
        await ctx.send("🔄 Mencuba reconnect ke Discord...")
        try:
            await self.bot.close()
            await asyncio.sleep(2)
            # Bot akan auto-reconnect jika process masih running
            await ctx.send("✅ Reconnect dijalankan!")
        except Exception as e:
            await ctx.send(f"❌ Error: {e}")

async def setup(bot):
    await bot.add_cog(SchedulerCog(bot))
