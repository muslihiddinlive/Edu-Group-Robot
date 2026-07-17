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
from aiohttp import web
import common, keyboards, backup, tariffs, game, admin
import common as _common
globals().update({k: v for k, v in vars(_common).items() if not k.startswith('__')})
import keyboards as _keyboards
globals().update({k: v for k, v in vars(_keyboards).items() if not k.startswith('__')})
import backup as _backup
globals().update({k: v for k, v in vars(_backup).items() if not k.startswith('__')})
import tariffs as _tariffs
globals().update({k: v for k, v in vars(_tariffs).items() if not k.startswith('__')})
import game as _game
globals().update({k: v for k, v in vars(_game).items() if not k.startswith('__')})
import admin as _admin
globals().update({k: v for k, v in vars(_admin).items() if not k.startswith('__')})

logger = logging.getLogger(__name__)
PUBLIC_COMMANDS = [
    ("start",   "Botni ishga tushirish"),
    ("contact", "Superadminga murojaat qilish"),
    ("cancel",  "Joriy amalni bekor qilish"),
    ("newgame", "O'yin boshlash (standart/speed/langmode/admin)"),
    ("endgame", "O'yinni tugatish"),
    ("scores",  "O'yin natijalarini ko'rish"),
    ("skip",    "Savolni o'tkazib yuborish"),
]

ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    ("newtopic",        "Yangi savol-javob topic yaratish"),
    ("listtopics",      "Mavjud topiclar ro'yxati"),
    ("deletetopic",     "Topicni o'chirish"),
    ("setprize",        "Topic uchun sovrin belgilash"),
    ("edittopicaccess", "Topic ruxsatini o'zgartirish"),
    ("addq",            "Topicga savol qo'shish (bitta-bitta)"),
    ("bulkq",           "Topicga savollarni ommaviy qo'shish"),
    ("listgames",       "Faol o'yinlar ro'yxati"),
    ("del",             "Xabarni o'chirish"),
    ("done",            "Amalni yakunlash"),
    ("addadmin",        "Yangi admin qo'shish"),
    ("removeadmin",     "Adminni o'chirish"),
    ("listadmins",      "Adminlar ro'yxati"),
    ("editadmin",       "Admin ma'lumotini tahrirlash"),
    ("setdisplayname",  "Ko'rinadigan ismni o'rnatish"),
    ("getemojiid",      "Premium emoji ID'sini topish"),
    ("addchatadmin",    "Guruh/kanalga admin tayinlash"),
    ("sendas",          "Bot nomidan xabar yuborish"),
    ("requireadmin",    "Guruhda faqat admin ishlatishini talab qilish"),
    ("addbadword",      "Taqiqlangan so'z qo'shish"),
    ("addsacredname",   "Diний shaxs nomini qo'shish"),
    ("addsevereword",   "Og'ir taqiqlangan so'z qo'shish"),
    ("addwarning",      "Ogohlantirish matnini qo'shish"),
    ("listbadwords",    "Taqiqlangan so'zlar ro'yxati"),
    ("removebadword",   "Taqiqlangan so'zni o'chirish"),
    ("removewarning",   "Ogohlantirish matnini o'chirish"),
    ("broadcast",       "Barcha userlarga xabar yuborish"),
    ("export",          "Zaxira nusxa yaratish"),
    ("restore",         "Zaxiradan tiklash"),
    ("setprice",        "Tarif narxini o'rnatish"),
    ("setchannel",      "Majburiy obuna kanalini o'rnatish"),
    ("delmsgs",         "Foydalanuvchi xabarlarini o'chirish"),
    ("delbotmsg",       "Bot xabarini o'chirish"),
    ("togglereaction",  "Superadmin reaksiyasini yoqish/o'chirish"),
    ("createtopic",     "Forum topic yaratish"),
    ("setgroup",        "DB guruhni ulash/almashtirish"),
    ("userinfo",        "Foydalanuvchi ma'lumotini ko'rish"),
    ("listusers",       "Foydalanuvchilar ro'yxati"),
]

async def sync_bot_commands(bot, extra_uids=None) -> None:
    """Oddiy foydalanuvchilarga qisqa, superadmin va bot-adminlarga
    to'liq komandalar ro'yxatini ko'rsatadi (BotCommandScope orqali).
    BotFather'dagi /setcommands FAQAT umumiy (default) ro'yxatga ta'sir
    qiladi — bu funksiya uni ustidan chiqib, maxsus foydalanuvchilarga
    boshqacha menyu ko'rsatadi."""
    public_cmds = [BotCommand(c, d) for c, d in PUBLIC_COMMANDS]
    admin_cmds  = [BotCommand(c, d) for c, d in ADMIN_COMMANDS]
    try:
        await bot.set_my_commands(public_cmds, scope=BotCommandScopeDefault())
    except Exception as e:
        logger.warning(f"sync_bot_commands (default): {e}")

    targets = {SUPERADMIN}
    for uid_s in load_admins().keys():
        try:
            targets.add(int(uid_s))
        except (TypeError, ValueError):
            pass
    if extra_uids:
        targets.update(extra_uids)

    for uid in targets:
        try:
            await bot.set_my_commands(admin_cmds, scope=BotCommandScopeChat(chat_id=uid))
        except Exception as e:
            logger.debug(f"sync_bot_commands ({uid}): {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    chat = update.effective_chat
    step = context.user_data.get("step")
    text = (update.message.text or "").strip()

    if chat.type in ("group", "supergroup"):
        if await handle_dot_command(update, context):
            return
        if await handle_admin_mode_message(update, context):
            return
        await _check_answer(update, context)
        return

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
                await update.message.reply_text(f"✅ *{mdesc(name)}* guruhiga yuborildi!", parse_mode="Markdown")
            except Exception as e:
                await update.message.reply_text(f"❌ Yuborib bo'lmadi:\n`{e}`", parse_mode="Markdown")
        return

    if step == "contact_waiting" and not is_admin_or_superadmin(uid):
        await _relay(update, context)
        return

    if step == "broadcast_target" and is_superadmin(uid):
        if text in TARGET_KEYS:
            context.user_data["bc_target"] = TARGET_KEYS[text]
            context.user_data["step"]      = "broadcast_msg"
            await update.message.reply_text(
                "📤 *Reklama xabarini yuboring:*\n⏹ /cancel",
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

    # setprice step
    if step == "setprice_input" and is_superadmin(uid):
        tarif = context.user_data.pop("setprice_tarif", None)
        context.user_data.pop("step", None)
        if tarif:
            try:
                price = int(text)
                cfg = load_config()
                cfg.setdefault("tarif_prices", {})[tarif] = price
                save_config(cfg)
                await update.message.reply_text(
                    f"✅ *{TARIF_NAMES[tarif]}* narxi: *{price} ⭐*",
                    parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("❌ Raqam kiriting.")
        return

    # setchannel step
    if step == "setchannel_input" and is_superadmin(uid):
        context.user_data.pop("step", None)
        try:
            cid = int(text)
        except ValueError:
            await update.message.reply_text("❌ Kanal ID raqam bo'lishi kerak.")
            return
        if cid == 0:
            set_sub_channel(None)
            await update.message.reply_text("✅ Obuna kanali o'chirildi.")
        else:
            set_sub_channel(cid)
            await update.message.reply_text(f"✅ Obuna kanali: `{cid}`", parse_mode="Markdown")
        return

    # class topic yaratish
    if step == "create_class_topic" and is_superadmin(uid):
        context.user_data.pop("step", None)
        tid = await create_class_topic(context.bot, text)
        if tid:
            await update.message.reply_text(
                f"✅ Sinf topici yaratildi: *{text}*\nID: `{tid}`", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Topic yaratib bo'lmadi.")
        return

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
            await update.message.reply_text(f"⚠️ `{new_uid}` allaqachon admin!", parse_mode="Markdown")
            return
        max_tl = context.user_data.get("aa_max_tl", MAX_TOPICS)
        max_mq = context.user_data.get("aa_max_mq", MAX_QUESTIONS)
        context.user_data.update({"aa_uid": new_uid, "step": "addadmin_tlimit"})
        await update.message.reply_text(
            f"➕ *Yangi admin: `{new_uid}`*\n\n📁 *Topic limiti:*",
            parse_mode="Markdown",
            reply_markup=_aa_tlimit_kb(max_tl))
        return

    if step == "addadmin_dname" and is_superadmin(uid):
        name = None if text == "-" else text
        context.user_data["aa_dname"] = name
        context.user_data["step"]     = "addadmin_can_add"
        await update.message.reply_text(
            "🏷 Nom: *" + (name or "(yo'q)") + "*\n\nBu admin o'z adminlarini qo'sha oladimi?",
            parse_mode="Markdown",
            reply_markup=_aa_can_add_kb())
        return

    if step == "admreact_custom_wait" and is_superadmin(uid):
        target = context.user_data.pop("ar_target", None)
        context.user_data.pop("step", None)
        cid = text.strip()
        if not cid.isdigit():
            await update.message.reply_text(
                "❌ Custom emoji ID faqat raqamlardan iborat bo'lishi kerak. Bekor qilindi.")
            return
        adm = load_admins()
        if not target or str(target) not in adm:
            await update.message.reply_text("❌ Bu admin topilmadi.")
            return
        adm[str(target)]["reaction_custom_emoji_id"] = cid
        adm[str(target)].pop("reaction_emoji", None)
        save_admins(adm)
        await update.message.reply_text(
            f"✅ `{target}` uchun premium/animatsion reaksiya (ID: `{cid}`) belgilandi.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[IKB("⬅️ Admin sozlamalariga", callback_data=f"edit_adm:{target}")]]))
        return

    if step == "emojipack_waiting" and is_superadmin(uid):
        context.user_data.pop("step", None)
        pack_name = _extract_pack_name(text)
        if not pack_name:
            await update.message.reply_text(
                "❌ Pack nomini aniqlab bo'lmadi. Shunchaki nomining o'zini "
                "(masalan `developeremojis`) yoki to'liq havolani yuboring.",
                parse_mode="Markdown")
            return
        try:
            ss = await context.bot.get_sticker_set(pack_name)
        except Exception as e:
            await update.message.reply_text(f"❌ Pack topilmadi: {e}")
            return
        items = [{"id": s.custom_emoji_id, "glyph": s.emoji or "❓"}
                 for s in ss.stickers if getattr(s, "custom_emoji_id", None)]
        if not items:
            await update.message.reply_text(
                "❌ Bu pack'da custom emoji topilmadi (ehtimol bu oddiy "
                "stiker to'plami, emoji pack emas).")
            return
        context.user_data["epack_items"] = items
        context.user_data["epack_name"]  = pack_name
        text_out, kb = _build_emoji_pack_page(items, pack_name, 0)
        await update.message.reply_text(text_out, parse_mode="Markdown", reply_markup=kb)
        return


    if step == "acadm_waiting_user" and is_superadmin(uid):
        target_chat = context.user_data.pop("acadm_chat", None)
        context.user_data.pop("step", None)
        target_uid  = None
        target_name = None
        fwd = update.message.forward_from
        if fwd:
            target_uid, target_name = fwd.id, (fwd.first_name or str(fwd.id))
        elif text.startswith("@"):
            found = _find_user_by_username(text)
            if found:
                target_uid, target_name, _ = found
        else:
            try:
                target_uid  = int(text.strip())
                target_name = str(target_uid)
            except ValueError:
                pass
        if target_uid is None:
            await update.message.reply_text(
                "❌ Foydalanuvchi topilmadi. Xabarini *forward* qiling yoki "
                "`@username` / user ID yuboring.", parse_mode="Markdown")
            return
        try:
            await context.bot.promote_chat_member(
                target_chat, target_uid,
                can_change_info=True, can_delete_messages=True, can_invite_users=True,
                can_restrict_members=True, can_pin_messages=True,
                can_manage_chat=True, can_manage_video_chats=True)
            chats     = load_chats()
            chat_name = chats.get(str(target_chat), {}).get("name", str(target_chat))
            await update.message.reply_text(
                f"✅ {mdesc(target_name)} — *{mdesc(chat_name)}*da admin qilindi!",
                parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Xatolik: {e}")
        return

    if step == "grant_ref_waiting" and is_superadmin(uid):
        context.user_data.pop("step", None)
        target_uid = context.user_data.pop("gr_uid", None)
        back_page  = context.user_data.pop("gr_back", 0)
        try:
            amount = int(text.strip())
        except ValueError:
            await update.message.reply_text("❌ Butun son kiriting (masalan `5` yoki `-3`).",
                                            parse_mode="Markdown")
            return
        users = load_users()
        u = users.setdefault(str(target_uid), {})
        u["referral_count"] = max(0, u.get("referral_count", 0) + amount)
        save_users(users)
        verb = "qo'shildi" if amount >= 0 else "ayirildi"
        await update.message.reply_text(
            f"✅ `{target_uid}` uchun {abs(amount)} ta referral {verb}! "
            f"(Jami: {u['referral_count']})",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[IKB("⬅️ Foydalanuvchiga qaytish",
                       callback_data=f"user_detail:{target_uid}:{back_page}")]]))
        return

    if step == "addbadword_waiting" and is_superadmin(uid):
        context.user_data.pop("step", None)
        word = text.lower().strip()
        bw   = load_badwords()
        if word in bw["words"] or word in bw["severe_words"]:
            await update.message.reply_text(f"⚠️ `{word}` allaqachon ro'yxatda!",
                                            parse_mode="Markdown")
            return
        bw["words"].append(word)
        save_badwords(bw)
        await update.message.reply_text(f"✅ So'z qo'shildi: `{word}`", parse_mode="Markdown")
        return

    if step == "editadmin_dname" and is_superadmin(uid):
        uid_e = context.user_data.pop("ea_uid", None)
        context.user_data.pop("step", None)
        if uid_e:
            name = None if text == "-" else text
            set_display_name(uid_e, name)
            msg = (f"✅ `{uid_e}` uchun nom: *{mdesc(name)}*" if name
                   else f"✅ `{uid_e}` nomi o'chirildi.")
            await update.message.reply_text(msg, parse_mode="Markdown")
        return

    if step in ("newtopic_access_custom", "access_custom_input"):
        tn = context.user_data.get("topic_name")
        t  = load_topic(tn)
        if not t:
            context.user_data.clear()
            return
        if not can_edit_topic_access(t, uid):
            context.user_data.pop("step", None)
            return
        allowed = parse_allowed(text)
        t["access"] = {"type": "custom", "allowed": allowed}
        save_topic(t)
        context.user_data.pop("step", None)
        s = ", ".join(str(a) for a in allowed) if allowed else "hech kim"
        await update.message.reply_text(
            f"✅ *{mdesc(tn)}* access: ✏️ Qo'lda\nRuxsat berilganlar: {s}",
            parse_mode="Markdown")
        return

    if step == "newtopic_name_prompt":
        if not is_admin_or_superadmin(uid):
            t_lim = get_user_topic_limit(uid)
            owned = count_admin_topics(uid)
            if owned >= t_lim:
                context.user_data.pop("step", None)
                await update.message.reply_text(
                    f"❌ Topic limit to'ldi! ({owned}/{t_lim})")
                return
        name = text.lower().strip()
        if not name.replace("_", "").isalnum():
            await update.message.reply_text("❌ Nom: harf, raqam, _ bo'lsin.")
            return
        if topic_exists(name):
            await update.message.reply_text(f"❌ `{name}` allaqachon bor!", parse_mode="Markdown")
            return
        context.user_data.update({"step": "newtopic_emoji", "topic_name": name})
        await update.message.reply_text(
            f"✅ Topic nomi: *{mdesc(name)}*\n\n🎨 Emojiini yuboring _(masalan: 🇬🇧 🔢 🧠)_",
            parse_mode="Markdown")
        return

    if step == "newtopic_emoji":
        name = context.user_data["topic_name"]
        created_by = uid
        save_topic({
            "name":       name,
            "emoji":      text,
            "prize":      None,
            "created_by": created_by,
            "access":     {"type": "all", "allowed": []},
            "questions":  [],
        })
        context.user_data["step"] = "newtopic_access"
        await update.message.reply_text(
            f"✅ Topic yaratildi: {text} *{mdesc(name)}*\n\n"
            "🔐 *Topicdan kimlar foydalana oladi?*",
            parse_mode="Markdown",
            reply_markup=_access_kb(name))
        return

    if not is_admin_or_superadmin(uid):
        return

    if step == "bulkq_waiting":
        await _process_bulkq(update, context)
        return

    if step == "addq_question" and text:
        context.user_data.update({"q_question": text, "step": "addq_answer"})
        await update.message.reply_text(
            f"❓ Savol: _{text}_\n\n✅ To'g'ri javobni yozing:",
            parse_mode="Markdown")
        return

    if step == "addq_answer":
        context.user_data.update({"q_answer": text.lower(), "q_alts": [], "step": "addq_alts"})
        kb = InlineKeyboardMarkup([
            [IKB("➕ Alternativ javob",          callback_data="addq_alt")],
            [IKB("🖼 Rasm/Video/GIF/Stiker",      callback_data="addq_media")],
            [IKB("✅ Saqlash (mediasiz)",          callback_data="addq_save_nomedia")],
        ])
        await update.message.reply_text(
            f"✅ Javob: *{text}*\n\nKeyingi qadam?",
            parse_mode="Markdown", reply_markup=kb)
        return

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
        names  = ", ".join(f"{t['emoji']}{mdesc(t['name'])}" for t in topics) if topics else "hali yo'q"
        await update.message.reply_text(
            "🎮 *Quiz Bot*\n\n"
            f"📚 Mavjud topiclar: {names}\n\n"
            "▶️ `/newgame <topic>` — o'yin boshlash\n"
            "⏹ `/endgame` — to'xtatish\n"
            "📊 `/scores` — ballar",
            parse_mode="Markdown")
        return

    raw = user.first_name or "Admin"
    dn  = mdesc(get_display_name(uid, raw))

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

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    chat = update.effective_chat
    if chat.type in ("group", "supergroup"):
        return
    step = context.user_data.get("step")

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
                await update.message.reply_text(f"✅ *{mdesc(name)}* guruhiga yuborildi!", parse_mode="Markdown")
            except Exception as e:
                await update.message.reply_text(f"❌ Yuborib bo'lmadi:\n`{e}`", parse_mode="Markdown")
        return

    if step == "contact_waiting" and not is_admin_or_superadmin(uid):
        await _relay(update, context)
        return

    if not is_admin_or_superadmin(uid):
        return

    if step == "broadcast_msg" and is_superadmin(uid):
        await _bc_received(update, context)
        return

    if step == "restore_waiting" and is_superadmin(uid):
        if update.message.document:
            await _process_restore(update, context)
        else:
            await update.message.reply_text("❌ JSON faylni yuboring.")
        return

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
        await update.message.reply_text(f"✅ *{mdesc(tn)}* uchun sovrin saqlandi! 🏆", parse_mode="Markdown")
        return

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

async def global_error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    """Global xatoliklarni tutish. Bu bo'lmasa, har qanday xatolik
    (masalan 'Message is not modified') jim yutilib, tugma/buyruq
    foydalanuvchi uchun 'ishlamayapti' bo'lib ko'rinardi.

    Eslatma: bu funksiya endi kanalga yoki superadminga hech qanday
    xabar YUBORMAYDI — faqat server logiga (Render → Logs) yozadi,
    kanal postlari yoki fon vazifalaridagi kutilgan/zararsiz xatolar
    tufayli superadminga spam ketmasligi uchun."""
    err = context.error
    err_text = str(err)

    # Telegramning zararsiz, tez-tez uchraydigan xatosi — e'tiborsiz qoldiramiz
    if "Message is not modified" in err_text:
        return

    logger.error("Update xatoligi:", exc_info=err)

    # Faqat callback tugma bosilganda foydalanuvchiga qisqa, ko'rinmas
    # (toast) ogohlantirish beramiz — bu chatga yozib spam qilmaydi va
    # faqat o'sha tugmani bosgan kishigagina ko'rinadi.
    try:
        if isinstance(update, Update) and update.callback_query:
            await update.callback_query.answer(
                "⚠️ Xatolik yuz berdi, qayta urinib ko'ring.", show_alert=True)
    except Exception:
        pass

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("step"):
        context.user_data.clear()
        await update.message.reply_text("❌ Bekor qilindi.", reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text("⚠️ Bekor qilinadigan jarayon yo'q.")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    data = q.data

    # ── Emoji pack picker ──
    if data.startswith("epage:"):
        if not is_superadmin(uid): return
        items = context.user_data.get("epack_items")
        pack_name = context.user_data.get("epack_name", "?")
        if not items:
            await q.edit_message_text("⚠️ Sessiya eskirgan, qaytadan /getemojiid yuboring.")
            return
        page = int(data.split(":")[1])
        text_out, kb = _build_emoji_pack_page(items, pack_name, page)
        await q.edit_message_text(text_out, parse_mode="Markdown", reply_markup=kb)
        return

    if data.startswith("epick:"):
        if not is_superadmin(uid): return
        idx   = int(data.split(":")[1])
        items = context.user_data.get("epack_items")
        if not items or idx >= len(items):
            await q.edit_message_text("⚠️ Sessiya eskirgan, qaytadan /getemojiid yuboring.")
            return
        item = items[idx]
        await q.edit_message_text(
            f"🆔 *Tanlangan emoji ID:*\n`{item['id']}`\n\n"
            "Buni `ReactionTypeCustomEmoji(custom_emoji_id=\"...\")` "
            "ichida ishlatishingiz mumkin.", parse_mode="Markdown")
        return

    # ── Guruh/kanalga admin tayinlash ──
    if data.startswith("acadm_chat:"):
        if not is_superadmin(uid): return
        target_chat = int(data.split(":", 1)[1])
        context.user_data.clear()
        context.user_data.update({"step": "acadm_waiting_user", "acadm_chat": target_chat})
        await q.edit_message_text(
            "👤 Endi admin qilmoqchi bo'lgan foydalanuvchining biror xabarini "
            "*forward* qiling, yoki `@username` / user ID yuboring:",
            parse_mode="Markdown")
        return

    # ── Userlar ro'yxati / tafsiloti / tarif-referral berish ──
    if data.startswith("userslist:"):
        if not is_superadmin(uid): return
        await _render_users_list(q, int(data.split(":")[1]))
        return

    if data.startswith("user_detail:"):
        if not is_superadmin(uid): return
        parts = data.split(":")
        target_uid = int(parts[1])
        back_page  = int(parts[2]) if len(parts) > 2 else 0
        text = format_user_info(uid=target_uid)
        kb = InlineKeyboardMarkup([
            [IKB("💎 Tarif berish", callback_data=f"grant_tarif:{target_uid}:{back_page}")],
            [IKB("🎁 Referral berish", callback_data=f"grant_ref:{target_uid}:{back_page}")],
            [IKB("⬅️ Orqaga", callback_data=f"userslist:{back_page}")],
        ])
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        return

    if data.startswith("grant_tarif:"):
        if not is_superadmin(uid): return
        _, target_uid, back_page = data.split(":")
        kb = InlineKeyboardMarkup(
            [[IKB(TARIF_NAMES[t], callback_data=f"gt:{target_uid}:{t}:{back_page}")]
             for t in (TARIF_FREE, TARIF_PLUS, TARIF_PREMIUM, TARIF_VIP)]
            + [[IKB("⬅️ Orqaga", callback_data=f"user_detail:{target_uid}:{back_page}")]])
        await q.edit_message_text("💎 Qaysi tarifni berasiz?", reply_markup=kb)
        return

    if data.startswith("gt:"):
        if not is_superadmin(uid): return
        _, target_uid, tarif, back_page = data.split(":")
        kb = InlineKeyboardMarkup([
            [IKB("7 kun",  callback_data=f"gtd:{target_uid}:{tarif}:7:{back_page}"),
             IKB("30 kun", callback_data=f"gtd:{target_uid}:{tarif}:30:{back_page}")],
            [IKB("♾ Doimiy", callback_data=f"gtd:{target_uid}:{tarif}:0:{back_page}")],
            [IKB("⬅️ Orqaga", callback_data=f"grant_tarif:{target_uid}:{back_page}")],
        ])
        await q.edit_message_text(
            f"💎 *{TARIF_NAMES[tarif]}* — qancha muddatga?",
            parse_mode="Markdown", reply_markup=kb)
        return

    if data.startswith("gtd:"):
        if not is_superadmin(uid): return
        _, target_uid_s, tarif, days_s, back_page = data.split(":")
        target_uid = int(target_uid_s)
        days = int(days_s)
        users = load_users()
        u = users.setdefault(target_uid_s, {})
        u["tarif"] = tarif
        u["tarif_expires"] = (
            (datetime.now(TZ) + timedelta(days=days)).isoformat() if days > 0 else None)
        save_users(users)
        dur = f"{days} kunga" if days > 0 else "doimiy"
        await q.edit_message_text(
            f"✅ `{target_uid}` ga *{TARIF_NAMES[tarif]}* ({dur}) berildi!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[IKB("⬅️ Orqaga", callback_data=f"user_detail:{target_uid}:{back_page}")]]))
        try:
            await context.bot.send_message(
                target_uid, f"🎉 Sizga *{TARIF_NAMES[tarif]}* tarifi berildi!",
                parse_mode="Markdown")
        except Exception:
            pass
        return

    if data.startswith("grant_ref:"):
        if not is_superadmin(uid): return
        _, target_uid, back_page = data.split(":")
        context.user_data.clear()
        context.user_data.update({"step": "grant_ref_waiting",
                                    "gr_uid": int(target_uid), "gr_back": int(back_page)})
        await q.edit_message_text(
            "🎁 Nechta referral qo'shmoqchisiz? Raqam yuboring (masalan `5`, ayirish uchun `-3`):",
            parse_mode="Markdown")
        return

    # ── So'kinish hodisasi tafsiloti ──
    if data.startswith("profview:"):
        iid = int(data.split(":", 1)[1])
        it  = get_incident(iid)
        if not it:
            await q.answer("❌ Topilmadi (eskirgan bo'lishi mumkin).", show_alert=True)
            return
        uname = f" (@{mdesc(it['offender_username'])})" if it.get("offender_username") else ""
        text = (
            f"🚨 *Hodisa #{it['id']}*\n\n"
            f"🕐 Vaqt: {it['time']}\n"
            f"💬 Guruh: {mdesc(it['chat_title'])}\n"
            f"👤 Kim: {mdesc(it['offender_name'])}{uname}\n"
            f"🎯 Kimga: {mdesc(it['target'])}\n"
            f"📝 Nima deb: `{mdesc(it['text'])}`\n"
            f"⚖️ Oqibat: {mdesc(it['consequence'])}"
            + ("\n🕌 *Diний nom bilan!*" if it.get("sacred_name") else "")
        )
        await q.edit_message_text(text, parse_mode="Markdown")
        return

    # ── User menu ──
    if data.startswith("u:") or data == "u:back":
        await _handle_user_menu(q, uid, data, context)
        return

    # ── Tarif sotib olish ──
    if data.startswith("buy:"):
        tarif = data.split(":")[1]
        if tarif not in (TARIF_PLUS, TARIF_PREMIUM, TARIF_VIP):
            return
        try:
            await _send_invoice(context.bot, q.message.chat.id, tarif, uid)
        except Exception as e:
            logger.error(f"send_invoice xato: {e}")
            await q.message.reply_text(
                "❌ To'lov hisobini yaratishda xatolik yuz berdi.\n"
                "Iltimos, birozdan so'ng qayta urinib ko'ring yoki adminga murojaat qiling.")
            return
        await q.message.delete()
        return

    # ── Superadmin menyu ──
    if data.startswith("menu:"):
        if not is_admin_or_superadmin(uid):
            return
        section = data.split(":")[1]

        if section == "back":
            if is_superadmin(uid):
                await q.edit_message_text(
                    "👑 Superadmin boshqaruv paneli:",
                    reply_markup=_superadmin_main_kb())
            else:
                lim   = get_admin_topic_limit(uid)
                mq    = get_admin_max_questions(uid)
                owned = count_admin_topics(uid)
                await q.edit_message_text(
                    f"📊 Topic: {owned}/{lim} | Savol/topic: {mq}",
                    reply_markup=_admin_main_kb(uid))
            return

        if section == "users":
            if not is_superadmin(uid):
                return
            await _render_users_list(q, 0)
            return

        if section == "topics":
            topics = all_topics()
            if not is_superadmin(uid):
                uname = q.from_user.username
                topics = [t for t in topics if can_manage_topic(t, uid, uname)]
            if not topics:
                await q.edit_message_text(
                    "📭 Topiclar yo'q.",
                    reply_markup=InlineKeyboardMarkup([
                        [IKB("➕ Yangi topic", callback_data="menu:newtopic_prompt")],
                        [IKB("⬅️ Orqaga",     callback_data="menu:back")],
                    ]))
                return
            lines = []
            for t in topics:
                mq = get_admin_max_questions(t.get("created_by", uid))
                lines.append(f"{t['emoji']} *{mdesc(t['name'])}* — {len(t['questions'])}/{mq}")
            btns = [[IKB(f"{t['emoji']} {t['name']}", callback_data=f"topic_detail:{t['name']}")] for t in topics]
            btns.append([IKB("➕ Yangi topic", callback_data="menu:newtopic_prompt"),
                         IKB("⬅️ Orqaga",     callback_data="menu:back")])
            await q.edit_message_text(
                f"📋 *Topiclar ({len(topics)} ta):*\n\n" + "\n".join(lines),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(btns))
            return

        if section == "newtopic_prompt":
            context.user_data.clear()
            context.user_data["step"] = "newtopic_name_prompt"
            await q.edit_message_text(
                "➕ *Yangi topic*\n\nTopic nomini yuboring _(faqat harf, raqam yoki pastki chiziq, masalan: `english`)_",
                parse_mode="Markdown")
            return

        if section == "addadmin_prompt":
            if not is_superadmin(uid):
                return
            context.user_data.clear()
            context.user_data.update({"step": "addadmin_uid", "aa_by": uid,
                                       "aa_max_tl": MAX_TOPICS, "aa_max_mq": MAX_QUESTIONS})
            await q.edit_message_text(
                "➕ *Admin qo'shish*\n\nUser ID kiriting:",
                parse_mode="Markdown")
            return

        if section == "badwords":
            if not is_superadmin(uid):
                return
            bw = load_badwords()
            words   = bw.get("words", [])
            severe  = bw.get("severe_words", [])
            await q.edit_message_text(
                f"🔤 *So'z filtri*\n\n"
                f"Oddiy: {len(words)} ta\nQo'pol: {len(severe)} ta\n\n"
                "Qo'shish: `/addbadword so'z`\n"
                "Qo'pol qo'shish: `/addsevereword so'z`\n"
                "Ro'yxat: `/listbadwords`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[IKB("⬅️ Orqaga", callback_data="menu:back")]]))
            return

        if section == "admins":
            if is_superadmin(uid):
                adm = load_admins()
                if not adm:
                    kb = InlineKeyboardMarkup([[IKB("➕ Admin qo'shish", callback_data="menu:addadmin_prompt"),
                                               IKB("⬅️ Orqaga", callback_data="menu:back")]])
                    await q.edit_message_text("👥 Admin yo'q.", reply_markup=kb)
                    return
                btns = [[IKB(f"⚙️ {k} ({v.get('display_name','—')})", callback_data=f"edit_adm:{k}")] for k, v in adm.items()]
                btns.append([IKB("➕ Admin qo'shish", callback_data="menu:addadmin_prompt"),
                             IKB("⬅️ Orqaga",        callback_data="menu:back")])
                lines = [f"👤 `{k}` — topic:{v['topic_limit']} savol:{v.get('max_questions',MAX_QUESTIONS)}"
                         for k, v in adm.items()]
                await q.edit_message_text(
                    f"👥 *Adminlar ({len(adm)} ta):*\n\n" + "\n".join(lines),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(btns))
            return

        if section == "users":
            if not is_superadmin(uid):
                return
            users = load_users()
            by_tarif = {}
            for u in users.values():
                t = u.get("tarif", TARIF_FREE)
                by_tarif[t] = by_tarif.get(t, 0) + 1
            lines = [f"• {TARIF_NAMES.get(t, t)}: {n} ta" for t, n in by_tarif.items()]
            kb = InlineKeyboardMarkup([[IKB("⬅️ Orqaga", callback_data="menu:back")]])
            await q.edit_message_text(
                f"👤 *Jami: {len(users)} ta*\n\n" + "\n".join(lines),
                parse_mode="Markdown", reply_markup=kb)
            return

        if section == "tarifs":
            if not is_superadmin(uid):
                return
            await q.edit_message_text(
                "💎 *Tarif sozlamalari:*",
                parse_mode="Markdown",
                reply_markup=_tarif_admin_kb())
            return

        if section == "export":
            if not is_superadmin(uid):
                return
            await q.edit_message_text("📦 Export qilinmoqda...")
            ok = await do_export(context.bot, to_backup_topic=True)
            await q.edit_message_text(
                "✅ Export muvaffaqiyatli!" if ok else "❌ Export xato!",
                reply_markup=InlineKeyboardMarkup([[IKB("⬅️ Orqaga", callback_data="menu:back")]]))
            return

        if section == "forum":
            if not is_superadmin(uid):
                return
            ft = get_forum_topics()
            lines = [f"• `{k}` → ID: `{v}`" for k, v in ft.items()]
            kb = InlineKeyboardMarkup([
                [IKB("👑 VIP topic",     callback_data="ft:vip"),
                 IKB("💎 Premium topic", callback_data="ft:premium")],
                [IKB("✨ PLUS topic",    callback_data="ft:plus"),
                 IKB("📦 Backup topic",  callback_data="ft:backup")],
                [IKB("🏫 Sinf topici",   callback_data="ft:class")],
                [IKB("⬅️ Orqaga",        callback_data="menu:back")],
            ])
            await q.edit_message_text(
                "🏫 *Forum topiclar:*\n\n" + ("\n".join(lines) if lines else "Hali yo'q"),
                parse_mode="Markdown", reply_markup=kb)
            return

        if section == "games":
            if not is_superadmin(uid):
                return
            active = {cid: g for cid, g in games.items() if g.get("active")}
            btns   = []
            if active:
                for cid, g in active.items():
                    btns.append([IKB(f"⏹ {g['emoji']}{g['topic']} ({cid})",
                                     callback_data=f"stopgame_ask:{cid}")])
            btns.append([IKB("⬅️ Orqaga", callback_data="menu:back")])
            await q.edit_message_text(
                f"🎮 *Faol o'yinlar: {len(active)} ta*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(btns))
            return

        if section == "settings":
            if not is_superadmin(uid):
                return
            cur_ch = get_sub_channel()
            kb = InlineKeyboardMarkup([
                [IKB("📢 Obuna kanalini o'rnatish", callback_data="setchannel")],
                [IKB("🔐 Admin talab (guruhlar)",   callback_data="menu:requireadmin")],
                [IKB("📤 Botdan xabar yuborish",    callback_data="menu:sendas")],
                [IKB("⬅️ Orqaga",                   callback_data="menu:back")],
            ])
            await q.edit_message_text(
                f"⚙️ *Sozlamalar:*\n\n📢 Obuna kanali: `{cur_ch or 'belgilanmagan'}`",
                parse_mode="Markdown", reply_markup=kb)
            return

        if section == "broadcast":
            if not is_superadmin(uid):
                return
            context.user_data["step"] = "broadcast_target"
            chats = load_chats()
            by_t  = {}
            for c in chats.values():
                by_t[c["type"]] = by_t.get(c["type"], 0) + 1
            stats = " | ".join(f"{t}:{n}" for t, n in by_t.items()) or "0"
            await q.edit_message_text(
                f"📢 *Reklama yuborish*\n\n💬 Chatlar: {stats}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [IKB("👥 Hammaga",         callback_data="bc_target:all")],
                    [IKB("👤 Userlarga",        callback_data="bc_target:private")],
                    [IKB("🏘 Guruhlarga",       callback_data="bc_target:groups")],
                    [IKB("📢 Kanallarga",       callback_data="bc_target:channels")],
                    [IKB("⬅️ Orqaga",           callback_data="menu:back")],
                ]))
            return

        if section == "addq":
            topics = [t for t in all_topics() if can_manage_topic(t, uid, q.from_user.username)]
            if not topics:
                await q.edit_message_text("❌ Topiclar yo'q.")
                return
            btns = [[IKB(f"{t['emoji']} {t['name']}", callback_data=f"addq_topic:{t['name']}")] for t in topics]
            await q.edit_message_text("📚 Qaysi topicga savol?", reply_markup=InlineKeyboardMarkup(btns))
            return

        if section == "newgame":
            topics = all_topics()
            if not topics:
                await q.edit_message_text("❌ Topic yo'q.")
                return
            btns = [[IKB(f"{t['emoji']} {t['name']}", callback_data=f"newgame_topic:{t['name']}")] for t in topics]
            await q.edit_message_text("🎮 Qaysi topic?", reply_markup=InlineKeyboardMarkup(btns))
            return

        if section == "requireadmin":
            if not is_superadmin(uid):
                return
            chats  = load_chats()
            groups = {k: v for k, v in chats.items() if v.get("type") in ("group", "supergroup")}
            if not groups:
                await q.edit_message_text("❌ Guruh yo'q.")
                return
            btns = []
            for k, v in groups.items():
                req = v.get("require_admin", False)
                s   = "🟢" if req else "🔴"
                btns.append([IKB(f"{s} {v.get('name', k)}", callback_data=f"req_adm:{k}")])
            btns.append([IKB("⬅️ Orqaga", callback_data="menu:settings")])
            await q.edit_message_text(
                "🔐 *Admin talab:*", parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(btns))
            return

        if section == "sendas":
            if not is_superadmin(uid):
                return
            chats  = load_chats()
            groups = {k: v for k, v in chats.items() if v.get("type") in ("group", "supergroup")}
            if not groups:
                await q.edit_message_text("❌ Guruh yo'q.")
                return
            btns = [[IKB(v.get("name", k), callback_data=f"sendas:{k}")] for k, v in groups.items()]
            btns.append([IKB("⬅️ Orqaga", callback_data="menu:settings")])
            await q.edit_message_text("📤 Qaysi guruhga?", reply_markup=InlineKeyboardMarkup(btns))
            return

        return

    # ── Broadcast inline target ──
    if data.startswith("bc_target:"):
        if not is_superadmin(uid): return
        target = data.split(":")[1]
        context.user_data["bc_target"] = target
        context.user_data["step"]      = "broadcast_msg"
        await q.edit_message_text(
            "📤 *Reklama xabarini yuboring:*\n_(matn, rasm, video — barchasi)_\n\n⏹ /cancel",
            parse_mode="Markdown")
        return

    # ── Tarif narxi o'zgartirish ──
    if data.startswith("setprice:"):
        if not is_superadmin(uid): return
        tarif = data.split(":")[1]
        context.user_data["step"]         = "setprice_input"
        context.user_data["setprice_tarif"] = tarif
        await q.edit_message_text(
            f"💰 *{TARIF_NAMES.get(tarif, tarif)}* uchun yangi narx (stars) kiriting:",
            parse_mode="Markdown")
        return

    if data == "setchannel":
        if not is_superadmin(uid): return
        context.user_data["step"] = "setchannel_input"
        cur = get_sub_channel()
        await q.edit_message_text(
            f"📢 Joriy obuna kanali: `{cur or 'belgilanmagan'}`\n\n"
            "Kanal ID kiriting (masalan: `-100123456789`)\n"
            "O'chirish uchun: `0`",
            parse_mode="Markdown")
        return

    # ── Forum topic ──
    if data.startswith("ft:"):
        if not is_superadmin(uid): return
        action = data.split(":")[1]
        if action == "class":
            context.user_data["step"] = "create_class_topic"
            await q.edit_message_text(
                "🏫 Sinf nomini yozing:\n_(masalan: `8A sinfi`, `9B sinfi`)_",
                parse_mode="Markdown")
            return
        tarif_map = {"vip": TARIF_VIP, "premium": TARIF_PREMIUM, "plus": TARIF_PLUS}
        if action in tarif_map:
            tid = await get_tarif_topic_id(context.bot, tarif_map[action])
            msg = f"✅ *{TARIF_NAMES[tarif_map[action]]}* topic ID: `{tid}`" if tid else "❌ Yaratib bo'lmadi."
            await q.edit_message_text(msg, parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup([[IKB("⬅️ Orqaga", callback_data="menu:forum")]]))
        elif action == "backup":
            tid = await get_backup_topic_id(context.bot)
            msg = f"✅ Backup topic ID: `{tid}`" if tid else "❌ Yaratib bo'lmadi."
            await q.edit_message_text(msg, parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup([[IKB("⬅️ Orqaga", callback_data="menu:forum")]]))
        return

    # ── Sendas ──
    if data.startswith("sendas:"):
        if not is_superadmin(uid): return
        cid_str = data.split(":", 1)[1]
        chats   = load_chats()
        name    = chats.get(cid_str, {}).get("name", cid_str)
        context.user_data.clear()
        context.user_data["step"]        = "sendas_waiting"
        context.user_data["sendas_chat"] = int(cid_str)
        await q.edit_message_text(
            f"📤 *{mdesc(name)}* guruhiga xabar yuboring:\n\n⏹ /cancel", parse_mode="Markdown")
        return

    # ── Require admin ──
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
        groups = {k: v for k, v in chats.items() if v.get("type") in ("group", "supergroup")}
        btns = []
        for k, v in groups.items():
            req = v.get("require_admin", False)
            s   = "🟢" if req else "🔴"
            btns.append([IKB(f"{s} {v.get('name', k)}", callback_data=f"req_adm:{k}")])
        btns.append([IKB("⬅️ Orqaga", callback_data="menu:settings")])
        await q.edit_message_text(
            f"✅ *{mdesc(name)}* — {status}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(btns))
        return

    if data == "req_adm_done":
        if not is_superadmin(uid): return
        await q.edit_message_text("✅ Sozlamalar saqlandi.")
        return

    # ── Topic access ──
    if data.startswith("acc:"):
        _, at, tn = data.split(":", 2)
        t = load_topic(tn)
        if not t: await q.edit_message_text("❌ Topic topilmadi."); return
        if not can_edit_topic_access(t, uid):
            await q.answer("❌ Ruxsat yo'q!", show_alert=True); return
        is_creation_flow = context.user_data.get("step") == "newtopic_access"
        if at == "custom":
            context.user_data["topic_name"] = tn
            context.user_data["step"]       = "access_custom_input"
            await q.edit_message_text(
                f"✏️ *{mdesc(tn)}* — ruxsat beriladiganlar:\n\n`@username` yoki `123456789`",
                parse_mode="Markdown")
            return
        t["access"] = {"type": at, "allowed": []}
        save_topic(t)
        if is_creation_flow:
            context.user_data.clear()
            after_kb = (_after_action_kb(tn) if is_admin_or_superadmin(uid)
                        else _after_action_kb_user(tn))
            await q.edit_message_text(
                f"✅ *{mdesc(tn)}* topic tayyor!\n🔐 Access: {ACCESS_LABELS.get(at, at)}\n\n"
                "Endi savollar qo'shishingiz mumkin 👇",
                parse_mode="Markdown", reply_markup=after_kb)
        else:
            await q.edit_message_text(
                f"✅ *{mdesc(tn)}* — Access: {ACCESS_LABELS.get(at, at)}",
                parse_mode="Markdown")
        return

    if data.startswith("eta:"):
        if not is_admin_or_superadmin(uid): return
        tn = data.split(":", 1)[1]
        t  = load_topic(tn)
        if not t: return
        if not can_edit_topic_access(t, uid):
            await q.answer("❌ Faqat topic egasi!", show_alert=True); return
        cur = ACCESS_LABELS.get(t.get("access", {}).get("type", "all"), "—")
        await q.edit_message_text(
            f"🔐 *{mdesc(tn)}* — hozir: {cur}\n\nYangi access:",
            parse_mode="Markdown", reply_markup=_access_kb(tn))
        return

    # ── editadmin ──
    if data.startswith("eal_t:"):
        if not is_superadmin(uid): return
        _, uid_e, val = data.split(":")
        uid_e = int(uid_e); val = int(val)
        adm = load_admins()
        if str(uid_e) not in adm: return
        adm[str(uid_e)]["topic_limit"] = val
        save_admins(adm)
        await q.edit_message_text(_editadmin_txt(uid_e, adm[str(uid_e)]),
                                  parse_mode="Markdown",
                                  reply_markup=_editadmin_kb(uid_e, adm[str(uid_e)]))
        return

    if data.startswith("eal_q:"):
        if not is_superadmin(uid): return
        _, uid_e, val = data.split(":")
        uid_e = int(uid_e); val = int(val)
        adm = load_admins()
        if str(uid_e) not in adm: return
        adm[str(uid_e)]["max_questions"] = val
        save_admins(adm)
        await q.edit_message_text(_editadmin_txt(uid_e, adm[str(uid_e)]),
                                  parse_mode="Markdown",
                                  reply_markup=_editadmin_kb(uid_e, adm[str(uid_e)]))
        return

    if data.startswith("eal_ca:"):
        if not is_superadmin(uid): return
        uid_e = int(data.split(":")[1])
        adm = load_admins()
        if str(uid_e) not in adm: return
        info = adm[str(uid_e)]
        new_state = not info.get("can_add_admins", False)
        info["can_add_admins"] = new_state
        if new_state and not info.get("sub_admin_settings"):
            info["sub_admin_settings"] = {
                "max_admins":              5,
                "max_topic_limit":         info.get("topic_limit", 1),
                "max_questions_per_topic": info.get("max_questions", MAX_QUESTIONS),
            }
        save_admins(adm)
        await q.edit_message_text(_editadmin_txt(uid_e, adm[str(uid_e)]),
                                  parse_mode="Markdown",
                                  reply_markup=_editadmin_kb(uid_e, adm[str(uid_e)]))
        return

    if data.startswith("eal_dn:"):
        if not is_superadmin(uid): return
        uid_e = int(data.split(":")[1])
        context.user_data["step"]   = "editadmin_dname"
        context.user_data["ea_uid"] = uid_e
        await q.edit_message_text(
            f"🏷 `{uid_e}` uchun yangi nom:\n_(o'chirish: `-`)_",
            parse_mode="Markdown")
        return

    # ── Admin uchun reaksiya tanlash ──
    if data.startswith("admreact_page:"):
        if not is_superadmin(uid): return
        _, target_s, page_s = data.split(":")
        target = int(target_s)
        await q.edit_message_text(
            "🎭 *Bu adminning guruhdagi xabarlariga qanday reaksiya bosay?*",
            parse_mode="Markdown",
            reply_markup=_reaction_pick_kb(target, int(page_s)))
        return

    if data.startswith("admreact_custom:"):
        if not is_superadmin(uid): return
        target = int(data.split(":")[1])
        context.user_data.clear()
        context.user_data.update({"step": "admreact_custom_wait", "ar_target": target})
        await q.edit_message_text(
            "🆔 *Custom emoji ID yuboring*\n\n"
            "_(Premium/animatsion reaksiya uchun. ID'ni topish uchun "
            "istalgan premium emojini guruhda ishlatib, botga "
            "@userinfobot yoki shunga o'xshash vositalar orqali "
            "custom_emoji_id'ni aniqlashingiz mumkin)_",
            parse_mode="Markdown")
        return

    if data.startswith("admreact_skip:"):
        if not is_superadmin(uid): return
        target = int(data.split(":")[1])
        adm = load_admins()
        if str(target) in adm:
            adm[str(target)].pop("reaction_emoji", None)
            adm[str(target)].pop("reaction_custom_emoji_id", None)
            save_admins(adm)
        back_kb = InlineKeyboardMarkup(
            [[IKB("⬅️ Admin sozlamalariga", callback_data=f"edit_adm:{target}")]]
        ) if str(target) in adm else None
        await q.edit_message_text(
            f"✅ `{target}` uchun standart reaksiya (🔥) qo'llaniladi.",
            parse_mode="Markdown", reply_markup=back_kb)
        return

    if data.startswith("admreact:"):
        if not is_superadmin(uid): return
        _, target_s, emoji = data.split(":", 2)
        target = int(target_s)
        adm = load_admins()
        if str(target) not in adm:
            await q.edit_message_text("❌ Bu admin topilmadi.")
            return
        adm[str(target)]["reaction_emoji"] = emoji
        adm[str(target)].pop("reaction_custom_emoji_id", None)
        save_admins(adm)
        await q.edit_message_text(
            f"✅ `{target}` uchun reaksiya belgilandi: {emoji}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[IKB("⬅️ Admin sozlamalariga", callback_data=f"edit_adm:{target}")]]))
        return

    if data.startswith("edit_adm:"):
        if not is_superadmin(uid): return
        uid_e = int(data.split(":")[1])
        adm   = load_admins()
        if str(uid_e) not in adm: return
        await q.edit_message_text(_editadmin_txt(uid_e, adm[str(uid_e)]),
                                  parse_mode="Markdown",
                                  reply_markup=_editadmin_kb(uid_e, adm[str(uid_e)]))
        return

    if data.startswith("del_adm:"):
        if not is_superadmin(uid): return
        uid_e = int(data.split(":")[1])
        adm   = load_admins()
        adm.pop(str(uid_e), None)
        save_admins(adm)
        await q.edit_message_text(f"✅ `{uid_e}` adminlikdan olib tashlandi.", parse_mode="Markdown")
        return

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
            f"👥 *Adminlar:*\n\n" + "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(btns))
        return

    # ── addadmin steps ──
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
            context.user_data["step"] = "addadmin_dname"
            kb = InlineKeyboardMarkup([
                [IKB("⏩ O'tkazib yuborish", callback_data="aa_skip_dn")],
                [IKB("❌ Bekor",             callback_data="aa_cancel")],
            ])
            await q.edit_message_text(
                f"📁 {context.user_data.get('aa_tl')} ta | ❓ {val} ta\n\n"
                "🏷 *Display name:*\n_(o'tkazish uchun tugma bosing)_",
                parse_mode="Markdown", reply_markup=kb)
        else:
            await _finalize_addadmin(q, context, by, display_name=None, can_add=False, sub_s={})
        return

    if data == "aa_skip_dn":
        if not is_superadmin(uid): return
        context.user_data["aa_dname"] = None
        context.user_data["step"]     = "addadmin_can_add"
        await q.edit_message_text("Bu admin o'z adminlarini qo'sha oladimi?",
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
                "👥 *Max nechta sub-admin qo'sha oladi?*",
                reply_markup=_aa_cnt_kb())
        return

    if data.startswith("aa_sm:"):
        if not is_superadmin(uid): return
        val = int(data.split(":")[1])
        context.user_data["aa_sub_ma"] = val
        context.user_data["step"]      = "addadmin_sub_tl"
        await q.edit_message_text(
            f"👥 Max sub-admin: *{val}* ta\n\n📁 *Sub-admin topic limiti:*",
            parse_mode="Markdown", reply_markup=_aa_sub_tl_kb())
        return

    if data.startswith("aa_st:"):
        if not is_superadmin(uid): return
        val = int(data.split(":")[1])
        context.user_data["aa_sub_tl"] = val
        context.user_data["step"]      = "addadmin_sub_ql"
        await q.edit_message_text(
            f"📁 Sub-admin topic: *{val}* ta\n\n❓ *Sub-admin savol limiti:*",
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

    # ── addq ──
    if data.startswith("bulkq_topic:"):
        name = data.split(":", 1)[1]
        t    = load_topic(name)
        if not t: return
        if not can_manage_topic(t, uid, q.from_user.username) and t.get("created_by") != uid:
            await q.answer("❌ Ruxsat yo'q!", show_alert=True); return
        mq  = get_admin_max_questions(uid) if is_admin_or_superadmin(uid) else get_user_q_limit(uid)
        rem = mq - len(t["questions"])
        if rem <= 0:
            await q.edit_message_text(f"❌ Limit: {mq} ta savol!"); return
        context.user_data.clear()
        context.user_data.update({"step": "bulkq_waiting", "topic_name": name})
        await q.edit_message_text(
            f"📥 *{t['emoji']} {mdesc(name)}* — ommaviy qo'shish\n\n"
            f"📊 {len(t['questions'])}/{mq} | yana {rem} ta\n\n"
            "Har bir savolni yangi qatorga yozing, `-` bilan ajrating:\n\n"
            "`savol - javob - sinonim1 - sinonim2`\n\n"
            "*Masalan:*\n"
            "`apple - olma`\n"
            "`orange - apelsin - olovrang - sabzirang`\n\n"
            "⏹ /done bilan tugatasiz",
            parse_mode="Markdown")
        return

    if data.startswith("addq_topic:"):
        name = data.split(":", 1)[1]
        t    = load_topic(name)
        if not t: return
        if not can_manage_topic(t, uid, q.from_user.username) and t.get("created_by") != uid:
            await q.answer("❌ Ruxsat yo'q!", show_alert=True); return
        mq = get_admin_max_questions(uid) if is_admin_or_superadmin(uid) else get_user_q_limit(uid)
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
        tn  = context.user_data.get("topic_name", "?")
        t   = load_topic(tn)
        mq  = get_admin_max_questions(uid)
        rem = mq - len(t["questions"]) if t else 0
        await q.edit_message_text(
            f"📥 Savollarni yuboring ({rem} ta qolgan):\n\n`apple - olma`",
            parse_mode="Markdown")
        return

    if data == "addq_alt":
        context.user_data["step"] = "addq_alt_text"
        await q.edit_message_text("✏️ Alternativ javobni yozing:")
        return

    if data == "addq_media":
        context.user_data["step"] = "addq_media_waiting"
        await q.edit_message_text(
            "🖼 *Rasm, video, GIF yoki stiker:*\n_(o'tkazish: /skip)_",
            parse_mode="Markdown")
        return

    if data == "addq_save_nomedia":
        context.user_data.update({"q_media_type": "none", "q_file_id": None})
        await _save_q(update, context)
        return

    if data == "addq_continue":
        context.user_data["step"] = "addq_question"
        await q.edit_message_text("📝 Keyingi savol matnini yozing:")
        return

    if data == "addq_finish":
        tn = context.user_data.get("topic_name", "?")
        context.user_data.clear()
        back_cb = "menu:back" if is_admin_or_superadmin(uid) else "u:back"
        await q.edit_message_text(
            f"✅ *Tugatildi!*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[IKB("🏠 Bosh menyu", callback_data=back_cb)]]))
        return

    # ── deltopic ──
    if data.startswith("deltopic:"):
        parts  = data.split(":")
        name   = parts[1]
        origin = parts[2] if len(parts) > 2 else None
        t      = load_topic(name)
        is_owner = bool(t) and t.get("created_by") == uid
        if not (is_superadmin(uid) or is_owner):
            await q.answer("❌ Ruxsat yo'q!", show_alert=True); return
        p = topic_path(name)
        if os.path.exists(p):
            os.remove(p)
            mark_changed()
        for g in games.values():
            if g.get("topic") == name:
                g["active"] = False
        back_kb = None
        if origin:
            back_cb = "menu:topics" if origin == "menu" else "u:topics"
            back_kb = InlineKeyboardMarkup([[IKB("⬅️ Orqaga", callback_data=back_cb)]])
        await q.edit_message_text(f"🗑 *{mdesc(name)}* o'chirildi.", parse_mode="Markdown",
                                  reply_markup=back_kb)
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
            f"🏆 *{mdesc(name)}* uchun sovrinni yuboring _(rasm, GIF yoki stiker)_:",
            parse_mode="Markdown")
        return

    # ── games ──
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

    if data.startswith("newgame_topic:"):
        tn   = data.split(":", 1)[1]
        chat = q.message.chat
        if chat.type not in ("group", "supergroup"):
            await q.answer("❌ Faqat guruhlarda!", show_alert=True)
            return
        t = load_topic(tn)
        if not t or not t["questions"]:
            await q.answer("❌ Bu topicda savollar yo'q!", show_alert=True)
            return
        cid = chat.id
        g   = get_game(cid)
        if g["active"]:
            await q.answer(f"⚠️ Allaqachon o'yin bor!", show_alert=True)
            return
        qs = t["questions"].copy()
        random.shuffle(qs)
        g.update({"active": True, "mode": "standard", "topic": tn, "emoji": t["emoji"],
                  "questions": qs, "asked": 0, "current": None, "current_reversed": False,
                  "current_msg_id": None, "scores": {}, "waiting": False,
                  "time_limit": None, "admin_ranks": [], "started_by": q.from_user.id})
        await q.edit_message_text(
            f"🎮 *O'YIN BOSHLANDI!*\n\n{t['emoji']} *{tn.capitalize()}*\n"
            f"📊 {len(qs)} ta savol",
            parse_mode="Markdown")
        await send_question(cid, context)
        return

    if data.startswith("topic_detail:"):
        parts  = data.split(":")
        name   = parts[1]
        origin = parts[2] if len(parts) > 2 else "menu"
        t      = load_topic(name)
        if not t:
            await q.edit_message_text("❌ Topic topilmadi.")
            return
        is_owner = t.get("created_by") == uid
        if not (is_superadmin(uid) or is_bot_admin(uid) or is_owner):
            await q.answer("❌ Ruxsat yo'q!", show_alert=True); return
        mq  = get_admin_max_questions(uid) if is_admin_or_superadmin(uid) else get_user_q_limit(uid)
        acc = ACCESS_LABELS.get(t.get("access", {}).get("type", "all"), "—")
        back_cb = "menu:topics" if origin == "menu" else "u:topics"
        rows = [
            [IKB("📝 Bitta-bitta",    callback_data=f"addq_topic:{name}"),
             IKB("📥 Ommaviy",         callback_data=f"bulkq_topic:{name}")],
        ]
        if is_superadmin(uid) or is_owner:
            rows.append([IKB("🔐 Access", callback_data=f"eta:{name}")])
        del_row = []
        if is_superadmin(uid) or is_owner:
            del_row.append(IKB("🗑 O'chirish", callback_data=f"deltopic:{name}:{origin}"))
        del_row.append(IKB("⬅️ Orqaga", callback_data=back_cb))
        rows.append(del_row)
        kb = InlineKeyboardMarkup(rows)
        await q.edit_message_text(
            f"{t['emoji']} *{mdesc(name)}*\n\n"
            f"❓ Savollar: {len(t['questions'])}/{mq}\n"
            f"🔐 Access: {acc}\n"
            f"👤 Yaratgan: `{t.get('created_by', '?')}`",
            parse_mode="Markdown", reply_markup=kb)
        return

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

async def run_bot():
    if not WEBHOOK_URL:
        raise SystemExit(
            "❌ WEBHOOK_URL topilmadi! Render → Environment'da "
            "WEBHOOK_URL=https://<app-nomi>.onrender.com qo'shing.")

    app = Application.builder().token(BOT_TOKEN).build()

    core._BOT_REF = app.bot

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
        ("getemojiid",      cmd_getemojiid),
        ("addchatadmin",    cmd_addchatadmin),
        ("sendas",          cmd_sendas),
        ("requireadmin",    cmd_requireadmin),
        ("addbadword",      cmd_addbadword),
        ("addsacredname",   cmd_addsacredname),
        ("addsevereword",   cmd_addsevereword),
        ("addwarning",      cmd_addwarning),
        ("listbadwords",    cmd_listbadwords),
        ("removebadword",   cmd_removebadword),
        ("removewarning",   cmd_removewarning),
        ("broadcast",       cmd_broadcast),
        ("export",          cmd_export),
        ("restore",         cmd_restore),
        # Yangi komandalar
        ("setprice",        cmd_setprice),
        ("setchannel",      cmd_setchannel),
        ("delmsgs",         cmd_delmsgs),
        ("delbotmsg",       cmd_delbotmsg),
        ("togglereaction",  cmd_togglereaction),
        ("createtopic",     cmd_createtopic),
        ("setgroup",        cmd_setgroup),
        ("userinfo",        cmd_userinfo),
        ("listusers",       cmd_listusers),
    ]
    for name, handler in cmds:
        app.add_handler(CommandHandler(name, handler))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(PreCheckoutQueryHandler(cmd_precheckout))
    app.add_error_handler(global_error_handler)

    # Guruh xabarlarini track qilish — group=0
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & ~filters.COMMAND,
        group_tracker,
    ), group=0)
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & filters.COMMAND,
        group_tracker,
    ), group=0)

    # Stars to'lov — group=0
    app.add_handler(MessageHandler(
        filters.SUCCESSFUL_PAYMENT,
        cmd_successful_payment,
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

    # Kanal postlari — ⚡ avtomatik reaksiya
    app.add_handler(MessageHandler(
        filters.UpdateType.CHANNEL_POST | filters.UpdateType.EDITED_CHANNEL_POST,
        channel_post_tracker,
    ))

    # Chat member
    app.add_handler(ChatMemberHandler(
        handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    await app.initialize()

    logger.info("Zahiradan avtomatik tiklash...")
    await auto_restore_on_startup(app.bot)
    await sync_bot_commands(app.bot)

    if not core.get_supergroup_id():
        try:
            await app.bot.send_message(
                SUPERADMIN,
                "⚠️ *Bot ishga tushdi, lekin DB guruh ulanmagan!*\n\n"
                "Zaxira nusxalash va VIP/Premium/PLUS topic'lari ishlashi uchun "
                "kerakli guruhning ICHIDA `/setgroup` buyrug'ini yuboring "
                "(yoki `/setgroup -100...` orqali tashqaridan).",
                parse_mode="Markdown")
        except Exception:
            pass

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
        return web.Response(text="🤖 Lang Bot v5.0 ishlamoqda")

    web_app = web.Application()
    web_app.router.add_get("/", health)
    web_app.router.add_get("/health", health)
    web_app.router.add_post(f"/{WEBHOOK_PATH}", telegram_webhook)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"HTTP server: 0.0.0.0:{PORT}")

    webhook_url = f"{WEBHOOK_URL}/{WEBHOOK_PATH}"
    await app.bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET or None,
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )
    logger.info(f"Webhook: {webhook_url.replace(BOT_TOKEN, '***')}")

    if app.job_queue:
        app.job_queue.run_daily(
            daily_export_job,
            time=dt_time(0, 0, 0, tzinfo=TZ),
            name="daily_export",
        )
        logger.info("Daily export: 00:00 Toshkent")

    await app.start()
    logger.info("Bot ishga tushdi (v5.0).")

    try:
        await asyncio.Event().wait()
    finally:
        logger.info("Bot to'xtatilmoqda...")
        await app.stop()
        await app.shutdown()
        await runner.cleanup()

def main():
    asyncio.run(run_bot())

