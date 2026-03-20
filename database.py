"""
Database Cog - MongoDB Operations (OPTIMIZED V2)
"""
import asyncio
import discord
from discord.ext import commands
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument, UpdateOne
from datetime import datetime, timedelta
import logging
from config import Config
from bson.objectid import ObjectId

logger = logging.getLogger('DkatanaBot')

class DatabaseCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.client = None
        self.db = None
        self.connect()

    def connect(self):
        try:
            self.client = MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=5000)
            self.db = self.client[Config.DB_NAME]

            # Create indexes for speed
            self.db.users.create_index("user_id", unique=True)
            self.db.attendance.create_index([("user_id", ASCENDING), ("date", ASCENDING)])
            self.db.leave_requests.create_index("user_id")
            self.db.settings.create_index("guild_id", unique=True)
            self.db.leaderboard.create_index("user_id", unique=True)
            self.db.voice_lock_history.create_index("timestamp")

            logger.info("✅ MongoDB connected")
        except Exception as e:
            logger.error(f"❌ MongoDB error: {e}")
            raise

    async def _run(self, fn, *args, **kwargs):
        """Run pymongo sync ops in a thread to prevent blocking the event loop."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    # ========== USER ==========
    async def get_user(self, user_id: int):
        return await self._run(self.db.users.find_one, {"user_id": user_id})

    
    # ========== FIXED JERSEY NUMBER SYSTEM ==========
    async def get_or_assign_jersey_number(self, user_id: int, guild_id: int, username: str, display_name: str) -> int:
        """
        Get existing jersey number or assign new one.
        Jersey numbers are FIXED and permanent like sports jersey numbers.
        """
        # Check if user already has a jersey number
        user = await self._run(self.db.users.find_one, {"user_id": user_id})

        if user and user.get("jersey_number"):
            return user["jersey_number"]

        # Assign new jersey number
        new_number = await self.get_next_available_jersey_number()

        if user:
            # Update existing user with jersey number
            await self._run(
                self.db.users.update_one,
                {"user_id": user_id},
                {"$set": {
                    "jersey_number": new_number,
                    "jersey_assigned_at": datetime.now(),
                    "updated_at": datetime.now()
                }}
            )
        else:
            # Create new user with jersey number
            user_data = {
                "user_id": user_id,
                "guild_id": guild_id,
                "username": username,
                "display_name": display_name,
                "jersey_number": new_number,  # FIXED NUMBER - Permanent
                "leaderboard_number": new_number,  # For backward compatibility
                "jersey_assigned_at": datetime.now(),
                "role_ahli": True,
                "join_date": datetime.now(),
                "created_at": datetime.now(),
                "updated_at": datetime.now()
            }
            await self._run(self.db.users.insert_one, user_data)

        logger.info(f"🎽 Assigned jersey number #{new_number} to {display_name} ({user_id})")
        return new_number

    async def get_next_available_jersey_number(self) -> int:
        """Get next available jersey number (fills gaps if any)"""
        # Get all used jersey numbers
        used_numbers = set()
        users = await self._run(lambda: list(self.db.users.find({"jersey_number": {"$exists": True}})))
        for user in users:
            if user.get("jersey_number"):
                used_numbers.add(user["jersey_number"])

        # Find first available number starting from 1
        number = 1
        while number in used_numbers:
            number += 1

        return number

    async def get_user_by_jersey_number(self, jersey_number: int):
        """Find user by their jersey number"""
        return await self._run(self.db.users.find_one, {"jersey_number": jersey_number})

    async def transfer_jersey_number(self, from_user_id: int, to_user_id: int, admin_id: int = None):
        """
        Transfer jersey number from one user to another (rare case, requires admin).
        This should only be used in special circumstances.
        """
        from_user = await self.get_user(from_user_id)
        to_user = await self.get_user(to_user_id)

        if not from_user or not from_user.get("jersey_number"):
            return False, "Pengguna asal tidak mempunyai nombor jersei"

        if to_user and to_user.get("jersey_number"):
            return False, "Pengguna baru sudah mempunyai nombor jersei"

        jersey_num = from_user["jersey_number"]

        # Transfer the number
        self.db.users.update_one(
            {"user_id": from_user_id},
            {"$unset": {"jersey_number": ""},
             "$set": {"jersey_transferred_at": datetime.now(), "updated_at": datetime.now()}}
        )

        if to_user:
            self.db.users.update_one(
                {"user_id": to_user_id},
                {"$set": {
                    "jersey_number": jersey_num,
                    "jersey_transferred_from": from_user_id,
                    "jersey_assigned_at": datetime.now(),
                    "updated_at": datetime.now()
                }}
            )
        else:
            # Create placeholder for transfer
            self.db.users.insert_one({
                "user_id": to_user_id,
                "jersey_number": jersey_num,
                "jersey_transferred_from": from_user_id,
                "jersey_assigned_at": datetime.now(),
                "created_at": datetime.now()
            })

        # Log the transfer
        self.db.jersey_transfers.insert_one({
            "jersey_number": jersey_num,
            "from_user_id": from_user_id,
            "to_user_id": to_user_id,
            "transferred_by": admin_id,
            "transferred_at": datetime.now()
        })

        return True, f"Nombor jersei #{jersey_num} berjaya dipindahkan"

    async def get_jersey_number_stats(self):
        """Get statistics about jersey numbers"""
        total_assigned = self.db.users.count_documents({"jersey_number": {"$exists": True}})

        # Find gaps in numbering
        used_numbers = sorted([u["jersey_number"] for u in self.db.users.find({"jersey_number": {"$exists": True}}) if u.get("jersey_number")])

        gaps = []
        if used_numbers:
            expected = 1
            for num in used_numbers:
                while expected < num:
                    gaps.append(expected)
                    expected += 1
                expected += 1

        return {
            "total_assigned": total_assigned,
            "highest_number": max(used_numbers) if used_numbers else 0,
            "available_gaps": gaps[:10],  # Show first 10 gaps
            "next_available": await self.get_next_available_jersey_number()
        }

    
    async def create_user(self, user_id: int, guild_id: int, username: str, display_name: str):
        # Use fixed jersey number system
        jersey_number = await self.get_or_assign_jersey_number(user_id, guild_id, username, display_name)

        user_data = {
            "user_id": user_id,
            "guild_id": guild_id,
            "username": username,
            "display_name": display_name,
            "jersey_number": jersey_number,  # FIXED - Permanent number
            "leaderboard_number": jersey_number,  # For backward compatibility
            "role_ahli": True,
            "join_date": datetime.now(),
            "created_at": datetime.now(),
            "updated_at": datetime.now()
        }
        try:
            self.db.users.insert_one(user_data)
            return user_data
        except:
            return None

    async def get_next_leaderboard_number(self):
        last = self.db.users.find_one(sort=[("leaderboard_number", DESCENDING)])
        return (last["leaderboard_number"] + 1) if last and "leaderboard_number" in last else 1

    async def get_all_ahli_dkatana(self):
        return list(self.db.users.find({"role_ahli": True}))

    async def mark_for_deletion(self, user_id: int, guild_id: int):
        """Mark user for deletion after 5 days"""
        deletion_time = datetime.now() + timedelta(days=5)
        self.db.users.update_one(
            {"user_id": user_id},
            {"$set": {"marked_for_deletion": deletion_time, "left_guild_at": datetime.now()}}
        )

    # ========== ATTENDANCE ==========
    async def get_attendance(self, user_id: int, date: str):
        return self.db.attendance.find_one({"user_id": user_id, "date": date})

    async def mark_attendance(self, user_id: int, date: str, status: str, voice_minutes: int = 0, notes: str = None):
        self.db.attendance.update_one(
            {"user_id": user_id, "date": date},
            {"$set": {
                "status": status,
                "voice_minutes": voice_minutes,
                "notes": notes,
                "marked_at": datetime.now()
            }},
            upsert=True
        )

    # ========== USER STATS ==========
    async def update_user_stats(self, user_id: int, status: str):
        """Simpan statistik ringkas per user"""
        try:
            key = (status or "").lower().strip()
            if key not in {"hadir", "cuti", "tidak_hadir", "cuti_rasmi", "cuti_am"}:
                key = "lain"

            self.db.leaderboard.update_one(
                {"user_id": user_id},
                {
                    "$inc": {f"stats.{key}": 1},
                    "$set": {"last_status": status, "updated_at": datetime.now()},
                    "$setOnInsert": {"created_at": datetime.now()},
                },
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"update_user_stats warning: {e}")

    async def has_marked_today(self, user_id: int, date: str):
        att = await self.get_attendance(user_id, date)
        return att is not None and att.get("status") in ["hadir", "cuti"]

    async def get_all_attendance_for_week(self, week_dates: list):
        """Get all attendance for multiple dates in ONE query"""
        return list(self.db.attendance.find({"date": {"$in": week_dates}}))

    async def get_attendance_stats(self, date_str: str):
        """Get attendance stats for a specific date"""
        pipeline = [
            {"$match": {"date": date_str}},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}}
        ]
        results = list(self.db.attendance.aggregate(pipeline))
        stats = {r["_id"]: r["count"] for r in results}
        return {
            "hadir": stats.get("hadir", 0),
            "cuti": stats.get("cuti", 0),
            "cuti_rasmi": stats.get("cuti_rasmi", 0),
            "cuti_am": stats.get("cuti_am", 0),
            "tidak_hadir": stats.get("tidak_hadir", 0)
        }

    # ========== LEAVE ==========
    def _to_object_id(self, request_id: str):
        try:
            return ObjectId(request_id)
        except Exception:
            return None

    async def create_leave_request(self, user_id: int, username: str, start_date: str, end_date: str, reason: str, leaderboard_number: int):
        leave_data = {
            "user_id": user_id,
            "username": username,
            "leaderboard_number": leaderboard_number,
            "start_date": start_date,
            "end_date": end_date,
            "reason": reason,
            "status": "pending",
            "requested_at": datetime.now()
        }
        result = self.db.leave_requests.insert_one(leave_data)
        leave_data["_id"] = result.inserted_id
        return leave_data

    async def get_leave_request(self, request_id):
        oid = self._to_object_id(str(request_id))
        if not oid:
            return None
        return self.db.leave_requests.find_one({"_id": oid})

    def _set_leave_status_if_pending_sync(self, request_id: str, status: str, approved_by: int | None = None):
        oid = self._to_object_id(str(request_id))
        if not oid:
            return None
        return self.db.leave_requests.find_one_and_update(
            {"_id": oid, "status": "pending"},
            {"$set": {
                "status": status,
                "approved_at": datetime.now() if status != "pending" else None,
                "approved_by": approved_by,
            }},
            return_document=ReturnDocument.AFTER,
        )

    async def set_leave_status_if_pending(self, request_id: str, status: str, approved_by: int | None = None):
        return await asyncio.to_thread(self._set_leave_status_if_pending_sync, request_id, status, approved_by)

    async def update_leave_request(self, request_id, status: str, approved_by: int = None):
        oid = self._to_object_id(str(request_id))
        if not oid:
            return
        self.db.leave_requests.update_one(
            {"_id": oid},
            {"$set": {
                "status": status,
                "approved_at": datetime.now() if status != "pending" else None,
                "approved_by": approved_by
            }}
        )

    # ========== BULK ATTENDANCE ==========
    def _bulk_mark_attendance_sync(self, user_id: int, dates: list, status: str, voice_minutes: int = 0, notes: str | None = None):
        now = datetime.now()
        ops = []
        for d in dates:
            ops.append(
                UpdateOne(
                    {"user_id": user_id, "date": d},
                    {"$set": {
                        "status": status,
                        "voice_minutes": voice_minutes,
                        "notes": notes,
                        "marked_at": now,
                    }},
                    upsert=True,
                )
            )
        if ops:
            self.db.attendance.bulk_write(ops, ordered=False)

    async def bulk_mark_attendance(self, user_id: int, dates: list, status: str, voice_minutes: int = 0, notes: str | None = None):
        if not dates:
            return
        await asyncio.to_thread(self._bulk_mark_attendance_sync, user_id, dates, status, voice_minutes, notes)

    async def get_pending_leave_requests(self):
        return list(self.db.leave_requests.find({"status": "pending"}))


    async def set_leave_status(self, request_id: str, status: str, approved_by: int | None = None):
        doc = await self.set_leave_status_if_pending(request_id, status, approved_by)
        return doc is not None

    async def delete_leave_request(self, request_id: str):
        oid = self._to_object_id(str(request_id))
        if not oid:
            return False
        result = await self._run(self.db.leave_requests.delete_one, {"_id": oid})
        return bool(getattr(result, 'deleted_count', 0))
    async def is_on_leave(self, user_id: int, date: str):
        return self.db.leave_requests.find_one({
            "user_id": user_id,
            "status": "approved",
            "start_date": {"$lte": date},
            "end_date": {"$gte": date}
        })

    # ========== SETTINGS ==========
    async def get_guild_settings(self, guild_id: int):
        settings = await self._run(self.db.settings.find_one, {"guild_id": guild_id})
        if not settings:
            settings = {
                "guild_id": guild_id,
                "official_leave_days": [],
                "attendance_time": Config.ATTENDANCE_TIME,
                "attendance_cutoff": Config.ATTENDANCE_CUTOFF_TIME,
                "voice_required_minutes": Config.VOICE_REQUIRED_MINUTES,
                "leaderboard_role_id": Config.AHLI_DKATANA_ROLE_ID
            }
            await self._run(self.db.settings.insert_one, settings)
        return settings

    async def update_guild_settings(self, guild_id: int, data: dict):
        data["updated_at"] = datetime.now()
        await self._run(self.db.settings.update_one, {"guild_id": guild_id}, {"$set": data}, upsert=True)

    # ========== VOICE LOCK HISTORY ==========
    async def log_voice_lock_action(self, admin_id: int, admin_name: str, action: str, reason: str = None):
        """Log voice channel lock/unlock actions"""
        await self._run(self.db.voice_lock_history.insert_one, {
            "admin_id": admin_id,
            "admin_name": admin_name,
            "action": action,  # "lock" or "unlock"
            "reason": reason,
            "timestamp": datetime.now()
        })

    async def get_recent_voice_lock_actions(self, limit: int = 10):
        """Get recent voice lock/unlock actions"""
        return await self._run(lambda: list(self.db.voice_lock_history.find().sort("timestamp", DESCENDING).limit(limit)))

    # ========== CLEANUP ==========
    async def cleanup_old_data(self):
        cutoff = datetime.now()
        users = await self._run(lambda: list(self.db.users.find({"marked_for_deletion": {"$lte": cutoff}})))
        deleted = 0
        for user in users:
            self.delete_user(user["user_id"])
            deleted += 1
        return deleted

    async def delete_user(self, user_id: int):
        await self._run(self.db.users.delete_one, {"user_id": user_id})
        await self._run(self.db.attendance.delete_many, {"user_id": user_id})
        await self._run(self.db.leave_requests.delete_many, {"user_id": user_id})
        self.db.leaderboard.delete_one({"user_id": user_id})

    async def reset_weekly_data(self):
        """Reset weekly data - placeholder for weekly reset functionality"""
        pass


    # ========== ARCHIVE / SOFT DELETE ==========
    async def archive_old_attendance(self, days: int = 7):
        """
        Soft delete: Move attendance data older than X days to archive collection
        Returns: (archived_count, errors)
        """
        try:
            cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            archive_collection_name = f"{Config.ARCHIVE_COLLECTION_PREFIX}attendance"

            # Find old records
            old_records = list(self.db.attendance.find({"date": {"$lt": cutoff_date}}))

            if not old_records:
                return 0, 0

            archived_count = 0
            error_count = 0

            # Move to archive collection
            for record in old_records:
                try:
                    # Add metadata
                    record["archived_at"] = datetime.now()
                    record["archived_by"] = "system_auto"
                    record["retention_days"] = days

                    # Insert to archive
                    self.db[archive_collection_name].insert_one(record)

                    # Delete from main collection
                    self.db.attendance.delete_one({"_id": record["_id"]})

                    archived_count += 1
                except Exception as e:
                    logger.error(f"Error archiving record {record.get('_id')}: {e}")
                    error_count += 1

            logger.info(f"📦 Archived {archived_count} attendance records (older than {days} days)")
            if error_count > 0:
                logger.warning(f"⚠️ {error_count} errors during archiving")

            return archived_count, error_count

        except Exception as e:
            logger.error(f"Error in archive_old_attendance: {e}")
            return 0, 0

    async def archive_old_leave_requests(self, days: int = 30):
        """
        Archive processed leave requests older than X days
        Keeps pending requests
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=days)
            archive_collection_name = f"{Config.ARCHIVE_COLLECTION_PREFIX}leave_requests"

            # Find old processed (non-pending) records
            old_records = list(self.db.leave_requests.find({
                "requested_at": {"$lt": cutoff_date},
                "status": {"$ne": "pending"}
            }))

            if not old_records:
                return 0, 0

            archived_count = 0
            error_count = 0

            for record in old_records:
                try:
                    record["archived_at"] = datetime.now()
                    record["archived_by"] = "system_auto"

                    self.db[archive_collection_name].insert_one(record)
                    self.db.leave_requests.delete_one({"_id": record["_id"]})

                    archived_count += 1
                except Exception as e:
                    logger.error(f"Error archiving leave request {record.get('_id')}: {e}")
                    error_count += 1

            logger.info(f"📦 Archived {archived_count} leave requests (older than {days} days)")
            return archived_count, error_count

        except Exception as e:
            logger.error(f"Error in archive_old_leave_requests: {e}")
            return 0, 0

    async def get_archive_stats(self):
        """Get statistics about archived data"""
        try:
            stats = {
                "attendance_archive": self.db[f"{Config.ARCHIVE_COLLECTION_PREFIX}attendance"].count_documents({}),
                "leave_archive": self.db[f"{Config.ARCHIVE_COLLECTION_PREFIX}leave_requests"].count_documents({}),
                "current_attendance": self.db.attendance.count_documents({}),
                "current_leave": self.db.leave_requests.count_documents({})
            }
            return stats
        except Exception as e:
            logger.error(f"Error getting archive stats: {e}")
            return {}

    async def restore_from_archive(self, user_id: int = None, date: str = None, limit: int = 100):
        """
        Restore data from archive to main collection
        For admin use only
        """
        try:
            archive_coll = self.db[f"{Config.ARCHIVE_COLLECTION_PREFIX}attendance"]
            query = {}
            if user_id:
                query["user_id"] = user_id
            if date:
                query["date"] = date

            records = list(archive_coll.find(query).limit(limit))

            restored = 0
            for record in record:
                try:
                    # Remove archive metadata
                    record.pop("archived_at", None)
                    record.pop("archived_by", None)
                    record.pop("retention_days", None)

                    # Restore to main collection
                    self.db.attendance.insert_one(record)

                    # Delete from archive
                    archive_coll.delete_one({"_id": record["_id"]})
                    restored += 1
                except Exception as e:
                    logger.error(f"Error restoring record: {e}")

            return restored
        except Exception as e:
            logger.error(f"Error in restore_from_archive: {e}")
            return 0

async def setup(bot):
    await bot.add_cog(DatabaseCog(bot))
