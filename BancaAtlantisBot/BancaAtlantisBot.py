import os
import logging
import requests
import io
import json
import datetime
import re
try:
    import gspread
    from google.oauth2.service_account import Credentials
except Exception:
    gspread = None
    Credentials = None

# Ensure a pytz module is available (use a minimal fake if not installed)
try:
    import pytz  # type: ignore
except Exception:
    import sys, types
    pytz = types.ModuleType("pytz")
    class BaseTzInfo: pass
    pytz.BaseTzInfo = BaseTzInfo
    class _UTC(BaseTzInfo): pass
    pytz.UTC = _UTC()
    pytz.timezone = lambda name: pytz.UTC
    sys.modules["pytz"] = pytz
# Patch apscheduler.util.astimezone to accept zoneinfo timezones by mapping them to pytz.UTC
try:
    import apscheduler.util  # type: ignore
    def _patched_astimezone(tz):
        from datetime import tzinfo
        if tz is None:
            return None
        if isinstance(tz, str):
            return pytz.timezone(tz)
        # convert zoneinfo tzinfo (or any non-pytz tz) to pytz.UTC
        if isinstance(tz, tzinfo) and not isinstance(tz, getattr(pytz, "BaseTzInfo", object)):
            return pytz.UTC
        return tz
    apscheduler.util.astimezone = _patched_astimezone
    apscheduler.util.get_localzone = lambda: pytz.UTC
except Exception:
    logging.warning("Could not patch apscheduler.util; install pytz to avoid timezone errors.")

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    BotCommandScopeChat,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.request import HTTPXRequest
from flask import Flask, jsonify
import signal
import sys
import threading

# Bot token from environment variables (preferred) or token.txt
# For Render deployment, set BOT_TOKEN in the service environment.
token_from_env = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
if not token_from_env and os.path.exists("token.txt"):
    with open("token.txt", "r", encoding="utf-8") as f:
        token_from_env = f.read().strip()
BOT_TOKEN = (token_from_env or "").strip()
if not BOT_TOKEN:
    logging.warning("BOT_TOKEN is not configured. Set BOT_TOKEN or TELEGRAM_BOT_TOKEN before starting the bot.")

# Function to convert text to Unicode bold
def to_bold(text):
    bold_chars = {
        'a': '𝐚', 'b': '𝐛', 'c': '𝐜', 'd': '𝐝', 'e': '𝐞', 'f': '𝐟', 'g': '𝐠', 'h': '𝐡', 'i': '𝐢', 'j': '𝐣',
        'k': '𝐤', 'l': '𝐥', 'm': '𝐦', 'n': '𝐧', 'o': '𝐨', 'p': '𝐩', 'q': '𝐪', 'r': '𝐫', 's': '𝐬', 't': '𝐭',
        'u': '𝐮', 'v': '𝐯', 'w': '𝐰', 'x': '𝐱', 'y': '𝐲', 'z': '𝐳',
        'A': '𝐀', 'B': '𝐁', 'C': '𝐂', 'D': '𝐃', 'E': '𝐄', 'F': '𝐅', 'G': '𝐆', 'H': '𝐇', 'I': '𝐈', 'J': '𝐉',
        'K': '𝐊', 'L': '𝐋', 'M': '𝐌', 'N': '𝐍', 'O': '𝐎', 'P': '𝐏', 'Q': '𝐐', 'R': '𝐑', 'S': '𝐒', 'T': '𝐓',
        'U': '𝐔', 'V': '𝐕', 'W': '𝐖', 'X': '𝐗', 'Y': '𝐘', 'Z': '𝐙',
        ' ': ' ', '.': '.', ':': ':', '(': '(', ')': ')', '-': '-', '•': '•', '!': '!', '?': '?', ',': ','
    }
    return ''.join(bold_chars.get(c, c) for c in text)

# Global dictionary to store pending armadietti (temporary, not persisted)
PENDING_ARMADIETTI = {}

# Define question sequences for each option
QUESTIONS = {
    "armadietto": [
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n📥 " + to_bold("Inserisci il nick del dipendente:")
    ],
    "versamento": [
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n📥 " + to_bold("Inserisci il nick del dipendente:"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n👤 " + to_bold("Inserisci il nick del cliente:"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n💵 " + to_bold("Inserisci l'importo versato:"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n💰 " + to_bold("Inserisci le commissioni:"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n🏦 " + to_bold("Inserisci la provenienza:")
    ],
    "bonifico": [
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n📥 " + to_bold("Inserisci il nick del dipendente:"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n👤 " + to_bold("Inserisci il nick del mittente (cliente):"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n👥 " + to_bold("Inserisci il nick del beneficiario:"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n💸 " + to_bold("Inserisci l'importo:"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n💰 " + to_bold("Inserisci le commissioni:"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n📝 " + to_bold("Inserisci la causale:")
    ],
    "upgrade_carta": [
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n📥 " + to_bold("Inserisci il nick del dipendente:"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n👤 " + to_bold("Inserisci il nick del cliente:"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n💳 " + to_bold("Seleziona il tipo di conto:")  # This will be handled with buttons
    ],
    "assegno": [
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n📥 " + to_bold("Inserisci il nick del dipendente:"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n👤 " + to_bold("Inserisci il nick del beneficiario (cliente):"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n👥 " + to_bold("Inserisci il nick del mittente (chi ha fatto l'assegno):"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n🧾 " + to_bold("Inserisci l'importo dell'assegno:"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n📝 " + to_bold("Inserisci la causale:")
    ],
    "piva": [
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n📥 " + to_bold("Inserisci il nick del dipendente:"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n👤 " + to_bold("Inserisci il nick del cliente (Direttore P.iva):"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n🏢 " + to_bold("Inserisci il nome della P.Iva:")
    ],
    "congedo": [
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n✒️ " + to_bold("Nick in game:"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n📅 " + to_bold("Data inizio congedo:") + "\n" + to_bold("Formato: DD/MM/YY oppure DD/MM/YYYY") + "\n" + to_bold("Esempi: 11/02/26 oppure 11/02/2026"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n📅 " + to_bold("Data fine congedo:") + "\n" + to_bold("Formato: DD/MM/YY oppure DD/MM/YYYY") + "\n" + to_bold("Esempi: 11/02/26 oppure 11/02/2026"),
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n📝 " + to_bold("Motivazione:")
    ]
}

CONTO_TYPES = ["🪸 Conto corallo", "⚪ Conto perla", "💚 Conto smeraldo", "🌊 Conto oceano", "🧜‍♂️ Conto Poseidon"]

DATA_FILE = "bot_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Ensure all default fields exist (migration for old files)
            defaults = get_default_data()
            for key in defaults:
                if key not in data:
                    logging.info(f"⚠️ Adding missing field '{key}' to database")
                    data[key] = defaults[key]
            logging.info(f"📂 Data loaded: {len(data.get('congedi', []))} congedi")
            return data
        except Exception as e:
            logging.error(f"❌ Error loading data: {e}", exc_info=True)
            return get_default_data()
    else:
        logging.warning(f"⚠️ Data file {DATA_FILE} not found, creating new")
        return get_default_data()

def get_default_data():
    return {
        "congedi": [],  # Congedi with status field (pending/approved/rejected)
        "bonifici": [],
        "versamenti": [],
        "assegni": [],
        "piva": [],
        "upgrade_carta": [],
        "banned_users": [],
        "weekly_stats": {  # Reset every Monday at midnight
            "last_reset": datetime.date.today().isoformat(),
            "bonifici": [],
            "versamenti": [],
            "assegni": [],
            "piva": [],
            "upgrade_carta": []
        }
    }

def save_data(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        logging.info(f"✅ Data saved successfully to {DATA_FILE}")
        # Verify it was saved
        import os
        file_size = os.path.getsize(DATA_FILE)
        logging.info(f"📁 File size: {file_size} bytes")
    except Exception as e:
        logging.error(f"❌ Error saving data: {e}", exc_info=True)

def is_user_banned(user_id):
    """Check if a user is banned from using the bot."""
    data = load_data()
    if "banned_users" not in data:
        data["banned_users"] = []
        save_data(data)
    return user_id in data.get("banned_users", [])

def is_username_banned(username):
    data = load_data()
    if "banned_usernames" not in data:
        data["banned_usernames"] = []
        save_data(data)
    if not username:
        return False
    u = username.lower()
    return u in [x.lower() for x in data.get("banned_usernames", [])]

def migrate_old_congedi_structure():
    """Migrate old active_congedi/expired_congedi to new congedi structure."""
    data = load_data()
    migrated = False
    
    # Migrate active_congedi to congedi with status="approved"
    if "active_congedi" in data and data["active_congedi"]:
        for old_record in data["active_congedi"]:
            new_record = {
                "nick": old_record.get("nick"),
                "start": old_record.get("start"),
                "end": old_record.get("end"),
                "motiv": old_record.get("motiv"),
                "submitted": old_record.get("submitted"),
                "status": "approved",
                "employee_nick": old_record.get("nick", "").split(" (")[0].strip() if old_record.get("nick") else ""
            }
            data["congedi"].append(new_record)
            migrated = True
        data["active_congedi"] = []
        logging.info(f"Migrated {len(data['congedi'])} congedi from active_congedi")
    
    # Migrate expired_congedi to congedi with status="expired"
    if "expired_congedi" in data and data["expired_congedi"]:
        for old_record in data["expired_congedi"]:
            new_record = {
                "nick": old_record.get("nick"),
                "start": old_record.get("start"),
                "end": old_record.get("end"),
                "motiv": old_record.get("motiv"),
                "submitted": old_record.get("submitted"),
                "status": "expired",
                "employee_nick": old_record.get("nick", "").split(" (")[0].strip() if old_record.get("nick") else ""
            }
            data["congedi"].append(new_record)
            migrated = True
        data["expired_congedi"] = []
        logging.info(f"Migrated expired congedi")
    
    if migrated:
        save_data(data)
        logging.info("Database migration completed for congedi structure")

async def delete_message_callback(context: ContextTypes.DEFAULT_TYPE):
    """Delete a message after a delay."""
    import asyncio
    try:
        job_data = context.job.data
        chat_id = job_data['chat_id']
        message_id = job_data['message_id']
        await asyncio.sleep(10)
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        logging.info(f"✓ Messaggio {message_id} cancellato dalla chat {chat_id}")
    except Exception as e:
        logging.warning(f"⚠️ Errore nella cancellazione del messaggio: {e}")

async def schedule_delete_message(bot, chat_id, message_id):
    """Delete a message after 10 seconds delay."""
    import asyncio
    try:
        await asyncio.sleep(10)
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        logging.info(f"✓ Messaggio {message_id} cancellato dalla chat {chat_id}")
    except Exception as e:
        logging.warning(f"⚠️ Errore nella cancellazione del messaggio: {e}")

def migrate_database():
    """Add employee_nick field to all records that don't have it."""
    data = load_data()
    migrated = False
    
    categories = ["bonifici", "versamenti", "assegni", "piva", "upgrade_carta"]
    
    for category in categories:
        records = data.get(category, [])
        for record in records:
            # If record doesn't have employee_nick, extract it from summary
            if "employee_nick" not in record or not record["employee_nick"]:
                summary = record.get("summary", "")
                # Extract first value after ": " which is always the nick dipendente
                parts = summary.split(": ")
                if len(parts) > 1:
                    first_value = parts[1].strip()
                    # Remove everything after ( or newlines or @
                    nick = re.split(r'[\n@(]', first_value)[0].strip()
                    if nick:
                        record["employee_nick"] = nick
                        migrated = True
                        logging.info(f"✅ Migrated {category}: added employee_nick = {nick}")
    
    if migrated:
        save_data(data)
        logging.info(f"📊 Database migration completed: added employee_nick to all records")
    else:
        logging.info(f"ℹ️ Database already migrated: all records have employee_nick")

def validate_database():
    """Remove records with invalid or missing employee_nick."""
    data = load_data()
    cleaned = False
    
    categories = ["bonifici", "versamenti", "assegni", "piva", "upgrade_carta"]
    
    for category in categories:
        records = data.get(category, [])
        # Filter out records without valid employee_nick
        valid_records = []
        for record in records:
            nick = record.get("employee_nick", "").strip()
            if nick and len(nick) > 0:
                valid_records.append(record)
            else:
                cleaned = True
                logging.warning(f"🗑️ Removed invalid record from {category}: no valid employee_nick")
        
        data[category] = valid_records
    
    if cleaned:
        save_data(data)
        logging.info(f"🧹 Database validation completed: removed invalid records")
    else:
        logging.info(f"✅ Database is valid: all records have valid employee_nick")

def check_and_reset_weekly_stats(data):
    """Reset weekly stats every Monday at midnight."""
    if "weekly_stats" not in data:
        data["weekly_stats"] = {
            "last_reset": datetime.date.today().isoformat(),
            "bonifici": [],
            "versamenti": [],
            "assegni": [],
            "piva": [],
            "upgrade_carta": []
        }
        return
    
    last_reset_str = data["weekly_stats"].get("last_reset", datetime.date.today().isoformat())
    try:
        last_reset = datetime.datetime.strptime(last_reset_str, "%Y-%m-%d").date()
    except:
        last_reset = datetime.date.today()
    
    today = datetime.date.today()
    # Get the start of the week (Monday)
    start_of_week = today - datetime.timedelta(days=today.weekday())
    
    # If last reset was before this week's Monday, reset now
    if last_reset < start_of_week:
        logging.info(f"📅 Resetting weekly stats (last reset was {last_reset_str})")
        data["weekly_stats"] = {
            "last_reset": today.isoformat(),
            "bonifici": [],
            "versamenti": [],
            "assegni": [],
            "piva": [],
            "upgrade_carta": []
        }
        save_data(data)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Solo in privato, non nel gruppo
    if update.effective_chat.type != "private":
        return
    
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    
    # Check if user is banned
    if is_user_banned(user_id) or is_username_banned(update.effective_user.username):
        await update.message.reply_text(
            "❌ " + to_bold("Accesso Negato") + "\n\n"
            "Sei stato limitato dall'utilizzo di questo bot.\n"
            "Contatta un amministratore per ripristinare l'accesso.",
            parse_mode="Markdown"
        )
        return
    
    try:
        data = load_data()
        ids = data.get("known_user_ids", [])
        if user_id not in ids:
            ids.append(user_id)
        data["known_user_ids"] = ids
        unames = data.get("known_usernames", [])
        uname = update.effective_user.username
        if uname:
            if uname.lower() not in [x.lower() for x in unames]:
                unames.append(uname)
            known_users = data.get("known_users", {})
            known_users[uname.lower()] = user_id
            data["known_users"] = known_users
        save_data(data)
    except Exception:
        pass

    text = (
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n"
        f"📥 " + to_bold(f"Benvenuto {user_name}!") + "\n"
        "Hai attivato il bot ufficiale della " + to_bold("Banca di AtlantisRP") + "!\n\n"
        "Questo bot serve per gestire segnalazioni e richieste inerenti all'azienda\n"
        "in modo rapido, sicuro e tracciabile.\n\n"
        "👇 Utilizza i bottoni qui sotto per inviare una segnalazione o una richiesta relativa a:"
    )

    keyboard = [
        [InlineKeyboardButton("💵 Versamento", callback_data="versamento")],
        [InlineKeyboardButton("💸 Bonifico", callback_data="bonifico")],
        [InlineKeyboardButton("🧾 Incasso Assegno", callback_data="assegno")],
        [InlineKeyboardButton("🏢 P. IVA", callback_data="piva")],
        [InlineKeyboardButton("💳 Up. Carta", callback_data="upgrade_carta")],
        [InlineKeyboardButton("📅 Richiedi Congedo", callback_data="congedo")],
        [InlineKeyboardButton("🔑 Richiedi Armadietto", callback_data="armadietto")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Try to send local image, otherwise try downloading from provided link, otherwise send text only
    image_url = "https://ibb.co/yngrXmNm"
    try:
        with open("banca.png", "rb") as photo:
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=photo, caption=text, reply_markup=reply_markup)
    except Exception:
        try:
            resp = requests.get(image_url, timeout=10, allow_redirects=True)
            resp.raise_for_status()
            bio = io.BytesIO(resp.content)
            bio.name = "banca.jpg"
            bio.seek(0)
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=bio, caption=text, reply_markup=reply_markup)
        except Exception:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()
    logging.info(f"🔘 Button pressed: {data}")
    
    # Handle congedo/armadietto approval/rejection
    if data.startswith("approve_congedo_") or data.startswith("reject_congedo_") or data.startswith("approve_armadietto_") or data.startswith("reject_armadietto_"):
        is_approve = data.startswith("approve_")
        if "congedo" in data:
            record_id = data.replace("approve_congedo_", "").replace("reject_congedo_", "")
            record_type = "congedo"
            db_key = "congedi"
            # For congedi, get from database
            db_data = load_data()
            records = db_data.get(db_key, [])
            record = None
            record_index = None
            
            for idx, r in enumerate(records):
                if r.get("record_id") == record_id:
                    record = r
                    record_index = idx
                    break
        else:
            record_id = data.replace("approve_armadietto_", "").replace("reject_armadietto_", "")
            record_type = "armadietto"
            # For armadietti, get from memory only (not persisted)
            record = PENDING_ARMADIETTI.get(record_id)
            record_index = None
            db_data = None
        
        logging.info(f"Processing {record_type} callback: {record_id}, approve={is_approve}")
        
        if not record:
            logging.warning(f"❌ {record_type} {record_id} not found")
            try:
                await query.edit_message_text(f"❌ Errore: {record_type} non trovato. Prova a inviare di nuovo.")
            except Exception as e:
                logging.error(f"Error editing message: {e}")
            return
        
        user_id = record.get("user_id")
        
        if is_approve:
            try:
                logging.info(f"✅ Processing approval for {record_type} {record_id}")
                
                # For congedi, update status in database
                if record_type == "congedo":
                    record["status"] = "approved"
                    db_data[db_key][record_index] = record
                    save_data(db_data)
                    logging.info(f"✅ {record_type.capitalize()} approvato: {record.get('nick')}")
                    try:
                        if SHEET_WORKBOOK is not None:
                            row = [record.get("nick",""), record.get("start",""), record.get("end",""), record.get("motiv","")]
                            lr, _ = append_row_side_by_side("Congedi", row, right_start_col=9)
                            if lr:
                                active = _is_congedo_active(record.get("end",""))
                                _set_commission_cell_color("Congedi", lr, 3, active)
                            logging.info("📝 Congedo scritto su foglio Google (Congedi)")
                    except Exception as se:
                        logging.warning(f"Scrittura congedo su Sheets fallita: {se}")
                elif record_type == "armadietto":
                    # For armadietti, just remove from memory (already approved)
                    del PENDING_ARMADIETTI[record_id]
                    logging.info(f"✅ Armadietto approvato: {record.get('nick')}")
                
                # Notify user
                if record_type == "congedo":
                    approval_text = (
                        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n"
                        "✅ " + to_bold("Congedo Approvato") + "\n\n"
                        f"Il tuo congedo è stato " + to_bold("approvato") + ".\n"
                        f"Nick: {to_bold(record['nick'])}\n"
                        f"Dal {to_bold(record['start'])} al {to_bold(record['end'])}\n"
                        f"Motivo: {to_bold(record['motiv'])}"
                    )
                else:
                    approval_text = (
                        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n"
                        "✅ " + to_bold("Armadietto Approvato") + "\n\n"
                        f"La tua richiesta di armadietto è stata " + to_bold("approvata") + ".\n"
                        f"Nick: {to_bold(record['nick'])}"
                    )
                
                try:
                    await context.bot.send_message(chat_id=user_id, text=approval_text, parse_mode="Markdown")
                    logging.info(f"Sent approval message to user {user_id}")
                except Exception as e:
                    logging.warning(f"Could not send approval message to user {user_id}: {e}")
                
                await query.edit_message_text(query.message.text + "\n\n✅ " + to_bold("Approvato"), parse_mode="Markdown")
            except Exception as e:
                logging.error(f"❌ Error approving {record_type}: {e}", exc_info=True)
                try:
                    await query.edit_message_text(f"❌ Errore nel salvataggio del {record_type}")
                except Exception as edit_error:
                    logging.error(f"Error editing message on exception: {edit_error}")
        else:
            # Rejection
            try:
                logging.info(f"❌ Processing rejection for {record_type} {record_id}")
                
                # For congedi, update status in database
                if record_type == "congedo":
                    record["status"] = "rejected"
                    db_data[db_key][record_index] = record
                    save_data(db_data)
                elif record_type == "armadietto":
                    # For armadietti, just remove from memory (already rejected)
                    del PENDING_ARMADIETTI[record_id]
                    logging.info(f"❌ Armadietto rifiutato: {record.get('nick')}")
                
                # Notify user of rejection
                if record_type == "congedo":
                    rejection_text = (
                        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n"
                        "❌ " + to_bold("Congedo Rifiutato") + "\n\n"
                        f"Il tuo congedo è stato " + to_bold("rifiutato") + ".\n"
                        f"Nick: {to_bold(record['nick'])}\n"
                        f"Dal {to_bold(record['start'])} al {to_bold(record['end'])}"
                    )
                else:
                    rejection_text = (
                        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n"
                        "❌ " + to_bold("Armadietto Rifiutato") + "\n\n"
                        f"La tua richiesta di armadietto è stata " + to_bold("rifiutata") + ".\n"
                        f"Nick: {to_bold(record['nick'])}"
                    )
                
                try:
                    await context.bot.send_message(chat_id=user_id, text=rejection_text, parse_mode="Markdown")
                except Exception as e:
                    logging.warning(f"Could not send rejection message to user {user_id}: {e}")
                
                await query.edit_message_text(query.message.text + "\n\n❌ " + to_bold("Rifiutato"), parse_mode="Markdown")
            except Exception as e:
                logging.error(f"❌ Error rejecting {record_type}: {e}", exc_info=True)
        
        return
        
        if is_approve:
            try:
                logging.info(f"✅ Processing approval for {record_type} {record_id}")
                
                # Update status in database
                record["status"] = "approved"
                db_data[db_key][record_index] = record
                save_data(db_data)
                logging.info(f"✅ {record_type.capitalize()} approvato: {record.get('nick')}")
                
                # Notify user
                if record_type == "congedo":
                    approval_text = (
                        "\ud835\udc00\ud835\udc2d\ud835\udc25\ud835\udc1a\ud835\udc27\ud835\udc2d\ud835\udc22\ud835\udc2c \ud835\udc0d\ud835\udc1a\ud835\udc2d\ud835\udc22\ud835\udc28\ud835\udc27\ud835\udc1a\ud835\udc25 \ud835\udc01\ud835\udc1a\ud835\udc27\ud835\udc24 \u2022 \ud835\udc01\ud835\udc28\ud835\udc2d\\n\\n"
                        "\u2705 " + to_bold("Congedo Approvato") + "\\n\\n"
                        f"Il tuo congedo è stato " + to_bold("approvato") + ".\\n"
                        f"Nick: {to_bold(record['nick'])}\\n"
                        f"Dal {to_bold(record['start'])} al {to_bold(record['end'])}\\n"
                        f"Motivo: {to_bold(record['motiv'])}"
                    )
                else:
                    approval_text = (
                        "\ud835\udc00\ud835\udc2d\ud835\udc25\ud835\udc1a\ud835\udc27\ud835\udc2d\ud835\udc22\ud835\udc2c \ud835\udc0d\ud835\udc1a\ud835\udc2d\ud835\udc22\ud835\udc28\ud835\udc27\ud835\udc1a\ud835\udc25 \ud835\udc01\ud835\udc1a\ud835\udc27\ud835\udc24 \u2022 \ud835\udc01\ud835\udc28\ud835\udc2d\\n\\n"
                        "\u2705 " + to_bold("Armadietto Approvato") + "\\n\\n"
                        f"La tua richiesta di armadietto è stata " + to_bold("approvata") + ".\\n"
                        f"Nick: {to_bold(record['nick'])}"
                    )
                
                try:
                    await context.bot.send_message(chat_id=user_id, text=approval_text, parse_mode="Markdown")
                    logging.info(f"Sent approval message to user {user_id}")
                except Exception as e:
                    logging.warning(f"Could not send approval message to user {user_id}: {e}")
                
                await query.edit_message_text(query.message.text + "\\n\\n\u2705 " + to_bold("Approvato"), parse_mode="Markdown")
            except Exception as e:
                logging.error(f"❌ Error approving {record_type}: {e}", exc_info=True)
                try:
                    await query.edit_message_text(f"❌ Errore nel salvataggio del {record_type}")
                except Exception as edit_error:
                    logging.error(f"Error editing message on exception: {edit_error}")
        else:
            # Rejection
            try:
                logging.info(f"❌ Processing rejection for {record_type} {record_id}")
                
                # Update status in database
                record["status"] = "rejected"
                db_data[db_key][record_index] = record
                save_data(db_data)
                
                # Notify user of rejection
                if record_type == "congedo":
                    rejection_text = (
                        "\ud835\udc00\ud835\udc2d\ud835\udc25\ud835\udc1a\ud835\udc27\ud835\udc2d\ud835\udc22\ud835\udc2c \ud835\udc0d\ud835\udc1a\ud835\udc2d\ud835\udc22\ud835\udc28\ud835\udc27\ud835\udc1a\ud835\udc25 \ud835\udc01\ud835\udc1a\ud835\udc27\ud835\udc24 \u2022 \ud835\udc01\ud835\udc28\ud835\udc2d\\n\\n"
                        "❌ " + to_bold("Congedo Rifiutato") + "\\n\\n"
                        f"Il tuo congedo è stato " + to_bold("rifiutato") + ".\\n"
                        f"Nick: {to_bold(record['nick'])}\\n"
                        f"Dal {to_bold(record['start'])} al {to_bold(record['end'])}"
                    )
                else:
                    rejection_text = (
                        "\ud835\udc00\ud835\udc2d\ud835\udc25\ud835\udc1a\ud835\udc27\ud835\udc2d\ud835\udc22\ud835\udc2c \ud835\udc0d\ud835\udc1a\ud835\udc2d\ud835\udc22\ud835\udc28\ud835\udc27\ud835\udc1a\ud835\udc25 \ud835\udc01\ud835\udc1a\ud835\udc27\ud835\udc24 \u2022 \ud835\udc01\ud835\udc28\ud835\udc2d\\n\\n"
                        "❌ " + to_bold("Armadietto Rifiutato") + "\\n\\n"
                        f"La tua richiesta di armadietto è stata " + to_bold("rifiutata") + ".\\n"
                        f"Nick: {to_bold(record['nick'])}"
                    )
                
                try:
                    await context.bot.send_message(chat_id=user_id, text=rejection_text, parse_mode="Markdown")
                except Exception as e:
                    logging.warning(f"Could not send rejection message to user {user_id}: {e}")
                
                await query.edit_message_text(query.message.text + "\n\n❌ " + to_bold("Rifiutato"), parse_mode="Markdown")
            except Exception as e:
                logging.error(f"❌ Error rejecting {record_type}: {e}", exc_info=True)
        
        return
    
    if data in QUESTIONS:
        # Start the question flow
        context.user_data['option'] = data
        context.user_data['step'] = 0
        context.user_data['answers'] = []
        context.user_data['user_id'] = update.effective_user.id
        context.user_data['username'] = update.effective_user.username
        await ask_next_question(update, context)
    elif data.startswith("conto_"):
        # Handle conto type selection
        conto_type = data.replace("conto_", "").replace("_", " ").title()
        context.user_data['answers'].append(conto_type)
        context.user_data['step'] += 1
        await query.delete_message()
        await ask_next_question(update, context)
    elif data == "invia":
        # Send summary to group
        summary = build_summary(context.user_data)
        option = context.user_data['option']
        logging.info(f"📨 Sending summary for {option}. Summary text:\n{summary}")
        
        user_id = update.effective_user.id
        username = update.effective_user.username
        
        # For congedi and armadietto, add approve/reject buttons
        if option in ["congedo", "armadietto"]:
            record_id = f"{user_id}_{int(datetime.datetime.now().timestamp())}"
            
            if option == "congedo":
                # Save congedo to database with status pending
                record = {
                    "record_id": record_id,
                    "nick": context.user_data['answers'][0],
                    "start": context.user_data['answers'][1],
                    "end": context.user_data['answers'][2],
                    "motiv": context.user_data['answers'][3],
                    "user_id": user_id,
                    "username": username,
                    "status": "pending",
                    "date": datetime.date.today().isoformat(),
                    "employee_nick": context.user_data['answers'][0].split(" (")[0].strip() if context.user_data['answers'][0] else ""
                }
                
                # Store in database
                db_data = load_data()
                if "congedi" not in db_data:
                    db_data["congedi"] = []
                db_data["congedi"].append(record)
                save_data(db_data)
                logging.info(f"💾 Stored congedo in database")
            else:  # armadietto
                # For armadietto, only store in memory (don't persist to database)
                armadietto_data = {
                    "nick": context.user_data['answers'][0],
                    "user_id": user_id,
                    "username": username
                }
                PENDING_ARMADIETTI[record_id] = armadietto_data
                logging.info(f"📌 Stored armadietto in memory: {record_id}")
                logging.info(f"PENDING_ARMADIETTI content: {armadietto_data}")
            
            # Build summary
            if option == "congedo":
                summary = build_summary(context.user_data)
            else:  # armadietto
                summary = (
                    "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n"
                    "📋 " + to_bold("Richiesta Armadietto") + "\n\n"
                    "#Armadietto\n\n"
                    f"📥 {to_bold('Nick Dipendente')}: {context.user_data['answers'][0]} (@{username if username else user_id})\n"
                )
            
            keyboard = [
                [InlineKeyboardButton("✅ Accetta", callback_data=f"approve_{option}_{record_id}")],
                [InlineKeyboardButton("❌ Rifiuta", callback_data=f"reject_{option}_{record_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(chat_id=-1003640567963, text=summary, reply_markup=reply_markup)
            logging.info(f"📬 {option.capitalize()} message sent to group")
        else:
            await context.bot.send_message(chat_id=-1003640567963, text=summary)
            # Store data for non-congedo/armadietto
            store_data(context.user_data, summary)
        
        msg = await query.edit_message_text("✅ Richiesta inviata con successo!")
        # Schedule message deletion after 10 seconds
        import asyncio
        asyncio.create_task(schedule_delete_message(context.bot, update.effective_chat.id, msg.message_id))
        context.user_data.clear()
    elif data == "cancella":
        msg = await query.edit_message_text("❌ Richiesta cancellata.")
        # Schedule message deletion after 10 seconds
        import asyncio
        asyncio.create_task(schedule_delete_message(context.bot, update.effective_chat.id, msg.message_id))
        context.user_data.clear()
    elif data.startswith("listamoduli_"):
        # Handle listamoduli mode selection
        mode = data.replace("listamoduli_", "")
        await listamoduli_show(update, context, mode)
    elif data.startswith("controlla_"):
        # Handle controlla mode selection
        mode = data.replace("controlla_", "")
        client_nick = context.user_data.get("controlla_nick")
        if not client_nick:
            await query.edit_message_text("❌ Errore: nome cliente non trovato")
            return
        
        try:
            versamenti = []
            if SHEET_WORKBOOK is not None:
                versamenti = sheets_read_records("Versamenti", "totali" if mode == "totali" else "settimanali")
            else:
                db = load_data()
                if mode == "totali":
                    versamenti = db.get("versamenti", [])
                elif mode == "settimanali":
                    versamenti = db.get("weekly_stats", {}).get("versamenti", [])
            
            # Filter for this client and sum importi
            total = 0
            client_versamenti = []
            
            for versamento in versamenti:
                cn = str(versamento.get("client_nick", "")).strip()
                if not cn:
                    s = versamento.get("summary", "")
                    for line in s.splitlines():
                        t = line.strip()
                        if t.startswith("👤 "):
                            import re
                            m = re.search(r":\s*(.+)", t)
                            if m:
                                cn = m.group(1).strip()
                            break
                if cn and cn.lower() == client_nick.lower():
                    client_versamenti.append(versamento)
                    amt = versamento.get("importo")
                    if amt is None:
                        s = versamento.get("summary", "")
                        for line in s.splitlines():
                            t = line.strip()
                            if t.startswith("💵 "):
                                import re
                                m = re.search(r":\s*([0-9.,]+)", t)
                                if m:
                                    a = m.group(1).replace(".", "").replace(",", ".")
                                    try:
                                        amt = float(a)
                                    except Exception:
                                        amt = 0
                                break
                    try:
                        total += int(float(amt or 0))
                    except Exception:
                        pass
            
            if client_versamenti:
                text = f"💰 <b>Depositi di {client_nick}</b>\n\n"
                text += f"Modalità: {'📊 Totale' if mode == 'totali' else '📅 Settimanale'}\n\n"
                text += f"💵 Totale depositato: <b>{total:,}</b>\n"
                text += f"📋 Numero versamenti: <b>{len(client_versamenti)}</b>"
            else:
                text = f"⚠️ Nessun versamento trovato per <b>{client_nick}</b>\n\n"
                text += f"Modalità: {'📊 Totale' if mode == 'totali' else '📅 Settimanale'}"
            
            await query.edit_message_text(text, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Error in controlla: {e}")
            await query.edit_message_text(f"❌ Errore: {e}")


def store_data(user_data, summary):
    data = load_data()
    option = user_data['option']
    answers = user_data['answers']
    today = datetime.date.today().isoformat()
    category_keys = {
        "bonifico": "bonifici",
        "versamento": "versamenti",
        "assegno": "assegni",
        "piva": "piva",
        "upgrade_carta": "upgrade_carta"
    }
    # Note: congedi are now handled with approval/rejection, not auto-stored
    if option != "congedo":
        record = {
            "date": today,
            "summary": summary,
            "employee_nick": answers[0] if answers else ""
        }
        if option == "versamento":
            def _norm_amount(x):
                try:
                    s = str(x).strip().replace(".", "").replace(",", ".")
                    return float(s)
                except Exception:
                    return 0.0
            record["client_nick"] = answers[1] if len(answers) > 1 else ""
            record["importo"] = _norm_amount(answers[2] if len(answers) > 2 else 0)
        elif option == "bonifico":
            def _norm_amount(x):
                try:
                    s = str(x).strip().replace(".", "").replace(",", ".")
                    return float(s)
                except Exception:
                    return 0.0
            record["sender_nick"] = answers[1] if len(answers) > 1 else ""
            record["beneficiary_nick"] = answers[2] if len(answers) > 2 else ""
            record["importo"] = _norm_amount(answers[3] if len(answers) > 3 else 0)
            record["commissioni_user"] = answers[4] if len(answers) > 4 else ""
            record["causale"] = answers[5] if len(answers) > 5 else ""
        elif option == "assegno":
            def _norm_amount(x):
                try:
                    s = str(x).strip().replace(".", "").replace(",", ".")
                    return float(s)
                except Exception:
                    return 0.0
            record["beneficiary_nick"] = answers[1] if len(answers) > 1 else ""
            record["sender_nick"] = answers[2] if len(answers) > 2 else ""
            record["importo"] = _norm_amount(answers[3] if len(answers) > 3 else 0)
            record["causale"] = answers[4] if len(answers) > 4 else ""
        elif option == "piva":
            record["direttore_nick"] = answers[1] if len(answers) > 1 else ""
            record["nome_piva"] = answers[2] if len(answers) > 2 else ""
        elif option == "upgrade_carta":
            record["cliente_nick"] = answers[1] if len(answers) > 1 else ""
            record["tipo_conto"] = answers[2] if len(answers) > 2 else ""
        key = category_keys.get(option, option)
        data[key].append(record)
        
        # Also add to weekly stats
        check_and_reset_weekly_stats(data)
        if "weekly_stats" not in data:
            data["weekly_stats"] = {}
        if key not in data["weekly_stats"]:
            data["weekly_stats"][key] = []
        data["weekly_stats"][key].append(record)
        
        save_data(data)

    def calculate_commission(amount):
        import math
        try:
            # Normalize decimal comma to dot and parse
            if isinstance(amount, str):
                a = amount.replace(',', '.').strip()
            else:
                a = amount
            a = float(a)
        except Exception:
            return 0
        if a <= 0:
            return 0
        if a <= 1000.00:
            c = 30.0
        elif a <= 20000.00:
            c = a * 0.03
        elif a <= 50000.00:
            c = a * 0.05
        elif a <= 100000.00:
            c = a * 0.07
        else:
            c = a * 0.10
        # Round half up to nearest integer (e.g., 60.5 -> 61, 60.4 -> 60)
        return int(math.floor(c + 0.5))
    def _parse_user_comm(raw):
        try:
            s = str(raw).strip()
            # keep digits, dot and comma and minus
            s = s.replace(',', '.')
            filtered = ''.join(ch for ch in s if (ch.isdigit() or ch == '.' or ch == '-'))
            if filtered == '' or filtered == '.' or filtered == '-':
                return 0
            v = float(filtered)
            import math as _math
            return int(_math.floor(v + 0.5))
        except Exception:
            return 0
    
    # Also try to append to Google Sheets if configured
    try:
        if SHEET_WORKBOOK is not None:
            logging.info(f"Appending {option} to Google Sheets...")
            # Map option -> sheet name and row format
            if option == "versamento":
                # answers: nick dip, nick cliente, importo, commissioni (user), provenienza
                expected = calculate_commission(answers[2])
                user_comm = _parse_user_comm(answers[3])
                # Row: Nick Dipendente, Nick Cliente, Importo versato, Provenienza, Commissioni, Data
                row = [answers[0], answers[1], answers[2], answers[4], str(user_comm), today]
                lr, _ = append_row_side_by_side("Versamenti", row, right_start_col=9)
                # color commission cell (col 5) based on match
                try:
                    ws = _ensure_worksheet("Versamenti")
                    if lr:
                        _set_commission_cell_color(ws.title, lr, 5, expected == user_comm)
                except Exception as e:
                    logging.warning(f"Could not color Versamenti commission cell: {e}")
                logging.info(f"✅ Versamento appended to Versamenti sheet")
            elif option == "bonifico":
                # answers: nick dip, nick mittente, nick beneficiario, importo, commissioni (user), causale
                expected = calculate_commission(answers[3])
                user_comm = _parse_user_comm(answers[4])
                causale = answers[5] if len(answers) > 5 else ""
                row = [answers[0], answers[1], answers[2], answers[3], str(user_comm), causale, today]
                lr, _ = append_row_side_by_side("Bonifici", row, right_start_col=9)
                try:
                    ws = _ensure_worksheet("Bonifici")
                    if lr:
                        _set_commission_cell_color(ws.title, lr, 5, expected == user_comm)
                except Exception as e:
                    logging.warning(f"Could not color Bonifici commission cell: {e}")
                logging.info(f"✅ Bonifico appended to Bonifici sheet")
            elif option == "assegno":
                # answers: nick dip, nick beneficiario(cliente), nick mittente, importo assegno, causale
                # Sheet expects: Nick Dipendente, Nick Mittente, Nick Beneficiario, Importo, Causale, Data
                row = [answers[0], answers[2], answers[1], answers[3], answers[4], today]
                append_row_side_by_side("Assegni", row, right_start_col=9)
                logging.info(f"✅ Assegno appended to Assegni sheet")
            elif option == "piva":
                # answers: nick dip, nick cliente(direttore), nome p.iva
                row = [answers[0], answers[1], answers[2], today]
                append_row_side_by_side("PIva", row, right_start_col=9)
                logging.info(f"✅ PIva appended to PIva sheet")
            elif option == "upgrade_carta":
                # answers: nick dip, nick cliente, tipo conto
                row = [answers[0], answers[1], answers[2], today]
                append_row_side_by_side("Upgrade Carta", row, right_start_col=9)
                logging.info(f"✅ Upgrade Carta appended to Upgrade Carta sheet")
            elif option == "congedo":
                # For congedi we appended earlier; also add to sheet as active congedi
                row = [answers[0], answers[1], answers[2], answers[3]]
                append_row_side_by_side("Congedi", row, right_start_col=9)
                logging.info(f"✅ Congedo appended to Congedi sheet")
        else:
            logging.warning("SHEET_WORKBOOK is None; Google Sheets integration not available")
    except Exception as e:
        logging.warning(f"Google Sheets append failed: {e}")


###########################
# Google Sheets helpers  #
###########################

SHEET_WORKBOOK = None

def init_sheets():
    """Initialize Google Sheets client and open workbook.
    Expects a service account JSON path in env `GSPREAD_CREDENTIALS` (or file gspread_credentials.json)
    and the target sheet id in env `GOOGLE_SHEET_ID` (or file sheet_id.txt).
    """
    global SHEET_WORKBOOK
    if gspread is None or Credentials is None:
        logging.info("gspread not installed; Google Sheets integration disabled.")
        return
    cred_path = os.getenv("GSPREAD_CREDENTIALS") or "gspread_credentials.json"
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id and os.path.exists("sheet_id.txt"):
        with open("sheet_id.txt", "r", encoding="utf-8") as f:
            sheet_id = f.read().strip()
    if not sheet_id:
        logging.info("No GOOGLE_SHEET_ID provided; Sheets integration disabled.")
        return
    if not os.path.exists(cred_path):
        logging.warning(f"Credentials file {cred_path} not found; Sheets integration disabled.")
        return
    try:
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        creds = Credentials.from_service_account_file(cred_path, scopes=scopes)
        client = gspread.authorize(creds)
        SHEET_WORKBOOK = client.open_by_key(sheet_id)
        logging.info("✅ Google Sheets initialized successfully.")
    except Exception as e:
        logging.error(f"❌ Failed to initialize Google Sheets: {e}")
        SHEET_WORKBOOK = None

def append_row(sheet_name, row):
    """Append a row to worksheet `sheet_name`. Creates worksheet if missing."""
    if SHEET_WORKBOOK is None:
        raise RuntimeError("Sheets not initialized")
    try:
        try:
            ws = SHEET_WORKBOOK.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            sanitized = sheet_name.replace('.', '').replace(' ', '')
            try:
                ws = SHEET_WORKBOOK.worksheet(sanitized)
            except gspread.WorksheetNotFound:
                ws = SHEET_WORKBOOK.add_worksheet(title=sanitized, rows=1000, cols=20)
        ws.append_row(row, value_input_option='USER_ENTERED')
    except Exception as e:
        logging.warning(f"Failed appending row to {sheet_name}: {e}")

def _ensure_worksheet(sheet_name):
    if SHEET_WORKBOOK is None:
        raise RuntimeError("Sheets not initialized")
    try:
        return SHEET_WORKBOOK.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        sanitized = sheet_name.replace('.', '').replace(' ', '')
        try:
            return SHEET_WORKBOOK.worksheet(sanitized)
        except gspread.WorksheetNotFound:
            return SHEET_WORKBOOK.add_worksheet(title=sanitized, rows=1000, cols=20)

def _get_archive_title(sheet_name):
    return f"Archivio {sheet_name}"

def _ensure_archive_worksheet(sheet_name):
    title = _get_archive_title(sheet_name)
    return _ensure_worksheet(title)

def _col_letter(n):
    s = ''
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s

def append_row_side_by_side(sheet_name, row, right_start_col=9):
    if SHEET_WORKBOOK is None:
        raise RuntimeError("Sheets not initialized")
    try:
        ws = _ensure_worksheet(sheet_name)
        left_row = max(2, len(ws.col_values(1)) + 1)
        left_start = _col_letter(1)
        left_end = _col_letter(1 + len(row) - 1)
        ws.update(f"{left_start}{left_row}:{left_end}{left_row}", [row], value_input_option='USER_ENTERED')
        return left_row, None
    except Exception as e:
        logging.warning(f"Failed side-by-side append to {sheet_name}: {e}")
        return None, None

def clear_weekly_sections_in_sheets():
    if SHEET_WORKBOOK is None:
        return
    layouts = {
        "Versamenti": 6,
        "Bonifici": 7,
        "Assegni": 6,
        "PIva": 4,
        "Upgrade Carta": 4,
        "Congedi": 4,
    }
    for name, width in layouts.items():
        try:
            ws = _ensure_worksheet(name)
            start = _col_letter(9)
            end = _col_letter(9 + width - 1)
            rng = f"{start}2:{end}"
            try:
                ws.batch_clear([rng])
            except Exception:
                ws.update(rng, [[""] * width] * (ws.row_count - 1))
        except Exception as e:
            logging.warning(f"Failed clearing weekly section for {name}: {e}")

def _set_commission_cell_color(sheet_name, row_index, col_index, correct):
    try:
        try:
            ws = SHEET_WORKBOOK.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            sanitized = sheet_name.replace('.', '').replace(' ', '')
            try:
                ws = SHEET_WORKBOOK.worksheet(sanitized)
            except gspread.WorksheetNotFound:
                ws = SHEET_WORKBOOK.add_worksheet(title=sanitized, rows=1000, cols=20)
        def col_to_letter(n):
            string = ''
            while n > 0:
                n, remainder = divmod(n - 1, 26)
                string = chr(65 + remainder) + string
            return string
        cell_a1 = f"{col_to_letter(col_index)}{row_index}"
        color = {True: {"red": 0.80, "green": 0.92, "blue": 0.80}, False: {"red": 0.96, "green": 0.78, "blue": 0.78}}[bool(correct)]
        try:
            ws.format(cell_a1, {"backgroundColor": color})
        except Exception:
            body = {
                'requests': [
                    {
                        'repeatCell': {
                            'range': {
                                'sheetId': ws._properties['sheetId'],
                                'startRowIndex': row_index-1,
                                'endRowIndex': row_index,
                                'startColumnIndex': col_index-1,
                                'endColumnIndex': col_index
                            },
                            'cell': {
                                'userEnteredFormat': {
                                    'backgroundColor': color
                                }
                            },
                            'fields': 'userEnteredFormat.backgroundColor'
                        }
                    }
                ]
            }
            SHEET_WORKBOOK.batch_update(body)
    except Exception as e:
        logging.warning(f"Failed to set commission cell color for {sheet_name}: {e}")

def _is_congedo_active(end_str):
    try:
        try:
            end_date = datetime.datetime.strptime(end_str, "%d/%m/%Y").date()
        except ValueError:
            end_date = datetime.datetime.strptime(end_str, "%d/%m/%y").date()
        return end_date >= datetime.date.today()
    except Exception:
        return True

def recolor_congedi_sheet():
    if SHEET_WORKBOOK is None:
        return
    try:
        ws = _ensure_worksheet("Congedi")
        rows = ws.get("A2:D")
        for idx, row in enumerate(rows):
            end_str = row[2] if len(row) > 2 else ""
            active = _is_congedo_active(end_str)
            try:
                _set_commission_cell_color("Congedi", idx + 2, 3, active)
            except Exception as e:
                logging.warning(f"Failed coloring congedi row {idx+2}: {e}")
    except Exception as e:
        logging.warning(f"Could not recolor Congedi sheet: {e}")

def _parse_amount(s):
    try:
        if s is None:
            return 0.0
        v = str(s).strip()
        if v == "":
            return 0.0
        v = v.replace(".", "").replace(",", ".")
        return float(v)
    except Exception:
        return 0.0

def sheets_read_records(sheet_name, mode):
    if SHEET_WORKBOOK is None:
        return []
    ws = _ensure_worksheet(sheet_name)
    widths = {"Versamenti":6,"Bonifici":7,"Assegni":6,"PIva":4,"Upgrade Carta":4,"Congedi":4}
    w = widths.get(sheet_name, 6)
    sc = 1
    start = _col_letter(sc)
    end = _col_letter(sc + w - 1)
    rng = f"{start}2:{end}"
    vals = []
    try:
        vals = ws.get(rng)
    except Exception as e:
        logging.warning(f"Sheets read failed for {sheet_name} {mode}: {e}")
        return []
    records = []
    for row in vals:
        if not any(str(x).strip() for x in row):
            continue
        while len(row) < w:
            row.append("")
        if sheet_name == "Versamenti":
            v3n = _parse_amount(row[3])
            v4n = _parse_amount(row[4])
            prov = row[3]
            comm = row[4]
            if v3n and not v4n:
                comm = row[3]
                prov = row[4]
            records.append({
                "employee_nick": row[0],
                "client_nick": row[1],
                "importo": _parse_amount(row[2]),
                "provenienza": prov,
                "commissioni_user": comm,
                "date": row[5]
            })
        elif sheet_name == "Bonifici":
            records.append({
                "employee_nick": row[0],
                "sender_nick": row[1],
                "beneficiary_nick": row[2],
                "importo": _parse_amount(row[3]),
                "commissioni_user": row[4],
                "causale": row[5],
                "date": row[6]
            })
        elif sheet_name == "Assegni":
            records.append({
                "employee_nick": row[0],
                "sender_nick": row[1],
                "beneficiary_nick": row[2],
                "importo": _parse_amount(row[3]),
                "causale": row[4],
                "date": row[5]
            })
        elif sheet_name == "PIva":
            records.append({
                "employee_nick": row[0],
                "direttore_nick": row[1],
                "nome_piva": row[2],
                "date": row[3]
            })
        elif sheet_name == "Upgrade Carta":
            records.append({
                "employee_nick": row[0],
                "cliente_nick": row[1],
                "tipo_conto": row[2],
                "date": row[3]
            })
        elif sheet_name == "Congedi":
            records.append({
                "employee_nick": row[0],
                "start_date": row[1],
                "end_date": row[2],
                "motivo": row[3]
            })
    if mode == "settimanali":
        try:
            today = datetime.date.today()
            start_week = today - datetime.timedelta(days=today.weekday())
            end_week = start_week + datetime.timedelta(days=6)
            def _in_week(dstr):
                try:
                    d = datetime.date.fromisoformat(str(dstr))
                    return start_week <= d <= end_week
                except Exception:
                    return False
            records = [r for r in records if _in_week(r.get("date",""))]
        except Exception:
            pass
    return records

def sheets_count_modules(mode):
    if SHEET_WORKBOOK is None:
        return {}
    modules_per_employee = {}
    sheets = ["Versamenti", "Bonifici", "Assegni", "PIva", "Upgrade Carta", "Congedi"]
    for sheet in sheets:
        for r in sheets_read_records(sheet, mode):
            nick = str(r.get("employee_nick", "")).strip()
            if nick:
                modules_per_employee[nick] = modules_per_employee.get(nick, 0) + 1
    try:
        db = load_data()
        excluded = [x.lower() for x in db.get("excluded_employees", [])]
        if excluded:
            modules_per_employee = {k: v for k, v in modules_per_employee.items() if k.lower() not in excluded}
    except Exception:
        pass
    return modules_per_employee

async def ask_next_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    option = context.user_data['option']
    step = context.user_data['step']
    questions = QUESTIONS[option]
    
    if step < len(questions):
        question = questions[step]
        if option == "upgrade_carta" and step == 2:
            # Send buttons for conto type
            keyboard = [[InlineKeyboardButton(ct, callback_data=f"conto_{ct.lower().replace(' ', '_').replace('💎', '').replace('💍', '').replace('💚', '').replace('🌊', '').replace('🏛️', '').strip()}")] for ct in CONTO_TYPES]
            reply_markup = InlineKeyboardMarkup(keyboard)
            msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=question, reply_markup=reply_markup)
        else:
            msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=question)
        context.user_data['question_msg_id'] = msg.message_id
    else:
        # All questions answered, send summary
        summary = build_summary(context.user_data)
        keyboard = [
            [InlineKeyboardButton("✅ Invia", callback_data="invia")],
            [InlineKeyboardButton("❌ Cancella", callback_data="cancella")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=summary, reply_markup=reply_markup)

def build_summary(user_data):
    option = user_data['option']
    answers = user_data['answers']
    summary = f"𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n📋 {to_bold('Riepilogo ' + option.title())}\n\n#{option.title()}\n\n"
    labels = {
        "versamento": ["Nick Dipendente", "Nick Cliente", "Importo versato", "Commissioni", "Provenienza"],
        "bonifico": ["Nick Dipendente", "Nick Mittente (cliente)", "Nick beneficiario", "Importo", "Commissioni", "Causale"],
        "upgrade_carta": ["Nick Dipendente", "Nick Cliente", "Tipo conto"],
        "assegno": ["Nick Dipendente", "Nick Beneficiario (cliente)", "Nick Mittente", "Importo Assegno", "Causale"],
        "piva": ["Nick Dipendente", "Nick Cliente (Direttore P.iva)", "Nome P.Iva"],
        "congedo": ["Nick in game", "Data inizio congedo", "Data fine congedo", "Motivazione"],
        "armadietto": ["Nick Dipendente"]
    }
    emojis = ["📥", "👤", "💵", "💰", "🏦"] if option == "versamento" else (
        ["📥", "👤", "👥", "💸", "💰", "📝"] if option == "bonifico" else (
        ["📥", "👤", "💳"] if option == "upgrade_carta" else (
        ["📥", "👤", "👥", "🧾", "📝"] if option == "assegno" else (
        ["📥", "👤", "🏢"] if option == "piva" else (
        ["✒️", "📅", "📅", "📝"] if option == "congedo" else ["📥"]
        )
        )
    )
    )
    )
    for i, (emoji, label, a) in enumerate(zip(emojis, labels[option], answers)):
        # For first field (nick), add @username for all modules
        if i == 0:
            username = user_data.get('username')
            user_id = user_data.get('user_id')
            mention = f"@{username}" if username else f"@{user_id}"
            summary += f"{emoji} {to_bold(label)}: {a} ({mention})\n"
            logging.info(f"🏷️ First field with mention: {a} ({mention})")
        else:
            summary += f"{emoji} {to_bold(label)}: {a}\n"
    return summary

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text responses from the user."""
    # Ignore text messages if not in the middle of a conversation
    if 'step' not in context.user_data:
        return
    
    # Ignore messages from group
    if update.effective_chat.id == -1003640567963:
        return
    
    answer = update.message.text.strip()
    
    # Skip empty messages
    if not answer:
        return
    
    # Check if user is banned
    data = load_data()
    banned = update.effective_user.id in data.get('banned_users', [])
    uname_banned = is_username_banned(getattr(update.effective_user, "username", None))
    if banned or uname_banned:
        await update.message.reply_text("❌ Siete limitati dall'utilizzo del bot.")
        return
    
    step = context.user_data['step']
    option = context.user_data['option']
    
    # For congedo step 1 and 2 (date fields), validate date format
    if option == "congedo" and step in [1, 2]:
        # Validate date format: DD/MM/YY or DD/MM/YYYY
        if not re.match(r'^\d{2}/\d{2}/\d{2,4}$', answer):
            error_text = (
                "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n"
                "❌ " + to_bold("Formato Data Errato") + "\n\n"
                "La data deve essere nel formato:\n" +
                to_bold("DD/MM/YY") + " oppure " + to_bold("        YY") + "\n\n"
                "Esempi: " + to_bold("11/02/26") + " oppure " + to_bold("11/02/2026")
            )
            msg = await update.message.reply_text(error_text, parse_mode="Markdown")
            import asyncio
            asyncio.create_task(schedule_delete_message(context.bot, update.effective_chat.id, msg.message_id))
            return
        
        # Try to parse the date to ensure it's valid
        try:
            # Try YY format first
            datetime.datetime.strptime(answer, "%d/%m/%y")
        except ValueError:
            try:
                # Try YYYY format
                datetime.datetime.strptime(answer, "%d/%m/%Y")
            except ValueError:
                # Invalid date
                error_text = (
                    "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n"
                    "❌ " + to_bold("Data Non Valida") + "\n\n"
                    "La data inserita non è valida.\n"
                    "Verifica di aver inserito una data reale nel formato:\n" +
                    to_bold("DD/MM/YY") + " oppure " + to_bold("DD/MM/YYYY")
                )
                msg = await update.message.reply_text(error_text, parse_mode="Markdown")
                import asyncio
                asyncio.create_task(schedule_delete_message(context.bot, update.effective_chat.id, msg.message_id))
                return
    
    context.user_data['answers'].append(answer)
    
    # Delete question and user message
    question_msg_id = context.user_data.get('question_msg_id')
    if question_msg_id:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=question_msg_id)
        except Exception:
            pass  # Ignore if message not found
    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
    except Exception:
        pass  # Ignore if message not found
    
    context.user_data['step'] += 1
    await ask_next_question(update, context)

async def congedi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != -1003640567963:
        return
    today = datetime.date.today()
    
    try:
        recolor_congedi_sheet()
    except Exception as e:
        logging.warning(f"Recolor congedi command skipped: {e}")
    
    # Filter for approved congedi that are still active
    active = []
    if SHEET_WORKBOOK is not None:
        records = sheets_read_records("Congedi", "totali")
        for r in records:
            try:
                try:
                    end_date = datetime.datetime.strptime(r.get("end_date",""), "%d/%m/%Y").date()
                except ValueError:
                    end_date = datetime.datetime.strptime(r.get("end_date",""), "%d/%m/%y").date()
                if end_date >= today:
                    active.append({
                        "nick": r.get("employee_nick",""),
                        "start": r.get("start_date",""),
                        "end": r.get("end_date",""),
                        "motiv": r.get("motivo",""),
                    })
            except Exception:
                active.append({
                    "nick": r.get("employee_nick",""),
                    "start": r.get("start_date",""),
                    "end": r.get("end_date",""),
                    "motiv": r.get("motivo",""),
                })
    else:
        data = load_data()
        logging.info(f"📋 /congedi command called. Current DB has {len(data.get('congedi', []))} congedi")
        for c in data.get("congedi", []):
            if c.get("status") != "approved":
                continue
            try:
                try:
                    end_date = datetime.datetime.strptime(c["end"], "%d/%m/%Y").date()
                except ValueError:
                    end_date = datetime.datetime.strptime(c["end"], "%d/%m/%y").date()
                if end_date >= today:
                    active.append(c)
            except (ValueError, KeyError):
                logging.info(f"Date parsing failed for congedo {c.get('nick')}, treating as active: {c['end']}")
                active.append(c)
    
    text = "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n📅 " + to_bold("Congedi Attivi") + "\n\n"
    if active:
        for c in active:
            text += f"👤 {to_bold(c['nick'])} - Dal {to_bold(c['start'])} al {to_bold(c['end'])} - {c['motiv']}\n"
    else:
        text += "❌ Non sono presenti congedi attivi questa settimana."
    await update.message.reply_text(text, parse_mode="Markdown")

async def congedi_scaduti_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != -1003640567963:
        return
    today = datetime.date.today()
    week_ago = today - datetime.timedelta(days=7)
    
    # Filter for approved congedi that are expired but within the last 7 days
    expired_recent = []
    if SHEET_WORKBOOK is not None:
        records = sheets_read_records("Congedi", "totali")
        for r in records:
            try:
                try:
                    end_date = datetime.datetime.strptime(r.get("end_date",""), "%d/%m/%Y").date()
                except ValueError:
                    end_date = datetime.datetime.strptime(r.get("end_date",""), "%d/%m/%y").date()
                if week_ago <= end_date < today:
                    expired_recent.append({
                        "nick": r.get("employee_nick",""),
                        "start": r.get("start_date",""),
                        "end": r.get("end_date",""),
                        "motiv": r.get("motivo",""),
                    })
            except Exception:
                logging.info(f"Date parsing failed for congedo {r.get('employee_nick')}, keeping it: {r.get('end_date')}")
    else:
        data = load_data()
        logging.info(f"📋 /congediscaduti command called. Current DB has {len(data.get('congedi', []))} congedi")
        for c in data.get("congedi", []):
            if c.get("status") != "approved":
                continue
            try:
                try:
                    end_date = datetime.datetime.strptime(c["end"], "%d/%m/%Y").date()
                except ValueError:
                    end_date = datetime.datetime.strptime(c["end"], "%d/%m/%y").date()
                if week_ago <= end_date < today:
                    expired_recent.append(c)
            except (ValueError, KeyError):
                logging.info(f"Date parsing failed for congedo {c.get('nick')}, keeping it: {c['end']}")
    text = "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n📅 " + to_bold("Congedi Scaduti (ultima settimana)") + "\n\n"
    if expired_recent:
        for c in expired_recent:
            text += f"👤 {to_bold(c['nick'])} - Dal {to_bold(c['start'])} al {to_bold(c['end'])} - {c['motiv']}\n"
    else:
        text += "❌ Non sono presenti congedi scaduti nell'ultima settimana."
    await update.message.reply_text(text, parse_mode="Markdown")

def get_week_range(date):
    start = date - datetime.timedelta(days=date.weekday())  # Monday
    end = start + datetime.timedelta(days=6)  # Sunday
    return start, end

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE, category):
    if update.effective_chat.id != -1003640567963:
        return
    if SHEET_WORKBOOK is not None:
        sheet_map = {"bonifici":"Bonifici","versamenti":"Versamenti","assegni":"Assegni","piva":"PIva","upgrade_carta":"Upgrade Carta"}
        sheet_name = sheet_map.get(category, "")
        filtered = sheets_read_records(sheet_name, "settimanali") if sheet_name else []
    else:
        data = load_data()
        today = datetime.date.today()
        start_week, end_week = get_week_range(today)
        records = data.get(category, [])
        filtered = [r for r in records if start_week <= datetime.date.fromisoformat(r["date"]) <= end_week]
    text = f"𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n📋 " + to_bold(f"{category.title()} Questa Settimana") + "\n\n"
    if filtered:
        if SHEET_WORKBOOK is not None:
            for r in filtered:
                if category == "versamenti":
                    text += f"📥 {to_bold('Nick Dipendente')}: {r.get('employee_nick','')}\n"
                    text += f"👤 {to_bold('Nick Cliente')}: {r.get('client_nick','')}\n"
                    text += f"💵 {to_bold('Importo versato')}: {int(r.get('importo',0))}\n"
                    text += f"💰 {to_bold('Commissioni')}: {r.get('commissioni_user','')}\n"
                    text += f"🏦 {to_bold('Provenienza')}: {r.get('provenienza','')}\n"
                    text += f"📅 {to_bold('Data')}: {r.get('date','')}\n\n"
                elif category == "bonifici":
                    text += f"📥 {to_bold('Nick Dipendente')}: {r.get('employee_nick','')}\n"
                    text += f"👤 {to_bold('Nick Mittente')}: {r.get('sender_nick','')}\n"
                    text += f"👥 {to_bold('Nick Beneficiario')}: {r.get('beneficiary_nick','')}\n"
                    text += f"💸 {to_bold('Importo')}: {int(r.get('importo',0))}\n"
                    text += f"💰 {to_bold('Commissioni')}: {r.get('commissioni_user','')}\n"
                    text += f"📝 {to_bold('Causale')}: {r.get('causale','')}\n"
                    text += f"📅 {to_bold('Data')}: {r.get('date','')}\n\n"
                elif category == "assegni":
                    text += f"📥 {to_bold('Nick Dipendente')}: {r.get('employee_nick','')}\n"
                    text += f"👥 {to_bold('Nick Mittente')}: {r.get('sender_nick','')}\n"
                    text += f"👤 {to_bold('Nick Beneficiario')}: {r.get('beneficiary_nick','')}\n"
                    text += f"🧾 {to_bold('Importo Assegno')}: {int(r.get('importo',0))}\n"
                    text += f"📝 {to_bold('Causale')}: {r.get('causale','')}\n"
                    text += f"📅 {to_bold('Data')}: {r.get('date','')}\n\n"
                elif category == "piva":
                    text += f"📥 {to_bold('Nick Dipendente')}: {r.get('employee_nick','')}\n"
                    text += f"👤 {to_bold('Nick Direttore')}: {r.get('direttore_nick','')}\n"
                    text += f"🏢 {to_bold('Nome P.Iva')}: {r.get('nome_piva','')}\n"
                    text += f"📅 {to_bold('Data')}: {r.get('date','')}\n\n"
                elif category == "upgrade_carta":
                    text += f"📥 {to_bold('Nick Dipendente')}: {r.get('employee_nick','')}\n"
                    text += f"👤 {to_bold('Nick Cliente')}: {r.get('cliente_nick','')}\n"
                    text += f"💳 {to_bold('Tipo conto')}: {r.get('tipo_conto','')}\n"
                    text += f"📅 {to_bold('Data')}: {r.get('date','')}\n\n"
        else:
            for r in filtered:
                text += r["summary"] + "\n\n"
    else:
        text += f"❌ Non sono presenti {category.lower()} questa settimana."
    await update.message.reply_text(text, parse_mode="Markdown")



async def limita_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ban a user from using the bot. Only works in the group."""
    if update.effective_chat.id != -1003640567963:
        return
    
    # Check if command has arguments
    if not context.args:
        await update.message.reply_text(
            "❌ " + to_bold("Errore") + "\n\n"
            "Uso: /limita @utente\n"
            "Esempio: /limita @SoyLe0",
            parse_mode="Markdown"
        )
        return
    
    user_id = None
    user_name = None
    
    # Try to extract user ID from message entities (mentions)
    if update.message.entities:
        for entity in update.message.entities:
            if entity.type == "text_mention" and entity.user:
                # Direct mention with user info loaded
                user_id = entity.user.id
                user_name = entity.user.first_name or entity.user.username or f"Utente {user_id}"
                break
            elif entity.type == "mention":
                # Mention in text format @username
                mention_text = update.message.text[entity.offset:entity.offset+entity.length]
                username = mention_text.lstrip("@")
                # Try to resolve username to user_id using get_chat
                try:
                    chat = await context.bot.get_chat(chat_id=mention_text)
                    user_id = chat.id
                    user_name = chat.first_name or chat.username or f"Utente {user_id}"
                except Exception:
                    pass
                break
    
    # Fallback: try with context.args
    if not user_id:
        target = context.args[0].strip("@")
        try:
            # Try to resolve using get_chat with @username format
            chat = await context.bot.get_chat(chat_id=f"@{target}")
            user_id = chat.id
            user_name = chat.first_name or chat.username or target
        except Exception:
            # Try as numeric ID
            try:
                user_id = int(target)
                user_name = f"Utente {user_id}"
            except ValueError:
                await update.message.reply_text(
                    "❌ " + to_bold("Utente non trovato") + "\n\n"
                    "Non riesco a trovare l'utente: " + target,
                    parse_mode="Markdown"
                )
                return
    
    # Add user to ban list
    data = load_data()
    if "banned_users" not in data:
        data["banned_users"] = []
    if "banned_usernames" not in data:
        data["banned_usernames"] = []
    
    if user_id not in data["banned_users"]:
        data["banned_users"].append(user_id)
    # Also store username if available or provided by args
    uname = None
    try:
        uname = update.effective_user.username
    except Exception:
        uname = None
    if not uname and context.args:
        arg0 = context.args[0]
        if arg0.startswith("@"):
            uname = arg0[1:]
    if uname:
        if uname.lower() not in [x.lower() for x in data["banned_usernames"]]:
            data["banned_usernames"].append(uname)
        save_data(data)
        await update.message.reply_text(
            "✅ " + to_bold("Utente Limitato") + "\n\n"
            f"L'utente {to_bold(user_name)} è stato limitato dall'utilizzo del bot.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "⚠️ " + to_bold("Avviso") + "\n\n"
            f"L'utente {to_bold(user_name)} è già limitato.",
            parse_mode="Markdown"
        )

async def unlimita_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unban a user. Only works in the group."""
    if update.effective_chat.id != -1003640567963:
        return
    
    # Check if command has arguments
    if not context.args:
        await update.message.reply_text(
            "❌ " + to_bold("Errore") + "\n\n"
            "Uso: /unlimita @utente\n"
            "Esempio: /unlimita @SoyLe0",
            parse_mode="Markdown"
        )
        return
    
    user_id = None
    user_name = None
    
    # Try to extract user ID from message entities (mentions)
    if update.message.entities:
        for entity in update.message.entities:
            if entity.type == "text_mention" and entity.user:
                # Direct mention with user info loaded
                user_id = entity.user.id
                user_name = entity.user.first_name or entity.user.username or f"Utente {user_id}"
                break
            elif entity.type == "mention":
                # Mention in text format @username
                mention_text = update.message.text[entity.offset:entity.offset+entity.length]
                username = mention_text.lstrip("@")
                # Try to resolve username to user_id using get_chat
                try:
                    chat = await context.bot.get_chat(chat_id=mention_text)
                    user_id = chat.id
                    user_name = chat.first_name or chat.username or f"Utente {user_id}"
                except Exception:
                    pass
                break
    
    # Fallback: try with context.args
    if not user_id:
        target = context.args[0].strip("@")
        try:
            # Try to resolve using get_chat with @username format
            chat = await context.bot.get_chat(chat_id=f"@{target}")
            user_id = chat.id
            user_name = chat.first_name or chat.username or target
        except Exception:
            # Try as numeric ID
            try:
                user_id = int(target)
                user_name = f"Utente {user_id}"
            except ValueError:
                await update.message.reply_text(
                    "❌ " + to_bold("Utente non trovato") + "\n\n"
                    "Non riesco a trovare l'utente: " + target,
                    parse_mode="Markdown"
                )
                return
    
    # Remove user from ban list
    data = load_data()
    if "banned_users" not in data:
        data["banned_users"] = []
    if "banned_usernames" not in data:
        data["banned_usernames"] = []
    
    if user_id in data["banned_users"]:
        data["banned_users"].remove(user_id)
    # Also remove username if provided
    uname = None
    try:
        uname = update.effective_user.username
    except Exception:
        uname = None
    if not uname and context.args:
        arg0 = context.args[0]
        if arg0.startswith("@"):
            uname = arg0[1:]
    if uname:
        data["banned_usernames"] = [x for x in data["banned_usernames"] if x.lower() != uname.lower()]
        save_data(data)
        await update.message.reply_text(
            "✅ " + to_bold("Limitazione Rimossa") + "\n\n"
            f"L'utente {to_bold(user_name)} può ora utilizzare il bot.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "⚠️ " + to_bold("Avviso") + "\n\n"
            f"L'utente {to_bold(user_name)} non è limitato.",
            parse_mode="Markdown"
        )

async def bonifici_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await list_command(update, context, "bonifici")

async def versamenti_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await list_command(update, context, "versamenti")

async def assegni_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await list_command(update, context, "assegni")

async def piva_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await list_command(update, context, "piva")

async def upgrade_carta_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await list_command(update, context, "upgrade_carta")

async def listamoduli_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List the number of modules completed by each employee (from database only)."""
    if update.effective_chat.id != -1003640567963:
        return
    
    # Send message with two buttons to choose between Totali and Settimanali
    text = (
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n"
        "📊 <b>Statistiche Moduli</b>\n\n"
        "Scegli quale visualizzazione vuoi:"
    )
    
    keyboard = [
        [InlineKeyboardButton("📈 Totali", callback_data="listamoduli_totali")],
        [InlineKeyboardButton("📅 Settimanali", callback_data="listamoduli_settimanali")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=reply_markup)

async def listamoduli_show(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    """Display modules statistics based on mode (totali or settimanali)."""
    query = update.callback_query
    await query.answer()
    
    # Load data
    if SHEET_WORKBOOK is not None:
        modules_per_employee = sheets_count_modules("totali" if mode == "totali" else "settimanali")
        try:
            db = load_data()
            excluded = [x.lower() for x in db.get("excluded_employees", [])]
            if excluded:
                modules_per_employee = {k: v for k, v in modules_per_employee.items() if k.lower() not in excluded}
        except Exception:
            pass
    else:
        data = load_data()
        check_and_reset_weekly_stats(data)
        modules_per_employee = {}
        categories = ["versamenti", "bonifici", "assegni", "piva", "upgrade_carta"]
        if mode == "totali":
            for category in categories:
                records = data.get(category, [])
                for record in records:
                    nick = record.get("employee_nick", "").strip()
                    if nick:
                        modules_per_employee[nick] = modules_per_employee.get(nick, 0) + 1
            congedi = data.get("congedi", [])
            for record in congedi:
                if record.get("status") == "approved":
                    nick = record.get("employee_nick", "").strip()
                    if nick:
                        modules_per_employee[nick] = modules_per_employee.get(nick, 0) + 1
        else:
            weekly = data.get("weekly_stats", {})
            for category in categories:
                records = weekly.get(category, [])
                for record in records:
                    nick = record.get("employee_nick", "").strip()
                    if nick:
                        modules_per_employee[nick] = modules_per_employee.get(nick, 0) + 1
        excluded = [x.lower() for x in data.get("excluded_employees", [])]
        if excluded:
            modules_per_employee = {k: v for k, v in modules_per_employee.items() if k.lower() not in excluded}
    
    # Build response text
    if not modules_per_employee:
        text = (
            "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n"
            "📊 <b>Statistiche Moduli " + ("Totali" if mode == "totali" else "Settimanali") + "</b>\n\n"
            "❌ Nessun modulo compilato."
        )
    else:
        text = (
            "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n"
            "📊 <b>Statistiche Moduli " + ("Totali" if mode == "totali" else "Settimanali") + "</b>\n\n"
        )
        
        # Sort by number of modules (descending)
        sorted_employees = sorted(modules_per_employee.items(), key=lambda x: x[1], reverse=True)
        
        for nick, count in sorted_employees:
            emoji = "📈" if count >= 5 else "📝" if count >= 3 else "📌"
            text += f"{emoji} <b>{nick}</b>: {count} moduli\n"
        
        total = sum(modules_per_employee.values())
        text += f"\n📊 Totale moduli compilati: <b>{total}</b>\n"
        text += f"👥 Dipendenti attivi: <b>{len(modules_per_employee)}</b>"
    
    await query.edit_message_text(text, parse_mode="HTML")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != -1003640567963:
        return
    text = (
        "𝐀𝐭𝐥𝐚𝐧𝐭𝐢𝐬 𝐍𝐚𝐭𝐢𝐨𝐧𝐚𝐥 𝐁𝐚𝐧𝐤 • 𝐁𝐨𝐭\n\n"
        "📋 <b>COMANDI DISPONIBILI</b>\n"
        "═══════════════════════════════════════\n\n"
        "📅 <b>/congedi</b> - Congedi Attivi\n"
        "   » Visualizza l'elenco dei congedi attivi\n\n"
        "⏰ <b>/congediscaduti</b> - Congedi Scaduti\n"
        "   » Mostra i congedi scaduti dell'ultima settimana\n\n"
        "📊 <b>/listamoduli</b> - Lista Moduli\n"
        "   » Mostra il numero di moduli compilati per dipendente\n\n"
        "� <b>/licenzia</b> - Licenzia Dipendente\n"
        "   » Rimuove completamente un dipendente dal sistema\n"
        "   » Uso: /licenzia Nick Dipendente\n\n"
        "💰 <b>/controlla</b> - Controlla Depositi Cliente\n"
        "   » Mostra il totale depositato da un cliente\n"
        "   » Uso: /controlla Nick Cliente\n\n"
        "�🚫 <b>/limita</b> - Limita Utente\n"
        "   » Bannare un utente dal bot\n"
        "   » Uso: /limita @utente\n\n"
        "✅ <b>/unlimita</b> - Rimuovi Limitazione\n"
        "   » Sbannare un utente limitato\n"
        "   » Uso: /unlimita @utente\n\n"
        "❓ <b>/help</b> - Guida Comandi\n"
        "   » Visualizza questo messaggio di aiuto\n\n"
        "═══════════════════════════════════════\n\n"
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def set_commands(app):
    commands = [
        BotCommand("congedi", "Lista congedi attivi"),
        BotCommand("congediscaduti", "Congedi scaduti nell'ultima settimana"),
        BotCommand("listamoduli", "Numero di moduli compilati per dipendente"),
        BotCommand("licenzia", "Rimuovi dipendente dal sistema"),
        BotCommand("controlla", "Visualizza depositi cliente"),
        BotCommand("negra", "Invia GIF casuale a utente"),
        BotCommand("limita", "Limita un utente dal bot"),
        BotCommand("unlimita", "Rimuovi la limitazione di un utente"),
        BotCommand("help", "Comandi disponibili")
    ]
    await app.bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=-1003640567963))

async def keep_alive_job(context: ContextTypes.DEFAULT_TYPE):
    """Keep-alive job to prevent the bot from stopping due to inactivity."""
    try:
        # Simple health check - get bot info
        bot_info = await context.bot.get_me()
        logging.info(f"🔄 Keep-alive ping: Bot {bot_info.username} is active")
    except Exception as e:
        logging.error(f"❌ Keep-alive job failed: {e}")

async def licenzia_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != -1003640567963:
        return
    
    if not context.args:
        await update.message.reply_text("❌ Uso: /licenzia NickDipendente")
        return
    
    employee_nick = " ".join(context.args)
    
    try:
        db = load_data()
        if "excluded_employees" not in db:
            db["excluded_employees"] = []
        if employee_nick.lower() not in [x.lower() for x in db["excluded_employees"]]:
            db["excluded_employees"].append(employee_nick)
            save_data(db)
            await update.message.reply_text(
                f"✅ <b>{employee_nick}</b> escluso da /listamoduli (rimane nel foglio).",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                f"⚠️ <b>{employee_nick}</b> è già escluso da /listamoduli.",
                parse_mode="HTML"
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Errore: {e}")
        logging.error(f"Errore in /licenzia: {e}")

async def controlla_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != -1003640567963:
        return
    
    if not context.args:
        await update.message.reply_text("❌ Uso: /controlla NickCliente")
        return
    
    client_nick = " ".join(context.args)
    context.user_data["controlla_nick"] = client_nick
    
    # Show mode selection buttons
    keyboard = [
        [
            InlineKeyboardButton("📊 Totale", callback_data="controlla_totali"),
            InlineKeyboardButton("📅 Settimanale", callback_data="controlla_settimanali")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Scegli quale visualizzazione per <b>{client_nick}</b>:",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def negra_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != -1003640567963:
        return
    if not context.args:
        await update.message.reply_text("❌ Uso: /negra [numero] @utente")
        return
    count = 1
    target_arg = None
    try:
        if len(context.args) >= 2 and str(context.args[0]).isdigit():
            count = max(1, int(context.args[0]))
            target_arg = context.args[1]
        else:
            target_arg = context.args[0]
    except Exception:
        target_arg = context.args[0]
    user_id = None
    user_name = None
    if update.message.entities:
        for entity in update.message.entities:
            if entity.type == "text_mention" and entity.user:
                user_id = entity.user.id
                user_name = entity.user.first_name or entity.user.username or f"Utente {user_id}"
                break
            elif entity.type == "mention":
                mention_text = update.message.text[entity.offset:entity.offset+entity.length]
                username = mention_text.lstrip("@")
                resolved = False
                try:
                    chat = await context.bot.get_chat(chat_id=f"@{username}")
                    user_id = chat.id
                    user_name = chat.first_name or chat.username or f"Utente {user_id}"
                    resolved = True
                except Exception:
                    try:
                        chat = await context.bot.get_chat(chat_id=username)
                        user_id = chat.id
                        user_name = chat.first_name or chat.username or f"Utente {user_id}"
                        resolved = True
                    except Exception:
                        data = load_data()
                        ku = data.get("known_users", {})
                        uid = ku.get(username.lower())
                        if uid:
                            user_id = uid
                            user_name = username
                            resolved = True
                break
    if not user_id:
        target = str(target_arg or "").strip("@")
        data = load_data()
        ku = data.get("known_users", {})
        uid = ku.get(target.lower())
        if uid:
            user_id = uid
            user_name = target
        else:
            try:
                chat = await context.bot.get_chat(chat_id=f"@{target}")
                user_id = chat.id
                user_name = chat.first_name or chat.username or target
            except Exception:
                try:
                    chat = await context.bot.get_chat(chat_id=target)
                    user_id = chat.id
                    user_name = chat.first_name or chat.username or target
                except Exception:
                    try:
                        user_id = int(target)
                        user_name = f"Utente {user_id}"
                    except ValueError:
                        await update.message.reply_text(f"❌ Utente non trovato: {target}")
                        return
    data = load_data()
    known_ids = set(data.get("known_user_ids", []))
    if user_id not in known_ids:
        await update.message.reply_text(f"❌ L'utente {user_name} non ha mai avviato il bot.")
        return
    try:
        import random
        import os
        path = os.path.join(os.getcwd(), "gifs.txt")
        lines = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
        except FileNotFoundError:
            lines = []
        if not lines:
            await update.message.reply_text("❌ Nessuna GIF disponibile.")
            return
        for _ in range(count):
            file_id = random.choice(lines)
            await context.bot.send_animation(chat_id=user_id, animation=file_id)
        await update.message.reply_text(f"✅ {count} GIF inviata/e a {user_name}")
    except Exception as e:
        await update.message.reply_text(f"❌ Impossibile inviare GIF a {user_name}: {e}")

async def aggiungi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    if update.effective_user.id != 1832934927:
        await update.message.reply_text("❌ Comando riservato.")
        return
    data = load_data()
    data["gif_collecting"] = True
    save_data(data)
    await update.message.reply_text("✅ Inizia a inviare GIF. Userò il file_id e lo salverò in gifs.txt. Usa /ferma per terminare.")

async def ferma_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    if update.effective_user.id != 1832934927:
        await update.message.reply_text("❌ Comando riservato.")
        return
    data = load_data()
    data["gif_collecting"] = False
    save_data(data)
    await update.message.reply_text("🛑 Raccolta GIF terminata.")

async def handle_gif_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    if update.effective_user.id != 1832934927:
        return
    data = load_data()
    if not data.get("gif_collecting", False):
        return
    file_id = None
    try:
        if update.message.animation:
            file_id = update.message.animation.file_id
        elif update.message.document and str(update.message.document.file_name).lower().endswith(".gif"):
            file_id = update.message.document.file_id
    except Exception:
        file_id = None
    if not file_id:
        return
    import os
    path = os.path.join(os.getcwd(), "gifs.txt")
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(file_id + "\n")
        await update.message.reply_text("✅ GIF salvata.")
    except Exception as e:
        await update.message.reply_text(f"❌ Errore salvataggio GIF: {e}")

def create_web_app():
    web_app = Flask(__name__)

    @web_app.get("/")
    def index():
        return jsonify({
            "status": "ok",
            "service": "BancaAtlantisBot",
            "message": "Bot Telegram attivo in modalità web service"
        })

    @web_app.get("/health")
    def health():
        return jsonify({
            "status": "ok",
            "service": "BancaAtlantisBot",
            "bot_token_configured": bool(BOT_TOKEN)
        })

    return web_app


app = create_web_app()


def start_background_bot():
    if os.getenv("WEBSERVICE_MODE", "0") != "1":
        return
    if not BOT_TOKEN:
        logging.warning("Skipping background bot startup because BOT_TOKEN is not configured.")
        return
    thread = threading.Thread(target=run_bot, name="telegram-bot", daemon=True)
    thread.start()
    logging.info("🧵 Telegram bot started in background thread for web service mode")




def run_bot():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN:
        logging.error("BOT_TOKEN is not configured. Set BOT_TOKEN or TELEGRAM_BOT_TOKEN before starting the bot.")
        return None

    # Create custom request with increased timeouts
    request = HTTPXRequest(read_timeout=30, write_timeout=30, connect_timeout=30, pool_timeout=30)
    app = ApplicationBuilder().token(BOT_TOKEN).request(request).post_init(set_commands).build()

    # Schedule keep-alive job every 5 minutes to prevent inactivity timeout
    app.job_queue.run_repeating(keep_alive_job, interval=300, first=10)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CommandHandler("congedi", congedi_command))
    app.add_handler(CommandHandler("congediscaduti", congedi_scaduti_command))
    app.add_handler(CommandHandler("listamoduli", listamoduli_command))
    app.add_handler(CommandHandler("licenzia", licenzia_command))
    app.add_handler(CommandHandler("controlla", controlla_command))
    app.add_handler(CommandHandler("negra", negra_command))
    app.add_handler(CommandHandler("aggiungi", aggiungi_command))
    app.add_handler(CommandHandler("ferma", ferma_command))
    app.add_handler(MessageHandler(filters.ANIMATION, handle_gif_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_gif_message))
    app.add_handler(CommandHandler("bonifici", bonifici_command))
    app.add_handler(CommandHandler("versamenti", versamenti_command))
    app.add_handler(CommandHandler("assegni", assegni_command))
    app.add_handler(CommandHandler("piva", piva_command))
    app.add_handler(CommandHandler("upgrade_carta", upgrade_carta_command))
    app.add_handler(CommandHandler("limita", limita_command))
    app.add_handler(CommandHandler("unlimita", unlimita_command))
    app.add_handler(CommandHandler("help", help_command))
    # Initialize Google Sheets integration (if credentials and sheet id provided)
    try:
        init_sheets()
    except Exception as e:
        logging.warning(f"init_sheets failed: {e}")

    # Migrate database to add employee_nick field to all records
    migrate_database()

    # Validate database and remove invalid records
    validate_database()

    # Migrate old congedi structure to new one
    migrate_old_congedi_structure()

    def migrate_versamenti_fields():
        db = load_data()
        changed = False
        def _norm_amount(x):
            try:
                s = str(x).strip().replace(".", "").replace(",", ".")
                return float(s)
            except Exception:
                return 0.0
        def _fill(record):
            s = record.get("summary", "")
            if not record.get("client_nick"):
                for line in s.splitlines():
                    t = line.strip()
                    if t.startswith("👤 "):
                        import re
                        m = re.search(r":\\s*(.+)", t)
                        if m:
                            record["client_nick"] = m.group(1).strip()
                        break
            if record.get("importo") is None:
                for line in s.splitlines():
                    t = line.strip()
                    if t.startswith("💵 "):
                        import re
                        m = re.search(r":\\s*([0-9.,]+)", t)
                        if m:
                            record["importo"] = _norm_amount(m.group(1))
                        break
        for rec in db.get("versamenti", []):
            before = (rec.get("client_nick"), rec.get("importo"))
            _fill(rec)
            after = (rec.get("client_nick"), rec.get("importo"))
            if before != after:
                changed = True
        ws = db.get("weekly_stats", {}).get("versamenti", [])
        for rec in ws:
            before = (rec.get("client_nick"), rec.get("importo"))
            _fill(rec)
            after = (rec.get("client_nick"), rec.get("importo"))
            if before != after:
                changed = True
        if changed:
            save_data(db)
            logging.info("✅ Migrazione campi versamenti completata")
    try:
        migrate_versamenti_fields()
    except Exception as e:
        logging.warning(f"Migrazione versamenti non riuscita: {e}")
    def migrate_generic():
        db = load_data()
        changed = False
        def _norm_amount(x):
            try:
                s = str(x).strip().replace(".", "").replace(",", ".")
                return float(s)
            except Exception:
                return 0.0
        def _parse_value(s, prefix):
            for line in s.splitlines():
                t = line.strip()
                if t.startswith(prefix):
                    import re
                    m = re.search(r":\\s*(.+)", t)
                    if m:
                        return m.group(1).strip()
                    break
            return ""
        for rec in db.get("bonifici", []):
            s = rec.get("summary", "")
            if not rec.get("sender_nick"):
                v = _parse_value(s, "👤 ")
                if v:
                    rec["sender_nick"] = v
                    changed = True
            if not rec.get("beneficiary_nick"):
                v = _parse_value(s, "👥 ")
                if v:
                    rec["beneficiary_nick"] = v
                    changed = True
            if rec.get("importo") is None:
                v = _parse_value(s, "💸 ")
                if v:
                    rec["importo"] = _norm_amount(v)
                    changed = True
            if not rec.get("causale"):
                v = _parse_value(s, "📝 ")
                if v:
                    rec["causale"] = v
                    changed = True
        for rec in db.get("assegni", []):
            s = rec.get("summary", "")
            if not rec.get("beneficiary_nick"):
                v = _parse_value(s, "👤 ")
                if v:
                    rec["beneficiary_nick"] = v
                    changed = True
            if not rec.get("sender_nick"):
                v = _parse_value(s, "👥 ")
                if v:
                    rec["sender_nick"] = v
                    changed = True
            if rec.get("importo") is None:
                v = _parse_value(s, "🧾 ")
                if v:
                    rec["importo"] = _norm_amount(v)
                    changed = True
            if not rec.get("causale"):
                v = _parse_value(s, "📝 ")
                if v:
                    rec["causale"] = v
                    changed = True
        for rec in db.get("piva", []):
            s = rec.get("summary", "")
            if not rec.get("direttore_nick"):
                v = _parse_value(s, "👤 ")
                if v:
                    rec["direttore_nick"] = v
                    changed = True
            if not rec.get("nome_piva"):
                v = _parse_value(s, "🏢 ")
                if v:
                    rec["nome_piva"] = v
                    changed = True
        for rec in db.get("upgrade_carta", []):
            s = rec.get("summary", "")
            if not rec.get("cliente_nick"):
                v = _parse_value(s, "👤 ")
                if v:
                    rec["cliente_nick"] = v
                    changed = True
            if not rec.get("tipo_conto"):
                v = _parse_value(s, "💳 ")
                if v:
                    rec["tipo_conto"] = v
                    changed = True
        weekly = db.get("weekly_stats", {})
        def _apply_week(cat, prefixes):
            lst = weekly.get(cat, [])
            for rec in lst:
                s = rec.get("summary", "")
                for key, pref, kind in prefixes:
                    if not rec.get(key) or rec.get(key) is None:
                        v = _parse_value(s, pref)
                        if v:
                            rec[key] = _norm_amount(v) if kind == "amount" else v
                            changed = True
        _apply_week("bonifici", [("sender_nick","👤 ","text"),("beneficiary_nick","👥 ","text"),("importo","💸 ","amount"),("causale","📝 ","text")])
        _apply_week("assegni", [("beneficiary_nick","👤 ","text"),("sender_nick","👥 ","text"),("importo","🧾 ","amount"),("causale","📝 ","text")])
        _apply_week("piva", [("direttore_nick","👤 ","text"),("nome_piva","🏢 ","text")])
        _apply_week("upgrade_carta", [("cliente_nick","👤 ","text"),("tipo_conto","💳 ","text")])
        if changed:
            save_data(db)
            logging.info("✅ Migrazione campi moduli generici completata")
    try:
        migrate_generic()
    except Exception as e:
        logging.warning(f"Migrazione moduli generici non riuscita: {e}")

    # Graceful shutdown handling
    def signal_handler(sig, frame):
        logging.info("\nShutdown signal received, stopping bot...")
        app.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logging.info("🚀 Bot avvio... Keep-alive job attivato ogni 5 minuti")
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logging.error(f"Error during polling: {e}")
        app.stop()


def main():
    if os.getenv("WEBSERVICE_MODE", "0") == "1":
        web_app = create_web_app()
        port = int(os.getenv("PORT", "10000"))
        import threading
        thread = threading.Thread(target=run_bot, daemon=True)
        thread.start()
        logging.info(f"🌐 Web service mode enabled on port {port}")
        web_app.run(host="0.0.0.0", port=port, debug=False)
    else:
        run_bot()


if __name__ == "__main__":
    main()
