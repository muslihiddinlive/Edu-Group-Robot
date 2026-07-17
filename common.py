"""
Avtomatik generatsiya qilingan modul — asl bot.py/core.py'dan ajratildi.
"""
import os
import io
import re
import json
import random
import logging
import asyncio
from datetime import datetime, timedelta, timezone, time as dt_time
from zoneinfo import ZoneInfo

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardButton as IKB, InlineKeyboardMarkup,
    LabeledPrice, BotCommand, BotCommandScopeChat, BotCommandScopeDefault,
    ReactionTypeEmoji, ReactionTypeCustomEmoji, ChatPermissions, ReplyKeyboardMarkup, ReplyKeyboardRemove,
)
from telegram.constants import ParseMode, ChatAction, ReactionEmoji
from telegram.error import TelegramError, BadRequest, Forbidden
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler,
    PreCheckoutQueryHandler, CallbackQueryHandler, ContextTypes, filters,
)

TZ = timezone(timedelta(hours=5))  # UTC+5 — Toshkent

logger = logging.getLogger(__name__)

def _require_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise SystemExit(f"❌ '{key}' ENV topilmadi!")
    return val

BOT_TOKEN      = _require_env("BOT_TOKEN")

SUPERADMIN     = int(_require_env("SUPERADMIN_ID"))

SUPERGROUP_ID_ENV = int(os.environ.get("SUPERGROUP_ID", "0"))

PORT           = int(os.environ.get("PORT", "8080"))

WEBHOOK_URL    = (os.environ.get("WEBHOOK_URL")
                  or os.environ.get("RENDER_EXTERNAL_URL")
                  or "").rstrip("/")

WEBHOOK_PATH   = f"webhook/{BOT_TOKEN}"

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

TOPICS_DIR     = "topics"

ADMINS_FILE    = "admins.json"

CHATS_FILE     = "chats.json"

CONFIG_FILE    = "config.json"

BADWORDS_FILE  = "badwords.json"

USERS_FILE     = "users.json"

INCIDENTS_FILE = "incidents.json"

PAYMENTS_FILE  = "processed_payments.json"

def _jload(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        # Fayl yarim yozilgan holda qolgan bo'lishi mumkin (masalan process
        # yozish paytida o'chib qolsa). Butun botni yiqitish o'rniga bo'sh
        # dict qaytaramiz va logga yozamiz — auto-restore keyingi ishga
        # tushishda Telegram'dagi backup'dan tiklaydi.
        logger.error(f"JSON o'qishda xato ({path}): {e} — bo'sh dict qaytarilmoqda")
        return {}

def _jsave(path: str, data):
    # Atomik yozish: avval vaqtinchalik faylga yozamiz, keyin os.replace()
    # bilan almashtiramiz. Bu process yozish o'rtasida o'chib qolsa ham
    # (Render restart/OOM/sleep-wake) asosiy fayl hech qachon yarim
    # yozilgan holatda qolmasligini kafolatlaydi.
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    from backup import mark_changed
    mark_changed()

def load_config() -> dict:  return _jload(CONFIG_FILE)

def save_config(d: dict):   _jsave(CONFIG_FILE, d)

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

def load_admins() -> dict:  return _jload(ADMINS_FILE)

def is_bot_admin(uid: int) -> bool:
    return str(uid) in load_admins()

def is_admin_or_superadmin(uid: int) -> bool:
    return uid == SUPERADMIN or is_bot_admin(uid)

def mdesc(s) -> str:
    """Legacy Markdown (parse_mode='Markdown') uchun maxsus belgilarni
    escape qiladi. Foydalanuvchi kiritgan ixtiyoriy matnni (masalan
    display_name yoki topic nomi) Markdown xabarlariga xavfsiz qo'yish
    uchun ishlatiladi — aks holda bitta toq '_' yoki '*' butun xabarni
    'Can't parse entities' xatosi bilan qulatib yuborishi mumkin."""
    if s is None:
        return ""
    s = str(s)
    for ch in ("\\", "_", "*", "`", "["):
        s = s.replace(ch, "\\" + ch)
    return s

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
        uname = f"@{mdesc(user.username)}" if user.username else "—"
        lang = getattr(user, "language_code", "—") or "—"
        is_bot = "✅" if getattr(user, "is_bot", False) else "❌"
        is_premium = "✅" if getattr(user, "is_premium", False) else "❌"
    elif u_data:
        fn   = u_data.get("first_name", "—")
        ln   = u_data.get("last_name",  "—")
        uname = f"@{mdesc(u_data['username'])}" if u_data.get("username") else "—"
        lang = u_data.get("language_code", "—") or "—"
        is_bot = "—"
        is_premium = "—"
    else:
        return f"👤 ID: `{uid}`\n_(Ma'lumot topilmadi)_"

    from tariffs import get_user_tarif, TARIF_NAMES  # aylanma importdan qochish uchun
    from game import get_user_topic_names
    tarif = get_user_tarif(uid) if uid else "—"
    tarif_name = TARIF_NAMES.get(tarif, tarif)
    topic_names  = get_user_topic_names(uid) if uid else []
    topics_count = len(topic_names)
    topics_line  = ", ".join(f"`{mdesc(n)}`" for n in topic_names) if topic_names else "—"
    ref_count = u_data.get("referral_count", 0) if u_data else "—"
    joined = u_data.get("joined_at", "—") if u_data else "—"
    ref_by = u_data.get("referral_by", "—") if u_data else "—"
    subscribed = "✅" if u_data and u_data.get("is_subscribed") else "❌"

    return (
        f"👤 *Foydalanuvchi ma'lumotlari:*\n\n"
        f"🆔 ID: `{uid}`\n"
        f"💬 Chat ID: `{uid}`\n"
        f"👤 Ism: *{mdesc(fn)}*\n"
        f"👤 Familiya: *{mdesc(ln)}*\n"
        f"📛 Username: {uname}\n"
        f"🌐 Til: `{lang}`\n"
        f"🤖 Bot: {is_bot}\n"
        f"⭐ Telegram Premium: {is_premium}\n"
        f"📅 Qo'shilgan: `{joined}`\n"
        f"💎 Tarif: *{tarif_name}*\n"
        f"📁 Topiclar ({topics_count}): {topics_line}\n"
        f"👥 Referallar: {ref_count}\n"
        f"🔗 Referral by: `{ref_by}`\n"
        f"📢 Obuna: {subscribed}"
    )

def is_superadmin(uid: int) -> bool:
    return uid == SUPERADMIN

async def _require_bot_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return True
    from admin import get_group_setting  # aylanma importdan qochish uchun
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

async def send_named_reaction(bot, chat_id: int, msg_id: int, emoji: str):
    """Berilgan standart Telegram reaksiya emojisi bilan reaksiya bosadi."""
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=msg_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
            is_big=False,
        )
    except Exception as e:
        logger.debug(f"Named reaction error: {e}")

async def send_custom_reaction(bot, chat_id: int, msg_id: int, custom_emoji_id: str):
    """Premium/animatsion custom_emoji_id bo'yicha reaksiya bosadi.
    Eslatma: custom emoji reaksiyalar faqat Telegram Premium bilan bog'liq
    guruh sozlamalarida ishlaydi."""
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=msg_id,
            reaction=[ReactionTypeCustomEmoji(custom_emoji_id=custom_emoji_id)],
            is_big=False,
        )
    except Exception as e:
        logger.debug(f"Custom reaction error: {e}")

def get_forum_topics() -> dict:
    return load_config().get("forum_topics", {})

def save_forum_topics(data: dict):
    cfg = load_config()
    cfg["forum_topics"] = data
    save_config(cfg)

async def ensure_forum_topic(bot, name: str, key: str, icon_color: int = 0x6FB9F0) -> int | None:
    """Topic mavjud bo'lmasa yaratadi, ID qaytaradi."""
    from backup import get_supergroup_id  # aylanma importdan qochish uchun
    gid = get_supergroup_id()
    if not gid:
        return None
    ft = get_forum_topics()
    if key in ft:
        return ft[key]
    try:
        result = await bot.create_forum_topic(
            chat_id=gid,
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

