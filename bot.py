#!/usr/bin/env python3
"""
Lang Bot v5.0 — 2-qism (ASOSIY FAYL)

Bu fayl bot.py ikkiga bo'lingandan hosil bo'lgan ikkinchi qism:
admin komandalari, matn/media handlerlar, callback handler va ilova ishga tushirish (main).
Barcha config, storage, keyboard va user-menu funksiyalari core.py faylida joylashgan.

Ishga tushirish avvalgidek: python3 bot.py
"""

import core
from core import *
# `import *` orqali "_" bilan boshlanadigan (private) nomlar ko'chmaydi,
# core.py'dagi shunday funksiyalar ham (masalan _handle_user_menu,
# _send_invoice, _admin_main_kb va h.k.) bot.py'da ishlatilgani uchun
# ularni ham to'liq ko'chiramiz:
globals().update({k: v for k, v in vars(core).items() if not k.startswith("__")})

# ══════════════════════════════════════════════════════
#  ADMIN KOMANDALAR
# ══════════════════════════════════════════════════════

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
        names = ", ".join(f"`{t['name']}`" for t in topics) if topics else "(yo'q)"
        await update.message.reply_text(
            "❌ Foydalanish:\n"
            "`/newgame <topic>` — standart\n"
            "`/newgame <topic> langmode` — tarjima o'yini\n"
            "`/newgame <topic> speed [vaqt]` — tezkor (masalan `30s`)\n"
            "`/newgame admin` — admin belgilaydigan o'yin\n\n"
            f"📚 Mavjud topiclar: {names}", parse_mode="Markdown")
        return

    cid = chat.id
    g   = get_game(cid)
    if g["active"]:
        label = "admin o'yini" if g["mode"] == "admin" else f"{g['emoji']}{g['topic']}"
        await update.message.reply_text(
            f"⚠️ Allaqachon *{label}* ketmoqda!", parse_mode="Markdown")
        return

    # ── Module 1: Admin mode ──
    if args[0].lower() == "admin":
        g.update({"active": True, "mode": "admin", "topic": None, "emoji": "",
                  "questions": [], "asked": 0, "current": None,
                  "current_msg_id": None, "scores": {}, "waiting": False,
                  "time_limit": None, "admin_ranks": [],
                  "started_by": update.effective_user.id})
        await update.message.reply_text(
            "🎮 *ADMIN O'YINI BOSHLANDI!*\n\n"
            "✅ ni biror xabarga *javob* tariqasida yuboring (yoki "
            "`✅ @username`) — o'sha kishi keyingi o'ringa yoziladi.\n\n"
            "⏹ Tugatish: `/endgame`", parse_mode="Markdown")
        return

    tn = args[0].lower()
    t  = load_topic(tn)
    if not t:
        await update.message.reply_text(f"❌ `{tn}` mavjud emas!", parse_mode="Markdown")
        return
    if not t["questions"]:
        await update.message.reply_text("❌ Bu topicda savollar yo'q!")
        return

    mode       = "standard"
    time_limit = None
    if len(args) >= 2:
        m2 = args[1].lower()
        if m2 == "langmode":
            mode = "lang"
        elif m2 == "speed":
            mode = "speed"
            if len(args) >= 3:
                time_limit = parse_duration(args[2])
                if time_limit is None:
                    await update.message.reply_text(
                        "❌ Vaqt formati noto'g'ri! Masalan: `30s`, `5d`, `2soat`, `1kun`",
                        parse_mode="Markdown")
                    return
        else:
            await update.message.reply_text(
                "❌ Noma'lum rejim! `langmode` yoki `speed` bo'lishi kerak.")
            return

    qs = t["questions"].copy()
    random.shuffle(qs)
    g.update({"active": True, "mode": mode, "topic": tn, "emoji": t["emoji"],
              "questions": qs, "asked": 0, "current": None, "current_reversed": False,
              "current_msg_id": None, "scores": {}, "waiting": False,
              "time_limit": time_limit, "admin_ranks": [],
              "started_by": update.effective_user.id})
    mode_label = {"standard": "", "lang": " 🔤 Lang mode",
                  "speed": f" ⚡ Speed mode" + (f" ({args[2]})" if time_limit else "")}[mode]
    await update.message.reply_text(
        f"🎮 *O'YIN BOSHLANDI!*{mode_label}\n\n{t['emoji']} *{tn.capitalize()}*\n"
        f"📊 {len(qs)} ta savol\n\n🎯 Reply qilib javob bering!",
        parse_mode="Markdown")
    await send_question(cid, context)

async def cmd_endgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid  = update.effective_chat.id
    uid  = update.effective_user.id
    chat = update.effective_chat
    if not is_superadmin(uid):
        if chat.type not in ("group", "supergroup"):
            return
        if not await is_group_admin(update, context):
            await update.message.reply_text("❌ Faqat admin!")
            return
    g = get_game(cid)
    if not g["active"]:
        await update.message.reply_text("⚠️ Faol o'yin yo'q.")
        return

    if g.get("mode") == "admin":
        await _finish_admin_game(cid, context)
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

# ── Superadmin yangi komandalar ──

async def cmd_setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tarif narxini o'zgartirish. /setprice plus 30"""
    if not is_superadmin(update.effective_user.id):
        return
    args = context.args
    if len(args) < 2:
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

# PLUS/MINUS komandalar
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

# ══════════════════════════════════════════════════════
#  TEXT HANDLER
# ══════════════════════════════════════════════════════

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
            parse_mode="Markdown")
        return

    if step == "emojipack_waiting" and is_superadmin(uid):
        context.user_data.pop("step", None)
        pack_name = text.strip().lstrip("@")
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

# ══════════════════════════════════════════════════════
#  MEDIA HANDLER
# ══════════════════════════════════════════════════════

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

# ══════════════════════════════════════════════════════
#  GROUP TRACKER + PROFANITY + REACTION
# ══════════════════════════════════════════════════════

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

# ══════════════════════════════════════════════════════
#  ERROR HANDLER
# ══════════════════════════════════════════════════════

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

# ══════════════════════════════════════════════════════
#  CALLBACK HANDLER
# ══════════════════════════════════════════════════════

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
        await q.edit_message_text(
            f"✅ `{target}` uchun standart reaksiya (🔥) qo'llaniladi.",
            parse_mode="Markdown")
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
            parse_mode="Markdown")
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
#  MAIN
# ══════════════════════════════════════════════════════

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
    logger.info(f"Webhook: {webhook_url}")

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


if __name__ == "__main__":
    main()
