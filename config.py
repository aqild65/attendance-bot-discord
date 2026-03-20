"""
Konfigurasi Bot Dkatana V2 - Optimized
"""
import os

# Load .env jika wujud (Bot-Hosting kadang-kadang tidak auto-load)
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    # Kalau python-dotenv tiada, bot masih boleh jalan guna env variable dari panel.
    pass
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Konfigurasi untuk bot Dkatana"""

    # Bot Token (WAJIB)
    TOKEN = os.getenv('DISCORD_TOKEN', 'YOUR_BOT_TOKEN_HERE')

    # Prefix Command
    PREFIX = os.getenv('COMMAND_PREFIX', '!')

    # MongoDB Connection (WAJIB)
    MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017')
    DB_NAME = os.getenv('DB_NAME', 'dkatana_bot')

    # Channel IDs
    ATTENDANCE_CHANNEL_ID = int(os.getenv('ATTENDANCE_CHANNEL_ID', '0'))
    LEADERBOARD_CHANNEL_ID = int(os.getenv('LEADERBOARD_CHANNEL_ID', '0'))
    LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID', '0'))
    LOG_APPLY_CUTI_CHANNEL_ID = int(os.getenv('LOG_APPLY_CUTI_CHANNEL_ID', '0'))
    LOG_ADMIN_CHANNEL_ID = int(os.getenv('LOG_ADMIN_CHANNEL_ID', '0'))
    ADMIN_PANEL_CHANNEL_ID = int(os.getenv('ADMIN_PANEL_CHANNEL_ID', '0'))
    VOICE_ATTENDANCE_CHANNEL_ID = int(os.getenv('VOICE_ATTENDANCE_CHANNEL_ID', '0'))
    AFTER_ATTENDANCE_VC_ID = int(os.getenv('AFTER_ATTENDANCE_VC_ID', '0'))

    # Auto move ahli selepas HADIR berjaya direkodkan
    AUTO_MOVE_AFTER_HADIR = os.getenv('AUTO_MOVE_AFTER_HADIR', '0').strip().lower() in ('1', 'true', 'yes', 'y', 'on')

    # Kutipan Settings (Admin Panel)
    KUTIPAN_LEADERBOARD_CHANNEL_ID = int(os.getenv('KUTIPAN_LEADERBOARD_CHANNEL_ID', '0'))
    KUTIPAN_STATS_CHANNEL_ID = int(os.getenv('KUTIPAN_STATS_CHANNEL_ID', '0'))
    KUTIPAN_BACKUP_CHANNEL_ID = int(os.getenv('KUTIPAN_BACKUP_CHANNEL_ID', '0'))
    KUTIPAN_LOG_CHANNEL_ID = int(os.getenv('KUTIPAN_LOG_CHANNEL_ID', os.getenv('LOG_CHANNEL_ID', '0')))

    # Audit duit keluar (admin note)
    # Jika tak set, akan fallback ke KUTIPAN_LOG_CHANNEL_ID / LOG_CHANNEL_ID
    AUDIT_DUIT_LOG_CHANNEL_ID = int(
        os.getenv(
            'AUDIT_DUIT_LOG_CHANNEL_ID',
            os.getenv('KUTIPAN_LOG_CHANNEL_ID', os.getenv('LOG_CHANNEL_ID', '0'))
        )
    )

    # Bendahari Role (untuk panel kutipan)
    BENDAHARI_ROLE_ID = int(os.getenv('BENDAHARI_ROLE_ID', os.getenv('ADMIN_ROLE_ID', '0')))

    # Amaun kutipan
    # Default ikut server DK (boleh diubah dalam Admin Panel)
    KUTIPAN_WEEKLY_AMOUNT = int(os.getenv('KUTIPAN_WEEKLY_AMOUNT', '6000'))
    KUTIPAN_PENALTY_AMOUNT = int(os.getenv('KUTIPAN_PENALTY_AMOUNT', '12000'))

    # Role IDs
    AHLI_DKATANA_ROLE_ID = int(os.getenv('AHLI_DKATANA_ROLE_ID', '0'))
    ADMIN_ROLE_ID = int(os.getenv('ADMIN_ROLE_ID', '0'))

    # Guild ID
    GUILD_ID = int(os.getenv('GUILD_ID', '0'))

    # Attendance Settings
    ATTENDANCE_TIME = os.getenv('ATTENDANCE_TIME', '21:30')
    ATTENDANCE_CUTOFF_TIME = os.getenv('ATTENDANCE_CUTOFF_TIME', '03:59')
    VOICE_REQUIRED_MINUTES = int(os.getenv('VOICE_REQUIRED_MINUTES', '10'))

    # Timezone
    TIMEZONE = os.getenv('TIMEZONE', 'Asia/Kuala_Lumpur')

    # Auto Restart Time (5 PM)
    AUTO_RESTART_HOUR = int(os.getenv('AUTO_RESTART_HOUR', '17'))
    AUTO_RESTART_MINUTE = int(os.getenv('AUTO_RESTART_MINUTE', '0'))

    # Auto restart toggle
    AUTO_RESTART_ENABLED = os.getenv('AUTO_RESTART_ENABLED', '1').strip().lower() in ('1', 'true', 'yes', 'y', 'on')

    # Manual restart command toggle (default OFF - guna auto restart)
    ENABLE_MANUAL_RESTART = os.getenv('ENABLE_MANUAL_RESTART', '0').strip().lower() in ('1', 'true', 'yes', 'y', 'on')

    # Weekly Reset Day (0=Monday, 6=Sunday)
    WEEKLY_RESET_DAY = int(os.getenv('WEEKLY_RESET_DAY', '0'))

    # Anti-spam Settings
    SPAM_THRESHOLD = int(os.getenv('SPAM_THRESHOLD', '5'))
    SPAM_TIME_WINDOW = int(os.getenv('SPAM_TIME_WINDOW', '5'))
    SPAM_MUTE_DURATION = int(os.getenv('SPAM_MUTE_DURATION', '300'))

    # Colors
    COLOR_SUCCESS = 0x00FF00
    COLOR_WARNING = 0xFFA500
    COLOR_ERROR = 0xFF0000
    COLOR_INFO = 0x3498DB
    COLOR_GOLD = 0xFFD700
    COLOR_PURPLE = 0x9B59B6
    COLOR_DARK = 0x2C3E50
# Auto Setup on Startup
    AUTO_SETUP_ON_STARTUP = os.getenv('AUTO_SETUP_ON_STARTUP', 'true').lower() == 'true'

    # Clear existing messages on startup before posting new ones
    CLEAR_MESSAGES_ON_STARTUP = os.getenv('CLEAR_MESSAGES_ON_STARTUP', 'true').lower() == 'true'
    COLOR_LIGHT = 0xECF0F1

    # Emojis
    EMOJI_HADIR = '🟢'
    EMOJI_CUTI = '🟡'
    EMOJI_TIDAK_HADIR = '🔴'
    EMOJI_CUTI_RASMI = '⛱️'
    EMOJI_KOSONG = '⚪'
    EMOJI_WARNING = '⚠️'
    EMOJI_SUCCESS = '✅'
    EMOJI_ERROR = '❌'
    EMOJI_LOADING = '⏳'
    EMOJI_CLOCK = '🕐'
    EMOJI_CALENDAR = '📅'
    EMOJI_CHART = '📊'
    EMOJI_TROPHY = '🏆'
    EMOJI_MEDAL_1 = '🥇'
    EMOJI_MEDAL_2 = '🥈'
    EMOJI_MEDAL_3 = '🥉'
    EMOJI_LOCK = '🔒'
    EMOJI_UNLOCK = '🔓'
    EMOJI_MICROPHONE = '🎤'
    EMOJI_DATABASE = '🗄️'
    EMOJI_LOG = '📋'
    EMOJI_REFRESH = '🔄'
    EMOJI_EDIT = '✏️'
    EMOJI_DELETE = '🗑️'
    EMOJI_CROWN = '👑'
    EMOJI_SHIELD = '🛡️'
    EMOJI_HAMMER = '🔨'
    EMOJI_PERSON = '👤'
    EMOJI_PEOPLE = '👥'
    EMOJI_SETTINGS = '⚙️'
    EMOJI_BELL = '🔔'
    EMOJI_MAIL = '✉️'
    EMOJI_KEY = '🔑'
    EMOJI_STAR = '⭐'
    EMOJI_FIRE = '🔥'
    EMOJI_TARGET = '🎯'
    EMOJI_TREND_UP = '📈'
    EMOJI_TREND_DOWN = '📉'
    EMOJI_PARTY = '🎉'
    EMOJI_MEGAPHONE = '📣'
    EMOJI_CHECK = '☑️'

    # Data Retention Settings (NEW)
    DATA_RETENTION_DAYS = int(os.getenv('DATA_RETENTION_DAYS', '7'))
    AUTO_ARCHIVE_OLD_DATA = os.getenv('AUTO_ARCHIVE_OLD_DATA', 'true').lower() == 'true'
    ARCHIVE_COLLECTION_PREFIX = os.getenv('ARCHIVE_COLLECTION_PREFIX', 'archive_')