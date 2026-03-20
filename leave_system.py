"""
Leave System Cog - Fast Approval System
"""
import asyncio
import discord
from discord.utils import MISSING
from discord.ext import commands
from datetime import datetime, timedelta
import pytz
import logging
from config import Config

try:
    from .logger import ApprovalView
except Exception:
    from cogs.logger import ApprovalView

logger = logging.getLogger('DkatanaBot')


class ProcessingApprovalView(discord.ui.View):
    """View sementara (disable) bila admin klik"""
    def __init__(self, action: str):
        super().__init__(timeout=None)
        approve_label = "Processing... ⏳" if action == "approve" else "LULUS ✅"
        reject_label = "Processing... ⏳" if action == "reject" else "TOLAK ❌"

        self.add_item(discord.ui.Button(label=approve_label, style=discord.ButtonStyle.success, disabled=True))
        self.add_item(discord.ui.Button(label=reject_label, style=discord.ButtonStyle.danger, disabled=True))


class LeaveSystemCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._processing_requests: set = set()

    def get_time(self):
        return datetime.now(pytz.timezone(Config.TIMEZONE))

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        admin_role = interaction.guild.get_role(Config.ADMIN_ROLE_ID)
        roles = getattr(interaction.user, "roles", [])
        return bool(admin_role and admin_role in roles)

    async def _safe_edit_message(self, interaction: discord.Interaction, *, content=MISSING, view=MISSING):
        try:
            if interaction.message:
                await interaction.message.edit(content=content, view=view)
        except Exception as e:
            logger.warning(f"LeaveSystem: gagal edit message ({e})")

    async def _after_approve(self, req: dict, admin):
        try:
            db = self.bot.get_cog('DatabaseCog')
            if not db:
                return

            start = datetime.strptime(req["start_date"], "%d/%m/%Y")
            end = datetime.strptime(req["end_date"], "%d/%m/%Y")
            dates = []
            current = start
            while current <= end:
                dates.append(current.strftime("%Y-%m-%d"))
                current += timedelta(days=1)

            if dates:
                await db.bulk_mark_attendance(req["user_id"], dates, "cuti", notes="Cuti diluluskan")

            lb = self.bot.get_cog('LeaderboardCog')
            if lb:
                lb.request_leaderboard_update(delay=0.2)

            user = self.bot.get_user(req["user_id"])
            if user:
                try:
                    embed = discord.Embed(
                        title="✅ Permohonan Cuti Diluluskan!",
                        description=f"Cuti anda **DILULUSKAN**!\n📅 `{req['start_date']} - {req['end_date']}`",
                        color=0x00FF00
                    )
                    await user.send(embed=embed)
                except:
                    pass

            log = self.bot.get_cog('LoggerCog')
            if log:
                await log.log_leave_approved(req, admin)
        except Exception as e:
            logger.error(f"LeaveSystem _after_approve error: {e}")

    async def _after_reject(self, req: dict, admin):
        try:
            user = self.bot.get_user(req["user_id"])
            if user:
                try:
                    embed = discord.Embed(
                        title="❌ Permohonan Cuti Ditolak",
                        description=f"Cuti anda **DITOLAK**!\n📅 `{req['start_date']} - {req['end_date']}`",
                        color=0xFF0000
                    )
                    await user.send(embed=embed)
                except:
                    pass

            log = self.bot.get_cog('LoggerCog')
            if log:
                await log.log_leave_rejected(req, admin)
        except Exception as e:
            logger.error(f"LeaveSystem _after_reject error: {e}")

    async def approve_leave(self, interaction: discord.Interaction, req_id: str, user_id: int):
        if not self._is_admin(interaction):
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Hanya admin!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Hanya admin!", ephemeral=True)
            return

        if req_id in self._processing_requests:
            msg = "⏳ Sedang diproses..."
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
            return

        self._processing_requests.add(req_id)
        try:
            processing_view = ProcessingApprovalView(action="approve")
            try:
                await interaction.response.edit_message(view=processing_view)
            except Exception:
                try:
                    if not interaction.response.is_done():
                        await interaction.response.defer(ephemeral=True)
                except:
                    pass
                await self._safe_edit_message(interaction, view=processing_view)

            db = self.bot.get_cog('DatabaseCog')
            if not db:
                await self._safe_edit_message(interaction, view=ApprovalView(self, req_id, user_id))
                await interaction.followup.send("❌ Database tidak tersedia.", ephemeral=True)
                return

            try:
                req = await db.set_leave_status_if_pending(req_id, "approved", interaction.user.id)
            except Exception as e:
                logger.error(f"LeaveSystem approve error: {e}")
                await self._safe_edit_message(interaction, view=ApprovalView(self, req_id, user_id))
                await interaction.followup.send("❌ Ralat database.", ephemeral=True)
                return

            if not req:
                existing = await db.get_leave_request(req_id)
                if not existing:
                    await self._safe_edit_message(interaction, view=None)
                    await interaction.followup.send("❌ Permohonan tak jumpa!", ephemeral=True)
                    return

                await self._safe_edit_message(interaction, view=None)
                await interaction.followup.send(f"ℹ️ Sudah **{existing.get('status', 'diproses')}**.", ephemeral=True)
                return

            await self._safe_edit_message(
                interaction,
                content=f"✅ **DILULUSKAN** oleh {interaction.user.mention}",
                view=None
            )
            await interaction.followup.send("✅ Diluluskan!", ephemeral=True)
            asyncio.create_task(self._after_approve(req, interaction.user))
        finally:
            self._processing_requests.discard(req_id)

    async def reject_leave(self, interaction: discord.Interaction, req_id: str, user_id: int):
        if not self._is_admin(interaction):
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Hanya admin!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Hanya admin!", ephemeral=True)
            return

        if req_id in self._processing_requests:
            msg = "⏳ Sedang diproses..."
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
            return

        self._processing_requests.add(req_id)
        try:
            processing_view = ProcessingApprovalView(action="reject")
            try:
                await interaction.response.edit_message(view=processing_view)
            except Exception:
                try:
                    if not interaction.response.is_done():
                        await interaction.response.defer(ephemeral=True)
                except:
                    pass
                await self._safe_edit_message(interaction, view=processing_view)

            db = self.bot.get_cog('DatabaseCog')
            if not db:
                await self._safe_edit_message(interaction, view=ApprovalView(self, req_id, user_id))
                await interaction.followup.send("❌ Database tidak tersedia.", ephemeral=True)
                return

            try:
                req = await db.set_leave_status_if_pending(req_id, "rejected", interaction.user.id)
            except Exception as e:
                logger.error(f"LeaveSystem reject error: {e}")
                await self._safe_edit_message(interaction, view=ApprovalView(self, req_id, user_id))
                await interaction.followup.send("❌ Ralat database.", ephemeral=True)
                return

            if not req:
                existing = await db.get_leave_request(req_id)
                if not existing:
                    await self._safe_edit_message(interaction, view=None)
                    await interaction.followup.send("❌ Permohonan tak jumpa!", ephemeral=True)
                    return

                await self._safe_edit_message(interaction, view=None)
                await interaction.followup.send(f"ℹ️ Sudah **{existing.get('status', 'diproses')}**.", ephemeral=True)
                return

            await self._safe_edit_message(
                interaction,
                content=f"❌ **DITOLAK** oleh {interaction.user.mention}",
                view=None
            )
            await interaction.followup.send("✅ Ditolak!", ephemeral=True)
            asyncio.create_task(self._after_reject(req, interaction.user))
        finally:
            self._processing_requests.discard(req_id)

    @commands.command(name="pending_leaves")
    @commands.has_permissions(administrator=True)
    async def pending_leaves_cmd(self, ctx):
        """Show pending leave requests"""
        db = self.bot.get_cog('DatabaseCog')
        reqs = await db.get_pending_leave_requests()

        if not reqs:
            await ctx.send("📭 Tiada permohonan pending.", delete_after=5)
            return

        embed = discord.Embed(title="⏳ Permohonan Cuti Pending", color=0xFFA500)
        for req in reqs[:5]:
            user = self.bot.get_user(req["user_id"])
            name = user.display_name if user else req["username"]
            embed.add_field(name=f"👤 {name}", value=f"📅 `{req['start_date']} - {req['end_date']}`", inline=False)

        await ctx.send(embed=embed, delete_after=30)

async def setup(bot):
    await bot.add_cog(LeaveSystemCog(bot))
