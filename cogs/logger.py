"""
Logger Cog - System Logs
"""
import discord
from discord.ext import commands
from discord.ui import View, Button
from datetime import datetime
import pytz
import logging
from config import Config

logger = logging.getLogger('DkatanaBot')


class ApprovalView(View):
    """View for approve/reject"""
    def __init__(self, cog, req_id, user_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.req_id = str(req_id)
        self.user_id = user_id

    @discord.ui.button(label="LULUS ✅", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: Button):
        await self.cog.approve_leave(interaction, self.req_id, self.user_id)

    @discord.ui.button(label="TOLAK ❌", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: Button):
        await self.cog.reject_leave(interaction, self.req_id, self.user_id)


class LoggerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def get_time(self):
        return datetime.now(pytz.timezone(Config.TIMEZONE))

    def get_log_channel(self):
        ch = self.get_admin_log_channel()
        if ch:
            return ch
        return self.bot.get_channel(Config.LOG_CHANNEL_ID)

    def get_apply_cuti_channel(self):
        return self.bot.get_channel(Config.LOG_APPLY_CUTI_CHANNEL_ID)

    def get_admin_log_channel(self):
        return self.bot.get_channel(Config.LOG_ADMIN_CHANNEL_ID)

    async def log_attendance(self, user, status, voice_mins, auto=False):
        """Log attendance"""
        ch = self.get_log_channel()
        if not ch:
            logger.error("Log channel not found!")
            return

        emojis = {"hadir": "🟢", "cuti": "🟡", "tidak_hadir": "🔴", "cuti_rasmi": "⛱️"}
        texts = {"hadir": "HADIR", "cuti": "CUTI", "tidak_hadir": "TIDAK HADIR", "cuti_rasmi": "CUTI RASMI"}

        embed = discord.Embed(
            title=f"{emojis.get(status, '❓')} Kehadiran {'(Auto)' if auto else ''}",
            color=Config.COLOR_SUCCESS if status == "hadir" else Config.COLOR_WARNING if status == "cuti" else Config.COLOR_ERROR
        )
        embed.add_field(name="👤 User", value=f"{user.mention} (`{user.id}`)", inline=False)
        embed.add_field(name="📊 Status", value=f"**{texts.get(status, status)}**", inline=True)
        if status == "hadir":
            embed.add_field(name="⏱️ Masa Voice", value=f"`{voice_mins} minit`", inline=True)
        embed.set_footer(text=self.get_time().strftime("📅 %d/%m/%Y ⏰ %H:%M:%S"))

        try:
            await ch.send(embed=embed)
        except Exception as e:
            logger.error(f"Error sending attendance log: {e}")

    async def log_leave_request(self, user, leave):
        """Log leave request"""
        ch = self.get_apply_cuti_channel()
        if not ch:
            logger.error("Apply cuti log channel not found!")
            return

        embed = discord.Embed(
            title="📧 Permohonan Cuti Baharu",
            description="Sila luluskan atau tolak permohonan ini.",
            color=Config.COLOR_INFO
        )
        embed.add_field(name="👤 Pemohon", value=f"{user.mention} (`{user.id}`)", inline=False)
        embed.add_field(name="📅 Tarikh Cuti", value=f"`{leave['start_date']} - {leave['end_date']}`", inline=False)
        embed.add_field(name="📝 Sebab", value=f"```{leave['reason']}```", inline=False)
        embed.add_field(name="🔢 No. Kehadiran", value=f"`{leave.get('leaderboard_number', 'N/A')}`", inline=True)
        embed.set_footer(text=f"Dimohon pada {self.get_time().strftime('%d/%m/%Y %H:%M:%S')}")

        leave_cog = self.bot.get_cog('LeaveSystemCog')
        if leave_cog:
            view = ApprovalView(leave_cog, leave['_id'], user.id)
            try:
                await ch.send(embed=embed, view=view)
            except Exception as e:
                logger.error(f"Error sending leave request log: {e}")
        else:
            try:
                await ch.send(embed=embed)
            except Exception as e:
                logger.error(f"Error sending leave request log: {e}")

    async def log_leave_approved(self, leave, admin):
        """Log approved leave"""
        ch = self.get_apply_cuti_channel()
        if not ch:
            return

        user = self.bot.get_user(leave["user_id"])
        name = user.display_name if user else leave["username"]

        embed = discord.Embed(title="✅ Permohonan Cuti Diluluskan", color=Config.COLOR_SUCCESS)
        embed.add_field(name="👤 Pemohon", value=f"`{name}`", inline=True)
        embed.add_field(name="👤 Diluluskan Oleh", value=admin.mention, inline=True)
        embed.add_field(name="📅 Tarikh", value=f"`{leave['start_date']} - {leave['end_date']}`", inline=False)
        embed.set_footer(text=f"Diluluskan pada {self.get_time().strftime('%d/%m/%Y %H:%M:%S')}")

        try:
            await ch.send(embed=embed)
        except Exception as e:
            logger.error(f"Error sending approved log: {e}")

    async def log_leave_rejected(self, leave, admin):
        """Log rejected leave"""
        ch = self.get_apply_cuti_channel()
        if not ch:
            return

        user = self.bot.get_user(leave["user_id"])
        name = user.display_name if user else leave["username"]

        embed = discord.Embed(title="❌ Permohonan Cuti Ditolak", color=Config.COLOR_ERROR)
        embed.add_field(name="👤 Pemohon", value=f"`{name}`", inline=True)
        embed.add_field(name="👤 Ditolak Oleh", value=admin.mention, inline=True)
        embed.add_field(name="📅 Tarikh", value=f"`{leave['start_date']} - {leave['end_date']}`", inline=False)
        embed.set_footer(text=f"Ditolak pada {self.get_time().strftime('%d/%m/%Y %H:%M:%S')}")

        try:
            await ch.send(embed=embed)
        except Exception as e:
            logger.error(f"Error sending rejected log: {e}")

    async def log_admin_action(self, admin, action, details):
        """Log admin action"""
        ch = self.get_admin_log_channel()
        if not ch:
            logger.error("Admin log channel not found!")
            return

        embed = discord.Embed(title="👑 Tindakan Admin", color=Config.COLOR_PURPLE)
        embed.add_field(name="👤 Admin", value=f"{admin.mention} (`{admin.id}`)", inline=False)
        embed.add_field(name="⚙️ Tindakan", value=f"`{action}`", inline=True)
        embed.add_field(name="📝 Butiran", value=f"```{details}```", inline=False)
        embed.set_footer(text=f"{self.get_time().strftime('📅 %d/%m/%Y ⏰ %H:%M:%S')}")

        try:
            await ch.send(embed=embed)
        except Exception as e:
            logger.error(f"Error sending admin log: {e}")

    async def log_voice_action(self, admin, action, channel, reason=None):
        """Log voice action"""
        ch = self.get_admin_log_channel()
        if not ch:
            return

        action_emojis = {"LOCK": "🔒", "UNLOCK": "🔓"}

        embed = discord.Embed(
            title=f"{action_emojis.get(action, '🔊')} Voice Channel {action}",
            color=Config.COLOR_INFO if action == "UNLOCK" else Config.COLOR_ERROR
        )
        embed.add_field(name="👤 Oleh", value=admin.mention, inline=True)
        embed.add_field(name="📍 Channel", value=channel.mention, inline=True)
        if reason:
            embed.add_field(name="📝 Sebab", value=f"```{reason}```", inline=False)
        embed.set_footer(text=self.get_time().strftime("📅 %d/%m/%Y ⏰ %H:%M:%S"))

        try:
            await ch.send(embed=embed)
        except Exception as e:
            logger.error(f"Error sending voice log: {e}")

    async def log_voice_kick(self, admin, user, channel, reason):
        """Log voice kick"""
        ch = self.get_admin_log_channel()
        if not ch:
            return

        embed = discord.Embed(title="👢 User Dikick dari Voice", color=Config.COLOR_WARNING)
        embed.add_field(name="👤 Dikick", value=f"{user.mention} (`{user.id}`)", inline=False)
        embed.add_field(name="👤 Oleh", value=admin.mention, inline=True)
        embed.add_field(name="📍 Dari", value=channel.mention, inline=True)
        embed.add_field(name="📝 Sebab", value=f"```{reason}```", inline=False)
        embed.set_footer(text=self.get_time().strftime("📅 %d/%m/%Y ⏰ %H:%M:%S"))

        try:
            await ch.send(embed=embed)
        except Exception as e:
            logger.error(f"Error sending kick log: {e}")

    async def log_system(self, title, description, color=Config.COLOR_INFO):
        """Log system event"""
        ch = self.get_admin_log_channel()
        if not ch:
            return

        embed = discord.Embed(title=f"🖥️ {title}", description=description, color=color)
        embed.set_footer(text=self.get_time().strftime("📅 %d/%m/%Y ⏰ %H:%M:%S"))

        try:
            await ch.send(embed=embed)
        except Exception as e:
            logger.error(f"Error sending system log: {e}")

    async def log_restart(self):
        await self.log_system("Auto Restart", "Bot akan restart tidak lama lagi.", Config.COLOR_WARNING)

    async def log_weekly_reset(self):
        await self.log_system("Weekly Reset", "Data mingguan telah direset.", Config.COLOR_INFO)

async def setup(bot):
    await bot.add_cog(LoggerCog(bot))
