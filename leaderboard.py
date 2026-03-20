import discord
from discord.ext import commands
from datetime import datetime, timedelta
import pytz
import logging
import asyncio
from config import Config

logger = logging.getLogger('DkatanaBot')


class LeaderboardCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cache = {}
        self.cache_time = None
        self.data = None
        self.week_start = None
        self.week_end = None

        # Anti-duplicate / smoother updates
        self._lb_lock = asyncio.Lock()
        self._update_task: asyncio.Task | None = None
        self._update_requested = False
        self._message_cache: dict[int, discord.Message] = {}
        self._last_update_at = None
        self._min_update_interval_sec = 8.0

    def get_time(self):
        return datetime.now(pytz.timezone(Config.TIMEZONE))

    def get_week_range(self):
        today = self.get_time()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        return monday.strftime("%Y-%m-%d"), sunday.strftime("%Y-%m-%d")

    def format_date(self, date_str):
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%m/%Y")

    def get_day_abbr(self, date_str):
        days = ['I', 'S', 'R', 'K', 'J', 'S', 'A']
        return days[datetime.strptime(date_str, "%Y-%m-%d").weekday()]

    def get_emoji(self, status):
        return {"hadir": "🟢", "cuti": "🟡", "tidak_hadir": "🔴", "cuti_rasmi": "🏖️", "cuti_am": "🏝️", None: "⚪"}.get(status, "⚪")

    async def refresh_data(self):
        """Fetch all data ONCE and cache"""
        db_cog = self.bot.get_cog('DatabaseCog')
        self.week_start, self.week_end = self.get_week_range()

        guild = self.bot.get_guild(Config.GUILD_ID)
        if not guild:
            return

        ahli_role = guild.get_role(Config.AHLI_DKATANA_ROLE_ID)
        if not ahli_role:
            return

        members = {m.id: m for m in guild.members if ahli_role in m.roles and not m.bot}

        settings = await db_cog.get_guild_settings(Config.GUILD_ID)
        official_days = settings.get("official_leave_days", [])

        monday = datetime.strptime(self.week_start, "%Y-%m-%d")
        week_dates = [(monday + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

        all_attendance = await db_cog.get_all_attendance_for_week(week_dates)

        att_by_user = {}
        for att in all_attendance:
            uid = att["user_id"]
            if uid not in att_by_user:
                att_by_user[uid] = {}
            att_by_user[uid][att["date"]] = att["status"]

        self.data = []
        for uid, member in members.items():
            user_data = await db_cog.get_user(uid)
            if not user_data:
                user_data = await db_cog.create_user(uid, guild.id, member.name, member.display_name)

            attendance = []
            for date_str in week_dates:
                day_dt = datetime.strptime(date_str, "%Y-%m-%d")
                if day_dt.weekday() in official_days:
                    attendance.append("cuti_rasmi")
                elif uid in att_by_user and date_str in att_by_user[uid]:
                    attendance.append(att_by_user[uid][date_str])
                elif day_dt.date() < self.get_time().date():
                    attendance.append("tidak_hadir")
                else:
                    attendance.append(None)

            self.data.append({
                "mention": member.mention,
                "number": user_data.get("leaderboard_number", 0),
                "jersey_number": user_data.get("jersey_number", user_data.get("leaderboard_number", 0)),
                "attendance": attendance
            })

        # Sort by jersey number (fixed number) for consistent display
        self.data.sort(key=lambda x: x.get("jersey_number", x.get("number", 999)))
        self.cache_time = self.get_time()

        self.build_embeds()

    def build_embeds(self):
        """Pre-build all embeds with fixed jersey numbers"""
        if not self.data:
            return

        USERS_PER_EMBED = 15

        monday = datetime.strptime(self.week_start, "%Y-%m-%d")
        day_headers = [self.get_day_abbr((monday + timedelta(days=i)).strftime("%Y-%m-%d")) for i in range(7)]

        self.cache = {}
        total_batches = (len(self.data) + USERS_PER_EMBED - 1) // USERS_PER_EMBED

        for batch_idx in range(total_batches):
            batch_start = batch_idx * USERS_PER_EMBED
            batch_end = min(batch_start + USERS_PER_EMBED, len(self.data))
            batch = self.data[batch_start:batch_end]

            is_first = (batch_idx == 0)

            if is_first:
                embed = discord.Embed(
                    title=f"📊 CATATAN KEHADIRAN AHLI DKATANA 📊",
                    description=f"📅 **{self.format_date(self.week_start)}** `->` **{self.format_date(self.week_end)}**\n",
                    color=0xFFD700
                )
            else:
                embed = discord.Embed(
                    title=f"📊 (Sambungan {batch_idx + 1}/{total_batches}) 📊",
                    description="",
                    color=0xFFD700
                )

            text = ""
            for i, user in enumerate(batch, start=batch_start + 1):
                emojis = "".join([self.get_emoji(s) for s in user["attendance"]])

                # Get jersey number (fixed number like sports jersey)
                jersey_num = user.get("jersey_number", user.get("number", i))

                # Format jersey number with fixed width (like #01, #10, #99)
                jersey_display = f"🎽#{jersey_num:02d}" if isinstance(jersey_num, int) else f"🎽#{jersey_num}"

                # Rank medals for top 3
                rank = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")

                # Format: [Rank] [Jersey#] @User
                text += f"`{rank:<3}` {jersey_display} {user['mention']}\n`      {emojis}`\n\n"

            embed.add_field(name=f"📋 Senarai ({batch_start + 1}-{batch_end})", value=text or "📭 Tiada", inline=False)

            if is_first:
                embed.add_field(name="📖 **PPETUNJUK**", value="🟢= *Hadir*\n🟡= *Cuti*\n🔴= *Tidak Hadir*\n🏖️= *Cuti Rasmi*\n🏝️= *Cuti Am*\n⚪= *Belum Direkodkan*\n🎽= *Nombor Jersi*", inline=False)

            embed.set_footer(text=f"🔄 {self.get_time().strftime('%H:%M:%S')} | {len(self.data)} ahli | Ꭰ'Ҡᴀᴛᴀɴᴀ⚡ ・ AttendanceSystem")
            self.cache[batch_idx] = embed

    def get_cached_embed(self, page=0):
        """Get cached embed"""
        if not self.cache or not self.data or (self.get_time() - self.cache_time).seconds > 60:
            return None
        return self.cache.get(page)

    async def get_embed(self, page=0):
        """Get embed (refresh if needed)"""
        if not self.cache or (self.get_time() - self.cache_time).seconds > 60:
            await self.refresh_data()
        return self.cache.get(page, discord.Embed(title="📊 Leaderboard", description="📭 Tiada data"))

    async def update_leaderboard_channel(self, force_refresh: bool = True):
        """Update leaderboard channel (edit-in-place, elak delete+send yang lambat & duplicate)."""
        channel = self.bot.get_channel(Config.LEADERBOARD_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            logger.error("Leaderboard channel not found!")
            return

        async with self._lb_lock:
            now = self.get_time()
            if self._last_update_at and (now - self._last_update_at).total_seconds() < self._min_update_interval_sec:
                return
            self._last_update_at = self.get_time()
            if force_refresh or not self.cache or not self.data:
                await self.refresh_data()

            embeds = [self.cache[p] for p in sorted(self.cache.keys())] if self.cache else []
            if not embeds:
                embeds = [discord.Embed(title="📊 Leaderboard", description="📭 Tiada data")]

            db_cog = self.bot.get_cog('DatabaseCog')
            allowed = discord.AllowedMentions(users=False, roles=False, everyone=False)

            msg_ids: list[int] = []
            if db_cog:
                settings = await db_cog.get_guild_settings(channel.guild.id)
                raw_ids = settings.get('attendance_leaderboard_message_ids')
                if isinstance(raw_ids, list):
                    msg_ids = [int(x) for x in raw_ids if x]
                else:
                    one = settings.get('attendance_leaderboard_message_id')
                    msg_ids = [int(one)] if one else []

            new_ids: list[int] = []
            for i, emb in enumerate(embeds):
                msg = None
                if i < len(msg_ids):
                    try:
                        mid = int(msg_ids[i])
                        msg = self._message_cache.get(mid)
                        if msg is None:
                            msg = await channel.fetch_message(mid)
                            self._message_cache[mid] = msg
                        old_embed = msg.embeds[0].to_dict() if msg.embeds else None
                        new_embed = emb.to_dict()
                        old_content = msg.content or ""
                        if old_embed != new_embed or old_content != "":
                            await msg.edit(content=None, embed=emb, allowed_mentions=allowed)
                            await asyncio.sleep(0.9)
                    except Exception:
                        msg = None
                if msg is None:
                    msg = await channel.send(embed=emb, allowed_mentions=allowed)
                    self._message_cache[msg.id] = msg
                    await asyncio.sleep(1.1)
                new_ids.append(msg.id)

            # padam mesej lama yang terlebih
            for j in range(len(embeds), len(msg_ids)):
                try:
                    mid = int(msg_ids[j])
                    m = self._message_cache.get(mid) or await channel.fetch_message(mid)
                    await m.delete()
                    self._message_cache.pop(mid, None)
                except Exception:
                    pass

            if db_cog:
                await db_cog.update_guild_settings(channel.guild.id, {
                    'attendance_leaderboard_message_ids': new_ids,
                    'attendance_leaderboard_message_id': new_ids[0] if new_ids else None,
                })

    async def update_leaderboard_message(self):
        """Alias untuk update_leaderboard_channel"""
        await self.update_leaderboard_channel(force_refresh=True)

    def request_leaderboard_update(self, delay: float = 2.0):
        """Debounce update leaderboard supaya interaction lebih smooth (kurang duplicate)."""
        self._update_requested = True
        if self._update_task and not self._update_task.done():
            return

        async def runner():
            await asyncio.sleep(max(0.0, delay))
            while self._update_requested:
                self._update_requested = False
                await self.update_leaderboard_channel(force_refresh=True)
                if self._update_requested:
                    await asyncio.sleep(1.0)

        self._update_task = asyncio.create_task(runner())

    # ========== COMMANDS ==========
    @commands.command(name="setup_leaderboard")
    @commands.has_permissions(administrator=True)
    async def setup_leaderboard_cmd(self, ctx):
        """Setup leaderboard in channel"""
        await ctx.message.delete()

        async for msg in ctx.channel.history(limit=20):
            if msg.author == self.bot.user:
                await msg.delete()

        await self.refresh_data()

        if not self.data:
            await ctx.send(embed=discord.Embed(title="📊 Leaderboard", description="📭 Tiada data"))
            return

        for page in sorted(self.cache.keys()):
            await ctx.send(embed=self.cache[page])

    @commands.command(name="refresh_leaderboard")
    @commands.has_permissions(administrator=True)
    async def refresh_leaderboard_cmd(self, ctx):
        """Refresh leaderboard"""
        await ctx.message.delete()
        await self.update_leaderboard_channel()
        await ctx.send("✅ Leaderboard diupdate!", delete_after=3)

    @commands.command(name="leaderboard")
    async def leaderboard_cmd(self, ctx):
        """Show leaderboard"""
        await self.refresh_data()

        if not self.data:
            await ctx.send(embed=discord.Embed(title="📊 Leaderboard", description="📭 Tiada data"))
            return

        for page in sorted(self.cache.keys()):
            await ctx.send(embed=self.cache[page], delete_after=60)

async def setup(bot):
    await bot.add_cog(LeaderboardCog(bot))
