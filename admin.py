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
    ReactionTypeEmoji, ReactionTypeCustomEmoji, ReplyKeyboardMarkup, ReplyKeyboardRemove, ChatPermissions,
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
import tariffs as _tariffs
globals().update({k: v for k, v in vars(_tariffs).items() if not k.startswith('__')})

async def cmd_addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if update.effective_chat.type != "private":
        return
    adm = load_admins()
    if not is_superadmin(uid):
        info = adm.get(str(uid), {})
        if not info.get("can_add_admins"):
            return
        sub_s   = info.get("sub_admin_settings", {})
        max_adm = sub_s.get("max_admins", 0)
        if count_sub_admins(uid) >= max_adm:
            await update.message.reply_text(
                f"❌ Sub-admin limiti to'ldi! ({count_sub_admins(uid)}/{max_adm})")
            return
    args = context.args
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
                f"⚠️ `{new_uid}` allaqachon admin!", parse_mode="Markdown")
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
        await update.message.reply_text("👥 Admin yo'q.\n\nQo'shish: `/addadmin <uid>`",
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
        lines.append(f"👤 `{k}` [{dn}] topic:{owned}/{v['topic_limit']} "
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

MAX_MSG_HISTORY = 10000

async def cmd_setdisplayname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "🏷 *Display name:*\n\n"
            "O'zingiz: `/setdisplayname me 👑 Boss`\n"
            "Admin:    `/setdisplayname 123456 🌟 Ali`\n"
            "O'chirish: `/setdisplayname 123456 -`",
            parse_mode="Markdown")
        return
    target     = args[0]
    name_parts = args[1:]
    name = " ".join(name_parts) if name_parts else None
    if name == "-":
        name = None
    if target.lower() == "me":
        set_display_name(SUPERADMIN, name)
        msg = f"✅ O'z nomingiz: *{mdesc(name)}*" if name else "✅ Nomingiz o'chirildi."
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
        msg = (f"✅ `{uid_t}` uchun nom: *{mdesc(name)}*" if name
               else f"✅ `{uid_t}` nomi o'chirildi.")
    await update.message.reply_text(msg, parse_mode="Markdown")

BROADCAST_READY = "✅ Tayyor — Yuborishni boshlash"

async def cmd_newtopic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if update.effective_chat.type != "private":
        await update.message.reply_text("❌ Faqat botda (private) ishlaydi.")
        return
    # Kim topic yarata oladi?
    if not is_admin_or_superadmin(uid):
        # Oddiy user
        t_lim  = get_user_topic_limit(uid)
        owned  = count_admin_topics(uid)
        if owned >= t_lim:
            await update.message.reply_text(
                f"❌ Topic limit to'ldi! ({owned}/{t_lim})\n\n"
                "Qo'shimcha topic uchun:\n"
                "• Referal to'plang (+1 har 3 ta)\n"
                "• Tarif xarid qiling",
                reply_markup=InlineKeyboardMarkup([
                    [IKB("🛒 Tarif xarid qilish", callback_data="u:buy")]
                ]))
            return
    else:
        if is_superadmin(uid):
            pass  # superadmin cheksiz
        else:
            lim   = get_admin_topic_limit(uid)
            owned = count_admin_topics(uid)
            if owned >= lim:
                await update.message.reply_text(f"❌ Limit: {lim} ta topic ({owned}/{lim}).")
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
    context.user_data.clear()
    context.user_data.update({"step": "newtopic_emoji", "topic_name": name})
    await update.message.reply_text(
        f"✅ Topic nomi: *{mdesc(name)}*\n\n🎨 Emojiini yuboring _(masalan: 🇬🇧 🔢 🧠)_",
        parse_mode="Markdown")

TARGET_NAMES = {
    "all":      "👥 Hammaga",
    "private":  "👤 Faqat userlarga",
    "groups":   "🏘 Faqat guruhlarga",
    "channels": "📢 Faqat kanallarga",
}

TARGET_KEYS = {v: k for k, v in TARGET_NAMES.items()}

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
            await update.message.reply_text("❌ Sizga tegishli topic yo'q.")
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
        await update.message.reply_text("❌ Faqat topic egasi yoki superadmin!")
        return
    cur = ACCESS_LABELS.get(t.get("access", {}).get("type", "all"), "—")
    await update.message.reply_text(
        f"🔐 *{mdesc(name)}* — hozirgi: {cur}\n\nYangi access:",
        parse_mode="Markdown", reply_markup=_access_kb(name))

async def cmd_addq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if update.effective_chat.type != "private":
        await update.message.reply_text("❌ Faqat botda ishlaydi.")
        return
    if not is_admin_or_superadmin(uid):
        # Oddiy user ham savol qo'sha oladi — o'z topiciga
        topics = [t for t in all_topics() if t.get("created_by") == uid]
    else:
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
        topics = []
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
        return

    if not topics:
        await update.message.reply_text(
            "❌ Topicingiz yo'q.\n\nAvval /newtopic bilan topic yarating.")
        return
    kb = InlineKeyboardMarkup([
        [IKB(f"{t['emoji']} {t['name']} ({len(t['questions'])}/{get_user_q_limit(uid)})",
             callback_data=f"addq_topic:{t['name']}")]
        for t in topics
    ])
    await update.message.reply_text("📚 Qaysi topicga savol?", reply_markup=kb)

def save_admins(d: dict):
    _jsave(ADMINS_FILE, d)
    # backup.py (_BOT_REF) va bot.py (sync_bot_commands) — yuqori darajadagi
    # modullar, aylanma import bo'lmasligi uchun shu yerda kechiktirilgan
    # import qilamiz.
    from backup import _BOT_REF
    if _BOT_REF is not None:
        try:
            from bot import sync_bot_commands
            loop = asyncio.get_running_loop()
            loop.create_task(sync_bot_commands(_BOT_REF))
        except RuntimeError:
            pass  # event loop yo'q (masalan testda) — muammo emas

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
        f"📥 *{t['emoji']} {mdesc(name)}* — ommaviy\n\n"
        f"📊 {len(t['questions'])}/{mq} | yana {rem} ta\n\n"
        "*Format:*\n`apple - olma`\n`orange - apelsin - sabzirang`\n\n⏹ /done",
        parse_mode="Markdown")

def load_chats() -> dict:   return _jload(CHATS_FILE)

def save_chats(d: dict):    _jsave(CHATS_FILE, d)

def load_badwords() -> dict:
    d = _jload(BADWORDS_FILE)
    d.setdefault("words", [])
    d.setdefault("severe_words", [])
    d.setdefault("warnings", [])
    d.setdefault("sacred_names", ["muhammad", "muxammad", "muslihiddin",
                                   "ali", "holid", "xolid"])
    return d

def save_badwords(d: dict): _jsave(BADWORDS_FILE, d)

def load_incidents() -> list:
    d = _jload(INCIDENTS_FILE)
    return d.get("items", []) if isinstance(d, dict) else []

def save_incidents(items: list):
    _jsave(INCIDENTS_FILE, {"items": items[-2000:]})

def record_incident(chat, offender, target_name: str, text: str,
                     consequence: str, admin: bool, sacred: bool) -> int:
    """So'kinish hodisasini log qiladi va incident_id qaytaradi."""
    items = load_incidents()
    iid   = (items[-1]["id"] + 1) if items else 1
    items.append({
        "id": iid,
        "time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "chat_id": chat.id, "chat_title": chat.title or str(chat.id),
        "offender_id": offender.id, "offender_name": offender.first_name or "Anonim",
        "offender_username": offender.username or "",
        "target": target_name,
        "text": text[:500],
        "consequence": consequence,
        "was_admin": admin, "sacred_name": sacred,
    })
    save_incidents(items)
    return iid

def get_incident(iid: int):
    for it in load_incidents():
        if it["id"] == iid:
            return it
    return None

async def _process_bulkq(update: Update, context: ContextTypes.DEFAULT_TYPE, raw: str = None):
    tn  = context.user_data.get("topic_name")
    uid = update.effective_user.id
    t   = load_topic(tn)
    if not t:
        await update.message.reply_text("❌ Topic topilmadi.")
        context.user_data.clear()
        return
    try:
        mq    = get_admin_max_questions(uid)
        text  = (raw or update.message.text or "").strip()
        # Word/Docs'dan nusxalanganda "-" o'rniga turli chiziqchalar
        # (–, —, ‑, −) paydo bo'lishi mumkin — ularni ham qabul qilamiz
        for dash in ("–", "—", "‑", "−"):
            text = text.replace(dash, "-")
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
            msg += (f"\n\n❌ *{len(errors)} ta qator noto'g'ri formatda "
                     f"(kamida bitta \"-\" bo'lishi kerak):*\n" + "\n".join(errors[:5]))
            if len(errors) > 5:
                msg += f"\n… va yana {len(errors) - 5} ta"
        kb = None
        back_cb = "menu:back" if is_admin_or_superadmin(uid) else "u:back"
        if cnt < mq:
            kb = InlineKeyboardMarkup([
                [IKB("➕ Yana savollar", callback_data="bulkq_more"),
                 IKB("⏹ Tugatish",      callback_data="addq_finish")],
                [IKB("🏠 Bosh menyu", callback_data=back_cb)],
            ])
        else:
            kb = InlineKeyboardMarkup([[IKB("🏠 Bosh menyu", callback_data=back_cb)]])
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
        if cnt >= mq:
            context.user_data.clear()
    except Exception as e:
        logger.error(f"_process_bulkq xato: {e}")
        try:
            await update.message.reply_text(
                "⚠️ Savollarni qo'shishda kutilmagan xatolik yuz berdi. "
                "Formatni tekshirib qayta yuboring (masalan: `so'z - tarjima`).",
                parse_mode="Markdown")
        except Exception:
            pass

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
            f"{t['emoji']} *{mdesc(t['name'])}* — {len(t['questions'])}/{mq} "
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
        f"⚠️ *{mdesc(name)}* o'chirilsinmi? Barcha savollar ham o'chadi!",
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
        f"🏆 *{mdesc(name)}* uchun sovrinni yuboring _(rasm, GIF yoki stiker)_:",
        parse_mode="Markdown")

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

def get_group_setting(chat_id: int, key: str, default=False):
    return load_chats().get(str(chat_id), {}).get(key, default)

def set_group_setting(chat_id: int, key: str, value):
    chats = load_chats()
    if str(chat_id) not in chats:
        chats[str(chat_id)] = {"chat_id": chat_id, "type": "supergroup", "name": str(chat_id)}
    chats[str(chat_id)][key] = value
    save_chats(chats)

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

async def cmd_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_group_admin(update, context):
        return
    uid  = update.effective_user.id
    cid  = update.effective_chat.id
    args = context.args
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
    if not is_superadmin(uid):
        await update.message.reply_text("❌ Kengaytirilgan /del faqat superadmin uchun!")
        return
    target   = args[0]
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
            "❌ O'chiriladigan xabar topilmadi.\n_(Bot restart bo'lsa tarix yo'qoladi)_")
        return
    try:
        await update.message.delete()
    except Exception:
        pass
    prog = await context.bot.send_message(
        cid, f"🗑 *{len(to_del)} ta xabar o'chirilmoqda...*", parse_mode="Markdown")
    ids = [m["id"] for m in to_del]
    d, f = await _del_batch(context, cid, ids)
    del_set = set(ids)
    if cid in msg_history:
        msg_history[cid] = [m for m in msg_history[cid] if m["id"] not in del_set]
    try:
        await prog.edit_text(
            f"✅ *O'chirildi: {d} ta*\n❌ Xato: {f} ta",
            parse_mode="Markdown")
    except Exception:
        pass

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

DOT_COMMANDS = {"ban", "unban", "mute", "unmute", "kick", "warn", "unwarn",
                "pin", "unpin", "promote", "demote", "purge", "del"}

async def _can_use_dot_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    if uid is None:
        return False
    if uid == SUPERADMIN or is_bot_admin(uid):
        return True
    return await is_group_admin(update, context)

async def handle_dot_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """'.' bilan boshlanadigan himoyachi-bot uslubidagi komandalar
    (.ban .mute .warn .pin .del va h.k.). True qaytarsa — xabar shu
    yerda to'liq qayta ishlangan."""
    msg  = update.message
    chat = update.effective_chat
    if not msg or not msg.text or not chat or chat.type not in ("group", "supergroup"):
        return False
    text = msg.text.strip()
    if not text.startswith("."):
        return False
    parts = text[1:].split()
    if not parts:
        return False
    cmd  = parts[0].lower()
    rest = parts[1:]
    if cmd not in DOT_COMMANDS:
        return False
    if not await _can_use_dot_commands(update, context):
        return False  # admin komandasi emasday, oddiy matn sifatida o'tkazib yuboramiz

    reply = msg.reply_to_message

    if cmd == "ban":
        if not reply:
            await msg.reply_text("❗️ Foydalanuvchi xabariga javob tariqasida yozing."); return True
        try:
            await context.bot.ban_chat_member(chat.id, reply.from_user.id)
            await msg.reply_text(f"🚫 {mdesc(reply.from_user.first_name)} banned.", parse_mode="Markdown")
        except Exception as e:
            await msg.reply_text(f"❌ Xatolik: {e}")
        return True

    if cmd == "unban":
        target_uid = reply.from_user.id if reply else None
        if target_uid is None and rest:
            found = _find_user_by_username(rest[0])
            if found:
                target_uid = found[0]
        if target_uid is None:
            await msg.reply_text("❗️ Javob bering yoki `@username` yozing.", parse_mode="Markdown")
            return True
        try:
            await context.bot.unban_chat_member(chat.id, target_uid, only_if_banned=True)
            await msg.reply_text("✅ Unban qilindi.")
        except Exception as e:
            await msg.reply_text(f"❌ Xatolik: {e}")
        return True

    if cmd == "kick":
        if not reply:
            await msg.reply_text("❗️ Foydalanuvchi xabariga javob tariqasida yozing."); return True
        try:
            await context.bot.ban_chat_member(chat.id, reply.from_user.id)
            await asyncio.sleep(0.3)
            await context.bot.unban_chat_member(chat.id, reply.from_user.id)
            await msg.reply_text(f"🦵 {mdesc(reply.from_user.first_name)} chiqarib yuborildi.", parse_mode="Markdown")
        except Exception as e:
            await msg.reply_text(f"❌ Xatolik: {e}")
        return True

    if cmd == "mute":
        if not reply:
            await msg.reply_text("❗️ Foydalanuvchi xabariga javob tariqasida yozing."); return True
        until = None
        dur_label = "doimiy"
        if rest:
            secs = parse_duration(rest[0])
            if secs:
                until = datetime.now(TZ) + timedelta(seconds=secs)
                dur_label = rest[0]
        try:
            perms  = ChatPermissions(can_send_messages=False)
            kwargs = {"until_date": until} if until else {}
            await context.bot.restrict_chat_member(chat.id, reply.from_user.id,
                                                     permissions=perms, **kwargs)
            await msg.reply_text(
                f"🔇 {mdesc(reply.from_user.first_name)} mute qilindi ({dur_label}).",
                parse_mode="Markdown")
        except Exception as e:
            await msg.reply_text(f"❌ Xatolik: {e}")
        return True

    if cmd == "unmute":
        if not reply:
            await msg.reply_text("❗️ Foydalanuvchi xabariga javob tariqasida yozing."); return True
        try:
            perms = ChatPermissions(can_send_messages=True, can_send_photos=True,
                                     can_send_videos=True, can_send_other_messages=True,
                                     can_add_web_page_previews=True)
            await context.bot.restrict_chat_member(chat.id, reply.from_user.id, permissions=perms)
            await msg.reply_text(f"🔊 {mdesc(reply.from_user.first_name)} unmute qilindi.", parse_mode="Markdown")
        except Exception as e:
            await msg.reply_text(f"❌ Xatolik: {e}")
        return True

    if cmd in ("warn", "unwarn"):
        if not reply:
            await msg.reply_text("❗️ Foydalanuvchi xabariga javob tariqasida yozing."); return True
        warns = get_group_setting(chat.id, "warnings", {})
        uid_s = str(reply.from_user.id)
        cur   = warns.get(uid_s, 0)
        cur   = cur + 1 if cmd == "warn" else max(0, cur - 1)
        warns[uid_s] = cur
        set_group_setting(chat.id, "warnings", warns)
        await msg.reply_text(
            f"⚠️ {mdesc(reply.from_user.first_name)}: {cur}/3 ta ogohlantirish.",
            parse_mode="Markdown")
        if cmd == "warn" and cur >= 3:
            try:
                await context.bot.ban_chat_member(chat.id, reply.from_user.id)
                await asyncio.sleep(0.3)
                await context.bot.unban_chat_member(chat.id, reply.from_user.id)
                warns[uid_s] = 0
                set_group_setting(chat.id, "warnings", warns)
                await context.bot.send_message(
                    chat.id, "🚫 3 ta ogohlantirishdan so'ng chiqarib yuborildi.")
            except Exception:
                pass
        return True

    if cmd == "pin":
        if not reply:
            await msg.reply_text("❗️ Xabarga javob tariqasida yozing."); return True
        try:
            await context.bot.pin_chat_message(chat.id, reply.message_id)
            await msg.reply_text("📌 Pin qilindi.")
        except Exception as e:
            await msg.reply_text(f"❌ Xatolik: {e}")
        return True

    if cmd == "unpin":
        try:
            if reply:
                await context.bot.unpin_chat_message(chat.id, reply.message_id)
            else:
                await context.bot.unpin_all_chat_messages(chat.id)
            await msg.reply_text("📌 Unpin qilindi.")
        except Exception as e:
            await msg.reply_text(f"❌ Xatolik: {e}")
        return True

    if cmd in ("promote", "demote"):
        if not reply:
            await msg.reply_text("❗️ Foydalanuvchi xabariga javob tariqasida yozing."); return True
        try:
            grant = (cmd == "promote")
            await context.bot.promote_chat_member(
                chat.id, reply.from_user.id,
                can_delete_messages=grant, can_restrict_members=grant,
                can_pin_messages=grant, can_invite_users=grant)
            verb = "admin qilindi" if grant else "admindan olindi"
            await msg.reply_text(f"👮 {mdesc(reply.from_user.first_name)} {verb}.", parse_mode="Markdown")
        except Exception as e:
            await msg.reply_text(f"❌ Xatolik: {e}")
        return True

    if cmd == "purge":
        if not reply:
            await msg.reply_text("❗️ Boshlanish xabariga javob tariqasida yozing."); return True
        ids = list(range(reply.message_id, msg.message_id + 1))
        d, f = await _del_batch(context, chat.id, ids)
        del_set = set(ids)
        if chat.id in msg_history:
            msg_history[chat.id] = [m for m in msg_history[chat.id] if m["id"] not in del_set]
        try:
            await context.bot.send_message(chat.id, f"🧹 {d} ta xabar o'chirildi.")
        except Exception:
            pass
        return True

    if cmd == "del":
        # ".del @ a" — bot kuzatgan BUTUN tarixni tozalash
        if len(rest) >= 2 and rest[0] == "@" and rest[1].lower() == "a":
            hist = msg_history.get(chat.id, []).copy()
            ids  = [m["id"] for m in hist]
            d, f = await _del_batch(context, chat.id, ids)
            msg_history[chat.id] = []
            try:
                await msg.delete()
            except Exception:
                pass
            try:
                await context.bot.send_message(chat.id, f"🧹 Butun tarix tozalandi: {d} ta xabar.")
            except Exception:
                pass
            return True
        # ".del @username" — shu userning barcha xabarlarini o'chirish
        if rest and rest[0].startswith("@"):
            uname   = rest[0].lstrip("@").lower()
            hist    = msg_history.get(chat.id, [])
            to_del  = [m for m in hist if m.get("uname") == uname]
            ids     = [m["id"] for m in to_del]
            d, f    = await _del_batch(context, chat.id, ids)
            del_set = set(ids)
            if chat.id in msg_history:
                msg_history[chat.id] = [m for m in msg_history[chat.id] if m["id"] not in del_set]
            try:
                await msg.delete()
            except Exception:
                pass
            try:
                await context.bot.send_message(chat.id, f"🧹 @{uname}: {d} ta xabar o'chirildi.")
            except Exception:
                pass
            return True
        # ".del" (reply) — bitta xabarni o'chirish
        if reply:
            try:
                await reply.delete()
            except Exception:
                pass
            try:
                await msg.delete()
            except Exception:
                pass
            return True
        await msg.reply_text(
            "❗️ Xabarga javob bering, `.del @username` yoki `.del @ a` yozing.",
            parse_mode="Markdown")
        return True

    return True

async def create_class_topic(bot, class_name: str) -> int | None:
    """Sinf uchun forum topic yaratadi. Masalan: '8A sinfi'"""
    key = f"class_{class_name.lower().replace(' ', '_')}"
    return await ensure_forum_topic(bot, class_name, key, 0x6FB9F0)

async def cmd_delmsgs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guruh xabarlarini o'chirish. /delmsgs <chat_id> [user_id]"""
    if not is_superadmin(update.effective_user.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ `/delmsgs <chat_id> [user_id]`\n\n"
            "Misollar:\n"
            "`/delmsgs -100123456` — guruhning barcha xabarlarini\n"
            "`/delmsgs -100123456 123456789` — faqat shu userni",
            parse_mode="Markdown")
        return
    try:
        chat_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ chat_id raqam bo'lishi kerak.")
        return
    user_id = None
    if len(args) >= 2:
        try:
            user_id = int(args[1])
        except ValueError:
            pass
    history = msg_history.get(chat_id, [])
    if user_id:
        to_del = [m for m in history if m["uid"] == user_id]
    else:
        to_del = history.copy()
    if not to_del:
        await update.message.reply_text(
            "❌ Xabar topilmadi. Bot restart bo'lgandan beri xabar yo'q.")
        return
    prog = await update.message.reply_text(
        f"🗑 *{len(to_del)} ta xabar o'chirilmoqda...*", parse_mode="Markdown")
    ids = [m["id"] for m in to_del]
    d, f = await _del_batch(context, chat_id, ids)
    del_set = set(ids)
    if chat_id in msg_history:
        msg_history[chat_id] = [m for m in msg_history[chat_id] if m["id"] not in del_set]
    try:
        await prog.edit_text(
            f"✅ *O'chirildi: {d} ta*\n❌ Xato: {f} ta",
            parse_mode="Markdown")
    except Exception:
        pass

async def cmd_delbotmsg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot xabarini o'chirish. /delbotmsg <chat_id> <msg_id>"""
    if not is_superadmin(update.effective_user.id):
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "❌ `/delbotmsg <chat_id> <msg_id>`", parse_mode="Markdown")
        return
    try:
        chat_id = int(args[0])
        msg_id  = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ Raqam kiriting.")
        return
    try:
        await context.bot.delete_message(chat_id, msg_id)
        await update.message.reply_text("✅ Xabar o'chirildi.")
    except Exception as e:
        await update.message.reply_text(f"❌ O'chirib bo'lmadi:\n`{e}`",
                                        parse_mode="Markdown")

async def cmd_createtopic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Supergroup'da forum topic yaratish. /createtopic <nom>"""
    if not is_superadmin(update.effective_user.id):
        return
    if not get_supergroup_id():
        await update.message.reply_text(
            "❌ DB guruh ulanmagan! Avval `/setgroup` bilan ulang.",
            parse_mode="Markdown")
        return
    args = context.args
    if not args:
        ft = get_forum_topics()
        lines = [f"• `{k}` → thread_id: `{v}`" for k, v in ft.items()]
        text  = "📋 *Mavjud forum topiclar:*\n\n" + ("\n".join(lines) if lines else "Hali yo'q")
        kb = InlineKeyboardMarkup([
            [IKB("👑 VIP topic",     callback_data="ft:vip"),
             IKB("💎 Premium topic", callback_data="ft:premium")],
            [IKB("✨ PLUS topic",    callback_data="ft:plus"),
             IKB("📦 Backup topic",  callback_data="ft:backup")],
            [IKB("🏫 Sinf topici yaratish", callback_data="ft:class")],
        ])
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
        return
    name = " ".join(args)
    tid  = await ensure_forum_topic(context.bot, name, f"custom_{name.lower().replace(' ','_')}")
    if tid:
        await update.message.reply_text(
            f"✅ Topic yaratildi: *{mdesc(name)}*\nID: `{tid}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Topic yaratib bo'lmadi.")

USERS_PER_PAGE = 8

def _build_users_page(page: int):
    """(matn, keyboard) qaytaradi — userlar sahifasi uchun. Userlar
    bo'lmasa (None, None) qaytaradi."""
    users = load_users()
    if not users:
        return None, None
    uids  = sorted(users.keys(), key=lambda k: int(k))
    total = len(uids)
    start = page * USERS_PER_PAGE
    if start >= total:
        page, start = 0, 0
    chunk = uids[start:start + USERS_PER_PAGE]

    rows = []
    for uid_s in chunk:
        u     = users[uid_s]
        name  = u.get("first_name") or "Anonim"
        uname = f"@{u['username']}" if u.get("username") else uid_s
        tarif = TARIF_NAMES.get(get_user_tarif(int(uid_s)), "")
        rows.append([IKB(f"{name} ({uname}) — {tarif}",
                          callback_data=f"user_detail:{uid_s}:{page}")])
    nav = []
    if page > 0:
        nav.append(IKB("⬅️", callback_data=f"userslist:{page-1}"))
    if start + USERS_PER_PAGE < total:
        nav.append(IKB("➡️", callback_data=f"userslist:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([IKB("⬅️ Orqaga", callback_data="menu:back")])

    text = (f"👤 *Foydalanuvchilar ({total} ta)* — {page + 1}-sahifa\n\n"
            "Biror userni tanlang:")
    return text, InlineKeyboardMarkup(rows)

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

async def _render_users_list(q, page: int):
    """Userlarni sahifalab, har biriga bosilganda to'liq ma'lumot +
    tarif/referral berish tugmalari chiqadigan ro'yxatni ko'rsatadi."""
    text, kb = _build_users_page(page)
    if text is None:
        await q.edit_message_text(
            "👤 Userlar yo'q.",
            reply_markup=InlineKeyboardMarkup([[IKB("⬅️ Orqaga", callback_data="menu:back")]]))
        return
    await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)

async def cmd_userinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User ma'lumotlari. /userinfo <uid>"""
    if not is_superadmin(update.effective_user.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text("❌ `/userinfo <uid>`", parse_mode="Markdown")
        return
    try:
        uid_t = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Raqam kiriting.")
        return
    text = format_user_info(uid=uid_t)
    kb = InlineKeyboardMarkup([
        [IKB("💎 Tarif berish", callback_data=f"grant_tarif:{uid_t}:0")],
        [IKB("🎁 Referral berish", callback_data=f"grant_ref:{uid_t}:0")],
        [IKB("✍️ Xabar yozish", callback_data=f"user_msg:{uid_t}:0")],
    ])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

async def cmd_listusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Barcha foydalanuvchilar ro'yxati — har biri bosilsa to'liq
    ma'lumot (username, chat ID, topiclari) + tarif/referral berish
    tugmalari bilan."""
    if not is_superadmin(update.effective_user.id):
        return
    text, kb = _build_users_page(0)
    if text is None:
        await update.message.reply_text("👤 Userlar yo'q.")
        return
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)

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

async def cmd_addchatadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Superadmin: bot admin bo'lgan istalgan guruh YOKI kanalga boshqa
    foydalanuvchini admin qilib tayinlaydi."""
    if not is_superadmin(update.effective_user.id):
        return
    chats = load_chats()
    if not chats:
        await update.message.reply_text("❌ Ro'yxatda hech qanday guruh/kanal yo'q.")
        return
    btns = []
    for k, v in chats.items():
        icon = "📢" if v.get("type") == "channel" else "👥"
        btns.append([IKB(f"{icon} {v.get('name', k)}", callback_data=f"acadm_chat:{k}")])
    await update.message.reply_text(
        "👮 *Qaysi guruh/kanalga admin tayinlaysiz?*\n\n"
        "_(Bot o'sha joyda admin va \"Add new admins\" huquqiga ega bo'lishi kerak)_",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(btns))

def _extract_pack_name(text: str) -> str | None:
    """'developeremojis', '@developeremojis',
    'https://t.me/addemoji/developeremojis' yoki
    't.me/addstickers/developeremojis' — barchasidan pack nomini ajratadi."""
    t = text.strip()
    m = re.search(r"t\.me/(?:addemoji|addstickers)/([A-Za-z0-9_]+)", t, re.IGNORECASE)
    if m:
        return m.group(1)
    t = t.lstrip("@").strip()
    if re.fullmatch(r"[A-Za-z0-9_]+", t):
        return t
    return None

EMOJI_PACK_PAGE_SIZE = 40  # 8 satr x 5 tugma

def _build_emoji_pack_page(items: list, pack_name: str, page: int):
    per_page = EMOJI_PACK_PAGE_SIZE
    start = page * per_page
    chunk = items[start:start + per_page]
    rows, row = [], []
    for i, item in enumerate(chunk, start=start):
        row.append(IKB(item["glyph"], callback_data=f"epick:{i}"))
        if len(row) == 5:
            rows.append(row); row = []
    if row:
        rows.append(row)
    nav = []
    if page > 0:
        nav.append(IKB("⬅️", callback_data=f"epage:{page-1}"))
    if start + per_page < len(items):
        nav.append(IKB("➡️", callback_data=f"epage:{page+1}"))
    if nav:
        rows.append(nav)
    text = (f"📦 *{mdesc(pack_name)}* — {len(items)} ta emoji, {page + 1}-sahifa\n\n"
            "Birini tanlang _(ba'zi emojilar bir xil belgi bilan ko'rinishi "
            "mumkin — kerak bo'lsa bir nechtasini sinab ko'ring)_:")
    return text, InlineKeyboardMarkup(rows)

async def cmd_getemojiid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Superadmin: emoji pack (custom emoji sticker set) nomini so'raydi,
    keyin o'sha pack'dagi barcha emojilarni inline tugmalar qilib
    chiqaradi — birini tanlasa, custom_emoji_id'sini beradi."""
    if not is_superadmin(update.effective_user.id):
        return
    context.user_data["step"] = "emojipack_waiting"
    await update.message.reply_text(
        "📦 Emoji pack (sticker set) nomini yuboring.\n\n"
        "_Nomini topish: pack'dagi istalgan emojini bosing → \"Share\" yoki "
        "pack havolasidagi `addemoji/` dan keyingi qism (masalan "
        "`t.me/addemoji/`*`MyPack`*` — shu yerda \"MyPack\" nomi kerak)._",
        parse_mode="Markdown")

async def cmd_sendas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    chats  = load_chats()
    groups = {k: v for k, v in chats.items() if v.get("type") in ("group", "supergroup")}
    if not groups:
        await update.message.reply_text("❌ Ro'yxatda guruh yo'q.")
        return
    btns = [[IKB(v.get("name", k), callback_data=f"sendas:{k}")] for k, v in groups.items()]
    await update.message.reply_text(
        "📤 *Botdan xabar yuborish*\n\nQaysi guruhga?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns))

async def cmd_requireadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        return
    chats  = load_chats()
    groups = {k: v for k, v in chats.items() if v.get("type") in ("group", "supergroup")}
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
        "🟢 YONIQ\n🔴 O'CHIQ\n\nBosib yoqing/o'chiring:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(btns))

async def cmd_addbadword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id): return
    args = context.args
    if not args:
        context.user_data["step"] = "addbadword_waiting"
        await update.message.reply_text("✏️ Qo'shmoqchi bo'lgan so'zni yozing:")
        return
    word = " ".join(args).lower().strip()
    bw   = load_badwords()
    if word in bw["words"] or word in bw["severe_words"]:
        await update.message.reply_text(f"⚠️ `{word}` allaqachon ro'yxatda!",
                                        parse_mode="Markdown"); return
    bw["words"].append(word)
    save_badwords(bw)
    await update.message.reply_text(f"✅ So'z qo'shildi: `{word}`", parse_mode="Markdown")

async def cmd_addsacredname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Superadmin: diний shaxs nomini ro'yxatga qo'shadi — bu nomlar
    so'kinish bilan birga kelsa, xabar admin bo'lsa ham o'chiriladi."""
    if not is_superadmin(update.effective_user.id): return
    args = context.args
    if not args:
        await update.message.reply_text("❌ `/addsacredname Muhammad`", parse_mode="Markdown")
        return
    name = " ".join(args).lower().strip()
    bw   = load_badwords()
    if name in bw["sacred_names"]:
        await update.message.reply_text(f"⚠️ `{name}` allaqachon ro'yxatda!",
                                        parse_mode="Markdown"); return
    bw["sacred_names"].append(name)
    save_badwords(bw)
    await update.message.reply_text(f"✅ Diний nom qo'shildi: `{name}`", parse_mode="Markdown")

async def cmd_addsevereword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id): return
    args = context.args
    if not args:
        await update.message.reply_text("❌ `/addsevereword so'z`", parse_mode="Markdown"); return
    word = " ".join(args).lower().strip()
    bw   = load_badwords()
    if word in bw["severe_words"]:
        await update.message.reply_text(f"⚠️ `{word}` allaqachon qo'pol ro'yxatda!",
                                        parse_mode="Markdown"); return
    if word in bw["words"]:
        bw["words"].remove(word)
    bw["severe_words"].append(word)
    save_badwords(bw)
    await update.message.reply_text(f"🚫 Qo'pol so'z qo'shildi: `{word}`", parse_mode="Markdown")

async def cmd_addwarning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id): return
    args = context.args
    if not args:
        await update.message.reply_text("❌ `/addwarning Matn`", parse_mode="Markdown"); return
    text = " ".join(args)
    bw   = load_badwords()
    bw["warnings"].append(text)
    save_badwords(bw)
    await update.message.reply_text(f"✅ Ogohlantirish qo'shildi:\n_{text}_", parse_mode="Markdown")

async def cmd_listbadwords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id): return
    bw     = load_badwords()
    words  = bw.get("words", [])
    severe = bw.get("severe_words", [])
    warns  = bw.get("warnings", [])
    msg    = "🔤 *So'z filtri:*\n\n"
    if words:
        msg += f"⚠️ *Oddiy ({len(words)} ta):*\n" + "\n".join(f"• `{w}`" for w in words) + "\n\n"
    if severe:
        msg += f"🚫 *Qo'pol ({len(severe)} ta):*\n" + "\n".join(f"• `{w}`" for w in severe) + "\n\n"
    if warns:
        msg += f"💬 *Ogohlantirishlar ({len(warns)} ta):*\n"
        for i, w in enumerate(warns, 1):
            msg += f"{i}. _{w}_\n"
    await update.message.reply_text(msg or "Bo'sh.", parse_mode="Markdown")

async def cmd_removebadword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id): return
    args = context.args
    if not args:
        await update.message.reply_text("❌ `/removebadword so'z`", parse_mode="Markdown"); return
    word = " ".join(args).lower().strip()
    bw   = load_badwords()
    if word in bw["words"]:
        bw["words"].remove(word); save_badwords(bw)
        await update.message.reply_text(f"✅ O'chirildi: `{word}`", parse_mode="Markdown")
    elif word in bw["severe_words"]:
        bw["severe_words"].remove(word); save_badwords(bw)
        await update.message.reply_text(f"✅ Qo'pol ro'yxatdan o'chirildi: `{word}`",
                                        parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ `{word}` topilmadi!", parse_mode="Markdown")

async def cmd_removewarning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id): return
    args = context.args
    if not args:
        await update.message.reply_text("❌ `/removewarning 1`", parse_mode="Markdown"); return
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
    await update.message.reply_text(f"✅ O'chirildi:\n_{removed}_", parse_mode="Markdown")

async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin_or_superadmin(update.effective_user.id):
        return
    try:
        tn  = context.user_data.get("topic_name", "?")
        t   = load_topic(tn) if tn and tn != "?" else None
        cnt = len(t["questions"]) if t else 0
        context.user_data.clear()
        if t:
            await update.message.reply_text(
                f"✅ *Tugatildi!*\n{tn}: {cnt} ta savol saqlangan.",
                parse_mode="Markdown")
        else:
            await update.message.reply_text(
                "✅ *Tugatildi!* (Faol savol qo'shish jarayoni topilmadi — "
                "ehtimol allaqachon yakunlangan edi.)",
                parse_mode="Markdown")
    except Exception as e:
        logger.error(f"cmd_done xato: {e}")
        context.user_data.clear()
        try:
            await update.message.reply_text(
                "⚠️ Yakunlashda xatolik yuz berdi, lekin jarayon tozalandi. "
                "Qayta urinib ko'ring.")
        except Exception:
            pass

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
    dn_str = f"\n🏷 Nom: {mdesc(display_name)}" if display_name else ""
    await q.edit_message_text(
        f"✅ *Admin qo'shildi!*\n\n"
        f"👤 UID: `{new_uid}`\n"
        f"📁 Topic limiti: {tlim} ta\n"
        f"❓ Savol limiti: {mq} ta/topic{dn_str}{ca_str}\n\n"
        f"Jami adminlar: {len(adm)} ta\n\n"
        f"🎭 *Bu adminning guruhdagi xabarlariga qanday reaksiya bosay?*",
        parse_mode="Markdown",
        reply_markup=_reaction_pick_kb(new_uid))

async def _relay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    hdr  = (f"📨 *Foydalanuvchidan:*\n"
            f"👤 [{mdesc(user.first_name)}](tg://user?id={user.id}) | `{user.id}`"
            + (f" | @{mdesc(user.username)}" if user.username else ""))
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

async def check_profanity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not msg.text or not user:
        return
    if user.is_bot or user.id == SUPERADMIN:
        return
    bw      = load_badwords()
    text    = msg.text
    severe  = bw.get("severe_words", [])
    normal  = bw.get("words", [])
    sacred  = bw.get("sacred_names", [])
    has_severe = _has_badword(text, severe)
    has_normal = _has_badword(text, normal)
    if not has_severe and not has_normal:
        return
    has_sacred = _has_badword(text, sacred)

    warn_msg = mdesc(_random_warning(bw.get("warnings", [])))
    ulink    = f"[{mdesc(user.first_name)}](tg://user?id={user.id})"

    try:
        bm = await context.bot.get_chat_member(chat.id, context.bot.id)
        bot_adm = bm.status in ("administrator", "creator")
    except Exception:
        bot_adm = False
    try:
        um = await context.bot.get_chat_member(chat.id, user.id)
        usr_adm = um.status in ("administrator", "creator")
    except Exception:
        usr_adm = False

    reply_user  = msg.reply_to_message.from_user if msg.reply_to_message else None
    target_name = (get_display_name(reply_user.id, reply_user.first_name or "Anonim")
                   if reply_user else "Umumiy (aniq kimgadir emas)")

    # Diний shaxslar nomi bilan qo'shib so'kinish — eng qattiq chora,
    # admin bo'lsa ham (leniency ishlamaydi).
    escalate = has_sacred and (has_severe or has_normal)

    deleted = False
    if bot_adm and (has_severe or escalate):
        try:
            await msg.delete()
            deleted = True
        except Exception:
            pass

    consequence = ""
    if escalate:
        try:
            await context.bot.set_message_reaction(
                chat_id=chat.id, message_id=msg.message_id,
                reaction=[ReactionTypeEmoji(emoji="🤬")], is_big=False)
        except Exception:
            pass
        await context.bot.send_message(
            chat.id,
            f"🤬 {ulink}, *diний shaxslar nomi bilan so'kinish qat'iyan man etiladi!*\n{warn_msg}",
            parse_mode="Markdown")
        consequence = ("O'chirildi + " if deleted else "") + "Diний nom bilan — kuchli ogohlantirish"
    elif has_severe:
        if usr_adm:
            try:
                await context.bot.set_message_reaction(
                    chat_id=chat.id, message_id=msg.message_id,
                    reaction=[ReactionTypeEmoji(emoji="🗿")], is_big=False)
            except Exception:
                pass
            await context.bot.send_message(
                chat.id, f"🚫 {ulink}, *{warn_msg}*\n_(Admin bo'lsangizda ham!)_",
                parse_mode="Markdown")
            consequence = "Admin — faqat ogohlantirildi (🗿)"
        else:
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
                f"🚫 {ulink} guruhdan *chiqarib yuborildi!*\n*Sabab:* Juda qo'pol so'z\n\n💬 _{warn_msg}_",
                parse_mode="Markdown")
            consequence = "Chiqarib yuborildi" if kicked else "Chiqarish muvaffaqiyatsiz"
    elif has_normal:
        if bot_adm and not deleted:
            try:
                await msg.delete()
                deleted = True
            except Exception:
                pass
        if usr_adm:
            try:
                await context.bot.set_message_reaction(
                    chat_id=chat.id, message_id=msg.message_id,
                    reaction=[ReactionTypeEmoji(emoji="🗿")], is_big=False)
            except Exception:
                pass
        await context.bot.send_message(chat.id, f"⚠️ {ulink}, {warn_msg}", parse_mode="Markdown")
        consequence = ("O'chirildi" if deleted else "Ogohlantirildi") + (", Admin (🗿)" if usr_adm else "")

    iid = record_incident(chat, user, target_name, text, consequence, usr_adm, escalate)
    await _notify_incident(context.bot, chat, user, iid)

async def _notify_incident(bot, chat, offender, incident_id: int):
    """Guruh egasi (creator) va superadminga so'kinish haqida shaxsiy xabar
    + 'Ko'rish' tugmasi bilan yuboradi."""
    who = f"@{mdesc(offender.username)}" if offender.username else mdesc(offender.first_name)
    text = (f"🚨 *So'kinish aniqlandi!*\n\n"
            f"👤 {who}\n"
            f"💬 Guruh: *{mdesc(chat.title or str(chat.id))}*")
    kb = InlineKeyboardMarkup([[IKB("🔍 Ko'rish", callback_data=f"profview:{incident_id}")]])
    recipients = {SUPERADMIN}
    try:
        admins = await bot.get_chat_administrators(chat.id)
        for a in admins:
            if a.status == "creator":
                recipients.add(a.user.id)
    except Exception as e:
        logger.warning(f"_notify_incident: adminlarni olib bo'lmadi: {e}")
    for rid in recipients:
        try:
            await bot.send_message(rid, text, parse_mode="Markdown", reply_markup=kb)
        except Exception as e:
            logger.debug(f"_notify_incident: {rid} ga yuborilmadi: {e}")

async def group_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        return
    msg = update.message
    if not msg:
        return
    u = update.effective_user
    if u:
        track_msg(chat.id, msg.message_id, u.id, u.username, msg.date.timestamp())

        # 🎭 Reaksiya — bot adminlariga (superadmindan tashqari) shaxsiy belgilangan reaksiya
        if u.id != SUPERADMIN and is_bot_admin(u.id):
            adm_data = load_admins().get(str(u.id), {})
            custom_id = adm_data.get("reaction_custom_emoji_id")
            named     = adm_data.get("reaction_emoji")
            if custom_id:
                asyncio.create_task(send_custom_reaction(context.bot, chat.id, msg.message_id, custom_id))
            elif named:
                asyncio.create_task(send_named_reaction(context.bot, chat.id, msg.message_id, named))
            else:
                asyncio.create_task(send_fire_reaction(context.bot, chat.id, msg.message_id))
        else:
            # 🔥 Reaksiya — tarif egalariga
            tarif = get_user_tarif(u.id)
            if tarif in (TARIF_PLUS, TARIF_PREMIUM, TARIF_VIP):
                asyncio.create_task(send_fire_reaction(context.bot, chat.id, msg.message_id))

        # ⚡ Reaksiya — superadmin xabarlariga (agar yoqilgan bo'lsa)
        if u.id == SUPERADMIN and load_config().get("lightning_reaction_enabled", True):
            asyncio.create_task(send_lightning_reaction(context.bot, chat.id, msg.message_id))

    if msg.text:
        await check_profanity(update, context)

async def channel_post_tracker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot kanalga qo'shilgach, kanaldagi har bir postga ⚡ reaksiya bosadi."""
    post = update.channel_post or update.edited_channel_post
    if not post:
        return
    asyncio.create_task(
        send_lightning_reaction(context.bot, post.chat.id, post.message_id))

