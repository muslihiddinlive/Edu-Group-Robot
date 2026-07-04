#!/usr/bin/env python3
"""
Lang Bot v5.0
REQUIRES: pip install "python-telegram-bot[job-queue]==21.9" aiohttp

YANGI (v5.0):
  - Foydalanuvchi tizimi (users.json) + referral
  - Tarif tizimi: Free / PLUS ✨ / Premium 💎 / VIP 👑
  - Telegram Stars to'lov
  - 🔥 Reaksiya (tarif egalariga)
  - Supergroup forum topic boshqaruvi
    (VIP/Premium/PLUS/Backup topic'lari, class topic'lari)
  - VIP: admin qo'shish huquqi (max 2 ta)
  - Inline button asosli UI (oddiy userlar uchun faqat button)
  - Superadmin: /setprice, /setchannel, /delmsgs, /delbotmsg

KERAKLI ENV:
  BOT_TOKEN, SUPERADMIN_ID, WEBHOOK_URL (yoki RENDER_EXTERNAL_URL)
  WEBHOOK_SECRET (ixtiyoriy)
  SUPERGROUP_ID  - forum topic ochish uchun supergroup ID
"""

import asyncio, io, json, os, random, logging, re
from datetime import datetime, timezone, timedelta, time as dt_time

TZ = timezone(timedelta(hours=5))  # UTC+5 — Toshkent

from aiohttp import web

from telegram import (
    Update,
    InlineKeyboardButton as IKB,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    LabeledPrice,
    ReactionTypeEmoji,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ChatMemberHandler, PreCheckoutQueryHandler,
    filters, ContextTypes,
)
from telegram.constants import ReactionEmoji

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════

def _require_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise SystemExit(f"❌ '{key}' ENV topilmadi!")
    return val

BOT_TOKEN      = _require_env("BOT_TOKEN")
SUPERADMIN     = int(_require_env("SUPERADMIN_ID"))
SUPERGROUP_ID  = int(os.environ.get("SUPERGROUP_ID", "0"))

PORT           = int(os.environ.get("PORT", "8080"))
WEBHOOK_URL    = (os.environ.get("WEBHOOK_URL")
                  or os.environ.get("RENDER_EXTERNAL_URL")
                  or "").rstrip("/")
WEBHOOK_PATH   = f"webhook/{BOT_TOKEN}"
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

# Limitlar
MAX_TOPICS     = 10
MAX_QUESTIONS  = 1000
TOPICS_DIR     = "topics"
ADMINS_FILE    = "admins.json"
CHATS_FILE     = "chats.json"
CONFIG_FILE    = "config.json"
BADWORDS_FILE  = "badwords.json"
USERS_FILE     = "users.json"
MAX_MSG_HISTORY = 10000
EXPORT_VERSION  = 5
BROADCAST_READY = "✅ Tayyor — Yuborishni boshlash"

# Tarif identifikatorlari
TARIF_FREE    = "free"
TARIF_PLUS    = "plus"
TARIF_PREMIUM = "premium"
TARIF_VIP     = "vip"

TARIF_NAMES = {
    TARIF_FREE:    "Free",
    TARIF_PLUS:    "PLUS ✨",
    TARIF_PREMIUM: "Premium 💎",
    TARIF_VIP:     "VIP 👑",
}

# Default tarif narxlari (stars) — config.json'da o'zgartiriladi
DEFAULT_PRICES = {
    TARIF_PLUS:    25,
    TARIF_PREMIUM: 50,
    TARIF_VIP:     500,
}

# Default tarif limitlari
TARIF_TOPIC_LIMIT = {
    TARIF_FREE:    1,    # +1 har 3 referal, max 5
    TARIF_PLUS:    10,
    TARIF_PREMIUM: 20,
    TARIF_VIP:     70,
}

TARIF_Q_LIMIT = {
    TARIF_FREE:    10,   # obuna bo'lsa +10
    TARIF_PLUS:    100,
    TARIF_PREMIUM: 200,
    TARIF_VIP:     1500,
}

# Free user uchun referral bilan max topic
FREE_MAX_TOPIC_REFERRAL = 5
FREE_REFERRAL_PER_TOPIC = 3  # har 3 referalda +1

TARGET_NAMES = {
    "all":      "👥 Hammaga",
    "private":  "👤 Faqat userlarga",
    "groups":   "🏘 Faqat guruhlarga",
    "channels": "📢 Faqat kanallarga",
}
TARGET_KEYS = {v: k for k, v in TARGET_NAMES.items()}

ACCESS_LABELS = {
    "all":     "👥 Hamma adminlar",
    "owner":   "👤 Faqat men",
    "admins":  "🔑 Faqat bot adminlari",
    "custom":  "✏️ Qo'lda belgilangan",
}

os.makedirs(TOPICS_DIR, exist_ok=True)

# ══════════════════════════════════════════════════════
#  AUTO-BACKUP HOOK (har qanday o'zgarishda avtomatik export)
# ══════════════════════════════════════════════════════
_BOT_REF = None
_RESTORING = False
_suppress_export_hook = False
_export_task = None
EXPORT_DEBOUNCE_SECONDS = 3.0

async def _debounced_export_runner(delay: float):
    global _export_task
    try:
        await asyncio.sleep(delay)
        if _BOT_REF is not None:
            await do_export(_BOT_REF, to_backup_topic=True)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"Auto-backup (debounced) xato: {e}")

def mark_changed():
    """Har qanday saqlanadigan ma'lumot o'zgarganda chaqiriladi.
    3 soniyalik debounce bilan — ketma-ket tez o'zgarishlarda faqat
    bitta yangi backup fayl yaratiladi (Telegram flood limitidan qochish uchun)."""
    global _export_task
    if _RESTORING or _suppress_export_hook or _BOT_REF is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _export_task and not _export_task.done():
        _export_task.cancel()
    _export_task = loop.create_task(_debounced_export_runner(EXPORT_DEBOUNCE_SECONDS))

# ══════════════════════════════════════════════════════
#  FILE HELPERS
# ══════════════════════════════════════════════════════

def _jload(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _jsave(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    mark_changed()

# ── Topics ──
def _ensure_topics_dir():
    os.makedirs(TOPICS_DIR, exist_ok=True)

def topic_path(name: str) -> str:
    return os.path.join(TOPICS_DIR, f"{name.lower()}.json")

def topic_exists(name: str) -> bool:
    _ensure_topics_dir()
    return os.path.exists(topic_path(name))

def load_topic(name: str) -> dict | None:
    _ensure_topics_dir()
    p = topic_path(name)
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def save_topic(data: dict):
    _ensure_topics_dir()
    with open(topic_path(data["name"]), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    mark_changed()

def all_topics() -> list:
    _ensure_topics_dir()
    out = []
    for fn in sorted(os.listdir(TOPICS_DIR)):
        if fn.endswith(".json"):
            with open(os.path.join(TOPICS_DIR, fn), "r", encoding="utf-8") as f:
                out.append(json.load(f))
    return out

def count_topics() -> int:
    _ensure_topics_dir()
    return sum(1 for f in os.listdir(TOPICS_DIR) if f.endswith(".json"))

# ── Admins, Chats, Config, Badwords ──
def load_admins() -> dict:  return _jload(ADMINS_FILE)
def save_admins(d: dict):   _jsave(ADMINS_FILE, d)
def load_chats() -> dict:   return _jload(CHATS_FILE)
def save_chats(d: dict):    _jsave(CHATS_FILE, d)
def load_config() -> dict:  return _jload(CONFIG_FILE)
def save_config(d: dict):   _jsave(CONFIG_FILE, d)

def load_badwords() -> dict:
    d = _jload(BADWORDS_FILE)
    d.setdefault("words", [])
    d.setdefault("severe_words", [])
    d.setdefault("warnings", [])
    return d

def save_badwords(d: dict): _jsave(BADWORDS_FILE, d)

# ── Users ──
def load_users() -> dict:
    return _jload(USERS_FILE)

def save_users(d: dict):
    _jsave(USERS_FILE, d)

def get_user(uid: int) -> dict | None:
    return load_users().get(str(uid))

def save_user(uid: int, data: dict):
    users = load_users()
    users[str(uid)] = data
    save_users(users)

def register_user(user, ref_by: int | None = None) -> bool:
    """User'ni ro'yxatga oladi. Yangi bo'lsa True qaytaradi."""
    users = load_users()
    uid   = str(user.id)
    if uid in users:
        # Ma'lumotlarni yangilash
        users[uid]["first_name"] = user.first_name or ""
        users[uid]["last_name"]  = user.last_name or ""
        users[uid]["username"]   = user.username or ""
        save_users(users)
        return False
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    users[uid] = {
        "id":            user.id,
        "first_name":    user.first_name or "",
        "last_name":     user.last_name  or "",
        "username":      user.username   or "",
        "language_code": getattr(user, "language_code", "") or "",
        "joined_at":     now,
        "referral_by":   ref_by,
        "referral_count": 0,
        "tarif":         TARIF_FREE,
        "tarif_expires": None,
        "is_subscribed": False,
    }
    save_users(users)
    # Referalni hisoblaymiz
    if ref_by and str(ref_by) != uid:
        _add_referral(ref_by)
    return True

def _add_referral(ref_uid: int):
    users = load_users()
    k = str(ref_uid)
    if k in users:
        users[k]["referral_count"] = users[k].get("referral_count", 0) + 1
        save_users(users)

def get_user_tarif(uid: int) -> str:
    if uid == SUPERADMIN:
        return TARIF_VIP
    u = get_user(uid)
    if not u:
        return TARIF_FREE
    # Muddatini tekshirish
    tarif = u.get("tarif", TARIF_FREE)
    exp   = u.get("tarif_expires")
    if tarif != TARIF_FREE and exp:
        try:
            exp_dt = datetime.fromisoformat(exp)
            if datetime.now(TZ) > exp_dt:
                # Muddati o'tgan
                users = load_users()
                users[str(uid)]["tarif"]         = TARIF_FREE
                users[str(uid)]["tarif_expires"] = None
                save_users(users)
                return TARIF_FREE
        except Exception:
            pass
    return tarif

def get_user_topic_limit(uid: int) -> int:
    """User uchun topic limiti."""
    if uid == SUPERADMIN:
        return 9999
    # Admin bo'lsa
    adm = load_admins()
    if str(uid) in adm:
        return adm[str(uid)].get("topic_limit", 0)
    tarif = get_user_tarif(uid)
    base  = TARIF_TOPIC_LIMIT.get(tarif, 1)
    if tarif == TARIF_FREE:
        u  = get_user(uid)
        rc = u.get("referral_count", 0) if u else 0
        bonus = min(rc // FREE_REFERRAL_PER_TOPIC, FREE_MAX_TOPIC_REFERRAL - 1)
        return min(base + bonus, FREE_MAX_TOPIC_REFERRAL)
    return base

def get_user_q_limit(uid: int) -> int:
    """User uchun 1 topic'dagi savol limiti."""
    if uid == SUPERADMIN:
        return MAX_QUESTIONS
    # Admin bo'lsa
    adm = load_admins()
    if str(uid) in adm:
        return adm[str(uid)].get("max_questions", MAX_QUESTIONS)
    tarif = get_user_tarif(uid)
    limit = TARIF_Q_LIMIT.get(tarif, 10)
    if tarif == TARIF_FREE:
        u  = get_user(uid)
        if u and u.get("is_subscribed"):
            limit += 10
    return limit

def get_tarif_prices() -> dict:
    cfg = load_config()
    prices = cfg.get("tarif_prices", {})
    result = {}
    for t in (TARIF_PLUS, TARIF_PREMIUM, TARIF_VIP):
        result[t] = prices.get(t, DEFAULT_PRICES[t])
    return result

def get_sub_channel() -> int | None:
    return load_config().get("sub_channel")

def set_sub_channel(cid: int | None):
    cfg = load_config()
    if cid:
        cfg["sub_channel"] = cid
    else:
        cfg.pop("sub_channel", None)
    save_config(cfg)

async def check_subscription(bot, uid: int) -> bool:
    """Kanalga obuna bo'lganini tekshiradi."""
    ch = get_sub_channel()
    if not ch:
        return False
    try:
        m = await bot.get_chat_member(ch, uid)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return False

# ── Bad words ──
def _has_badword(text: str, words: list) -> bool:
    tl = text.lower()
    for w in words:
        if not w:
            continue
        pattern = r'(?<![a-zA-Zа-яА-ЯёЁa-zA-Z0-9\u0400-\u04FF])' + re.escape(w) + r'(?![a-zA-Zа-яА-ЯёЁa-zA-Z0-9\u0400-\u04FF])'
        if re.search(pattern, tl):
            return True
    return False

def _random_warning(warnings: list) -> str:
    return random.choice(warnings) if warnings else "⚠️ So'kinma!"

# ── Group settings ──
def get_group_setting(chat_id: int, key: str, default=False):
    return load_chats().get(str(chat_id), {}).get(key, default)

def set_group_setting(chat_id: int, key: str, value):
    chats = load_chats()
    if str(chat_id) not in chats:
        chats[str(chat_id)] = {"chat_id": chat_id, "type": "supergroup", "name": str(chat_id)}
    chats[str(chat_id)][key] = value
    save_chats(chats)

def is_bot_admin(uid: int) -> bool:
    return str(uid) in load_admins()

def is_admin_or_superadmin(uid: int) -> bool:
    return uid == SUPERADMIN or is_bot_admin(uid)

def get_admin_topic_limit(uid: int) -> int:
    return load_admins().get(str(uid), {}).get("topic_limit", 0)

def get_admin_max_questions(uid: int) -> int:
    if uid == SUPERADMIN:
        return MAX_QUESTIONS
    adm = load_admins()
    if str(uid) in adm:
        return adm[str(uid)].get("max_questions", MAX_QUESTIONS)
    return get_user_q_limit(uid)

def count_admin_topics(uid: int) -> int:
    return sum(1 for t in all_topics() if t.get("created_by") == uid)

def count_sub_admins(admin_uid: int) -> int:
    return sum(1 for v in load_admins().values() if v.get("added_by") == admin_uid)

def get_display_name(uid: int, fallback: str) -> str:
    if uid == SUPERADMIN:
        return load_config().get("display_name", fallback)
    return load_admins().get(str(uid), {}).get("display_name", fallback)

def set_display_name(uid: int, name: str | None):
    if uid == SUPERADMIN:
        cfg = load_config()
        if name:
            cfg["display_name"] = name
        else:
            cfg.pop("display_name", None)
        save_config(cfg)
    else:
        adm = load_admins()
        if str(uid) in adm:
            if name:
                adm[str(uid)]["display_name"] = name
            else:
                adm[str(uid)].pop("display_name", None)
            save_admins(adm)

def parse_allowed(text: str) -> list:
    result = []
    for item in re.split(r'[\s,]+', text.strip()):
        item = item.strip()
        if not item:
            continue
        if item.startswith('@'):
            result.append(item.lower())
        else:
            try:
                result.append(int(item))
            except ValueError:
                pass
    return result

def check_allowed(uid: int, username: str | None, allowed: list) -> bool:
    if uid in allowed:
        return True
    if username and f"@{username.lower()}" in allowed:
        return True
    return False

def can_manage_topic(topic: dict, uid: int, username: str = None) -> bool:
    if uid == SUPERADMIN:
        return True
    cb  = topic.get("created_by")
    acc = topic.get("access", {"type": "all"})
    at  = acc.get("type", "all")
    if at == "all":
        return is_admin_or_superadmin(uid)
    if at == "owner":
        return uid == cb
    if at == "admins":
        return is_bot_admin(uid)
    if at == "custom":
        allowed = acc.get("allowed", [])
        return uid == cb or check_allowed(uid, username, allowed)
    return False

def can_edit_topic_access(topic: dict, uid: int) -> bool:
    if uid == SUPERADMIN:
        return True
    return topic.get("created_by") == uid

def register_chat(chat):
    if chat.id == SUPERADMIN:
        return
    chats = load_chats()
    chats[str(chat.id)] = {
        "chat_id": chat.id,
        "type":    chat.type,
        "name":    chat.title or chat.first_name or str(chat.id),
    }
    save_chats(chats)

def unregister_chat(chat_id: int):
    chats = load_chats()
    chats.pop(str(chat_id), None)
    save_chats(chats)

def _matches(chat_type: str, target: str) -> bool:
    if target == "all":      return True
    if target == "private":  return chat_type == "private"
    if target == "groups":   return chat_type in ("group", "supergroup")
    if target == "channels": return chat_type == "channel"
    return False

# ══════════════════════════════════════════════════════
#  USER INFO FORMATTER
# ══════════════════════════════════════════════════════

def format_user_info(user=None, uid: int = None) -> str:
    """Telegram user yoki uid dan to'liq ma'lumot shakllantiradi."""
    u_data = None
    if uid:
        u_data = get_user(uid)
    elif user:
        uid = user.id
        u_data = get_user(user.id)

    if user:
        fn   = user.first_name or "—"
        ln   = user.last_name  or "—"
        uname = f"@{user.username}" if user.username else "—"
        lang = getattr(user, "language_code", "—") or "—"
        is_bot = "✅" if getattr(user, "is_bot", False) else "❌"
        is_premium = "✅" if getattr(user, "is_premium", False) else "❌"
    elif u_data:
        fn   = u_data.get("first_name", "—")
        ln   = u_data.get("last_name",  "—")
        uname = f"@{u_data['username']}" if u_data.get("username") else "—"
        lang = u_data.get("language_code", "—") or "—"
        is_bot = "—"
        is_premium = "—"
    else:
        return f"👤 ID: `{uid}`\n_(Ma'lumot topilmadi)_"

    tarif = get_user_tarif(uid) if uid else "—"
    tarif_name = TARIF_NAMES.get(tarif, tarif)
    topics_count = count_admin_topics(uid) if uid else "—"
    ref_count = u_data.get("referral_count", 0) if u_data else "—"
    joined = u_data.get("joined_at", "—") if u_data else "—"
    ref_by = u_data.get("referral_by", "—") if u_data else "—"
    subscribed = "✅" if u_data and u_data.get("is_subscribed") else "❌"

    return (
        f"👤 *Foydalanuvchi ma'lumotlari:*\n\n"
        f"🆔 ID: `{uid}`\n"
        f"👤 Ism: *{fn}*\n"
        f"👤 Familiya: *{ln}*\n"
        f"📛 Username: {uname}\n"
        f"🌐 Til: `{lang}`\n"
        f"🤖 Bot: {is_bot}\n"
        f"⭐ Telegram Premium: {is_premium}\n"
        f"📅 Qo'shilgan: `{joined}`\n"
        f"💎 Tarif: *{tarif_name}*\n"
        f"📁 Topiclar: {topics_count}\n"
        f"👥 Referallar: {ref_count}\n"
        f"🔗 Referral by: `{ref_by}`\n"
        f"📢 Obuna: {subscribed}"
    )

# ══════════════════════════════════════════════════════
#  GAME STATE
# ══════════════════════════════════════════════════════
games: dict = {}

def get_game(chat_id: int) -> dict:
    if chat_id not in games:
        games[chat_id] = {
            "active": False, "topic": None, "emoji": "",
            "questions": [], "asked": 0, "current": None,
            "current_msg_id": None, "scores": {}, "waiting": False,
        }
    return games[chat_id]

# ══════════════════════════════════════════════════════
#  MESSAGE HISTORY
# ══════════════════════════════════════════════════════
msg_history: dict = {}

def track_msg(chat_id: int, msg_id: int, uid: int, username: str, ts: float):
    h = msg_history.setdefault(chat_id, [])
    h.append({"id": msg_id, "uid": uid,
               "uname": (username or "").lower(), "ts": ts})
    if len(h) > MAX_MSG_HISTORY:
        msg_history[chat_id] = h[-MAX_MSG_HISTORY:]

async def _del_batch(context, chat_id: int, msg_ids: list) -> tuple[int, int]:
    d = f = 0
    for i, mid in enumerate(msg_ids):
        try:
            await context.bot.delete_message(chat_id, mid)
            d += 1
        except Exception:
            f += 1
        await asyncio.sleep(0.5 if i and i % 20 == 0 else 0.05)
    return d, f

# ══════════════════════════════════════════════════════
#  AUTH
# ══════════════════════════════════════════════════════
def is_superadmin(uid: int) -> bool:
    return uid == SUPERADMIN

async def _require_bot_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return True
    if not get_group_setting(chat.id, "require_admin", False):
        return True
    try:
        bm = await context.bot.get_chat_member(chat.id, context.bot.id)
        if bm.status in ("administrator", "creator"):
            return True
    except Exception:
        pass
    await update.message.reply_text(
        "⚠️ Bu guruhda ishlash uchun meni *admin* qilib qo'ying!",
        parse_mode="Markdown")
    return False

async def is_group_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    if uid == SUPERADMIN:
        return True
    msg = update.message or update.edited_message
    if msg and msg.sender_chat and msg.sender_chat.id == update.effective_chat.id:
        return True
    chat = update.effective_chat
    if chat.type in ("group", "supergroup") and uid:
        try:
            m = await context.bot.get_chat_member(chat.id, uid)
            return m.status in ("administrator", "creator")
        except Exception:
            return False
    return False

# ══════════════════════════════════════════════════════
#  REACTION
# ══════════════════════════════════════════════════════
async def send_fire_reaction(bot, chat_id: int, msg_id: int):
    """Tarif egasiga 🔥 reaksiya."""
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=msg_id,
            reaction=[ReactionTypeEmoji(emoji="🔥")],
            is_big=False,
        )
    except Exception as e:
        logger.debug(f"Reaction error: {e}")

async def send_lightning_reaction(bot, chat_id: int, msg_id: int):
    """Superadmin xabarlariga (guruhda) va kanal postlariga ⚡ reaksiya.
    Eslatma: reaksiya ishlashi uchun chat sozlamalarida 'Reactions'
    yoqilgan va bot tegishli huquqqa ega bo'lishi kerak."""
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=msg_id,
            reaction=[ReactionTypeEmoji(emoji="⚡")],
            is_big=False,
        )
    except Exception as e:
        logger.debug(f"Lightning reaction error: {e}")

# ══════════════════════════════════════════════════════
#  SUPERGROUP FORUM TOPICS
# ══════════════════════════════════════════════════════

# Forum topic ID'larini config'da saqlaymiz
def get_forum_topics() -> dict:
    return load_config().get("forum_topics", {})

def save_forum_topics(data: dict):
    cfg = load_config()
    cfg["forum_topics"] = data
    save_config(cfg)

async def ensure_forum_topic(bot, name: str, key: str, icon_color: int = 0x6FB9F0) -> int | None:
    """Topic mavjud bo'lmasa yaratadi, ID qaytaradi."""
    if not SUPERGROUP_ID:
        return None
    ft = get_forum_topics()
    if key in ft:
        return ft[key]
    try:
        result = await bot.create_forum_topic(
            chat_id=SUPERGROUP_ID,
            name=name,
            icon_color=icon_color,
        )
        tid = result.message_thread_id
        ft[key] = tid
        save_forum_topics(ft)
        logger.info(f"Forum topic yaratildi: {name} → {tid}")
        return tid
    except Exception as e:
        logger.warning(f"Forum topic yaratib bo'lmadi ({name}): {e}")
        return None

# Tarif uchun rang
TARIF_COLORS = {
    TARIF_VIP:     0xFFD67E,  # oltin
    TARIF_PREMIUM: 0x6C9CE8,  # ko'k
    TARIF_PLUS:    0x82E0A5,  # yashil
}

async def get_tarif_topic_id(bot, tarif: str) -> int | None:
    """Tarif uchun forum topic ID."""
    names = {
        TARIF_VIP:     "👑 VIP Members",
        TARIF_PREMIUM: "💎 Premium Members",
        TARIF_PLUS:    "✨ PLUS Members",
    }
    if tarif not in names:
        return None
    color = TARIF_COLORS.get(tarif, 0x6FB9F0)
    return await ensure_forum_topic(bot, names[tarif], f"tarif_{tarif}", color)

async def get_backup_topic_id(bot) -> int | None:
    return await ensure_forum_topic(bot, "📦 Backup", "backup", 0xFF6C6C)

async def update_tarif_topic_json(bot, tarif: str):
    """Tarif topic'idagi userlar JSON'ini yangilaydi (max 20 user/xabar)."""
    if not SUPERGROUP_ID:
        return
    tid = await get_tarif_topic_id(bot, tarif)
    if not tid:
        return
    users = load_users()
    tarif_users = [u for u in users.values() if u.get("tarif") == tarif]

    # 20 ta userga bo'lib JSON yozamiz
    chunk_size = 20
    for i in range(0, max(len(tarif_users), 1), chunk_size):
        chunk = tarif_users[i:i+chunk_size]
        data  = {"tarif": tarif, "chunk": i//chunk_size + 1, "users": chunk}
        raw   = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        buf   = io.BytesIO(raw)
        buf.name = f"{tarif}_users_{i//chunk_size + 1}.json"
        try:
            sent = await bot.send_document(
                chat_id=SUPERGROUP_ID,
                document=buf,
                caption=f"📋 {TARIF_NAMES.get(tarif)} | {len(chunk)} ta user | chunk {i//chunk_size+1}",
                message_thread_id=tid,
            )
            # Oxirgi chunkni pin qilamiz
            if i + chunk_size >= len(tarif_users):
                try:
                    await bot.unpin_all_chat_messages(SUPERGROUP_ID)
                except Exception:
                    pass
                try:
                    await bot.pin_chat_message(
                        SUPERGROUP_ID, sent.message_id, disable_notification=True)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Tarif topic JSON yuborib bo'lmadi ({tarif}): {e}")

async def create_class_topic(bot, class_name: str) -> int | None:
    """Sinf uchun forum topic yaratadi. Masalan: '8A sinfi'"""
    key = f"class_{class_name.lower().replace(' ', '_')}"
    return await ensure_forum_topic(bot, class_name, key, 0x6FB9F0)

# ══════════════════════════════════════════════════════
#  EXPORT / BACKUP
# ══════════════════════════════════════════════════════

async def do_export(bot, to_backup_topic: bool = True) -> bool:
    """Export qiladi — faqat supergroup backup topic'iga.
    message_id config.json'da saqlanadi, restart'da o'sha orqali restore."""
    if not SUPERGROUP_ID:
        logger.warning("Export: SUPERGROUP_ID yo'q!")
        return False
    now    = datetime.now(TZ)
    topics = all_topics()
    data   = {
        "export_version": EXPORT_VERSION,
        "export_date":    now.strftime("%Y-%m-%d %H:%M:%S (Toshkent)"),
        "admins":    load_admins(),
        "chats":     load_chats(),
        "config":    load_config(),
        "badwords":  load_badwords(),
        "users":     load_users(),
        "topics":    topics,
    }
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    buf = io.BytesIO(raw)
    buf.name = f"export_{now.strftime('%Y-%m-%d_%H-%M')}.json"
    q_total = sum(len(t.get("questions", [])) for t in topics)
    cap = (f"📦 *Lang Bot Export v{EXPORT_VERSION}*\n"
           f"📅 {now.strftime('%Y-%m-%d %H:%M')}\n\n"
           f"📚 Topiclar: {len(topics)}\n"
           f"❓ Savollar: {q_total}\n"
           f"👥 Adminlar: {len(data['admins'])}\n"
           f"👤 Userlar: {len(data['users'])}\n"
           f"💬 Chatlar: {len(data['chats'])}\n"
           f"📦 Hajm: {len(raw)//1024} KB\n\n"
           f"♻️ _Restart'da shu fayldan avtomatik tiklanadi_")
    tid = await get_backup_topic_id(bot)
    if not tid:
        logger.error("Export: Backup topic ID topilmadi!")
        return False
    try:
        sent = await bot.send_document(
            chat_id=SUPERGROUP_ID,
            document=buf,
            caption=cap,
            parse_mode="Markdown",
            message_thread_id=tid,
        )
        # message_id ni config'ga saqlaymiz — restore uchun
        # (bu yozuv o'zi yana export'ni chaqirib yubormasligi uchun hook vaqtincha o'chiriladi)
        global _suppress_export_hook
        _suppress_export_hook = True
        try:
            cfg = load_config()
            cfg["last_backup_msg_id"]      = sent.message_id
            cfg["last_backup_thread_id"]   = tid
            save_config(cfg)
        finally:
            _suppress_export_hook = False
        # Supergroup'da pin qilamiz
        try:
            await bot.pin_chat_message(
                SUPERGROUP_ID, sent.message_id, disable_notification=True)
        except Exception as e:
            logger.warning(f"Backup pin error: {e}")
        logger.info(f"Export OK: msg_id={sent.message_id}, thread={tid}")
        return True
    except Exception as e:
        logger.error(f"Export error: {e}")
        return False

async def daily_export_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Daily export...")
    await do_export(context.bot, to_backup_topic=True)

async def apply_restore_data(data: dict) -> tuple[int, int, int, int]:
    global _RESTORING
    _RESTORING = True
    try:
        ac = cc = tc = uc = 0
        if "admins"   in data: save_admins(data["admins"]);     ac = len(data["admins"])
        if "chats"    in data: save_chats(data["chats"]);       cc = len(data["chats"])
        if "config"   in data: save_config(data["config"])
        if "badwords" in data: save_badwords(data["badwords"])
        if "users"    in data: save_users(data["users"]);       uc = len(data["users"])
        for t in data.get("topics", []):
            if "name" in t: save_topic(t); tc += 1
        return ac, cc, tc, uc
    finally:
        _RESTORING = False

async def _process_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not (doc.file_name or "").endswith(".json"):
        await update.message.reply_text("❌ Faqat .json fayl.")
        return
    try:
        tgf = await context.bot.get_file(doc.file_id)
        raw = await tgf.download_as_bytearray()
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        await update.message.reply_text(f"❌ O'qib bo'lmadi:\n`{e}`",
                                        parse_mode="Markdown")
        return
    if not data.get("export_version"):
        await update.message.reply_text("❌ To'g'ri export fayli emas!")
        return
    ac, cc, tc, uc = await apply_restore_data(data)
    context.user_data.clear()
    await update.message.reply_text(
        f"✅ *Tiklash muvaffaqiyatli!*\n\n"
        f"📅 {data.get('export_date','?')}\n"
        f"👥 {ac} admin | 💬 {cc} chat | 📚 {tc} topic | 👤 {uc} user",
        parse_mode="Markdown")

async def auto_restore_on_startup(bot) -> None:
    """Bot ishga tushganda (deploy/redeploy yoki restart — xotira tozalanganda)
    config.json'dagi last_backup_msg_id orqali eng so'nggi backup'dan
    avtomatik tiklaydi."""
    cfg    = load_config()
    msg_id = cfg.get("last_backup_msg_id")
    if not msg_id:
        logger.info("Auto-restore: oldingi backup topilmadi — bo'sh boshlanadi.")
        return
    if not SUPERGROUP_ID:
        logger.warning("Auto-restore: SUPERGROUP_ID yo'q!")
        return

    fwd = None
    try:
        # Backup xabarini o'ziga forward qilib, document'ini olamiz
        fwd = await bot.forward_message(
            chat_id=SUPERGROUP_ID,
            from_chat_id=SUPERGROUP_ID,
            message_id=msg_id,
            disable_notification=True,
        )
        doc = fwd.document if fwd else None
        if not doc:
            raise ValueError("Forward qilingan xabarda document topilmadi")
        tgf  = await bot.get_file(doc.file_id)
        raw  = await tgf.download_as_bytearray()
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        logger.warning(f"Auto-restore: backup faylini o'qib bo'lmadi: {e}")
        return
    finally:
        # Forward orqali yaratilgan vaqtinchalik nusxani tozalaymiz
        if fwd is not None:
            try:
                await bot.delete_message(SUPERGROUP_ID, fwd.message_id)
            except Exception:
                pass

    if not data.get("export_version"):
        logger.warning("Auto-restore: noto'g'ri format — export_version yo'q.")
        return
    ac, cc, tc, uc = await apply_restore_data(data)
    logger.info(f"✅ Auto-restore OK: {ac} admin, {cc} chat, {tc} topic, {uc} user")

# ══════════════════════════════════════════════════════
#  BROADCAST
# ══════════════════════════════════════════════════════

async def _bc_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["bc_chat"] = update.effective_chat.id
    context.user_data["bc_msg"]  = update.message.message_id
    context.user_data["step"]    = "broadcast_ready"
    target = context.user_data.get("bc_target", "all")
    chats  = load_chats()
    count  = sum(1 for c in chats.values()
                 if _matches(c["type"], target) and c["chat_id"] != SUPERADMIN)
    kb = ReplyKeyboardMarkup([[BROADCAST_READY]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        f"📋 *Reklama tayyor!*\n🎯 {TARGET_NAMES.get(target, target)}\n"
        f"👥 Taxminiy: *{count}* ta\n\n⬇️ Pastdagi tugmani bosing:",
        parse_mode="Markdown", reply_markup=kb)

async def _do_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bc_chat = context.user_data.pop("bc_chat", None)
    bc_msg  = context.user_data.pop("bc_msg",  None)
    target  = context.user_data.pop("bc_target", "all")
    context.user_data.clear()
    if not bc_chat or not bc_msg:
        await update.message.reply_text("❌ Reklama xabari topilmadi.",
                                        reply_markup=ReplyKeyboardRemove())
        return
    chats = load_chats()
    dest  = [c["chat_id"] for c in chats.values()
             if _matches(c["type"], target) and c["chat_id"] != SUPERADMIN]
    await update.message.reply_text(
        f"⏳ *Yuborilmoqda...* {len(dest)} ta",
        parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    s = f = 0
    for cid in dest:
        try:
            await context.bot.copy_message(cid, bc_chat, bc_msg)
            s += 1
        except Exception as e:
            logger.warning(f"BC {cid}: {e}")
            f += 1
        await asyncio.sleep(0.05)
    await update.message.reply_text(
        f"✅ *Reklama tugadi!*\n📨 {s} ta ✅ | {f} ta ❌",
        parse_mode="Markdown")

# ══════════════════════════════════════════════════════
#  MEDIA PIN
# ══════════════════════════════════════════════════════

async def _pin_media(bot, mt: str, fi: str, caption: str = "") -> str | None:
    """Media faylni supergroup backup topic'iga yuborib, barqaror file_id oladi."""
    if not SUPERGROUP_ID:
        return fi  # supergroup yo'q bo'lsa original file_id qaytaramiz
    tid = await get_backup_topic_id(bot)
    try:
        if mt == "photo":
            s = await bot.send_photo(SUPERGROUP_ID, fi, caption=caption[:1024],
                                     message_thread_id=tid)
            return s.photo[-1].file_id
        if mt == "video":
            s = await bot.send_video(SUPERGROUP_ID, fi, caption=caption[:1024],
                                     message_thread_id=tid)
            return s.video.file_id
        if mt == "gif":
            s = await bot.send_animation(SUPERGROUP_ID, fi, caption=caption[:1024],
                                         message_thread_id=tid)
            return s.animation.file_id
        if mt == "sticker":
            s = await bot.send_sticker(SUPERGROUP_ID, fi, message_thread_id=tid)
            return s.sticker.file_id
    except Exception as e:
        logger.warning(f"pin_media ({mt}): {e}")
    return fi

# ══════════════════════════════════════════════════════
#  SAVE QUESTION
# ══════════════════════════════════════════════════════

async def _save_q(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    tn  = context.user_data.get("topic_name")
    t   = load_topic(tn)
    if not t:
        await update.message.reply_text("❌ Topic topilmadi.")
        context.user_data.clear()
        return
    mq = get_admin_max_questions(uid)
    if len(t["questions"]) >= mq:
        await update.message.reply_text(f"❌ Limit: {mq} ta savol!")
        context.user_data.clear()
        return
    mt = context.user_data.get("q_media_type", "none")
    fi = context.user_data.get("q_file_id", None)
    if mt != "none" and fi:
        stable = await _pin_media(context.bot, mt, fi,
                                  f"{tn} | {context.user_data.get('q_question','')}")
        if stable:
            fi = stable
    q = {
        "question":     context.user_data.get("q_question", ""),
        "answer":       context.user_data.get("q_answer", ""),
        "alternatives": context.user_data.get("q_alts", []),
        "media_type":   mt,
        "file_id":      fi,
    }
    t["questions"].append(q)
    save_topic(t)
    cnt = len(t["questions"])
    for k in ("q_question", "q_answer", "q_alts", "q_media_type", "q_file_id"):
        context.user_data.pop(k, None)
    context.user_data["step"] = "addq_question"
    icon = {"photo": "🖼", "video": "🎬", "gif": "🎞", "sticker": "🎭"}.get(mt, "📝")
    kb = InlineKeyboardMarkup([
        [IKB("➕ Yana savol", callback_data="addq_continue"),
         IKB("⏹ Tugatish",   callback_data="addq_finish")],
    ]) if cnt < mq else None
    await update.message.reply_text(
        f"✅ *Savol saqlandi!* {icon}\n📊 {t['emoji']} {tn}: {cnt}/{mq}",
        parse_mode="Markdown", reply_markup=kb)

# ══════════════════════════════════════════════════════
#  GAME
# ══════════════════════════════════════════════════════

async def send_question(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    g = get_game(chat_id)
    if not g["active"]:
        return
    if g["asked"] >= len(g["questions"]):
        await finish_game(chat_id, context)
        return
    q = g["questions"][g["asked"]]
    g["asked"] += 1
    g["current"] = q
    cap = (f"{g['emoji']} *Savol {g['asked']}/{len(g['questions'])}*\n\n"
           f"❓ {q['question']}\n\n↩️ Reply qilib javob bering:")
    mt = q.get("media_type", "none")
    fi = q.get("file_id")
    try:
        if mt == "photo" and fi:
            sent = await context.bot.send_photo(chat_id, fi, caption=cap, parse_mode="Markdown")
        elif mt == "video" and fi:
            sent = await context.bot.send_video(chat_id, fi, caption=cap, parse_mode="Markdown")
        elif mt == "gif" and fi:
            sent = await context.bot.send_animation(chat_id, fi, caption=cap, parse_mode="Markdown")
        elif mt == "sticker" and fi:
            await context.bot.send_sticker(chat_id, fi)
            sent = await context.bot.send_message(chat_id, cap, parse_mode="Markdown")
        else:
            sent = await context.bot.send_message(chat_id, cap, parse_mode="Markdown")
        g["current_msg_id"] = sent.message_id
    except Exception as e:
        logger.error(f"send_question: {e}")

async def _check_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    g   = get_game(cid)
    if not g["active"] or g["current"] is None or g["waiting"]:
        return
    reply = update.message.reply_to_message
    if reply is None or reply.message_id != g["current_msg_id"]:
        return
    user    = update.effective_user
    uid_s   = str(user.id)
    raw_nm  = user.first_name or "Anonim"
    dname   = get_display_name(user.id, raw_nm)
    ans     = update.message.text.strip().lower()
    correct = g["current"]["answer"].lower()
    alts    = [a.lower() for a in g["current"].get("alternatives", [])]
    ok      = (ans == correct or ans in alts)
    g["waiting"] = True
    if ok:
        if uid_s not in g["scores"]:
            g["scores"][uid_s] = {"name": raw_nm, "count": 0}
        g["scores"][uid_s]["count"] += 1
        ball = g["scores"][uid_s]["count"]
        await update.message.reply_text(
            f"✅ *TO'G'RI!* 🎉\n👤 {dname}: {ball} ball\n\n⏩ Keyingi...",
            parse_mode="Markdown")
    else:
        alt_t = f"\n➕ Shuningdek: _{', '.join(alts)}_" if alts else ""
        await update.message.reply_text(
            f"❌ *XATO!*\n✅ To'g'ri: *{correct}*{alt_t}\n\n⏩ Keyingi...",
            parse_mode="Markdown")
    g["waiting"] = False
    if g["asked"] >= len(g["questions"]):
        await finish_game(cid, context)
    else:
        await send_question(cid, context)

async def finish_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    g = get_game(chat_id)
    g["active"] = False
    t = load_topic(g["topic"]) if g["topic"] else None
    if not g["scores"]:
        await context.bot.send_message(
            chat_id, "📊 *O'yin tugadi!* Hech kim to'g'ri javob bermadi.",
            parse_mode="Markdown")
        return
    ss      = sorted(g["scores"].items(), key=lambda x: x[1]["count"], reverse=True)
    max_sc  = ss[0][1]["count"]
    medals  = ["🥇", "🥈", "🥉"]
    winners = [get_display_name(int(uid_s), d["name"]) for uid_s, d in ss
               if d["count"] == max_sc]
    hdr = (f"🏆 *G'OLIB: {winners[0]}* 🏆" if len(winners) == 1
           else f"🏆 *G'OLIBLAR: {', '.join(winners)}* 🏆")
    res = f"{hdr}\n📊 {max_sc}/{len(g['questions'])}\n\n📋 *Natijalar:*\n"
    for i, (uid_s, d) in enumerate(ss[:10]):
        m  = medals[i] if i < 3 else f"{i+1}."
        dn = get_display_name(int(uid_s), d["name"])
        res += f"{m} {dn}: {d['count']} ball\n"
    prize = t.get("prize") if t else None
    try:
        if prize:
            pt, fi = prize["type"], prize["file_id"]
            if pt == "photo":
                await context.bot.send_photo(chat_id, photo=fi, caption=res, parse_mode="Markdown")
            elif pt == "gif":
                await context.bot.send_animation(chat_id, animation=fi, caption=res, parse_mode="Markdown")
            elif pt == "sticker":
                await context.bot.send_sticker(chat_id, sticker=fi)
                await context.bot.send_message(chat_id, res, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id, res, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"finish_game: {e}")
        await context.bot.send_message(chat_id, res, parse_mode="Markdown")
    g["scores"] = {}

# ══════════════════════════════════════════════════════
#  ADDADMIN FINALIZE
# ══════════════════════════════════════════════════════

async def _finalize_addadmin(q, context: ContextTypes.DEFAULT_TYPE,
                              added_by: int, display_name, can_add: bool, sub_s: dict):
    pa      = context.user_data
    new_uid = pa.get("aa_uid")
    tlim    = pa.get("aa_tl")
    mq      = pa.get("aa_mq")
    context.user_data.clear()
    if not new_uid or not tlim or not mq:
        await q.edit_message_text("❌ Ma'lumotlar to'liq emas.")
        return
    adm = load_admins()
    if str(new_uid) in adm:
        await q.edit_message_text(f"⚠️ `{new_uid}` allaqachon admin!", parse_mode="Markdown")
        return
    entry = {"topic_limit": tlim, "max_questions": mq, "added_by": added_by}
    if display_name:
        entry["display_name"] = display_name
    if can_add:
        entry["can_add_admins"]     = True
        entry["sub_admin_settings"] = sub_s
    adm[str(new_uid)] = entry
    save_admins(adm)
    ca_str = ""
    if can_add:
        ca_str = (f"\n👥 Admin qo'sha oladi: ✅"
                  f"\n   Max sub-admin: {sub_s.get('max_admins','?')}"
                  f"\n   Sub-admin topic: {sub_s.get('max_topic_limit','?')}"
                  f"\n   Sub-admin savol: {sub_s.get('max_questions_per_topic','?')}")
    dn_str = f"\n🏷 Nom: {display_name}" if display_name else ""
    await q.edit_message_text(
        f"✅ *Admin qo'shildi!*\n\n"
        f"👤 UID: `{new_uid}`\n"
        f"📁 Topic limiti: {tlim} ta\n"
        f"❓ Savol limiti: {mq} ta/topic{dn_str}{ca_str}\n\n"
        f"Jami adminlar: {len(adm)} ta",
        parse_mode="Markdown")

# ══════════════════════════════════════════════════════
#  RELAY
# ══════════════════════════════════════════════════════

async def _relay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    hdr  = (f"📨 *Foydalanuvchidan:*\n"
            f"👤 [{user.first_name}](tg://user?id={user.id}) | `{user.id}`"
            + (f" | @{user.username}" if user.username else ""))
    try:
        await context.bot.send_message(SUPERADMIN, hdr, parse_mode="Markdown")
        await context.bot.forward_message(
            SUPERADMIN, update.effective_chat.id, update.message.message_id)
    except Exception as e:
        logger.error(f"relay: {e}")
    context.user_data.clear()
    await update.message.reply_text(
        "✅ Xabar yetkazildi! _(hech qayerda saqlanmadi)_",
        parse_mode="Markdown")

# ══════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════

def _access_kb(topic_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [IKB("👥 Hamma adminlar",      callback_data=f"acc:all:{topic_name}"),
         IKB("👤 Faqat men",           callback_data=f"acc:owner:{topic_name}")],
        [IKB("🔑 Faqat bot adminlari", callback_data=f"acc:admins:{topic_name}")],
        [IKB("✏️ Qo'lda kiritish",     callback_data=f"acc:custom:{topic_name}")],
    ])

def _aa_tlimit_kb(max_tl: int = 10) -> InlineKeyboardMarkup:
    vals = [v for v in [1, 2, 3, 5, 10] if v <= max_tl]
    return InlineKeyboardMarkup([
        [IKB(str(v), callback_data=f"aa_t:{v}") for v in vals],
        [IKB("❌ Bekor", callback_data="aa_cancel")],
    ])

def _aa_qlimit_kb(max_mq: int = 1000) -> InlineKeyboardMarkup:
    vals = [v for v in [100, 250, 500, 750, 1000] if v <= max_mq]
    return InlineKeyboardMarkup([
        [IKB(str(v), callback_data=f"aa_q:{v}") for v in vals],
        [IKB("❌ Bekor", callback_data="aa_cancel")],
    ])

def _aa_can_add_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [IKB("✅ Ha, admin qo'sha olsin", callback_data="aa_ca:1"),
         IKB("❌ Yo'q",                   callback_data="aa_ca:0")],
        [IKB("❌ Bekor", callback_data="aa_cancel")],
    ])

def _aa_cnt_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [IKB(str(v), callback_data=f"aa_sm:{v}") for v in [1, 2, 3, 5, 10]],
        [IKB("❌ Bekor", callback_data="aa_cancel")],
    ])

def _aa_sub_tl_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [IKB(str(v), callback_data=f"aa_st:{v}") for v in [1, 2, 3, 5, 10]],
        [IKB("❌ Bekor", callback_data="aa_cancel")],
    ])

def _aa_sub_ql_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [IKB(str(v), callback_data=f"aa_sq:{v}") for v in [100, 250, 500, 750, 1000]],
        [IKB("❌ Bekor", callback_data="aa_cancel")],
    ])

def _editadmin_kb(uid_e: int, info: dict) -> InlineKeyboardMarkup:
    tl = info["topic_limit"]
    mq = info.get("max_questions", MAX_QUESTIONS)
    row_t = [IKB(f"✅{v}" if tl == v else str(v), callback_data=f"eal_t:{uid_e}:{v}")
             for v in [1, 2, 3, 5, 10]]
    row_q = [IKB(f"✅{v}" if mq == v else str(v), callback_data=f"eal_q:{uid_e}:{v}")
             for v in [100, 250, 500, 750, 1000]]
    return InlineKeyboardMarkup([
        row_t, row_q,
        [IKB("🏷 Nom o'zgartirish", callback_data=f"eal_dn:{uid_e}"),
         IKB("❌ O'chirish",        callback_data=f"del_adm:{uid_e}")],
        [IKB("⬅️ Orqaga",           callback_data="list_adm_cb")],
    ])

def _editadmin_txt(uid_e: int, info: dict) -> str:
    tl    = info["topic_limit"]
    mq    = info.get("max_questions", MAX_QUESTIONS)
    dn    = info.get("display_name", "—")
    ca    = info.get("can_add_admins", False)
    owned = count_admin_topics(uid_e)
    sub_s = info.get("sub_admin_settings", {})
    extra = ""
    if ca:
        extra = (f"\n\n👥 *Admin qo'sha oladi:* ✅\n"
                 f"   Max sub-admin: {sub_s.get('max_admins','?')}\n"
                 f"   Sub-admin topic limiti: {sub_s.get('max_topic_limit','?')}\n"
                 f"   Sub-admin savol limiti: {sub_s.get('max_questions_per_topic','?')}")
    return (f"⚙️ *Admin tahrirlash: `{uid_e}`*\n\n"
            f"🏷 Nom: {dn}\n"
            f"📁 Topic limiti: *{tl}* ta | yaratilgan: {owned}\n"
            f"❓ Savol limiti: *{mq}* ta/topic{extra}\n\n"
            f"📁 *Topic limiti o'zgartirish:*\n"
            f"❓ *Savol limiti o'zgartirish:*")

# ── Superadmin asosiy menyu ──
def _superadmin_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [IKB("📁 Topiclar",     callback_data="menu:topics"),
         IKB("👥 Adminlar",     callback_data="menu:admins")],
        [IKB("👤 Userlar",      callback_data="menu:users"),
         IKB("💎 Tariflar",     callback_data="menu:tarifs")],
        [IKB("🎮 O'yinlar",     callback_data="menu:games"),
         IKB("🔤 So'z filtri",  callback_data="menu:badwords")],
        [IKB("📢 Reklama",      callback_data="menu:broadcast"),
         IKB("⚙️ Sozlamalar",   callback_data="menu:settings")],
        [IKB("📦 Export",       callback_data="menu:export"),
         IKB("🏫 Forum Topic",  callback_data="menu:forum")],
    ])

def _admin_main_kb(uid: int) -> InlineKeyboardMarkup:
    adm = load_admins()
    info = adm.get(str(uid), {})
    rows = [
        [IKB("📁 Topiclarim",   callback_data="menu:topics"),
         IKB("📝 Savol qo'sh",  callback_data="menu:addq")],
        [IKB("🎮 O'yin boshlash", callback_data="menu:newgame")],
    ]
    if info.get("can_add_admins"):
        rows.append([IKB("👥 Sub-adminlar", callback_data="menu:admins")])
    return InlineKeyboardMarkup(rows)

def _user_main_kb(uid: int) -> InlineKeyboardMarkup:
    tarif = get_user_tarif(uid)
    rows = [
        [IKB("💎 Tarifim",        callback_data="u:tarif"),
         IKB("👥 Referallarim",   callback_data="u:referral")],
        [IKB("📋 Topiclarim",     callback_data="u:topics"),
         IKB("➕ Yangi topic",    callback_data="u:newtopic")],
        [IKB("📨 Adminga murojaat", callback_data="u:contact")],
    ]
    if tarif == TARIF_FREE:
        rows.append([IKB("🛒 Tarif sotib olish", callback_data="u:buy")])
    return InlineKeyboardMarkup(rows)

def _buy_tarif_kb() -> InlineKeyboardMarkup:
    prices = get_tarif_prices()
    return InlineKeyboardMarkup([
        [IKB(f"✨ PLUS — {prices[TARIF_PLUS]} ⭐",       callback_data=f"buy:{TARIF_PLUS}")],
        [IKB(f"💎 Premium — {prices[TARIF_PREMIUM]} ⭐", callback_data=f"buy:{TARIF_PREMIUM}")],
        [IKB(f"👑 VIP — {prices[TARIF_VIP]} ⭐",        callback_data=f"buy:{TARIF_VIP}")],
        [IKB("❌ Bekor", callback_data="u:back")],
    ])

def _tarif_admin_kb() -> InlineKeyboardMarkup:
    prices = get_tarif_prices()
    return InlineKeyboardMarkup([
        [IKB(f"✨ PLUS narxi: {prices[TARIF_PLUS]} ⭐",       callback_data=f"setprice:{TARIF_PLUS}")],
        [IKB(f"💎 Premium narxi: {prices[TARIF_PREMIUM]} ⭐", callback_data=f"setprice:{TARIF_PREMIUM}")],
        [IKB(f"👑 VIP narxi: {prices[TARIF_VIP]} ⭐",        callback_data=f"setprice:{TARIF_VIP}")],
        [IKB("📢 Obuna kanali o'zgartirish",                  callback_data="setchannel")],
        [IKB("⬅️ Orqaga", callback_data="menu:back")],
    ])

# ══════════════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    user = update.effective_user
    chat = update.effective_chat
    register_chat(chat)

    # Referral
    ref_by = None
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                ref_by = int(arg[4:])
            except ValueError:
                pass

    is_new = register_user(user, ref_by=ref_by)

    # Guruhda
    if chat.type in ("group", "supergroup"):
        topics = all_topics()
        names  = ", ".join(f"{t['emoji']}{t['name']}" for t in topics) if topics else "hali yo'q"
        await update.message.reply_text(
            "🎮 *Quiz Bot*\n\n"
            f"📚 Mavjud topiclar: {names}\n\n"
            "▶️ `/newgame <topic>` — o'yin boshlash\n"
            "⏹ `/endgame` — to'xtatish\n"
            "📊 `/scores` — ballar",
            parse_mode="Markdown")
        return

    raw = user.first_name or "Admin"
    dn  = get_display_name(uid, raw)

    if is_superadmin(uid):
        await update.message.reply_text(
            f"👋 Salom, *{dn}*! 👑\n\n"
            "Superadmin boshqaruv paneli:",
            parse_mode="Markdown",
            reply_markup=_superadmin_main_kb())

    elif is_bot_admin(uid):
        lim   = get_admin_topic_limit(uid)
        mq    = get_admin_max_questions(uid)
        owned = count_admin_topics(uid)
        await update.message.reply_text(
            f"👋 Salom, *{dn}*!\n\n"
            f"📊 Topic: {owned}/{lim} | Savol/topic: {mq}",
            parse_mode="Markdown",
            reply_markup=_admin_main_kb(uid))

    else:
        tarif = get_user_tarif(uid)
        tarif_name = TARIF_NAMES.get(tarif, tarif)
        u_data = get_user(uid)
        rc     = u_data.get("referral_count", 0) if u_data else 0
        t_lim  = get_user_topic_limit(uid)
        q_lim  = get_user_q_limit(uid)
        bot_me = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_me.username}?start=ref_{uid}"

        welcome = "🎉 Botga xush kelibsiz!" if is_new else f"👋 Salom, *{dn}*!"
        await update.message.reply_text(
            f"{welcome}\n\n"
            f"💎 Tarif: *{tarif_name}*\n"
            f"📁 Topic limiti: {t_lim} ta\n"
            f"❓ Savol limiti: {q_lim} ta/topic\n"
            f"👥 Referallar: {rc}\n\n"
            f"🔗 Referral link:\n`{ref_link}`",
            parse_mode="Markdown",
            reply_markup=_user_main_kb(uid))

# ══════════════════════════════════════════════════════
#  USER MENU CALLBACKS
# ══════════════════════════════════════════════════════

async def _handle_user_menu(q, uid: int, data: str, context: ContextTypes.DEFAULT_TYPE):
    """Oddiy user uchun inline menu."""

    if data == "u:back":
        tarif = get_user_tarif(uid)
        tarif_name = TARIF_NAMES.get(tarif, tarif)
        t_lim = get_user_topic_limit(uid)
        q_lim = get_user_q_limit(uid)
        u_data = get_user(uid)
        rc = u_data.get("referral_count", 0) if u_data else 0
        await q.edit_message_text(
            f"💎 Tarif: *{tarif_name}*\n"
            f"📁 Topic limiti: {t_lim} ta\n"
            f"❓ Savol limiti: {q_lim} ta/topic\n"
            f"👥 Referallar: {rc}",
            parse_mode="Markdown",
            reply_markup=_user_main_kb(uid))
        return

    if data == "u:tarif":
        tarif  = get_user_tarif(uid)
        t_name = TARIF_NAMES.get(tarif, tarif)
        u_data = get_user(uid)
        exp    = u_data.get("tarif_expires", "Abadiy") if u_data else "—"
        t_lim  = get_user_topic_limit(uid)
        q_lim  = get_user_q_limit(uid)
        kb = InlineKeyboardMarkup([
            [IKB("🛒 Tarif o'zgartirish", callback_data="u:buy")],
            [IKB("⬅️ Orqaga",             callback_data="u:back")],
        ])
        await q.edit_message_text(
            f"💎 *Joriy tarif: {t_name}*\n\n"
            f"📁 Topic limiti: {t_lim} ta\n"
            f"❓ Savol/topic limiti: {q_lim} ta\n"
            f"⏳ Muddat: `{exp}`",
            parse_mode="Markdown", reply_markup=kb)
        return

    if data == "u:referral":
        u_data = get_user(uid)
        rc     = u_data.get("referral_count", 0) if u_data else 0
        t_lim  = get_user_topic_limit(uid)
        bot_me = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_me.username}?start=ref_{uid}"
        next_bonus = FREE_REFERRAL_PER_TOPIC - (rc % FREE_REFERRAL_PER_TOPIC)
        kb = InlineKeyboardMarkup([[IKB("⬅️ Orqaga", callback_data="u:back")]])
        await q.edit_message_text(
            f"👥 *Referallaringiz: {rc} ta*\n\n"
            f"📁 Joriy topic limiti: {t_lim} ta\n"
            f"➕ Keyingi bonus uchun: {next_bonus} ta referal\n\n"
            f"🔗 Referral havolangiz:\n`{ref_link}`\n\n"
            f"_Har {FREE_REFERRAL_PER_TOPIC} ta referal = +1 topic (max 5 ta)_",
            parse_mode="Markdown", reply_markup=kb)
        return

    if data == "u:topics":
        topics = [t for t in all_topics() if t.get("created_by") == uid]
        if not topics:
            kb = InlineKeyboardMarkup([
                [IKB("➕ Yangi topic", callback_data="u:newtopic")],
                [IKB("⬅️ Orqaga",     callback_data="u:back")],
            ])
            await q.edit_message_text("📭 Sizning topiclaringiz yo'q.",
                                      reply_markup=kb)
            return
        lines = [f"{t['emoji']} *{t['name']}* — {len(t['questions'])} savol"
                 for t in topics]
        kb = InlineKeyboardMarkup([[IKB("⬅️ Orqaga", callback_data="u:back")]])
        await q.edit_message_text(
            f"📋 *Topiclaringiz ({len(topics)} ta):*\n\n" + "\n".join(lines),
            parse_mode="Markdown", reply_markup=kb)
        return

    if data == "u:newtopic":
        if not is_admin_or_superadmin(uid):
            t_lim = get_user_topic_limit(uid)
            owned = count_admin_topics(uid)
            if owned >= t_lim:
                kb = InlineKeyboardMarkup([
                    [IKB("🛒 Tarif xarid qilish", callback_data="u:buy")],
                    [IKB("⬅️ Orqaga",             callback_data="u:back")],
                ])
                await q.edit_message_text(
                    f"❌ Topic limit to'ldi! ({owned}/{t_lim})\n\n"
                    "Qo'shimcha topic uchun:\n"
                    "• Referal to'plang (+1 har 3 ta)\n"
                    "• Tarif xarid qiling",
                    reply_markup=kb)
                return
        context.user_data.clear()
        context.user_data["step"] = "newtopic_name_prompt"
        await q.edit_message_text(
            "➕ *Yangi topic*\n\nTopic nomini yuboring _(faqat harf/raqam/_, masalan: `english`)_",
            parse_mode="Markdown")
        return

    if data == "u:contact":
        context.user_data["step"] = "contact_waiting"
        await q.edit_message_text(
            "📨 *Adminga xabar yozish*\n\n"
            "Xabaringizni yuboring — matn, rasm, video, GIF, stiker.\n"
            "⚠️ Hech qayerda saqlanmaydi!\n\n⏹ /cancel")
        return

    if data == "u:buy":
        await q.edit_message_text(
            "🛒 *Tarif tanlang:*\n\n"
            "To'lov Stars orqali amalga oshiriladi.\n"
            "Xarid qilingach superadminga xabar boriladi.",
            parse_mode="Markdown",
            reply_markup=_buy_tarif_kb())
        return

# ══════════════════════════════════════════════════════
#  STARS TO'LOV
# ══════════════════════════════════════════════════════

async def _send_invoice(bot, chat_id: int, tarif: str, uid: int):
    prices = get_tarif_prices()
    stars  = prices.get(tarif, DEFAULT_PRICES.get(tarif, 100))
    names  = {
        TARIF_PLUS:    "PLUS ✨ tarifi",
        TARIF_PREMIUM: "Premium 💎 tarifi",
        TARIF_VIP:     "VIP 👑 tarifi",
    }
    descs = {
        TARIF_PLUS:    f"📁 10 topic | ❓ 100 savol/topic",
        TARIF_PREMIUM: f"📁 20 topic | ❓ 200 savol/topic",
        TARIF_VIP:     f"📁 70 topic | ❓ 1500 savol/topic + Admin qo'shish",
    }
    await bot.send_invoice(
        chat_id=chat_id,
        title=names.get(tarif, tarif),
        description=descs.get(tarif, ""),
        payload=f"tarif:{tarif}:{uid}",
        currency="XTR",
        prices=[LabeledPrice(label=names.get(tarif, tarif), amount=stars)],
    )

async def cmd_precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    await query.answer(ok=True)

async def cmd_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid     = update.effective_user.id
    user    = update.effective_user
    payment = update.message.successful_payment
    payload = payment.invoice_payload

    if not payload.startswith("tarif:"):
        return

    parts = payload.split(":")
    if len(parts) < 3:
        return

    tarif    = parts[1]
    buyer_id = int(parts[2])

    # Tarifni beramiz (30 kun)
    users = load_users()
    k     = str(buyer_id)
    if k not in users:
        register_user(user)
        users = load_users()
    users[k]["tarif"]         = tarif
    exp = datetime.now(TZ) + timedelta(days=30)
    users[k]["tarif_expires"] = exp.isoformat()
    save_users(users)

    tarif_name = TARIF_NAMES.get(tarif, tarif)
    stars      = payment.total_amount

    await update.message.reply_text(
        f"✅ *To'lov qabul qilindi!*\n\n"
        f"💎 Tarif: *{tarif_name}*\n"
        f"⭐ Stars: {stars}\n"
        f"⏳ Muddat: 30 kun\n\n"
        f"Endi barcha imkoniyatlar faol!",
        parse_mode="Markdown")

    # Superadminga xabar
    info_text = format_user_info(user=user)
    try:
        await context.bot.send_message(
            SUPERADMIN,
            f"💰 *YANGI TO'LOV!*\n\n"
            f"💎 Tarif: *{tarif_name}*\n"
            f"⭐ Stars: {stars}\n\n"
            f"{info_text}",
            parse_mode="Markdown")
    except Exception:
        pass

    # Tarif topic'ini yangilash
    asyncio.create_task(update_tarif_topic_json(context.bot, tarif))

# ══════════════════════════════════════════════════════
#  /contact
# ══════════════════════════════════════════════════════

async def cmd_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if update.effective_chat.type != "private":
        return
    if is_admin_or_superadmin(uid):
        await update.message.reply_text("❌ Adminlar bu funksiyadan foydalana olmaydi.")
        return
    context.user_data["step"] = "contact_waiting"
    await update.message.reply_text(
        "📨 *Adminga xabar yozish*\n\n"
        "Xabaringizni yuboring — matn, rasm, video, GIF, stiker.\n"
        "⚠️ Hech qayerda saqlanmaydi!\n\n⏹ /cancel",
        parse_mode="Markdown")

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("step"):
        context.user_data.clear()
        await update.message.reply_text("❌ Bekor qilindi.", reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text("⚠️ Bekor qilinadigan jarayon yo'q.")

# ══════════════════════════════════════════════════════
