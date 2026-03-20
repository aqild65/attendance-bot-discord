"""
Voice Manager Cog - With Realtime Status
"""
import discord
from discord.ext import commands, tasks
from datetime import datetime
import pytz
import logging
from config import Config

logger = logging.getLogger('DkatanaBot')


class VoiceManagerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_start_times = {}  # {user_id: datetime}
        self.update_status.start()

    def cog_unload(self):
        self.update_status.cancel()

    def get_time(self):
        return datetime.now(pytz.timezone(Config.TIMEZONE))

    # ========== VOICE STATUS UPDATE ==========
    async def update_voice_status(self, member, before, after):
        """Update voice status with realtime duration"""
        if member.bot:
            return

        user_id = member.id

        # User joined voice channel
        if after.channel and (not before.channel or before.channel.id != after.channel.id):
            self.voice_start_times[user_id] = self.get_time()

        # User left voice channel
        elif before.channel and (not after.channel or before.channel.id != after.channel.id):
            if user_id in self.voice_start_times:
                del self.voice_start_times[user_id]

    @tasks.loop(seconds=30)
    async def update_status(self):
        """Update voice channel status every 30 seconds"""
        try:
            guild = self.bot.get_guild(Config.GUILD_ID)
            if not guild:
                return

            channel = guild.get_channel(Config.VOICE_ATTENDANCE_CHANNEL_ID)
            if not channel:
                return

            current_time = self.get_time().strftime("%H:%M")
            member_count = len([m for m in channel.members if not m.bot])

            try:
                await channel.edit(status=f"🕐 {current_time} | 👥 {member_count} ahli")
            except:
                pass

        except Exception as e:
            logger.error(f"Update status error: {e}")

    @update_status.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()

    # ========== COMMANDS ==========
    @commands.command(name="voice_info")
    async def voice_info_cmd(self, ctx):
        """Show voice channel info"""
        if not ctx.author.voice:
            await ctx.send("❌ Anda tidak berada dalam voice channel!", delete_after=5)
            return

        channel = ctx.author.voice.channel
        members = channel.members

        embed = discord.Embed(
            title=f"🎤 {channel.name}",
            description=f"**{len(members)}** ahli dalam channel\n🕐 Masa: `{self.get_time().strftime('%H:%M')}`",
            color=0x3498DB
        )

        member_list = ""
        for m in members:
            if m.bot:
                continue

            duration_str = ""
            if m.id in self.voice_start_times:
                secs = (self.get_time() - self.voice_start_times[m.id]).total_seconds()
                mins = int(secs // 60)
                hrs = int(mins // 60)
                remaining_mins = mins % 60

                if hrs > 0:
                    duration_str = f" ({hrs}j {remaining_mins}m)"
                else:
                    duration_str = f" ({mins}m)"

            status = "🔇" if m.voice.mute else ""
            status += "🔕" if m.voice.deaf else ""
            status += "🎥" if m.voice.self_video else ""
            status += "🖥️" if m.voice.self_stream else ""

            member_list += f"{m.display_name}{duration_str} {status}\n"

        if member_list:
            embed.add_field(name="👥 Ahli", value=f"```{member_list}```", inline=False)

        embed.add_field(name="📍 ID", value=f"`{channel.id}`", inline=True)
        embed.add_field(name="🔊 Bitrate", value=f"`{channel.bitrate//1000} kbps`", inline=True)
        embed.add_field(name="👥 Limit", value=f"`{channel.user_limit or 'Tiada'}`", inline=True)

        await ctx.send(embed=embed, delete_after=30)

    @commands.command(name="lock")
    @commands.has_permissions(administrator=True)
    async def lock_cmd(self, ctx, channel: discord.VoiceChannel = None, *, reason: str = "Tiada sebab"):
        """Lock voice channel"""
        channel = channel or (ctx.author.voice.channel if ctx.author.voice else None)

        if not channel:
            await ctx.send("❌ Sila masuk voice channel atau specify channel!", delete_after=5)
            return

        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.connect = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)

        embed = discord.Embed(title="🔒 Voice Channel Dikunci", description=f"**{channel.name}**", color=0xFF0000)
        embed.add_field(name="🔒 Oleh", value=ctx.author.mention, inline=True)
        embed.add_field(name="📝 Sebab", value=f"```{reason}```", inline=False)

        await ctx.send(embed=embed)

        log = self.bot.get_cog('LoggerCog')
        if log:
            await log.log_voice_action(ctx.author, "LOCK", channel, reason)

    @commands.command(name="unlock")
    @commands.has_permissions(administrator=True)
    async def unlock_cmd(self, ctx, channel: discord.VoiceChannel = None):
        """Unlock voice channel"""
        channel = channel or (ctx.author.voice.channel if ctx.author.voice else None)

        if not channel:
            await ctx.send("❌ Sila masuk voice channel atau specify channel!", delete_after=5)
            return

        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.connect = True
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)

        embed = discord.Embed(title="🔓 Voice Channel Dibuka", description=f"**{channel.name}**", color=0x00FF00)
        embed.add_field(name="🔓 Oleh", value=ctx.author.mention, inline=True)

        await ctx.send(embed=embed)

        log = self.bot.get_cog('LoggerCog')
        if log:
            await log.log_voice_action(ctx.author, "UNLOCK", channel)

    @commands.command(name="kick")
    @commands.has_permissions(administrator=True)
    async def kick_cmd(self, ctx, member: discord.Member, *, reason: str = "Tiada sebab"):
        """Kick user from voice"""
        if not member.voice:
            await ctx.send(f"❌ {member.mention} tidak dalam voice channel!", delete_after=5)
            return

        await member.move_to(None, reason=f"Dikick oleh {ctx.author.name}: {reason}")

        embed = discord.Embed(title="👢 User Dikick", color=0xFFA500)
        embed.add_field(name="👤", value=member.mention, inline=True)
        embed.add_field(name="👢 Oleh", value=ctx.author.mention, inline=True)
        embed.add_field(name="📝", value=f"```{reason}```", inline=False)

        await ctx.send(embed=embed)

        log = self.bot.get_cog('LoggerCog')
        if log:
            await log.log_voice_kick(ctx.author, member, member.voice.channel, reason)

    @commands.command(name="mute")
    @commands.has_permissions(administrator=True)
    async def mute_cmd(self, ctx, member: discord.Member, *, reason: str = "Tiada sebab"):
        """Mute user"""
        if not member.voice:
            await ctx.send(f"❌ {member.mention} tidak dalam voice channel!", delete_after=5)
            return

        await member.edit(mute=True, reason=f"Dimute oleh {ctx.author.name}: {reason}")

        embed = discord.Embed(title="🔇 User Dimute", color=0xFFA500)
        embed.add_field(name="👤", value=member.mention, inline=True)
        embed.add_field(name="👤 Oleh", value=ctx.author.mention, inline=True)

        await ctx.send(embed=embed)

    @commands.command(name="unmute")
    @commands.has_permissions(administrator=True)
    async def unmute_cmd(self, ctx, member: discord.Member):
        """Unmute user"""
        if not member.voice:
            await ctx.send(f"❌ {member.mention} tidak dalam voice channel!", delete_after=5)
            return

        await member.edit(mute=False, reason=f"Diunmute oleh {ctx.author.name}")

        embed = discord.Embed(title="🔊 User Diunmute", color=0x00FF00)
        embed.add_field(name="👤", value=member.mention, inline=True)

        await ctx.send(embed=embed)

    @commands.command(name="move")
    @commands.has_permissions(administrator=True)
    async def move_cmd(self, ctx, member: discord.Member, channel: discord.VoiceChannel):
        """Move user to channel"""
        if not member.voice:
            await ctx.send(f"❌ {member.mention} tidak dalam voice channel!", delete_after=5)
            return

        old = member.voice.channel
        await member.move_to(channel, reason=f"Dipindahkan oleh {ctx.author.name}")

        embed = discord.Embed(title="📍 User Dipindahkan", color=0x3498DB)
        embed.add_field(name="👤", value=member.mention, inline=True)
        embed.add_field(name="📍 Dari", value=old.mention, inline=True)
        embed.add_field(name="📍 Ke", value=channel.mention, inline=True)

        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(VoiceManagerCog(bot))
