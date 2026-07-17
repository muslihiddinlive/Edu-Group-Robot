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
import common as _common
globals().update({k: v for k, v in vars(_common).items() if not k.startswith('__')})
import admin as _admin
globals().update({k: v for k, v in vars(_admin).items() if not k.startswith('__')})
import game as _game
globals().update({k: v for k, v in vars(_game).items() if not k.startswith('__')})
import tariffs as _tariffs
globals().update({k: v for k, v in vars(_tariffs).items() if not k.startswith('__')})

CONTROL_GROUP_ID = int(os.environ.get("CONTROL_GROUP_ID", "0")) or None

CONTROL_TAG      = "BOT-CONTROL-DATA::"

_SUPERGROUP_ID_CACHE: int | None = None  # runtime kesh (tez, sync o'qish uchun)

def get_supergroup_id() -> int:
    """DB/backup guruh ID'si. Xotiradagi keshdan o'qiydi (tez, sync) — bu
    kesh bot ishga tushganda (auto_restore_on_startup) yoki /setgroup
    orqali (set_supergroup_id) control guruhdan to'ldiriladi/yangilanadi.
    Hali sozlanmagan bo'lsa, eski ENV fallback'ga qaytadi (orqaga moslik)."""
    if _SUPERGROUP_ID_CACHE:
        return _SUPERGROUP_ID_CACHE
    return SUPERGROUP_ID_ENV

async def load_control_data(bot) -> dict:
    """CONTROL_GROUP_ID ichidagi pin xabardan konfiguratsiya o'qiydi."""
    if not CONTROL_GROUP_ID:
        return {}
    try:
        chat   = await bot.get_chat(CONTROL_GROUP_ID)
        pinned = chat.pinned_message
        if not pinned or not pinned.text or not pinned.text.startswith(CONTROL_TAG):
            return {}
        return json.loads(pinned.text[len(CONTROL_TAG):])
    except Exception as e:
        logger.warning(f"Control group o'qib bo'lmadi: {e}")
        return {}

async def save_control_data(bot, data: dict) -> bool:
    """Control guruhdagi pin xabarni yangilaydi (yo'q bo'lsa yaratadi+pin qiladi)."""
    if not CONTROL_GROUP_ID:
        logger.warning("CONTROL_GROUP_ID yo'q — control ma'lumot faqat joriy "
                        "jarayon xotirasida qoladi, disk tozalansa yo'qoladi!")
        return False
    text = CONTROL_TAG + json.dumps(data, ensure_ascii=False)
    try:
        chat   = await bot.get_chat(CONTROL_GROUP_ID)
        pinned = chat.pinned_message
        if pinned and pinned.text and pinned.text.startswith(CONTROL_TAG):
            await bot.edit_message_text(
                chat_id=CONTROL_GROUP_ID, message_id=pinned.message_id, text=text)
        else:
            sent = await bot.send_message(CONTROL_GROUP_ID, text)
            await bot.pin_chat_message(
                CONTROL_GROUP_ID, sent.message_id, disable_notification=True)
        return True
    except Exception as e:
        logger.error(f"Control ma'lumotni saqlab bo'lmadi: {e}")
        return False

async def set_supergroup_id(bot, gid: int | None) -> None:
    """Superadmin /setgroup orqali DB guruhni o'rnatadi/almashtiradi. Qiymat
    (1) darhol xotiradagi keshga, (2) control guruhdagi pin xabarga
    yoziladi — shu bilan Render qayta deploy/disk tozalashidan
    ta'sirlanmaydi (config.json'dan farqli o'laroq)."""
    global _SUPERGROUP_ID_CACHE
    _SUPERGROUP_ID_CACHE = gid
    ctrl = await load_control_data(bot)
    if gid:
        ctrl["supergroup_id"] = gid
    else:
        ctrl.pop("supergroup_id", None)
        # guruh uzilganda eski backup ko'rsatkichlari endi mos emas
        ctrl.pop("last_backup_msg_id", None)
        ctrl.pop("last_backup_thread_id", None)
    await save_control_data(bot, ctrl)

EXPORT_VERSION  = 5

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

async def get_backup_topic_id(bot) -> int | None:
    return await ensure_forum_topic(bot, "📦 Backup", "backup", 0xFF6C6C)

async def do_export(bot, to_backup_topic: bool = True) -> bool:
    """Export qiladi — faqat supergroup backup topic'iga.
    message_id endi CONTROL GROUP'da saqlanadi (config.json'da EMAS),
    shunda Render disk tozalansa ham restore ishlay oladi."""
    gid = get_supergroup_id()
    if not gid:
        logger.warning("Export: DB guruh ulanmagan! /setgroup bilan ulang.")
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
        "incidents": load_incidents(),
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
            chat_id=gid,
            document=buf,
            caption=cap,
            parse_mode="Markdown",
            message_thread_id=tid,
        )
        # message_id endi CONTROL GROUP'ga saqlanadi (local disk emas!)
        ctrl = await load_control_data(bot)
        ctrl["supergroup_id"]         = gid
        ctrl["last_backup_msg_id"]    = sent.message_id
        ctrl["last_backup_thread_id"] = tid
        await save_control_data(bot, ctrl)
        # Supergroup'da pin qilamiz
        try:
            await bot.pin_chat_message(
                gid, sent.message_id, disable_notification=True)
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
        if "badwords"  in data: save_badwords(data["badwords"])
        if "incidents" in data: save_incidents(data["incidents"])
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

async def cmd_setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """DB/backup guruhni ulash yoki almashtirish.
    - Guruhning ICHIDA argumentsiz yuborilsa — o'sha guruh ulanadi.
    - Yoki: /setgroup -100123456789
    - O'chirish: /setgroup 0"""
    if not is_superadmin(update.effective_user.id):
        return
    args = context.args
    chat = update.effective_chat

    if not args:
        if chat.type in ("group", "supergroup"):
            gid = chat.id
        else:
            cur = get_supergroup_id()
            note = ("\n\n💾 Bu ma'lumot control guruhda saqlanadi — qayta "
                     "deploy bo'lsa ham yo'qolmaydi." if CONTROL_GROUP_ID else
                     "\n\n⚠️ CONTROL_GROUP_ID sozlanmagan — bu qiymat faqat "
                     "joriy jarayon xotirasida turadi, qayta deploy bo'lsa "
                     "yo'qoladi!")
            await update.message.reply_text(
                f"🔗 *Hozirgi DB guruh:* `{cur or 'ulanmagan'}`\n\n"
                "Ulash uchun ikki yo'l bor:\n"
                "1️⃣ Kerakli guruhning ICHIDA shunchaki `/setgroup` yuboring\n"
                "2️⃣ Yoki: `/setgroup -100123456789`\n\n"
                f"❌ Uzish: `/setgroup 0`{note}",
                parse_mode="Markdown")
            return
    else:
        try:
            gid = int(args[0])
        except ValueError:
            await update.message.reply_text("❌ Guruh ID raqam bo'lishi kerak.")
            return

    if gid == 0:
        await set_supergroup_id(context.bot, None)
        await update.message.reply_text("✅ DB guruh uzildi.")
        return

    # Bot shu guruhda admin ekanligini va supergroup ekanligini tekshiramiz
    try:
        target_chat = await context.bot.get_chat(gid)
    except Exception as e:
        await update.message.reply_text(
            f"❌ Bu guruhga yeta olmadim:\n`{e}`\n\n"
            "Bot o'sha guruhga qo'shilganiga va admin ekanligiga ishonch hosil qiling.",
            parse_mode="Markdown")
        return

    is_forum = getattr(target_chat, "is_forum", False)
    try:
        bm = await context.bot.get_chat_member(gid, context.bot.id)
        bot_is_admin = bm.status in ("administrator", "creator")
    except Exception:
        bot_is_admin = False

    await set_supergroup_id(context.bot, gid)

    warn = ""
    if not bot_is_admin:
        warn += "\n⚠️ Bot bu guruhda *admin emas* — backup/topic funksiyalari ishlamaydi!"
    if target_chat.type == "supergroup" and not is_forum:
        warn += ("\n⚠️ Bu supergroup, lekin *Topics (Forum)* rejimi o'chiq — "
                 "guruh sozlamalaridan yoqing, aks holda VIP/Premium/Backup "
                 "topic'lari yaratilmaydi.")
    elif target_chat.type == "group":
        warn += ("\n⚠️ Bu oddiy guruh — Topics (Forum) rejimi faqat "
                 "supergroup'larda ishlaydi, shu sabab backup/tarif topic'lari "
                 "yaratilmaydi (lekin export/restore funksiyasi baribir ishlaydi).")

    await update.message.reply_text(
        f"✅ *DB guruh ulandi!*\n\n"
        f"🏷 Nomi: {mdesc(target_chat.title or '—')}\n"
        f"🆔 ID: `{gid}`{warn}\n\n"
        "Zaxira nusxa olib ko'ramiz...",
        parse_mode="Markdown")

    ok = await do_export(context.bot, to_backup_topic=True)
    await update.message.reply_text(
        "✅ Sinov zaxira nusxasi muvaffaqiyatli yuborildi!" if ok
        else "❌ Zaxira nusxa olishda xatolik — botga tegishli huquqlarni tekshiring.")

async def auto_restore_on_startup(bot) -> None:
    """Bot ishga tushganda (deploy/redeploy yoki restart — xotira tozalanganda)
    CONTROL_GROUP_ID'dagi pin xabardan qaysi DB guruh ishlatilishini va
    oxirgi backup qayerdaligini o'qib, avtomatik tiklaydi. Bu ma'lumot
    to'liq Telegram'da turgani uchun Render disk tozalansa ham yo'qolmaydi."""
    global _SUPERGROUP_ID_CACHE
    ctrl = await load_control_data(bot)
    if ctrl.get("supergroup_id"):
        _SUPERGROUP_ID_CACHE = ctrl["supergroup_id"]

    gid = get_supergroup_id()
    if not gid:
        logger.warning(
            "DB guruh hali sozlanmagan! Superadmin /setgroup <id> "
            "bilan o'rnatishi kerak.")
        return

    msg_id = ctrl.get("last_backup_msg_id")
    if not msg_id:
        logger.info("Auto-restore: oldingi backup topilmadi — bo'sh boshlanadi.")
        return

    fwd = None
    try:
        # Backup xabarini o'ziga forward qilib, document'ini olamiz
        fwd = await bot.forward_message(
            chat_id=gid,
            from_chat_id=gid,
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
                await bot.delete_message(gid, fwd.message_id)
            except Exception:
                pass

    if not data.get("export_version"):
        logger.warning("Auto-restore: noto'g'ri format — export_version yo'q.")
        return
    ac, cc, tc, uc = await apply_restore_data(data)
    logger.info(f"✅ Auto-restore OK: {ac} admin, {cc} chat, {tc} topic, {uc} user")

async def _pin_media(bot, mt: str, fi: str, caption: str = "") -> str | None:
    """Media faylni supergroup backup topic'iga yuborib, barqaror file_id oladi."""
    gid = get_supergroup_id()
    if not gid:
        return fi  # DB guruh ulanmagan bo'lsa original file_id qaytaramiz
    tid = await get_backup_topic_id(bot)
    try:
        if mt == "photo":
            s = await bot.send_photo(gid, fi, caption=caption[:1024],
                                     message_thread_id=tid)
            return s.photo[-1].file_id
        if mt == "video":
            s = await bot.send_video(gid, fi, caption=caption[:1024],
                                     message_thread_id=tid)
            return s.video.file_id
        if mt == "gif":
            s = await bot.send_animation(gid, fi, caption=caption[:1024],
                                         message_thread_id=tid)
            return s.animation.file_id
        if mt == "sticker":
            s = await bot.send_sticker(gid, fi, message_thread_id=tid)
            return s.sticker.file_id
    except Exception as e:
        logger.warning(f"pin_media ({mt}): {e}")
    return fi

async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    await update.message.reply_text("📦 Export qilinmoqda...")
    ok = await do_export(context.bot, to_backup_topic=True)
    await update.message.reply_text(
        "✅ Export muvaffaqiyatli!" if ok else "❌ Export xato!")

async def cmd_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    context.user_data["step"] = "restore_waiting"
    await update.message.reply_text(
        "♻️ *Ma'lumotlarni tiklash*\n\nExport JSON faylni yuboring.\n⏹ /cancel",
        parse_mode="Markdown")

