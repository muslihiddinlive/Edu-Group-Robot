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
import game as _game
globals().update({k: v for k, v in vars(_game).items() if not k.startswith('__')})

MAX_TOPICS     = 10

MAX_QUESTIONS  = 1000

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

DEFAULT_PRICES = {
    TARIF_PLUS:    25,
    TARIF_PREMIUM: 50,
    TARIF_VIP:     500,
}

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

FREE_MAX_TOPIC_REFERRAL = 5

FREE_REFERRAL_PER_TOPIC = 3  # har 3 referalda +1

def load_processed_payments() -> set:
    d = _jload(PAYMENTS_FILE)
    return set(d.get("charge_ids", []))

def is_payment_processed(charge_id: str) -> bool:
    return charge_id in load_processed_payments()

def mark_payment_processed(charge_id: str):
    ids = load_processed_payments()
    ids.add(charge_id)
    # Cheksiz o'smasligi uchun oxirgi 5000 tasini saqlaymiz
    _jsave(PAYMENTS_FILE, {"charge_ids": list(ids)[-5000:]})

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

async def cmd_setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tarif narxini o'zgartirish. /setprice plus 30"""
    if not is_superadmin(update.effective_user.id):
        return
    args = context.args
    if len(args) < 2:
        from keyboards import _tarif_admin_kb  # aylanma importdan qochish uchun
        prices = get_tarif_prices()
        await update.message.reply_text(
            "💰 *Tarif narxlari:*\n\n"
            f"✨ PLUS: {prices[TARIF_PLUS]} ⭐\n"
            f"💎 Premium: {prices[TARIF_PREMIUM]} ⭐\n"
            f"👑 VIP: {prices[TARIF_VIP]} ⭐\n\n"
            "Format: `/setprice plus 30`\n"
            "Tariflar: `plus`, `premium`, `vip`",
            parse_mode="Markdown",
            reply_markup=_tarif_admin_kb())
        return
    tarif_key = args[0].lower()
    tarif_map = {"plus": TARIF_PLUS, "premium": TARIF_PREMIUM, "vip": TARIF_VIP}
    if tarif_key not in tarif_map:
        await update.message.reply_text("❌ Tariflar: `plus`, `premium`, `vip`",
                                        parse_mode="Markdown")
        return
    try:
        price = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ Narx raqam bo'lishi kerak.")
        return
    cfg = load_config()
    cfg.setdefault("tarif_prices", {})[tarif_map[tarif_key]] = price
    save_config(cfg)
    await update.message.reply_text(
        f"✅ *{TARIF_NAMES[tarif_map[tarif_key]]}* narxi: *{price} ⭐*",
        parse_mode="Markdown")

async def cmd_setchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Obuna kanali o'rnatish. /setchannel -100123456"""
    if not is_superadmin(update.effective_user.id):
        return
    args = context.args
    if not args:
        cur = get_sub_channel()
        await update.message.reply_text(
            f"📢 *Obuna kanali:* `{cur or 'belgilanmagan'}`\n\n"
            "Format: `/setchannel -100123456789`\n"
            "O'chirish: `/setchannel 0`",
            parse_mode="Markdown")
        return
    try:
        cid = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Kanal ID raqam bo'lishi kerak.")
        return
    if cid == 0:
        set_sub_channel(None)
        await update.message.reply_text("✅ Obuna kanali o'chirildi.")
    else:
        set_sub_channel(cid)
        await update.message.reply_text(
            f"✅ Obuna kanali: `{cid}`", parse_mode="Markdown")

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

async def update_tarif_topic_json(bot, tarif: str):
    """Tarif topic'idagi userlar JSON'ini yangilaydi (max 20 user/xabar)."""
    gid = get_supergroup_id()
    if not gid:
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
                chat_id=gid,
                document=buf,
                caption=f"📋 {TARIF_NAMES.get(tarif)} | {len(chunk)} ta user | chunk {i//chunk_size+1}",
                message_thread_id=tid,
            )
            # Oxirgi chunkni pin qilamiz
            if i + chunk_size >= len(tarif_users):
                try:
                    await bot.unpin_all_chat_messages(gid)
                except Exception:
                    pass
                try:
                    await bot.pin_chat_message(
                        gid, sent.message_id, disable_notification=True)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Tarif topic JSON yuborib bo'lmadi ({tarif}): {e}")

async def cmd_togglereaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Superadmin: guruhlarda superadmin xabarlariga ⚡ reaksiya
    bosishni yoqadi/o'chiradi."""
    if not is_superadmin(update.effective_user.id):
        return
    cfg = load_config()
    cur = cfg.get("lightning_reaction_enabled", True)
    cfg["lightning_reaction_enabled"] = not cur
    save_config(cfg)
    state = "✅ yoqildi" if not cur else "❌ o'chirildi"
    await update.message.reply_text(f"⚡ Superadmin reaksiyasi {state}.")

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
        btns = [[IKB(f"{t['emoji']} {t['name']} ({len(t['questions'])} savol)",
                     callback_data=f"topic_detail:{t['name']}:u")] for t in topics]
        btns.append([IKB("⬅️ Orqaga", callback_data="u:back")])
        await q.edit_message_text(
            f"📋 *Topiclaringiz ({len(topics)} ta):*\n\nTafsilot uchun tanlang:",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))
        return

    if data == "u:newtopic":
        if not is_admin_or_superadmin(uid):
            from admin import count_admin_topics  # aylanma importdan qochish uchun
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
            "➕ *Yangi topic*\n\nTopic nomini yuboring _(faqat harf, raqam yoki pastki chiziq, masalan: `english`)_",
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

    tarif = parts[1]
    try:
        payload_uid = int(parts[2])
    except ValueError:
        payload_uid = uid

    # --- Duplicate-himoya: Telegram webhook ba'zan bitta update'ni 2 marta
    # yuborishi mumkin (masalan sleep/wake payti). Bir xil to'lovni qayta
    # ishlab, tarifni ikki marta faollashtirib yubormaslik uchun charge_id
    # bo'yicha tekshiramiz. ---
    charge_id = payment.telegram_payment_charge_id
    if is_payment_processed(charge_id):
        logger.warning(f"Takroriy successful_payment e'tiborsiz qoldirildi: {charge_id}")
        return
    mark_payment_processed(charge_id)

    # --- Har doim HAQIQIY to'lovchiga (update.effective_user) kredit
    # beramiz, payload'dagi ID'ga emas. Sabab: agar invoice xabari boshqa
    # userga forward qilinib to'lansa, payload_uid asl "buyurtmachi"ni
    # ko'rsatadi, lekin u ro'yxatdan o'tmagan bo'lsa users[k] KeyError
    # bilan yiqilardi. Haqiqiy to'lovchi esa har doim to'liq Telegram User
    # obyektiga ega bo'lgani uchun xavfsiz ro'yxatdan o'tkazish mumkin. ---
    if payload_uid != uid:
        logger.warning(
            f"To'lov: payload_uid ({payload_uid}) haqiqiy to'lovchidan "
            f"({uid}) farq qiladi — kredit haqiqiy to'lovchiga beriladi.")

    # Tarifni beramiz (30 kun)
    users = load_users()
    k     = str(uid)
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

