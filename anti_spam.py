"""
Anti-Spam Cog - Sistem Anti Spam Message
"""
import discord
from discord.ext import commands
from datetime import datetime, timedelta
import asyncio
import logging
from collections import defaultdict
from config import Config

logger = logging.getLogger('DkatanaBot')

class AntiSpamCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.message_tracker = defaultdict(list)
        self.muted_users = {}
        self.warned_users = set()

    def is_admin(self, member):
        admin_role = member.guild.get_role(Config.ADMIN_ROLE_ID)
        return admin_role in member.roles or member.guild_permissions.administrator

    def is_ahli_dkatana(self, member):
        ahli_role = member.guild.get_role(Config.AHLI_DKATANA_ROLE_ID)
        return ahli_role in member.roles

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        if self.is_admin(message.author):
            return

        user_id = message.author.id
        current_time = datetime.now()

        if user_id in self.muted_users:
            if current_time < self.muted_users[user_id]:
                try:
                    await message.delete()
                except:
                    pass
                return
            else:
                del self.muted_users[user_id]

        self.message_tracker[user_id].append(current_time)

        cutoff_time = current_time - timedelta(seconds=Config.SPAM_TIME_WINDOW)
        self.message_tracker[user_id] = [t for t in self.message_tracker[user_id] if t > cutoff_time]

        if len(self.message_tracker[user_id]) > Config.SPAM_THRESHOLD:
            await self.handle_spam(message)

    async def handle_spam(self, message):
        user = message.author

        async for msg in message.channel.history(limit=20):
            if msg.author.id == user.id:
                try:
                    await msg.delete()
                except:
                    pass

        if user.id not in self.warned_users:
            self.warned_users.add(user.id)

            embed = discord.Embed(
                title=f"{Config.EMOJI_WARNING} Amaran Anti-Spam",
                description=f"{user.mention}, sila jangan spam message!",
                color=Config.COLOR_WARNING
            )
            embed.add_field(
                name="⚠️ Peringatan",
                value=f"Anda telah menghantar lebih dari **{Config.SPAM_THRESHOLD}** message dalam **{Config.SPAM_TIME_WINDOW}** saat.",
                inline=False
            )
            embed.add_field(
                name="🔇 Tindakan",
                value="Jika anda terus spam, anda akan di-mute.",
                inline=False
            )

            warning_msg = await message.channel.send(embed=embed, delete_after=10)

            log_cog = self.bot.get_cog('LoggerCog')
            if log_cog:
                await log_cog.log_system("Amaran Spam", f"{user.mention} di channel {message.channel.mention}", Config.COLOR_WARNING)

        else:
            mute_duration = Config.SPAM_MUTE_DURATION
            unmute_time = datetime.now() + timedelta(seconds=mute_duration)
            self.muted_users[user.id] = unmute_time

            try:
                mute_role = discord.utils.get(message.guild.roles, name="Muted")
                if not mute_role:
                    mute_role = await message.guild.create_role(name="Muted", reason="Auto-created for anti-spam")
                    for channel in message.guild.channels:
                        await channel.set_permissions(mute_role, send_messages=False, add_reactions=False, speak=False)

                await user.add_roles(mute_role, reason="Spam detected")

                embed = discord.Embed(
                    title=f"{Config.EMOJI_LOCK} User Di-mute",
                    description=f"{user.mention} telah di-mute kerana spam!",
                    color=Config.COLOR_ERROR
                )
                embed.add_field(name="⏱️ Tempoh", value=f"`{mute_duration // 60} minit`", inline=True)

                await message.channel.send(embed=embed, delete_after=15)

                self.bot.loop.create_task(self.schedule_unmute(user, mute_role, mute_duration))

            except Exception as e:
                logger.error(f"Error muting user: {e}")

    async def schedule_unmute(self, user, mute_role, duration):
        await asyncio.sleep(duration)

        try:
            if mute_role in user.roles:
                await user.remove_roles(mute_role, reason="Auto-unmute")

                try:
                    embed = discord.Embed(
                        title=f"{Config.EMOJI_UNLOCK} Anda Telah Di-unmute",
                        description="Tempoh mute anda telah tamat.",
                        color=Config.COLOR_SUCCESS
                    )
                    await user.send(embed=embed)
                except:
                    pass

                if user.id in self.warned_users:
                    self.warned_users.remove(user.id)

                if user.id in self.muted_users:
                    del self.muted_users[user.id]

                logger.info(f"User {user.name} auto-unmuted")
        except Exception as e:
            logger.error(f"Error unmuting user: {e}")

    @commands.command(name="spam_mute")
    @commands.has_permissions(administrator=True)
    async def spam_mute_cmd(self, ctx, member: discord.Member, duration: int = 5, *, reason: str = "Tiada sebab"):
        if self.is_admin(member):
            await ctx.send("❌ Anda tidak boleh mute admin!", delete_after=5)
            return

        mute_role = discord.utils.get(ctx.guild.roles, name="Muted")
        if not mute_role:
            mute_role = await ctx.guild.create_role(name="Muted", reason="Auto-created")
            for channel in ctx.guild.channels:
                await channel.set_permissions(mute_role, send_messages=False, add_reactions=False, speak=False)

        await member.add_roles(mute_role, reason=f"Muted by {ctx.author.name}: {reason}")

        mute_duration = duration * 60
        unmute_time = datetime.now() + timedelta(minutes=duration)
        self.muted_users[member.id] = unmute_time

        embed = discord.Embed(
            title=f"{Config.EMOJI_LOCK} User Di-mute",
            description=f"**{member.display_name}** telah di-mute!",
            color=Config.COLOR_ERROR
        )
        embed.add_field(name="👤 Oleh", value=ctx.author.mention, inline=True)
        embed.add_field(name="⏱️ Tempoh", value=f"`{duration} minit`", inline=True)

        await ctx.send(embed=embed)

        self.bot.loop.create_task(self.schedule_unmute(member, mute_role, mute_duration))

    @commands.command(name="spam_unmute")
    @commands.has_permissions(administrator=True)
    async def spam_unmute_cmd(self, ctx, member: discord.Member, *, reason: str = "Tiada sebab"):
        mute_role = discord.utils.get(ctx.guild.roles, name="Muted")

        if not mute_role or mute_role not in member.roles:
            await ctx.send("❌ User ini tidak di-mute!", delete_after=5)
            return

        await member.remove_roles(mute_role, reason=f"Unmuted by {ctx.author.name}: {reason}")

        if member.id in self.muted_users:
            del self.muted_users[member.id]
        if member.id in self.warned_users:
            self.warned_users.remove(member.id)

        embed = discord.Embed(
            title=f"{Config.EMOJI_UNLOCK} User Di-unmute",
            description=f"**{member.display_name}** telah di-unmute!",
            color=Config.COLOR_SUCCESS
        )
        await ctx.send(embed=embed)

    @commands.command(name="spam_status")
    @commands.has_permissions(administrator=True)
    async def spam_status(self, ctx):
        embed = discord.Embed(title=f"{Config.EMOJI_SHIELD} Status Anti-Spam", color=Config.COLOR_INFO)
        embed.add_field(
            name="⚙️ Tetapan",
            value=f"Threshold: `{Config.SPAM_THRESHOLD}`\nTime Window: `{Config.SPAM_TIME_WINDOW}`s\nMute: `{Config.SPAM_MUTE_DURATION // 60}`m",
            inline=False
        )
        embed.add_field(name="👥 User Di-track", value=f"`{len(self.message_tracker)}`", inline=True)
        embed.add_field(name="🔇 User Di-mute", value=f"`{len(self.muted_users)}`", inline=True)
        embed.add_field(name="⚠️ User Di-amaran", value=f"`{len(self.warned_users)}`", inline=True)

        await ctx.send(embed=embed, delete_after=30)

    @commands.command(name="clear_warn")
    @commands.has_permissions(administrator=True)
    async def clear_warning(self, ctx, member: discord.Member):
        if member.id in self.warned_users:
            self.warned_users.remove(member.id)
            await ctx.send(f"✅ Amaran untuk **{member.display_name}** dibersihkan!", delete_after=5)
        else:
            await ctx.send(f"📭 **{member.display_name}** tiada amaran.", delete_after=5)

async def setup(bot):
    await bot.add_cog(AntiSpamCog(bot))
