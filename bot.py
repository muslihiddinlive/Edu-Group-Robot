#!/usr/bin/env python3
"""
Lang Bot v4.1
REQUIRES: pip install "python-telegram-bot[job-queue]==21.9" aiohttp

YANGI (v4.1):
  - Webhook rejimi (Render + UptimeRobot uchun) — polling o'rniga
  - Maxfiy ma'lumotlar (BOT_TOKEN, SUPERADMIN_ID, EXPORT_CHANNEL_ID)
    endi ENV o'zgaruvchilardan o'qiladi (ochiq kodda emas!)
  - Bot ishga tushganda zahira kanalidagi PIN qilingan eksportdan
    avtomatik tiklanadi (Render kabi vaqtinchalik disk uchun)
  - Faqat topic egasi (yoki superadmin) o'z topicining
    /edittopicaccess sozlamasini o'zgartira oladi

YANGI (v4.0):
  - Topic access control (kimlar savol qo'sha oladi)
  - Admin ierarxiya: Superadmin → Admin (can_add) → Sub-admin
  - Display name (superadmin va adminlar uchun, hamma joyda ko'rinadi)
  - Xabar tracking + kengaytirilgan /del
  - /edittopicaccess, /setdisplayname

Superadmin:
  /addadmin /removeadmin /listadmins /editadmin /setdisplayname
  /newtopic /listtopics /deletetopic /setprize /edittopicaccess
  /addq /bulkq /listgames /newgame /endgame /scores /del
  /broadcast /export /restore

Admin:
  /newtopic /listtopics /addq /bulkq /edittopicaccess (faqat o'z topiclari)
  /listadmins (o'z sub-adminlari) — agar huquq bo'lsa /addadmin

KERAKLI ENV O'ZGARUVCHILAR:
  BOT_TOKEN         - @BotFather'dan olingan token
  SUPERADMIN_ID     - superadminning Telegram user ID (raqam)
  EXPORT_CHANNEL_ID - zahira/eksport kanali ID (masalan -1001234567890,
                       bot bu kanalda ADMIN bo'lishi shart — pin qilish uchun)
  WEBHOOK_URL       - (ixtiyoriy) https://<app-nomi>.onrender.com
                       Render avtomatik beradigan RENDER_EXTERNAL_URL
                       mavjud bo'lsa, shuni ham ishlatish mumkin
  WEBHOOK_SECRET    - (ixtiyoriy, lekin tavsiya etiladi) maxfiy token,
                       faqat Telegramdan kelgan so'rovlarni tasdiqlash uchun
  PORT              - (Render avtomatik beradi, odatda kerak emas)
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
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ChatMemberHandler, filters, ContextTypes,
)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════
#  CONFIG  (maxfiy qiymatlar ENV orqali — hech qachon kodga yozmang!)
# ══════════════════════════════════════════════════════

def _require_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise SystemExit(
            f"❌ '{key}' ENV o'zgaruvchisi topilmadi! "
            f"Render → Environment bo'limida sozlang.")
    return val

BOT_TOKEN      = _require_env("BOT_TOKEN")
SUPERADMIN     = int(_require_env("SUPERADMIN_ID"))
EXPORT_CHANNEL = int(_require_env("EXPORT_CHANNEL_ID"))

# ── Webhook (Render) ──
PORT           = int(os.environ.get("PORT", "8080"))
WEBHOOK_URL    = (os.environ.get("WEBHOOK_URL")
                  or os.environ.get("RENDER_EXTERNAL_URL")
                  or "").rstrip("/")
WEBHOOK_PATH   = f"webhook/{BOT_TOKEN}"
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

MAX_TOPICS     = 10
MAX_QUESTIONS  = 1000
TOPICS_DIR     = "topics"
ADMINS_FILE    = "admins.json"
CHATS_FILE     = "chats.json"
CONFIG_FILE    = "config.json"
BADWORDS_FILE  = "badwords.json"
MAX_MSG_HISTORY = 10000
EXPORT_VERSION  = 4
BROADCAST_READY = "✅ Tayyor — Yuborishni boshlash"

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

# ── Topics ──
def topic_path(name: str) -> str:
    return os.path.join(TOPICS_DIR, f"{name.lower()}.json")

def topic_exists(name: str) -> bool:
    return os.path.exists(topic_path(name))

def load_topic(name: str) -> dict | None:
    p = topic_path(name)
    if not os.path.exists(p):
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def save_topic(data: dict):
    with open(topic_path(data["name"]), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def all_topics() -> list:
    out = []
    for fn in sorted(os.listdir(TOPICS_DIR)):
        if fn.endswith(".json"):
            with open(os.path.join(TOPICS_DIR, fn), "r", encoding="utf-8") as f:
                out.append(json.load(f))
    return out

def count_topics() -> int:
    return sum(1 for f in os.listdir(TOPICS_DIR) if f.endswith(".json"))

# ── Admins ──
def load_admins() -> dict:  return _jload(ADMINS_FILE)
def save_admins(d: dict):   _jsave(ADMINS_FILE, d)
def load_chats() -> dict:   return _jload(CHATS_FILE)
def save_chats(d: dict):    _jsave(CHATS_FILE, d)
def load_config() -> dict:  return _jload(CONFIG_FILE)
def save_config(d: dict):   _jsave(CONFIG_FILE, d)

# ── Bad words ──
def load_badwords() -> dict:
    d = _jload(BADWORDS_FILE)
    d.setdefault("words", [])
    d.setdefault("severe_words", [])
    d.setdefault("warnings", [])
    return d

def save_badwords(d: dict): _jsave(BADWORDS_FILE, d)

def _has_badword(text: str, words: list) -> bool:
    tl = text.lower()
    return any(w and w in tl for w in words)

def _random_warning(warnings: list) -> str:
    return random.choice(warnings) if warnings else "⚠️ So'kinma!"

# ── Group settings helpers ──
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
    return load_admins().get(str(uid), {}).get("max_questions", MAX_QUESTIONS)

def count_admin_topics(uid: int) -> int:
    return sum(1 for t in all_topics() if t.get("created_by") == uid)

def count_sub_admins(admin_uid: int) -> int:
    return sum(1 for v in load_admins().values() if v.get("added_by") == admin_uid)

# ── Display name ──
def get_display_name(uid: int, fallback: str) -> str:
    """Superadmin barcha uchun belgilaydi. Hamma joyda shu ko'rinadi."""
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

# ── Topic access ──
def parse_allowed(text: str) -> list:
    """'@ali 123456 @vali' → ['@ali', 123456, '@vali']"""
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
    """Bu user topicga savol qo'sha oladimi?"""
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
    """Topicning RUXSAT (/edittopicaccess) sozlamasini faqat superadmin
    yoki o'sha topicni yaratgan admin o'zgartira oladi — boshqa
    adminlar (garchi savol qo'sha olsa ham) bu sozlamani o'zgartira olmaydi."""
    if uid == SUPERADMIN:
        return True
    return topic.get("created_by") == uid

# ── Chats ──
def register_chat(chat):
    if chat.id in (EXPORT_CHANNEL, SUPERADMIN):
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
#  MESSAGE HISTORY  (for /del)
# ══════════════════════════════════════════════════════
msg_history: dict = {}   # {chat_id: [{"id","uid","uname","ts"}]}

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
    """Guruhda 'require_admin' yoniq bo'lsa, bot admin emasligini tekshiradi."""
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

# ══════════════════════════════════════════════════════
#  EXPORT / RESTORE
# ══════════════════════════════════════════════════════

async def do_export(bot) -> bool:
    now    = datetime.now(TZ)
    topics = all_topics()
    data   = {
        "export_version": EXPORT_VERSION,
        "export_date":    now.strftime("%Y-%m-%d %H:%M:%S (Toshkent)"),
        "admins":    load_admins(),
        "chats":     load_chats(),
        "config":    load_config(),
        "badwords":  load_badwords(),
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
           f"💬 Chatlar: {len(data['chats'])}\n"
           f"📦 Hajm: {len(raw)//1024} KB\n\n"
           f"♻️ _Restart'da shu fayldan avtomatik tiklanadi_")
    try:
        sent = await bot.send_document(chat_id=EXPORT_CHANNEL,
                                document=buf, caption=cap, parse_mode="Markdown")
        # Eng so'nggi eksportni pin qilamiz — bot restart bo'lganda
        # avtomatik tiklash aynan shu pin qilingan xabardan o'qiladi.
        try:
            await bot.unpin_all_chat_messages(EXPORT_CHANNEL)
        except Exception:
            pass
        try:
            await bot.pin_chat_message(
                EXPORT_CHANNEL, sent.message_id, disable_notification=True)
        except Exception as e:
            logger.warning(f"Export pin error: {e}")
        return True
    except Exception as e:
        logger.error(f"Export error: {e}")
        return False

async def daily_export_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Daily export...")
    await do_export(context.bot)

async def apply_restore_data(data: dict) -> tuple[int, int, int]:
    """Export JSON ma'lumotlarini diskka yozadi.
    Qaytaradi: (admin soni, chat soni, topic soni)."""
    ac = cc = tc = 0
    if "admins"   in data: save_admins(data["admins"]);     ac = len(data["admins"])
    if "chats"    in data: save_chats(data["chats"]);       cc = len(data["chats"])
    if "config"   in data: save_config(data["config"])
    if "badwords" in data: save_badwords(data["badwords"])
    for t in data.get("topics", []):
        if "name" in t: save_topic(t); tc += 1
    return ac, cc, tc

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
    ac, cc, tc = await apply_restore_data(data)
    context.user_data.clear()
    await update.message.reply_text(
        f"✅ *Tiklash muvaffaqiyatli!*\n\n"
        f"📅 {data.get('export_date','?')}\n"
        f"👥 {ac} admin | 💬 {cc} chat | 📚 {tc} topic",
        parse_mode="Markdown")

async def auto_restore_on_startup(bot) -> None:
    """Bot ishga tushganda EXPORT_CHANNEL'dagi PIN qilingan oxirgi
    eksport faylidan ma'lumotlarni avtomatik tiklaydi.
    Render kabi platformalarda disk har restart'da tozalanadi —
    shuning uchun bu funksiya avvalgi holatni qaytaradi."""
    try:
        chat = await bot.get_chat(EXPORT_CHANNEL)
    except Exception as e:
        logger.warning(f"Auto-restore: kanalni o'qib bo'lmadi ({e}). "
                       f"Bot zahira kanalida ADMIN ekanligini tekshiring.")
        return

    pinned = chat.pinned_message
    if not pinned or not pinned.document:
        logger.info("Auto-restore: pin qilingan zahira topilmadi — "
                    "bo'sh holatda boshlanadi.")
        return

    doc = pinned.document
    if not (doc.file_name or "").endswith(".json"):
        logger.info("Auto-restore: pin qilingan fayl .json emas, o'tkazib yuborildi.")
        return

    try:
        tgf  = await bot.get_file(doc.file_id)
        raw  = await tgf.download_as_bytearray()
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        logger.warning(f"Auto-restore: faylni o'qib bo'lmadi: {e}")
        return

    if not data.get("export_version"):
        logger.warning("Auto-restore: noto'g'ri export fayli, o'tkazib yuborildi.")
        return

    ac, cc, tc = await apply_restore_data(data)
    logger.info(
        f"Auto-restore tugadi: {ac} admin, {cc} chat, {tc} topic "
        f"(eksport sanasi: {data.get('export_date', '?')})")

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
#  MEDIA PIN (savol mediani kanalga pin qilish)
# ══════════════════════════════════════════════════════

async def _pin_media(bot, mt: str, fi: str, caption: str = "") -> str | None:
    try:
        if mt == "photo":
            s = await bot.send_photo(EXPORT_CHANNEL, fi, caption=caption[:1024])
            return s.photo[-1].file_id
        if mt == "video":
            s = await bot.send_video(EXPORT_CHANNEL, fi, caption=caption[:1024])
            return s.video.file_id
        if mt == "gif":
            s = await bot.send_animation(EXPORT_CHANNEL, fi, caption=caption[:1024])
            return s.animation.file_id
        if mt == "sticker":
            s = await bot.send_sticker(EXPORT_CHANNEL, fi)
            return s.sticker.file_id
    except Exception as e:
        logger.warning(f"pin_media ({mt}): {e}")
    return None

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

    # Mediasini export kanalga pin qil → barqaror file_id olamiz
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
    try:
        if ok:
            if uid_s not in g["scores"]:
                g["scores"][uid_s] = {"name": raw_nm, "count": 0}
            g["scores"][uid_s]["count"] += 1
            ball = g["scores"][uid_s]["count"]
            try:
                await update.message.reply_text(
                    f"✅ *TO'G'RI!* 🎉\n👤 {dname}: {ball} ball\n\n⏩ Keyingi...",
                    parse_mode="Markdown")
            except Exception:
                await context.bot.send_message(
                    cid,
                    f"✅ *TO'G'RI!* 🎉\n👤 {dname}: {ball} ball\n\n⏩ Keyingi...",
                    parse_mode="Markdown")
        else:
            alt_t = f"\n➕ Shuningdek: _{', '.join(alts)}_" if alts else ""
            try:
                await update.message.reply_text(
                    f"❌ *XATO!*\n✅ To'g'ri: *{correct}*{alt_t}\n\n⏩ Keyingi...",
                    parse_mode="Markdown")
            except Exception:
                await context.bot.send_message(
                    cid,
                    f"❌ *XATO!*\n✅ To'g'ri: *{correct}*{alt_t}\n\n⏩ Keyingi...",
                    parse_mode="Markdown")
    finally:
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

    winners = []
    for uid_s, d in ss:
        if d["count"] == max_sc:
            winners.append(get_display_name(int(uid_s), d["name"]))

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
        await q.edit_message_text("❌ Ma'lumotlar to'liq emas. /addadmin")
        return

    adm = load_admins()
    if str(new_uid) in adm:
        await q.edit_message_text(f"⚠️ `{new_uid}` allaqachon admin!", parse_mode="Markdown")
        return

    entry = {
        "topic_limit":   tlim,
        "max_questions": mq,
        "added_by":      added_by,
    }
    if display_name:
        entry["display_name"] = display_name
    if can_add:
        entry["can_add_admins"]       = True
        entry["sub_admin_settings"]   = sub_s

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
#  RELAY (contact)
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
#  SENDAS / REQUIREADMIN / BADWORDS COMMANDS
# ══════════════════════════════════════════════════════

async def cmd_sendas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Botdan guruhga xabar yozish."""
    if not is_superadmin(update.effective_user.id):
        return
    chats  = load_chats()
    groups = {k: v for k, v in chats.items()
              if v.get("type") in ("group", "supergroup")}
    if not groups:
        await update.message.reply_text("❌ Ro'yxatda guruh yo'q.")
        return
    btns = [[IKB(v.get("name", k), callback_data=f"sendas:{k}")]
            for k, v in groups.items()]
    await update.message.reply_text(
        "📤 *Botdan xabar yuborish*\n\nQaysi guruhga?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns))


async def cmd_requireadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guruhda bot admin bo'lishini talab qilish."""
    if not is_superadmin(update.effective_user.id):
        return
    chats  = load_chats()
    groups = {k: v for k, v in chats.items()
              if v.get("type") in ("group", "supergroup")}
    if not groups:
        await update.message.reply_text("❌ Guruh yo'q.")
        return
    btns = []
    for k, v in groups.items():
        req = v.get("require_admin", False)
        s   = "🟢" if req else "🔴"
        btns.append([IKB(f"{s} {v.get('name', k)}", callback_data=f"req_adm:{k}")])
    btns.append([IKB("✅ Tayyor", callback_data="req_adm_done")])
    await update.message.reply_text(
        "🔐 *Guruhlar — Admin talab:*\n\n"
        "🟢 YONIQ — bot admin bo'lmasa ishlamaydi\n"
        "🔴 O'CHIQ — har holda ishlaydi\n\n"
        "Bosib yoqing/o'chiring:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns))


async def cmd_addbadword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id): return
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Format: `/addbadword so'z`\n"
            "_(Xabar o'chirilib, ogohlantirish yuboriladi)_",
            parse_mode="Markdown"); return
    word = " ".join(args).lower().strip()
    bw   = load_badwords()
    if word in bw["words"] or word in bw["severe_words"]:
        await update.message.reply_text(f"⚠️ `{word}` allaqachon ro'yxatda!",
                                        parse_mode="Markdown"); return
    bw["words"].append(word)
    save_badwords(bw)
    await update.message.reply_text(
        f"✅ So'z qo'shildi: `{word}`\n_(O'chirish + ogohlantirish)_",
        parse_mode="Markdown")


async def cmd_addsevereword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id): return
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Format: `/addsevereword so'z`\n"
            "_(User bo'lsa: chiqarib yuborish + ogohlantirish + sizga xabar)_\n"
            "_(Admin bo'lsa: o'chirish + ogohlantirish + sizga xabar)_",
            parse_mode="Markdown"); return
    word = " ".join(args).lower().strip()
    bw   = load_badwords()
    if word in bw["severe_words"]:
        await update.message.reply_text(f"⚠️ `{word}` allaqachon juda qo'pol ro'yxatda!",
                                        parse_mode="Markdown"); return
    if word in bw["words"]:
        bw["words"].remove(word)
    bw["severe_words"].append(word)
    save_badwords(bw)
    await update.message.reply_text(
        f"🚫 Juda qo'pol so'z qo'shildi: `{word}`",
        parse_mode="Markdown")


async def cmd_addwarning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id): return
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Format: `/addwarning Matn kiriting yoshbola!`\n"
            "_(Bot ogohlantirish berganida tasodifiy tanlaydi)_",
            parse_mode="Markdown"); return
    text = " ".join(args)
    bw   = load_badwords()
    bw["warnings"].append(text)
    save_badwords(bw)
    await update.message.reply_text(
        f"✅ Ogohlantirish qo'shildi:\n\n💬 _{text}_",
        parse_mode="Markdown")


async def cmd_listbadwords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id): return
    bw      = load_badwords()
    words   = bw.get("words", [])
    severe  = bw.get("severe_words", [])
    warns   = bw.get("warnings", [])

    msg = "🔤 *So'z filtri:*\n\n"
    if words:
        msg += f"⚠️ *Oddiy so'zlar ({len(words)} ta):*\n"
        msg += "\n".join(f"• `{w}`" for w in words) + "\n\n"
    else:
        msg += "⚠️ Oddiy so'zlar yo'q.\n\n"

    if severe:
        msg += f"🚫 *Juda qo'pol so'zlar ({len(severe)} ta):*\n"
        msg += "\n".join(f"• `{w}`" for w in severe) + "\n\n"
    else:
        msg += "🚫 Juda qo'pol so'zlar yo'q.\n\n"

    if warns:
        msg += f"💬 *Ogohlantirish matnlari ({len(warns)} ta):*\n"
        for i, w in enumerate(warns, 1):
            msg += f"{i}. _{w}_\n"
    else:
        msg += "💬 Ogohlantirish matni yo'q."

    msg += ("\n\n`/addbadword` `/addsevereword`\n"
            "`/addwarning` `/removebadword`\n"
            "`/removewarning <raqam>`")
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_removebadword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id): return
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Format: `/removebadword so'z`", parse_mode="Markdown"); return
    word = " ".join(args).lower().strip()
    bw   = load_badwords()
    if word in bw["words"]:
        bw["words"].remove(word)
        save_badwords(bw)
        await update.message.reply_text(f"✅ O'chirildi: `{word}`", parse_mode="Markdown")
    elif word in bw["severe_words"]:
        bw["severe_words"].remove(word)
        save_badwords(bw)
        await update.message.reply_text(
            f"✅ Juda qo'pol ro'yxatidan o'chirildi: `{word}`", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ `{word}` topilmadi!", parse_mode="Markdown")


async def cmd_removewarning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id): return
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ Format: `/removewarning 1`", parse_mode="Markdown"); return
    try:
        n = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Raqam kiriting."); return
    bw    = load_badwords()
    warns = bw.get("warnings", [])
    if n < 1 or n > len(warns):
        await update.message.reply_text(f"❌ {n}-ogohlantirish yo'q!"); return
    removed = warns.pop(n - 1)
    save_badwords(bw)
    await update.message.reply_text(
        f"✅ O'chirildi:\n_{removed}_", parse_mode="Markdown")


# ══════════════════════════════════════════════════════
#  COMMANDS
# ══════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    chat = update.effective_chat
    register_chat(chat)
    raw = update.effective_user.first_name or "Admin"
    dn  = get_display_name(uid, raw)

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

    if is_superadmin(uid):
        await update.message.reply_text(
            f"👋 Salom, *{dn}*! 👑\n\n"
            "📁 `/newtopic` `/listtopics` `/deletetopic` `/setprize`\n"
            "📝 `/addq` `/bulkq`  🔐 `/edittopicaccess`\n"
            "🎮 `/listgames`\n\n"
            "👥 *Admin boshqarish:*\n"
            "➕ `/addadmin`  📋 `/listadmins`\n"
            "⚙️ `/editadmin <uid>`\n"
            "🏷 `/setdisplayname me <nom>` yoki `<uid> <nom>`\n\n"
            "📤 `/sendas` — botdan guruhga xabar\n"
            "🔐 `/requireadmin` — guruhda admin talab\n\n"
            "🔤 *So'z filtri:*\n"
            "`/addbadword` `/addsevereword`\n"
            "`/addwarning` `/listbadwords` `/removebadword`\n"
            "`/removewarning`\n\n"
            "📢 `/broadcast`\n"
            "📦 `/export`  ♻️ `/restore`",
            parse_mode="Markdown")

    elif is_bot_admin(uid):
        lim  = get_admin_topic_limit(uid)
        mq   = get_admin_max_questions(uid)
        owned = count_admin_topics(uid)
        info = load_admins().get(str(uid), {})
        ca   = info.get("can_add_admins", False)
        sub_s = info.get("sub_admin_settings", {})
        extra = ""
        if ca:
            extra = (f"\n👥 Sub-adminlar: {count_sub_admins(uid)}/{sub_s.get('max_admins','?')}"
                     f"\n➕ `/addadmin` — sub-admin qo'shish")
        await update.message.reply_text(
            f"👋 Salom, *{dn}*!\n\n"
            f"📊 Topic: {owned}/{lim} | Savol/topic: {mq}{extra}\n\n"
            "📁 `/newtopic` `/listtopics`\n"
            "📝 `/addq` `/bulkq`\n"
            "🔐 `/edittopicaccess`",
            parse_mode="Markdown")
    else:
        await update.message.reply_text("👋 Salom!\n\n📨 Adminga murojaat: /contact")


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


async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if update.effective_chat.type != "private":
        return

    adm = load_admins()

    # Kim add qila oladi?
    if not is_superadmin(uid):
        info = adm.get(str(uid), {})
        if not info.get("can_add_admins"):
            return
        sub_s  = info.get("sub_admin_settings", {})
        max_adm = sub_s.get("max_admins", 0)
        if count_sub_admins(uid) >= max_adm:
            await update.message.reply_text(
                f"❌ Sub-admin limiti to'ldi! ({count_sub_admins(uid)}/{max_adm})")
            return

    args = context.args

    # Limitlar (kim qo'shyapti)
    if is_superadmin(uid):
        max_tl = MAX_TOPICS
        max_mq = MAX_QUESTIONS
    else:
        sub_s  = adm.get(str(uid), {}).get("sub_admin_settings", {})
        max_tl = sub_s.get("max_topic_limit", 10)
        max_mq = sub_s.get("max_questions_per_topic", MAX_QUESTIONS)

    if args:
        try:
            new_uid = int(args[0])
        except ValueError:
            await update.message.reply_text("❌ User ID raqam bo'lishi kerak.")
            return
        if new_uid == SUPERADMIN:
            await update.message.reply_text("❌ Superadminni admin qilish shart emas.")
            return
        if str(new_uid) in adm:
            await update.message.reply_text(
                f"⚠️ `{new_uid}` allaqachon admin!\n"
                f"Tahrirlash: `/editadmin {new_uid}`",
                parse_mode="Markdown")
            return
        context.user_data.clear()
        context.user_data.update({"step": "addadmin_tlimit",
                                   "aa_uid": new_uid, "aa_by": uid,
                                   "aa_max_tl": max_tl, "aa_max_mq": max_mq})
        await update.message.reply_text(
            f"➕ *Yangi admin: `{new_uid}`*\n\n📁 *Topic limiti:*",
            parse_mode="Markdown",
            reply_markup=_aa_tlimit_kb(max_tl))
    else:
        context.user_data.clear()
        context.user_data.update({"step": "addadmin_uid", "aa_by": uid,
                                   "aa_max_tl": max_tl, "aa_max_mq": max_mq})
        await update.message.reply_text("➕ *Admin qo'shish*\n\nUser ID kiriting:",
                                        parse_mode="Markdown")


async def cmd_removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    adm = load_admins()
    if not is_superadmin(uid):
        info = adm.get(str(uid), {})
        if not info.get("can_add_admins"):
            return
    args = context.args
    if not args:
        await update.message.reply_text("❌ `/removeadmin <uid>`", parse_mode="Markdown")
        return
    try:
        rm_uid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Raqam kiriting.")
        return
    if str(rm_uid) not in adm:
        await update.message.reply_text(f"❌ `{rm_uid}` admin emas.", parse_mode="Markdown")
        return
    if not is_superadmin(uid):
        if adm[str(rm_uid)].get("added_by") != uid:
            await update.message.reply_text("❌ Faqat o'zingiz qo'shgan adminni o'chira olasiz!")
            return
    del adm[str(rm_uid)]
    save_admins(adm)
    await update.message.reply_text(
        f"✅ `{rm_uid}` adminlikdan olib tashlandi.", parse_mode="Markdown")


async def cmd_listadmins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin_or_superadmin(uid):
        return
    adm = load_admins()

    if not is_superadmin(uid):
        info = adm.get(str(uid), {})
        if not info.get("can_add_admins"):
            await update.message.reply_text("❌ Sizda admin boshqarish huquqi yo'q.")
            return
        sub = {k: v for k, v in adm.items() if v.get("added_by") == uid}
        if not sub:
            sub_s = info.get("sub_admin_settings", {})
            await update.message.reply_text(
                f"👥 Sub-adminlar yo'q. (0/{sub_s.get('max_admins','?')})\n\n"
                "Qo'shish: `/addadmin <uid>`", parse_mode="Markdown")
            return
        lines = [f"👤 `{k}` — topic:{count_admin_topics(int(k))}/{v['topic_limit']} "
                 f"savol:{v.get('max_questions',MAX_QUESTIONS)}"
                 for k, v in sub.items()]
        await update.message.reply_text(
            "👥 *Sizning sub-adminlaringiz:*\n\n" + "\n".join(lines),
            parse_mode="Markdown")
        return

    if not adm:
        await update.message.reply_text(
            "👥 Admin yo'q.\n\nQo'shish: `/addadmin <uid>`",
            parse_mode="Markdown")
        return

    lines = []
    btns  = []
    for k, v in adm.items():
        dn    = v.get("display_name", "—")
        owned = count_admin_topics(int(k))
        ca    = "✅" if v.get("can_add_admins") else "❌"
        by    = v.get("added_by", SUPERADMIN)
        by_s  = f" ← `{by}`" if by != SUPERADMIN else ""
        lines.append(
            f"👤 `{k}` [{dn}] topic:{owned}/{v['topic_limit']} "
            f"savol:{v.get('max_questions',MAX_QUESTIONS)} admin:{ca}{by_s}")
        btns.append([IKB(f"⚙️ {k} ({dn})", callback_data=f"edit_adm:{k}")])

    await update.message.reply_text(
        f"👥 *Adminlar ({len(adm)} ta):*\n\n" + "\n".join(lines) + "\n\nTahrirlash:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns))


async def cmd_editadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text("❌ `/editadmin <uid>`", parse_mode="Markdown")
        return
    try:
        uid_e = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Raqam kiriting.")
        return
    adm = load_admins()
    if str(uid_e) not in adm:
        await update.message.reply_text(f"❌ `{uid_e}` admin emas.", parse_mode="Markdown")
        return
    info = adm[str(uid_e)]
    await update.message.reply_text(
        _editadmin_txt(uid_e, info), parse_mode="Markdown",
        reply_markup=_editadmin_kb(uid_e, info))


async def cmd_setdisplayname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "🏷 *Display name belgilash*\n\n"
            "O'zingiz uchun: `/setdisplayname me 👑 Boss`\n"
            "Admin uchun:   `/setdisplayname 123456 🌟 Ali`\n"
            "O'chirish:     `/setdisplayname 123456 -`",
            parse_mode="Markdown")
        return
    target     = args[0]
    name_parts = args[1:]
    name = " ".join(name_parts) if name_parts else None
    if name == "-":
        name = None

    if target.lower() == "me":
        set_display_name(SUPERADMIN, name)
        msg = f"✅ O'z nomingiz: *{name}*" if name else "✅ Nomingiz o'chirildi."
    else:
        try:
            uid_t = int(target)
        except ValueError:
            await update.message.reply_text("❌ UID raqam yoki 'me' kiriting.")
            return
        adm = load_admins()
        if str(uid_t) not in adm and uid_t != SUPERADMIN:
            await update.message.reply_text(f"❌ `{uid_t}` admin emas.", parse_mode="Markdown")
            return
        set_display_name(uid_t, name)
        msg = (f"✅ `{uid_t}` uchun nom: *{name}*" if name
               else f"✅ `{uid_t}` nomi o'chirildi.")
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_newtopic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin_or_superadmin(uid):
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("❌ Faqat botda (private) ishlaydi.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("❌ `/newtopic english`", parse_mode="Markdown")
        return
    name = args[0].lower().strip()
    if not name.replace("_", "").isalnum():
        await update.message.reply_text("❌ Nom: harf, raqam, _ bo'lsin.")
        return
    if topic_exists(name):
        await update.message.reply_text(f"❌ `{name}` allaqachon bor!", parse_mode="Markdown")
        return
    if is_superadmin(uid):
        if count_topics() >= MAX_TOPICS:
            await update.message.reply_text(f"❌ Max {MAX_TOPICS} ta topic!")
            return
    else:
        lim   = get_admin_topic_limit(uid)
        owned = count_admin_topics(uid)
        if owned >= lim:
            await update.message.reply_text(
                f"❌ Limit: {lim} ta topic ({owned}/{lim}).")
            return
    context.user_data.clear()
    context.user_data.update({"step": "newtopic_emoji", "topic_name": name})
    await update.message.reply_text(
        f"✅ Topic nomi: *{name}*\n\n🎨 Emojiini yuboring _(masalan: 🇬🇧 🔢 🧠)_",
        parse_mode="Markdown")


async def cmd_edittopicaccess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin_or_superadmin(uid):
        return
    if update.effective_chat.type != "private":
        return
    args = context.args
    if not args:
        topics = all_topics()
        if not is_superadmin(uid):
            topics = [t for t in topics if can_edit_topic_access(t, uid)]
        if not topics:
            await update.message.reply_text("❌ Sizga tegishli topic yo'q.\n"
                                             "_(faqat o'zingiz yaratgan topiclarning "
                                             "ruxsatini o'zgartirishingiz mumkin)_",
                                             parse_mode="Markdown")
            return
        kb = InlineKeyboardMarkup([
            [IKB(f"{t['emoji']} {t['name']}", callback_data=f"eta:{t['name']}")]
            for t in topics
        ])
        await update.message.reply_text("🔐 Qaysi topicning accessini o'zgartirish?",
                                        reply_markup=kb)
        return
    name = args[0].lower()
    t = load_topic(name)
    if not t:
        await update.message.reply_text(f"❌ `{name}` mavjud emas!", parse_mode="Markdown")
        return
    if not can_edit_topic_access(t, uid):
        await update.message.reply_text(
            "❌ Faqat shu topicni yaratgan admin yoki superadmin "
            "uning ruxsatini o'zgartira oladi!")
        return
    cur = ACCESS_LABELS.get(t.get("access", {}).get("type", "all"), "—")
    await update.message.reply_text(
        f"🔐 *{name}* — hozirgi: {cur}\n\nYangi access:",
        parse_mode="Markdown", reply_markup=_access_kb(name))



async def cmd_addq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin_or_superadmin(uid):
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("❌ Faqat botda ishlaydi.")
        return
    args = context.args
    if not args:
        topics = all_topics()
        if not is_superadmin(uid):
            uname  = update.effective_user.username
            topics = [t for t in topics if can_manage_topic(t, uid, uname)]
        if not topics:
            await update.message.reply_text("❌ Sizga ruxsat berilgan topic yo'q.")
            return
        kb = InlineKeyboardMarkup([
            [IKB(
                f"{t['emoji']} {t['name']} "
                f"({len(t['questions'])}/{get_admin_max_questions(t.get('created_by', uid))})",
                callback_data=f"addq_topic:{t['name']}"
            )]
            for t in topics
        ])
        await update.message.reply_text("📚 Qaysi topicga savol?", reply_markup=kb)
        return
    name = args[0].lower()
    t = load_topic(name)
    if not t:
        await update.message.reply_text(f"❌ `{name}` mavjud emas!", parse_mode="Markdown")
        return
    if not can_manage_topic(t, uid, update.effective_user.username):
        await update.message.reply_text("❌ Bu topicga ruxsatingiz yo'q!")
        return
    mq = get_admin_max_questions(uid)
    if len(t["questions"]) >= mq:
        await update.message.reply_text(f"❌ Limit: {mq} ta savol!")
        return
    context.user_data.clear()
    context.user_data.update({"step": "addq_question", "topic_name": name})
    await update.message.reply_text(
        f"📝 *{t['emoji']} {name}* — savol qo'shish\n\nSavol matnini yozing:\n⏹ /done",
        parse_mode="Markdown")


async def cmd_bulkq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin_or_superadmin(uid):
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("❌ Faqat botda ishlaydi.")
        return
    args = context.args
    if not args:
        topics = all_topics()
        if not is_superadmin(uid):
            uname  = update.effective_user.username
            topics = [t for t in topics if can_manage_topic(t, uid, uname)]
        if not topics:
            await update.message.reply_text("❌ Topic yo'q.")
            return
        names = "\n".join(f"• `{t['name']}`" for t in topics)
        await update.message.reply_text(
            f"❌ `/bulkq english`\n\n📚 Mavjud:\n{names}\n\n"
            "*Format:*\n`apple - olma`\n`orange - apelsin - sabzirang`",
            parse_mode="Markdown")
        return
    name = args[0].lower()
    t = load_topic(name)
    if not t:
        await update.message.reply_text(f"❌ `{name}` mavjud emas!", parse_mode="Markdown")
        return
    if not can_manage_topic(t, uid, update.effective_user.username):
        await update.message.reply_text("❌ Bu topicga ruxsatingiz yo'q!")
        return
    mq = get_admin_max_questions(uid)
    if len(t["questions"]) >= mq:
        await update.message.reply_text(f"❌ Limit: {mq} ta savol!")
        return
    full  = update.message.text or ""
    extra = [l.strip() for l in full.splitlines()[1:] if l.strip()]
    context.user_data.clear()
    context.user_data.update({"step": "bulkq_waiting", "topic_name": name})
    if extra:
        await _process_bulkq(update, context, raw="\n".join(extra))
        return
    rem = mq - len(t["questions"])
    await update.message.reply_text(
        f"📥 *{t['emoji']} {name}* — ommaviy\n\n"
        f"📊 {len(t['questions'])}/{mq} | yana {rem} ta\n\n"
        "*Format:*\n`apple - olma`\n`orange - apelsin - sabzirang`\n\n⏹ /done",
        parse_mode="Markdown")


async def _process_bulkq(update: Update, context: ContextTypes.DEFAULT_TYPE, raw: str = None):
    tn  = context.user_data.get("topic_name")
    uid = update.effective_user.id
    t   = load_topic(tn)
    if not t:
        await update.message.reply_text("❌ Topic topilmadi.")
        context.user_data.clear()
        return
    mq    = get_admin_max_questions(uid)
    text  = (raw or update.message.text or "").strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    added = skipped = 0
    errors = []
    lim    = mq - len(t["questions"])
    for line in lines:
        if added >= lim:
            skipped += len(lines) - lines.index(line)
            break
        parts = [p.strip() for p in line.split("-") if p.strip()]
        if len(parts) < 2:
            errors.append(f"• `{line[:40]}`")
            continue
        t["questions"].append({
            "question":     parts[0],
            "answer":       parts[1].lower(),
            "alternatives": [p.lower() for p in parts[2:]],
            "media_type":   "none",
            "file_id":      None,
        })
        added += 1
    save_topic(t)
    cnt = len(t["questions"])
    msg = f"✅ *{added} ta savol qo'shildi!*\n📊 {t['emoji']} {tn}: {cnt}/{mq}"
    if skipped:
        msg += f"\n⚠️ {skipped} ta o'tkazildi (limit to'ldi)"
    if errors:
        msg += "\n\n❌ *Xatolar:*\n" + "\n".join(errors[:5])
    kb = None
    if cnt < mq:
        kb = InlineKeyboardMarkup([
            [IKB("➕ Yana savollar", callback_data="bulkq_more"),
             IKB("⏹ Tugatish",      callback_data="addq_finish")],
        ])
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
    if cnt >= mq:
        context.user_data.clear()


async def cmd_listtopics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin_or_superadmin(uid):
        return
    topics = all_topics()
    if not is_superadmin(uid):
        uname  = update.effective_user.username
        topics = [t for t in topics if can_manage_topic(t, uid, uname)]
    if not topics:
        await update.message.reply_text("📭 Topic yo'q.")
        return
    lines = []
    for t in topics:
        prize = "✅" if t.get("prize") else "❌"
        acc   = ACCESS_LABELS.get(t.get("access", {}).get("type", "all"), "👥")
        cb    = t.get("created_by", "?")
        mq    = get_admin_max_questions(cb if isinstance(cb, int) else uid)
        owner_s = f" (👤{cb})" if is_superadmin(uid) and cb != uid else ""
        lines.append(
            f"{t['emoji']} *{t['name']}* — {len(t['questions'])}/{mq} "
            f"| sovrin:{prize} | 🔐{acc}{owner_s}")
    hdr = (f"📋 *Barcha topiclar ({len(topics)}/{MAX_TOPICS}):*"
           if is_superadmin(uid) else
           f"📋 *Sizning topiclaringiz ({len(topics)}/{get_admin_topic_limit(uid)}):*")
    await update.message.reply_text(hdr + "\n\n" + "\n".join(lines), parse_mode="Markdown")


async def cmd_deletetopic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text("❌ `/deletetopic english`", parse_mode="Markdown")
        return
    name = args[0].lower()
    if not topic_exists(name):
        await update.message.reply_text(f"❌ `{name}` mavjud emas!", parse_mode="Markdown")
        return
    kb = InlineKeyboardMarkup([[
        IKB("✅ Ha, o'chir", callback_data=f"deltopic:{name}"),
        IKB("❌ Bekor",      callback_data="deltopic_no"),
    ]])
    await update.message.reply_text(
        f"⚠️ *{name}* o'chirilsinmi? Barcha savollar ham o'chadi!",
        parse_mode="Markdown", reply_markup=kb)


async def cmd_setprize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    if update.effective_chat.type != "private":
        return
    args = context.args
    if not args:
        topics = all_topics()
        if not topics:
            await update.message.reply_text("❌ Topic yo'q.")
            return
        kb = InlineKeyboardMarkup([
            [IKB(f"{t['emoji']} {t['name']}", callback_data=f"setprize_topic:{t['name']}")]
            for t in topics
        ])
        await update.message.reply_text("🏆 Qaysi topicga sovrin?", reply_markup=kb)
        return
    name = args[0].lower()
    if not topic_exists(name):
        await update.message.reply_text(f"❌ `{name}` mavjud emas!")
        return
    context.user_data.clear()
    context.user_data.update({"step": "setprize_waiting", "topic_name": name})
    await update.message.reply_text(
        f"🏆 *{name}* uchun sovrinni yuboring _(rasm, GIF yoki stiker)_:",
        parse_mode="Markdown")


async def cmd_listgames(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    active = {cid: g for cid, g in games.items() if g.get("active")}
    if not active:
        await update.message.reply_text("🎮 Faol o'yin yo'q.")
        return
    lines = []
    btns  = []
    for cid, g in active.items():
        lines.append(f"🟢 `{cid}` — {g['emoji']}{g['topic']} | "
                     f"{g['asked']}/{len(g['questions'])} | {len(g['scores'])} o'yinchi")
        btns.append([IKB(f"⏹ {g['emoji']}{g['topic']} ({cid})",
                         callback_data=f"stopgame_ask:{cid}")])
    await update.message.reply_text(
        "🎮 *Faol o'yinlar:*\n\n" + "\n".join(lines),
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))


async def cmd_newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("❌ Faqat guruhlarda!")
        return
    if not await _require_bot_admin(update, context):
        return
    if not await is_group_admin(update, context):
        await update.message.reply_text("❌ Faqat guruh admini o'yin boshlay oladi!")
        return
    register_chat(chat)
    args = context.args
    if not args:
        topics = all_topics()
        if not topics:
            await update.message.reply_text("❌ Topic yo'q.")
            return
        names = ", ".join(f"`{t['name']}`" for t in topics)
        await update.message.reply_text(
            f"❌ `/newgame english`\n\n📚 Mavjud: {names}", parse_mode="Markdown")
        return
    tn = args[0].lower()
    t  = load_topic(tn)
    if not t:
        await update.message.reply_text(f"❌ `{tn}` mavjud emas!", parse_mode="Markdown")
        return
    if not t["questions"]:
        await update.message.reply_text("❌ Bu topicda savollar yo'q!")
        return
    cid = chat.id
    g   = get_game(cid)
    if g["active"]:
        await update.message.reply_text(
            f"⚠️ Allaqachon *{g['emoji']}{g['topic']}* ketmoqda!",
            parse_mode="Markdown")
        return
    qs = t["questions"].copy()
    random.shuffle(qs)
    g.update({"active": True, "topic": tn, "emoji": t["emoji"],
              "questions": qs, "asked": 0, "current": None,
              "current_msg_id": None, "scores": {}, "waiting": False})
    await update.message.reply_text(
        f"🎮 *O'YIN BOSHLANDI!*\n\n{t['emoji']} *{tn.capitalize()}*\n"
        f"📊 {len(qs)} ta savol\n\n🎯 Reply qilib javob bering!",
        parse_mode="Markdown")
    await send_question(cid, context)


async def cmd_endgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    uid = update.effective_user.id
    if not is_superadmin(uid):
        if update.effective_chat.type not in ("group", "supergroup"):
            return
        if not await is_group_admin(update, context):
            await update.message.reply_text("❌ Faqat admin!")
            return
    g = get_game(cid)
    if not g["active"]:
        await update.message.reply_text("⚠️ Faol o'yin yo'q.")
        return
    g["active"] = False
    g["current"] = None
    g["waiting"] = False
    await update.message.reply_text(
        f"⏹ *{g['emoji']}{g['topic']} tugatildi!*", parse_mode="Markdown")


async def cmd_scores(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    g   = get_game(cid)
    if not g["scores"]:
        await update.message.reply_text("📊 Hozircha ballar yo'q.")
        return
    ss     = sorted(g["scores"].items(), key=lambda x: x[1]["count"], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    res    = f"📊 *Joriy — {g['emoji']}{g['topic']}:*\n\n"
    for i, (uid_s, d) in enumerate(ss[:10]):
        m  = medals[i] if i < 3 else f"{i+1}."
        dn = get_display_name(int(uid_s), d["name"])
        res += f"{m} {dn}: {d['count']} ball\n"
    await update.message.reply_text(res, parse_mode="Markdown")


async def cmd_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_group_admin(update, context):
        return
    uid  = update.effective_user.id
    cid  = update.effective_chat.id
    args = context.args

    # Oddiy: reply qilingan xabarni o'chirish
    if not args:
        reply = update.message.reply_to_message
        if not reply:
            await update.message.reply_text(
                "❌ Bot xabariga reply qilib /del yozing.\n"
                "_(Kengaytirilgan: `/del @a` yoki `/del @username [2024-06-13 14:30]`)_",
                parse_mode="Markdown")
            return
        if reply.from_user is None or reply.from_user.id != context.bot.id:
            await update.message.reply_text("❌ Faqat botning xabarlarini o'chirish mumkin.")
            return
        try:
            await context.bot.delete_message(cid, reply.message_id)
            await update.message.delete()
        except Exception as e:
            await update.message.reply_text(f"❌ {e}")
        return

    # Kengaytirilgan: faqat superadmin
    if not is_superadmin(uid):
        await update.message.reply_text("❌ Kengaytirilgan /del faqat superadmin uchun!")
        return

    target = args[0]  # @a yoki @username

    # Vaqtni parse qilish
    since_ts = None
    if len(args) >= 3:
        try:
            dt       = datetime.strptime(f"{args[1]} {args[2]}", "%Y-%m-%d %H:%M")
            since_ts = dt.replace(tzinfo=TZ).timestamp()
        except ValueError:
            await update.message.reply_text(
                "❌ Vaqt formati: `2024-06-13 14:30`", parse_mode="Markdown")
            return
    elif len(args) == 2:
        try:
            dt       = datetime.strptime(args[1], "%Y-%m-%d")
            since_ts = dt.replace(tzinfo=TZ).timestamp()
        except ValueError:
            pass

    history = msg_history.get(cid, [])

    if target == "@a":
        to_del = history
    else:
        uname  = target.lstrip("@").lower()
        to_del = [m for m in history
                  if m["uname"] == uname or str(m["uid"]) == uname]

    if since_ts:
        to_del = [m for m in to_del if m["ts"] >= since_ts]

    if not to_del:
        await update.message.reply_text(
            "❌ O'chiriladigan xabar topilmadi.\n"
            "_(Bot restart bo'lsa tarix yo'qoladi)_")
        return

    try:
        await update.message.delete()
    except Exception:
        pass

    prog = await context.bot.send_message(
        cid, f"🗑 *{len(to_del)} ta xabar o'chirilmoqda...*", parse_mode="Markdown")

    ids = [m["id"] for m in to_del]
    d, f = await _del_batch(context, cid, ids)

    # Tarixdan o'chirilganlarni tozalash
    del_set = set(ids)
    if cid in msg_history:
        msg_history[cid] = [m for m in msg_history[cid] if m["id"] not in del_set]

    try:
        await prog.edit_text(
            f"✅ *O'chirildi: {d} ta*\n❌ Xato: {f} ta",
            parse_mode="Markdown")
    except Exception:
        pass


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    await update.message.reply_text("📦 Export qilinmoqda...")
    ok = await do_export(context.bot)
    await update.message.reply_text(
        "✅ Export muvaffaqiyatli!" if ok else "❌ Export xato! Log ni tekshiring.")


async def cmd_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    context.user_data["step"] = "restore_waiting"
    await update.message.reply_text(
        "♻️ *Ma'lumotlarni tiklash*\n\nExport kanaldan JSON faylni yuboring.\n⏹ /cancel",
        parse_mode="Markdown")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    if update.effective_chat.type != "private":
        return
    context.user_data["step"] = "broadcast_target"
    chats  = load_chats()
    by_t   = {}
    for c in chats.values():
        by_t[c["type"]] = by_t.get(c["type"], 0) + 1
    stats = " | ".join(f"{t}:{n}" for t, n in by_t.items()) or "0"
    kb = ReplyKeyboardMarkup(
        [["👥 Hammaga", "👤 Faqat userlarga"],
         ["🏘 Faqat guruhlarga", "📢 Faqat kanallarga"]],
        resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        f"📢 *Reklama yuborish*\n\n💬 Chatlar: {stats}\n\nKimga?",
        parse_mode="Markdown", reply_markup=kb)


async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_or_superadmin(update.effective_user.id):
        return
    if context.user_data.get("step") == "addq_media_waiting":
        context.user_data.update({"q_media_type": "none", "q_file_id": None})
        await _save_q(update, context)
    else:
        await update.message.reply_text("⚠️ Hech narsa o'tkazilmadi.")


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_or_superadmin(update.effective_user.id):
        return
    tn  = context.user_data.get("topic_name", "?")
    t   = load_topic(tn)
    cnt = len(t["questions"]) if t else 0
    context.user_data.clear()
    await update.message.reply_text(
        f"✅ *Tugatildi!*\n{tn}: {cnt} ta savol saqlangan.",
        parse_mode="Markdown")

# ══════════════════════════════════════════════════════
#  TEXT HANDLER
# ══════════════════════════════════════════════════════

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    chat = update.effective_chat
    step = context.user_data.get("step")
    text = (update.message.text or "").strip()

    # Guruhlarda: faqat o'yin javobini tekshirish (tracking alohida handler'da)
    if chat.type in ("group", "supergroup"):
        await _check_answer(update, context)
        return

    # ── Sendas: botdan guruhga xabar (text) ──
    if step == "sendas_waiting" and is_superadmin(uid):
        target_cid = context.user_data.pop("sendas_chat", None)
        context.user_data.clear()
        if target_cid:
            try:
                await context.bot.copy_message(
                    chat_id=target_cid,
                    from_chat_id=update.effective_chat.id,
                    message_id=update.message.message_id)
                chats = load_chats()
                name  = chats.get(str(target_cid), {}).get("name", str(target_cid))
                await update.message.reply_text(
                    f"✅ *{name}* guruhiga yuborildi!", parse_mode="Markdown")
            except Exception as e:
                await update.message.reply_text(f"❌ Yuborib bo'lmadi:\n`{e}`",
                                                parse_mode="Markdown")
        return

    # ── Contact relay ──
    if step == "contact_waiting" and not is_admin_or_superadmin(uid):
        await _relay(update, context)
        return

    # ── Broadcast: target tanlash ──
    if step == "broadcast_target" and is_superadmin(uid):
        if text in TARGET_KEYS:
            context.user_data["bc_target"] = TARGET_KEYS[text]
            context.user_data["step"]      = "broadcast_msg"
            await update.message.reply_text(
                "📤 *Reklama xabarini yuboring:*\n"
                "_(matn, rasm, video, fayl, stiker — barchasi qabul qilinadi)_\n\n"
                "⏹ /cancel",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove())
        else:
            kb = ReplyKeyboardMarkup(
                [["👥 Hammaga", "👤 Faqat userlarga"],
                 ["🏘 Faqat guruhlarga", "📢 Faqat kanallarga"]],
                resize_keyboard=True)
            await update.message.reply_text("❌ Tugmalardan birini tanlang:", reply_markup=kb)
        return

    if step == "broadcast_msg" and is_superadmin(uid):
        await _bc_received(update, context)
        return

    if step == "broadcast_ready" and is_superadmin(uid) and text == BROADCAST_READY:
        await _do_broadcast(update, context)
        return

    # ── addadmin: UID kutish ──
    if step == "addadmin_uid" and is_admin_or_superadmin(uid):
        try:
            new_uid = int(text)
        except ValueError:
            await update.message.reply_text("❌ User ID raqam bo'lishi kerak.")
            return
        if new_uid == SUPERADMIN:
            await update.message.reply_text("❌ Superadminni admin qilish shart emas.")
            return
        adm = load_admins()
        if str(new_uid) in adm:
            await update.message.reply_text(
                f"⚠️ `{new_uid}` allaqachon admin!", parse_mode="Markdown")
            return
        max_tl = context.user_data.get("aa_max_tl", MAX_TOPICS)
        max_mq = context.user_data.get("aa_max_mq", MAX_QUESTIONS)
        context.user_data.update({"aa_uid": new_uid, "step": "addadmin_tlimit"})
        await update.message.reply_text(
            f"➕ *Yangi admin: `{new_uid}`*\n\n📁 *Topic limiti:*",
            parse_mode="Markdown",
            reply_markup=_aa_tlimit_kb(max_tl))
        return

    # ── addadmin: display name (text input) ──
    if step == "addadmin_dname" and is_superadmin(uid):
        name = None if text == "-" else text
        context.user_data["aa_dname"] = name
        context.user_data["step"]     = "addadmin_can_add"
        await update.message.reply_text(
            f"🏷 Nom: *{name or '(yo\'q)'}*\n\nBu admin o'z adminlarini qo'sha oladimi?",
            parse_mode="Markdown",
            reply_markup=_aa_can_add_kb())
        return

    # ── editadmin: display name (text input) ──
    if step == "editadmin_dname" and is_superadmin(uid):
        uid_e = context.user_data.pop("ea_uid", None)
        context.user_data.pop("step", None)
        if uid_e:
            name = None if text == "-" else text
            set_display_name(uid_e, name)
            msg = (f"✅ `{uid_e}` uchun nom: *{name}*" if name
                   else f"✅ `{uid_e}` nomi o'chirildi.")
            await update.message.reply_text(msg, parse_mode="Markdown")
        return

    # ── Topic access: custom input ──
    if step in ("newtopic_access_custom", "access_custom_input") and is_admin_or_superadmin(uid):
        tn = context.user_data.get("topic_name")
        t  = load_topic(tn)
        if not t:
            context.user_data.clear()
            return
        allowed = parse_allowed(text)
        t["access"] = {"type": "custom", "allowed": allowed}
        save_topic(t)
        context.user_data.pop("step", None)
        s = ", ".join(str(a) for a in allowed) if allowed else "hech kim"
        await update.message.reply_text(
            f"✅ *{tn}* access: ✏️ Qo'lda\n"
            f"Ruxsat berilganlar: {s}\n_(+ siz va superadmin)_",
            parse_mode="Markdown")
        return

    if not is_admin_or_superadmin(uid):
        return

    # ── bulkq ──
    if step == "bulkq_waiting":
        await _process_bulkq(update, context)
        return

    # ── newtopic: emoji ──
    if step == "newtopic_emoji":
        name = context.user_data["topic_name"]
        save_topic({
            "name":       name,
            "emoji":      text,
            "prize":      None,
            "created_by": uid,
            "access":     {"type": "all", "allowed": []},
            "questions":  [],
        })
        context.user_data["step"] = "newtopic_access"
        await update.message.reply_text(
            f"✅ Topic yaratildi: {text} *{name}*\n\n"
            "🔐 *Topicdan kimlar foydalana oladi?*\n"
            "_(savol qo'shish va ko'rish huquqi)_",
            parse_mode="Markdown",
            reply_markup=_access_kb(name))
        return

    # ── addq: savol matni ──
    if step == "addq_question" and text:
        context.user_data.update({"q_question": text, "step": "addq_answer"})
        await update.message.reply_text(
            f"❓ Savol: _{text}_\n\n✅ To'g'ri javobni yozing:",
            parse_mode="Markdown")
        return

    # ── addq: to'g'ri javob ──
    if step == "addq_answer":
        context.user_data.update(
            {"q_answer": text.lower(), "q_alts": [], "step": "addq_alts"})
        kb = InlineKeyboardMarkup([
            [IKB("➕ Alternativ javob",          callback_data="addq_alt")],
            [IKB("🖼 Rasm/Video/GIF/Stiker",      callback_data="addq_media")],
            [IKB("✅ Saqlash (mediasiz)",          callback_data="addq_save_nomedia")],
        ])
        await update.message.reply_text(
            f"✅ Javob: *{text}*\n\nKeyingi qadam?",
            parse_mode="Markdown", reply_markup=kb)
        return

    # ── addq: alternativ ──
    if step == "addq_alt_text":
        context.user_data.setdefault("q_alts", []).append(text.lower())
        alts = context.user_data["q_alts"]
        context.user_data["step"] = "addq_alts"
        kb = InlineKeyboardMarkup([
            [IKB("➕ Yana alternativ",            callback_data="addq_alt")],
            [IKB("🖼 Rasm/Video/GIF/Stiker",      callback_data="addq_media")],
            [IKB("✅ Saqlash (mediasiz)",          callback_data="addq_save_nomedia")],
        ])
        await update.message.reply_text(
            f"➕ Alternativlar: *{', '.join(alts)}*",
            parse_mode="Markdown", reply_markup=kb)
        return

# ══════════════════════════════════════════════════════
#  MEDIA HANDLER
# ══════════════════════════════════════════════════════

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    chat = update.effective_chat
    if chat.type in ("group", "supergroup"):
        return
    step = context.user_data.get("step")

    # Sendas: botdan guruhga xabar (media)
    if step == "sendas_waiting" and is_superadmin(uid):
        target_cid = context.user_data.pop("sendas_chat", None)
        context.user_data.clear()
        if target_cid:
            try:
                await context.bot.copy_message(
                    chat_id=target_cid,
                    from_chat_id=update.effective_chat.id,
                    message_id=update.message.message_id)
                chats = load_chats()
                name  = chats.get(str(target_cid), {}).get("name", str(target_cid))
                await update.message.reply_text(
                    f"✅ *{name}* guruhiga yuborildi!", parse_mode="Markdown")
            except Exception as e:
                await update.message.reply_text(f"❌ Yuborib bo'lmadi:\n`{e}`",
                                                parse_mode="Markdown")
        return

    # Contact relay
    if step == "contact_waiting" and not is_admin_or_superadmin(uid):
        await _relay(update, context)
        return

    if not is_admin_or_superadmin(uid):
        return

    # Broadcast media
    if step == "broadcast_msg" and is_superadmin(uid):
        await _bc_received(update, context)
        return

    # Restore
    if step == "restore_waiting" and is_superadmin(uid):
        if update.message.document:
            await _process_restore(update, context)
        else:
            await update.message.reply_text("❌ JSON faylni yuboring.")
        return

    # Prize
    if step == "setprize_waiting" and is_superadmin(uid):
        msg = update.message
        if msg.photo:
            prize = {"type": "photo",   "file_id": msg.photo[-1].file_id}
        elif msg.animation:
            prize = {"type": "gif",     "file_id": msg.animation.file_id}
        elif msg.sticker:
            prize = {"type": "sticker", "file_id": msg.sticker.file_id}
        else:
            await update.message.reply_text("❌ Rasm, GIF yoki stiker.")
            return
        tn = context.user_data.get("topic_name")
        t  = load_topic(tn)
        if t:
            t["prize"] = prize
            save_topic(t)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ *{tn}* uchun sovrin saqlandi! 🏆", parse_mode="Markdown")
        return

    # Question media
    if step == "addq_media_waiting":
        msg = update.message
        if msg.photo:
            mt, fi = "photo",   msg.photo[-1].file_id
        elif msg.video:
            mt, fi = "video",   msg.video.file_id
        elif msg.animation:
            mt, fi = "gif",     msg.animation.file_id
        elif msg.sticker:
            mt, fi = "sticker", msg.sticker.file_id
        else:
            await update.message.reply_text("❌ Rasm, video, GIF yoki stiker.")
            return
        context.user_data.update({"q_media_type": mt, "q_file_id": fi})
        await _save_q(update, context)
        return

# ══════════════════════════════════════════════════════
#  GROUP MESSAGE TRACKER  (barcha guruh xabarlarini eslab qolish)
# ══════════════════════════════════════════════════════

async def check_profanity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guruh xabarlarida yomon so'zlarni tekshirish va chora ko'rish."""
    msg  = update.message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not msg.text or not user:
        return
    if user.id == SUPERADMIN:
        return

    bw      = load_badwords()
    text    = msg.text
    severe  = bw.get("severe_words", [])
    normal  = bw.get("words", [])

    has_severe = _has_badword(text, severe)
    has_normal = _has_badword(text, normal)
    if not has_severe and not has_normal:
        return

    warn_msg = _random_warning(bw.get("warnings", []))
    ulink    = f"[{user.first_name}](tg://user?id={user.id})"

    # Bot adminmi?
    try:
        bm = await context.bot.get_chat_member(chat.id, context.bot.id)
        bot_adm = bm.status in ("administrator", "creator")
    except Exception:
        bot_adm = False

    # User adminmi?
    try:
        um = await context.bot.get_chat_member(chat.id, user.id)
        usr_adm = um.status in ("administrator", "creator")
    except Exception:
        usr_adm = False

    if has_severe:
        # ── Juda yomon so'z ──
        if bot_adm:
            try:
                await msg.delete()
            except Exception:
                pass

        if usr_adm:
            # Admin: faqat ogohlantirish
            await context.bot.send_message(
                chat.id,
                f"🚫 {ulink}, *{warn_msg}*\n"
                f"_(Admin bo'lsangizda ham bu so'z qabul qilinmaydi!)_",
                parse_mode="Markdown")
        else:
            # Oddiy user: chiqarib yuborish
            kicked = False
            if bot_adm:
                try:
                    await context.bot.ban_chat_member(chat.id, user.id)
                    await asyncio.sleep(0.5)
                    await context.bot.unban_chat_member(chat.id, user.id)
                    kicked = True
                except Exception as e:
                    logger.warning(f"Kick failed: {e}")
            await context.bot.send_message(
                chat.id,
                f"🚫 {ulink} guruhdan *chiqarib yuborildi!*\n"
                f"*Sabab:* Juda qo'pol so'z ishlatish\n\n"
                f"💬 _{warn_msg}_",
                parse_mode="Markdown")
            _ = kicked  # noqa

        # Superadminga bildirishnoma (saqlanmaydi)
        try:
            await context.bot.send_message(
                SUPERADMIN,
                f"🚨 *JUDA QO'POL SO'Z!*\n\n"
                f"👤 {ulink} | `{user.id}`"
                + (f" | @{user.username}" if user.username else "")
                + f"\n💬 Guruh: *{chat.title}* (`{chat.id}`)\n"
                  f"👮 User admin: {'✅' if usr_adm else '❌'}\n"
                  f"🤖 Bot admin: {'✅' if bot_adm else '❌'}\n"
                  f"🦵 Kick: {'✅' if not usr_adm and bot_adm else '❌'}\n\n"
                  f"📝 Xabar: `{text[:300]}`",
                parse_mode="Markdown")
        except Exception:
            pass

    elif has_normal:
        # ── Oddiy yomon so'z ──
        if bot_adm:
            try:
                await msg.delete()
            except Exception:
                pass
        await context.bot.send_message(
            chat.id,
            f"⚠️ {ulink}, {warn_msg}",
            parse_mode="Markdown")


async def group_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return
    msg = update.message
    if not msg:
        return
    u = update.effective_user
    if u:
        track_msg(chat.id, msg.message_id, u.id, u.username,
                  msg.date.timestamp())
    # Profanity check
    if msg.text:
        await check_profanity(update, context)

# ══════════════════════════════════════════════════════
#  CALLBACK HANDLER
# ══════════════════════════════════════════════════════

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    data = q.data

    # ── Sendas: guruh tanlash ──
    if data.startswith("sendas:"):
        if not is_superadmin(uid): return
        cid_str = data.split(":", 1)[1]
        chats   = load_chats()
        name    = chats.get(cid_str, {}).get("name", cid_str)
        context.user_data.clear()
        context.user_data["step"]        = "sendas_waiting"
        context.user_data["sendas_chat"] = int(cid_str)
        await q.edit_message_text(
            f"📤 *{name}* guruhiga xabar yuboring:\n\n"
            "_(Matn, rasm, video, stiker, fayl — barchasi qabul qilinadi)_\n\n"
            "⏹ /cancel", parse_mode="Markdown")
        return

    # ── Require admin: toggle ──
    if data.startswith("req_adm:"):
        if not is_superadmin(uid): return
        cid_str = data.split(":", 1)[1]
        chats   = load_chats()
        cur     = chats.get(cid_str, {}).get("require_admin", False)
        new_val = not cur
        if cid_str not in chats:
            chats[cid_str] = {"chat_id": int(cid_str), "type": "supergroup", "name": cid_str}
        chats[cid_str]["require_admin"] = new_val
        save_chats(chats)
        name   = chats[cid_str].get("name", cid_str)
        status = "🟢 YONIQ" if new_val else "🔴 O'CHIQ"
        # Rebuild requireadmin list
        groups = {k: v for k, v in chats.items()
                  if v.get("type") in ("group", "supergroup")}
        btns = []
        for k, v in groups.items():
            req = v.get("require_admin", False)
            s   = "🟢" if req else "🔴"
            btns.append([IKB(f"{s} {v.get('name', k)}", callback_data=f"req_adm:{k}")])
        btns.append([IKB("✅ Tayyor", callback_data="req_adm_done")])
        await q.edit_message_text(
            f"✅ *{name}* — Admin talab: {status}\n\n"
            "🔐 *Guruhlar — Admin talab:*\n_(Bosib yoqing/o'chiring)_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(btns))
        return

    if data == "req_adm_done":
        if not is_superadmin(uid): return
        await q.edit_message_text("✅ Sozlamalar saqlandi.")
        return

    # ── Topic access set ──
    if data.startswith("acc:"):
        if not is_admin_or_superadmin(uid): return
        _, at, tn = data.split(":", 2)
        t = load_topic(tn)
        if not t: await q.edit_message_text("❌ Topic topilmadi."); return
        if not can_edit_topic_access(t, uid):
            await q.answer("❌ Faqat topic egasi yoki superadmin "
                          "ruxsatni o'zgartira oladi!", show_alert=True); return
        if at == "custom":
            context.user_data["topic_name"] = tn
            context.user_data["step"]       = "access_custom_input"
            await q.edit_message_text(
                f"✏️ *{tn}* — ruxsat beriladigan userlarni kiriting:\n\n"
                "Format: `@username` yoki `123456789`\n"
                "_(probel yoki yangi qator bilan ajrating)_\n\n"
                "Misol: `@ali @vali 123456789`",
                parse_mode="Markdown")
        else:
            t["access"] = {"type": at, "allowed": []}
            save_topic(t)
            await q.edit_message_text(
                f"✅ *{tn}* — Access: {ACCESS_LABELS.get(at, at)}",
                parse_mode="Markdown")
        return

    # ── edittopicaccess selector ──
    if data.startswith("eta:"):
        if not is_admin_or_superadmin(uid): return
        tn = data.split(":", 1)[1]
        t  = load_topic(tn)
        if not t: return
        if not can_edit_topic_access(t, uid):
            await q.answer("❌ Faqat topic egasi yoki superadmin "
                          "ruxsatni o'zgartira oladi!", show_alert=True); return
        cur = ACCESS_LABELS.get(t.get("access", {}).get("type", "all"), "—")
        await q.edit_message_text(
            f"🔐 *{tn}* — hozir: {cur}\n\nYangi access:",
            parse_mode="Markdown", reply_markup=_access_kb(tn))
        return

    # ── editadmin: topic limit ──
    if data.startswith("eal_t:"):
        if not is_superadmin(uid): return
        _, uid_e, val = data.split(":")
        uid_e = int(uid_e); val = int(val)
        adm = load_admins()
        if str(uid_e) not in adm: return
        adm[str(uid_e)]["topic_limit"] = val
        save_admins(adm)
        await q.edit_message_text(
            _editadmin_txt(uid_e, adm[str(uid_e)]),
            parse_mode="Markdown",
            reply_markup=_editadmin_kb(uid_e, adm[str(uid_e)]))
        return

    # ── editadmin: max_questions ──
    if data.startswith("eal_q:"):
        if not is_superadmin(uid): return
        _, uid_e, val = data.split(":")
        uid_e = int(uid_e); val = int(val)
        adm = load_admins()
        if str(uid_e) not in adm: return
        adm[str(uid_e)]["max_questions"] = val
        save_admins(adm)
        await q.edit_message_text(
            _editadmin_txt(uid_e, adm[str(uid_e)]),
            parse_mode="Markdown",
            reply_markup=_editadmin_kb(uid_e, adm[str(uid_e)]))
        return

    # ── editadmin: display name tugmasi ──
    if data.startswith("eal_dn:"):
        if not is_superadmin(uid): return
        uid_e = int(data.split(":")[1])
        context.user_data["step"]   = "editadmin_dname"
        context.user_data["ea_uid"] = uid_e
        await q.edit_message_text(
            f"🏷 `{uid_e}` uchun yangi nom yozing:\n_(o'chirish: `-`)_",
            parse_mode="Markdown")
        return

    # ── editadmin: open ──
    if data.startswith("edit_adm:"):
        if not is_superadmin(uid): return
        uid_e = int(data.split(":")[1])
        adm   = load_admins()
        if str(uid_e) not in adm: return
        await q.edit_message_text(
            _editadmin_txt(uid_e, adm[str(uid_e)]),
            parse_mode="Markdown",
            reply_markup=_editadmin_kb(uid_e, adm[str(uid_e)]))
        return

    # ── editadmin: delete ──
    if data.startswith("del_adm:"):
        if not is_superadmin(uid): return
        uid_e = int(data.split(":")[1])
        adm   = load_admins()
        adm.pop(str(uid_e), None)
        save_admins(adm)
        await q.edit_message_text(
            f"✅ `{uid_e}` adminlikdan olib tashlandi.", parse_mode="Markdown")
        return

    # ── listadmins back ──
    if data == "list_adm_cb":
        if not is_superadmin(uid): return
        adm = load_admins()
        if not adm:
            await q.edit_message_text("👥 Admin yo'q."); return
        lines = []
        btns  = []
        for k, v in adm.items():
            dn    = v.get("display_name", "—")
            owned = count_admin_topics(int(k))
            lines.append(f"👤 `{k}` [{dn}] topic:{owned}/{v['topic_limit']}")
            btns.append([IKB(f"⚙️ {k}", callback_data=f"edit_adm:{k}")])
        await q.edit_message_text(
            f"👥 *Adminlar ({len(adm)} ta):*\n\n" + "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(btns))
        return

    # ══ addadmin multi-step ══

    if data.startswith("aa_t:"):
        if not is_admin_or_superadmin(uid): return
        val = int(data.split(":")[1])
        context.user_data["aa_tl"] = val
        context.user_data["step"]  = "addadmin_qlimit"
        max_mq = context.user_data.get("aa_max_mq", MAX_QUESTIONS)
        await q.edit_message_text(
            f"📁 Topic limiti: *{val}* ta\n\n❓ *Savol limiti (1 topic uchun):*",
            parse_mode="Markdown",
            reply_markup=_aa_qlimit_kb(max_mq))
        return

    if data.startswith("aa_q:"):
        if not is_admin_or_superadmin(uid): return
        val = int(data.split(":")[1])
        context.user_data["aa_mq"] = val
        by = context.user_data.get("aa_by", uid)

        if is_superadmin(by):
            # Superadmin: display name so'rash
            context.user_data["step"] = "addadmin_dname"
            kb = InlineKeyboardMarkup([
                [IKB("⏩ O'tkazib yuborish", callback_data="aa_skip_dn")],
                [IKB("❌ Bekor",             callback_data="aa_cancel")],
            ])
            await q.edit_message_text(
                f"📁 {context.user_data.get('aa_tl')} ta | ❓ {val} ta\n\n"
                "🏷 *Display name kiriting:*\n"
                "_(masalan: 🌟 Ali aka — har joyda shu ko'rinadi)_\n"
                "O'tkazib yuborish uchun pastdagi tugma.",
                parse_mode="Markdown", reply_markup=kb)
        else:
            # Sub-admin: to'g'ridan saqlash (display name, can_add yo'q)
            await _finalize_addadmin(q, context, by,
                                     display_name=None, can_add=False, sub_s={})
        return

    if data == "aa_skip_dn":
        if not is_superadmin(uid): return
        context.user_data["aa_dname"] = None
        context.user_data["step"]     = "addadmin_can_add"
        await q.edit_message_text(
            "Bu admin o'z adminlarini qo'sha oladimi?",
            reply_markup=_aa_can_add_kb())
        return

    if data.startswith("aa_ca:"):
        if not is_superadmin(uid): return
        val = int(data.split(":")[1])
        if not val:
            await _finalize_addadmin(q, context, uid,
                                     display_name=context.user_data.get("aa_dname"),
                                     can_add=False, sub_s={})
        else:
            context.user_data["step"] = "addadmin_sub_cnt"
            await q.edit_message_text(
                "👥 *Bu admin max nechta sub-admin qo'sha oladi?*",
                reply_markup=_aa_cnt_kb())
        return

    if data.startswith("aa_sm:"):
        if not is_superadmin(uid): return
        val = int(data.split(":")[1])
        context.user_data["aa_sub_ma"] = val
        context.user_data["step"]      = "addadmin_sub_tl"
        await q.edit_message_text(
            f"👥 Max sub-admin: *{val}* ta\n\n📁 *Sub-adminlar uchun max topic limiti:*",
            parse_mode="Markdown", reply_markup=_aa_sub_tl_kb())
        return

    if data.startswith("aa_st:"):
        if not is_superadmin(uid): return
        val = int(data.split(":")[1])
        context.user_data["aa_sub_tl"] = val
        context.user_data["step"]      = "addadmin_sub_ql"
        await q.edit_message_text(
            f"📁 Sub-admin topic: *{val}* ta\n\n❓ *Sub-adminlar uchun max savol limiti:*",
            parse_mode="Markdown", reply_markup=_aa_sub_ql_kb())
        return

    if data.startswith("aa_sq:"):
        if not is_superadmin(uid): return
        val   = int(data.split(":")[1])
        sub_s = {
            "max_admins":              context.user_data.get("aa_sub_ma", 1),
            "max_topic_limit":         context.user_data.get("aa_sub_tl", 1),
            "max_questions_per_topic": val,
        }
        await _finalize_addadmin(q, context, uid,
                                 display_name=context.user_data.get("aa_dname"),
                                 can_add=True, sub_s=sub_s)
        return

    if data == "aa_cancel":
        context.user_data.clear()
        await q.edit_message_text("❌ Admin qo'shish bekor qilindi.")
        return

    # ══ addq ══

    if data.startswith("addq_topic:"):
        if not is_admin_or_superadmin(uid): return
        name = data.split(":", 1)[1]
        t    = load_topic(name)
        if not t: return
        if not can_manage_topic(t, uid, q.from_user.username):
            await q.answer("❌ Ruxsat yo'q!", show_alert=True); return
        mq = get_admin_max_questions(uid)
        if len(t["questions"]) >= mq:
            await q.edit_message_text(f"❌ Limit: {mq} ta savol!"); return
        context.user_data.clear()
        context.user_data.update({"step": "addq_question", "topic_name": name})
        await q.edit_message_text(
            f"📝 *{t['emoji']} {name}* — savol qo'shish\n\nSavol matnini yozing:",
            parse_mode="Markdown")
        return

    if data == "bulkq_more":
        if not is_admin_or_superadmin(uid): return
        tn    = context.user_data.get("topic_name", "?")
        t     = load_topic(tn)
        mq    = get_admin_max_questions(uid)
        rem   = mq - len(t["questions"]) if t else 0
        await q.edit_message_text(
            f"📥 Savollarni yuboring ({rem} ta qolgan):\n\n`apple - olma`",
            parse_mode="Markdown")
        return

    if data == "addq_alt":
        if not is_admin_or_superadmin(uid): return
        context.user_data["step"] = "addq_alt_text"
        await q.edit_message_text("✏️ Alternativ javobni yozing:", parse_mode="Markdown")
        return

    if data == "addq_media":
        if not is_admin_or_superadmin(uid): return
        context.user_data["step"] = "addq_media_waiting"
        await q.edit_message_text(
            "🖼 *Rasm, video, GIF yoki stiker yuboring:*\n_(o'tkazish: /skip)_",
            parse_mode="Markdown")
        return

    if data == "addq_save_nomedia":
        if not is_admin_or_superadmin(uid): return
        context.user_data.update({"q_media_type": "none", "q_file_id": None})
        await _save_q(update, context)
        return

    if data == "addq_continue":
        if not is_admin_or_superadmin(uid): return
        context.user_data["step"] = "addq_question"
        await q.edit_message_text("📝 Keyingi savol matnini yozing:", parse_mode="Markdown")
        return

    if data == "addq_finish":
        if not is_admin_or_superadmin(uid): return
        tn = context.user_data.get("topic_name", "?")
        context.user_data.clear()
        await q.edit_message_text(
            f"✅ *Tugatildi!*\n`/listtopics` bilan ko'ring.",
            parse_mode="Markdown")
        return

    # ── deltopic ──
    if data.startswith("deltopic:"):
        if not is_superadmin(uid): return
        name = data.split(":", 1)[1]
        p    = topic_path(name)
        if os.path.exists(p):
            os.remove(p)
        for g in games.values():
            if g.get("topic") == name:
                g["active"] = False
        await q.edit_message_text(f"🗑 *{name}* o'chirildi.", parse_mode="Markdown")
        return

    if data == "deltopic_no":
        await q.edit_message_text("❌ Bekor qilindi.")
        return

    # ── setprize ──
    if data.startswith("setprize_topic:"):
        if not is_superadmin(uid): return
        name = data.split(":", 1)[1]
        context.user_data.clear()
        context.user_data.update({"step": "setprize_waiting", "topic_name": name})
        await q.edit_message_text(
            f"🏆 *{name}* uchun sovrinni yuboring _(rasm, GIF yoki stiker)_:",
            parse_mode="Markdown")
        return

    # ── listgames stop ──
    if data.startswith("stopgame_ask:"):
        if not is_superadmin(uid): return
        cid = int(data.split(":")[1])
        g   = games.get(cid)
        if not g or not g.get("active"):
            await q.edit_message_text("⚠️ Bu o'yin allaqachon tugagan.")
            return
        kb = InlineKeyboardMarkup([[
            IKB("✅ Ha, to'xtat", callback_data=f"stopgame_yes:{cid}"),
            IKB("❌ Bekor",       callback_data="stopgame_cancel"),
        ]])
        await q.edit_message_text(
            f"⚠️ `{cid}` — *{g['emoji']}{g['topic']}* to'xtatilsinmi?",
            parse_mode="Markdown", reply_markup=kb)
        return

    if data.startswith("stopgame_yes:"):
        if not is_superadmin(uid): return
        cid = int(data.split(":")[1])
        g   = games.get(cid)
        if g:
            g["active"] = False; g["current"] = None; g["waiting"] = False
            try:
                await context.bot.send_message(
                    cid, "⏹ *O'yin superadmin tomonidan to'xtatildi.*",
                    parse_mode="Markdown")
            except Exception:
                pass
        await q.edit_message_text(f"✅ `{cid}` o'yini to'xtatildi.", parse_mode="Markdown")
        return

    if data == "stopgame_cancel":
        await q.edit_message_text("❌ Bekor qilindi.")
        return

# ══════════════════════════════════════════════════════
#  CHAT MEMBER
# ══════════════════════════════════════════════════════

async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.my_chat_member
    if not m:
        return
    chat   = m.chat
    status = m.new_chat_member.status
    if status in ("administrator", "member"):
        if chat.type in ("channel", "group", "supergroup"):
            register_chat(chat)
    elif status in ("kicked", "left"):
        unregister_chat(chat.id)

# ══════════════════════════════════════════════════════
#  MAIN  (webhook rejimi — Render + UptimeRobot)
# ══════════════════════════════════════════════════════

async def run_bot():
    if not WEBHOOK_URL:
        raise SystemExit(
            "❌ WEBHOOK_URL topilmadi! Render → Environment'da "
            "WEBHOOK_URL=https://<app-nomi>.onrender.com kabi qo'shing "
            "(yoki Render avtomatik beradigan RENDER_EXTERNAL_URL ishlatiladi).")

    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    cmds = [
        ("start",           cmd_start),
        ("contact",         cmd_contact),
        ("cancel",          cmd_cancel),
        ("newtopic",        cmd_newtopic),
        ("listtopics",      cmd_listtopics),
        ("deletetopic",     cmd_deletetopic),
        ("setprize",        cmd_setprize),
        ("edittopicaccess", cmd_edittopicaccess),
        ("addq",            cmd_addq),
        ("bulkq",           cmd_bulkq),
        ("listgames",       cmd_listgames),
        ("newgame",         cmd_newgame),
        ("endgame",         cmd_endgame),
        ("scores",          cmd_scores),
        ("del",             cmd_del),
        ("skip",            cmd_skip),
        ("done",            cmd_done),
        ("addadmin",        cmd_addadmin),
        ("removeadmin",     cmd_removeadmin),
        ("listadmins",      cmd_listadmins),
        ("editadmin",       cmd_editadmin),
        ("setdisplayname",  cmd_setdisplayname),
        ("sendas",          cmd_sendas),
        ("requireadmin",    cmd_requireadmin),
        ("addbadword",      cmd_addbadword),
        ("addsevereword",   cmd_addsevereword),
        ("addwarning",      cmd_addwarning),
        ("listbadwords",    cmd_listbadwords),
        ("removebadword",   cmd_removebadword),
        ("removewarning",   cmd_removewarning),
        ("broadcast",       cmd_broadcast),
        ("export",          cmd_export),
        ("restore",         cmd_restore),
    ]
    for name, handler in cmds:
        app.add_handler(CommandHandler(name, handler))

    app.add_handler(CallbackQueryHandler(callback_handler))

    # Guruh xabarlarini track qilish (barcha turlar, /del uchun) — group=0
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & ~filters.COMMAND,
        group_tracker,
    ), group=0)

    # Guruh komandalarini ham track qilish
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & filters.COMMAND,
        group_tracker,
    ), group=0)

    # Media handler (private) — group=1
    app.add_handler(MessageHandler(
        (filters.PHOTO | filters.VIDEO | filters.ANIMATION |
         filters.Sticker.ALL | filters.Document.ALL |
         filters.AUDIO | filters.VOICE | filters.VIDEO_NOTE)
        & ~filters.COMMAND,
        handle_media,
    ), group=1)

    # Text handler — group=1
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_text,
    ), group=1)

    # Chat member
    app.add_handler(ChatMemberHandler(
        handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    await app.initialize()

    # ── Avtomatik tiklash (Render kabi vaqtinchalik diskdan keyin) ──
    logger.info("Zahiradan avtomatik tiklash tekshirilmoqda...")
    await auto_restore_on_startup(app.bot)

    # ── HTTP server (webhook + health-check) ──
    async def telegram_webhook(request: web.Request) -> web.Response:
        if WEBHOOK_SECRET:
            secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if secret != WEBHOOK_SECRET:
                return web.Response(status=403, text="forbidden")
        try:
            data = await request.json()
        except Exception:
            return web.Response(status=400, text="bad request")
        update = Update.de_json(data=data, bot=app.bot)
        await app.update_queue.put(update)
        return web.Response()

    async def health(request: web.Request) -> web.Response:
        # Render health-check va UptimeRobot shu yerga GET so'rov yuboradi —
        # bu botni "uxlab qolishdan" saqlaydi.
        return web.Response(text="🤖 Lang Bot ishlamoqda")

    web_app = web.Application()
    web_app.router.add_get("/", health)
    web_app.router.add_get("/health", health)
    web_app.router.add_post(f"/{WEBHOOK_PATH}", telegram_webhook)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"HTTP server ishga tushdi: 0.0.0.0:{PORT}")

    # ── Telegramga webhookni o'rnatish ──
    webhook_url = f"{WEBHOOK_URL}/{WEBHOOK_PATH}"
    await app.bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET or None,
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )
    logger.info(f"Webhook o'rnatildi: {webhook_url}")

    # Kunlik 00:00 export (Toshkent vaqti)
    if app.job_queue:
        app.job_queue.run_daily(
            daily_export_job,
            time=dt_time(0, 0, 0, tzinfo=TZ),
            name="daily_export",
        )
        logger.info("Daily export scheduled: 00:00 Tashkent")
    else:
        logger.warning("JobQueue not available! "
                       "Install: pip install 'python-telegram-bot[job-queue]==21.9'")

    await app.start()
    logger.info("Bot ishga tushdi (webhook rejimi).")

    try:
        await asyncio.Event().wait()  # to'xtatilguncha kutib turish
    finally:
        logger.info("Bot to'xtatilmoqda...")
        await app.stop()
        await app.shutdown()
        await runner.cleanup()


def main():
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
