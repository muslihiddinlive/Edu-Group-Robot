"""
Tests filling coverage gaps in core admin flows that had zero prior tests:
cmd_done, _process_bulkq (dash normalization + error hardening),
cmd_deletetopic, cmd_setprize, cmd_setprice, cmd_setchannel,
do_export / apply_restore_data roundtrip, cmd_broadcast entry.

Run with: pytest tests/test_core_flows.py -v
"""
import asyncio
import importlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

SUPERADMIN_ID = 999999


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "TEST:TOKEN")
    monkeypatch.setenv("SUPERADMIN_ID", str(SUPERADMIN_ID))
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("bot", None)
    sys.modules.pop("core", None)
    module = importlib.import_module("bot")
    monkeypatch.setattr(module, "mark_changed", lambda: None)
    yield module
    sys.modules.pop("bot", None)
    sys.modules.pop("core", None)


class FakeMsg:
    _next_id = 8000
    def __init__(self, text=None):
        FakeMsg._next_id += 1
        self.message_id = FakeMsg._next_id
        self.text = text
        self.replies = []
        self.reply_markups = []

    async def reply_text(self, text, reply_markup=None, **kw):
        self.replies.append(text)
        self.reply_markups.append(reply_markup)
        return FakeMsg(text)


class FakeUpdate:
    def __init__(self, uid, text, chat_type="private"):
        self.effective_user = SimpleNamespace(id=uid, username="tester")
        self.effective_chat = SimpleNamespace(type=chat_type, id=uid)
        self.message = FakeMsg(text)


@pytest.fixture
def ctx():
    c = SimpleNamespace()
    c.bot = MagicMock()
    c.args = []
    c.user_data = {}
    return c


def _mk_topic(bot, name="english", created_by=SUPERADMIN_ID, questions=None):
    bot.save_topic({
        "name": name, "emoji": "📘", "prize": None,
        "created_by": created_by, "access": {"type": "all", "allowed": []},
        "questions": questions or [],
    })


# ══════════════════════════════════════════════════════
# cmd_done — the reported bug area
# ══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_done_with_active_bulkq_session_reports_count(bot, ctx):
    _mk_topic(bot, questions=[{"question": "a", "answer": "b", "alternatives": []}])
    ctx.user_data.update({"step": "bulkq_waiting", "topic_name": "english"})
    update = FakeUpdate(SUPERADMIN_ID, "/done")
    await bot.cmd_done(update, ctx)
    assert update.message.replies
    assert "english" in update.message.replies[0]
    assert "1 ta savol" in update.message.replies[0]
    assert ctx.user_data == {}


@pytest.mark.asyncio
async def test_done_with_no_active_session_still_replies_gracefully(bot, ctx):
    """Bu — skrinshotdagi muammo: sessiya (masalan bot restart bo'lgani
    uchun) yo'qolgan bo'lsa ham, /done HECH NARSA demasdan jim
    qolmasligi kerak."""
    update = FakeUpdate(SUPERADMIN_ID, "/done")
    ctx.user_data.clear()  # hech qanday step yo'q — masalan restart simulyatsiyasi
    await bot.cmd_done(update, ctx)
    assert update.message.replies, "cmd_done hech narsa demasdan jim qoldi!"
    assert "Tugatildi" in update.message.replies[0]


@pytest.mark.asyncio
async def test_done_with_deleted_topic_does_not_crash(bot, ctx):
    """topic_name user_data'da bor, lekin topic keyinchalik o'chirilgan bo'lsa."""
    ctx.user_data.update({"step": "bulkq_waiting", "topic_name": "ghost_topic"})
    update = FakeUpdate(SUPERADMIN_ID, "/done")
    await bot.cmd_done(update, ctx)
    assert update.message.replies
    assert "Tugatildi" in update.message.replies[0]


@pytest.mark.asyncio
async def test_done_non_admin_silently_ignored(bot, ctx):
    update = FakeUpdate(111, "/done")
    await bot.cmd_done(update, ctx)
    assert update.message.replies == []


# ══════════════════════════════════════════════════════
# _process_bulkq — dash normalization + error hardening
# ══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bulkq_accepts_various_dash_characters(bot, ctx):
    """Skrinshotdagi asosiy sabab: Word/Docs'dan nusxalanganda oddiy '-'
    o'rniga en-dash/em-dash paydo bo'lishi mumkin — bular ham qabul
    qilinishi kerak, aks holda HAMMA qator 'xato' bo'lib qoladi."""
    _mk_topic(bot)
    ctx.user_data.update({"topic_name": "english"})
    text = (
        "apple - olma\n"
        "orange – apelsin – olovrang\n"   # en-dash (–)
        "banana — banan\n"                 # em-dash (—)
        "grape ‑ uzum"                      # non-breaking hyphen (‑)
    )
    update = FakeUpdate(SUPERADMIN_ID, text)
    await bot._process_bulkq(update, ctx)
    t = bot.load_topic("english")
    assert len(t["questions"]) == 4
    assert any(q["question"] == "orange" and q["answer"] == "apelsin" for q in t["questions"])
    reply = update.message.replies[-1]
    assert "4 ta savol qo'shildi" in reply
    assert "Xatolar" not in reply and "noto'g'ri formatda" not in reply


@pytest.mark.asyncio
async def test_bulkq_invalid_lines_reported_clearly(bot, ctx):
    _mk_topic(bot)
    ctx.user_data.update({"topic_name": "english"})
    text = "apple - olma\nbu qatorda chiziqcha yoq\nbanana - banan"
    update = FakeUpdate(SUPERADMIN_ID, text)
    await bot._process_bulkq(update, ctx)
    t = bot.load_topic("english")
    assert len(t["questions"]) == 2
    reply = update.message.replies[-1]
    assert "1 ta qator noto'g'ri formatda" in reply


@pytest.mark.asyncio
async def test_bulkq_respects_question_limit_and_skips_rest(bot, ctx, monkeypatch):
    _mk_topic(bot)
    monkeypatch.setattr(bot, "get_admin_max_questions", lambda uid: 2)
    ctx.user_data.update({"topic_name": "english"})
    text = "a - 1\nb - 2\nc - 3\nd - 4"
    update = FakeUpdate(SUPERADMIN_ID, text)
    await bot._process_bulkq(update, ctx)
    t = bot.load_topic("english")
    assert len(t["questions"]) == 2
    assert "o'tkazildi" in update.message.replies[-1]
    # Limit to'lgani uchun sessiya avtomatik tugatilishi kerak
    assert ctx.user_data == {}


@pytest.mark.asyncio
async def test_bulkq_missing_topic_clears_session_gracefully(bot, ctx):
    ctx.user_data.update({"topic_name": "yoq_topic", "step": "bulkq_waiting"})
    update = FakeUpdate(SUPERADMIN_ID, "a - b")
    await bot._process_bulkq(update, ctx)
    assert "Topic topilmadi" in update.message.replies[-1]
    assert ctx.user_data == {}


# ══════════════════════════════════════════════════════
# addq — bitta-bitta savol qo'shish to'liq oqimi
# ══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_addq_single_full_flow_saves_question(bot, ctx):
    _mk_topic(bot)
    ctx.user_data.update({"step": "addq_question", "topic_name": "english"})
    update1 = FakeUpdate(SUPERADMIN_ID, "apple")
    await bot.handle_text(update1, ctx)
    assert ctx.user_data.get("step") == "addq_answer"
    assert ctx.user_data.get("q_question") == "apple"

    update2 = FakeUpdate(SUPERADMIN_ID, "Olma")
    await bot.handle_text(update2, ctx)
    assert ctx.user_data.get("step") == "addq_alts"
    assert ctx.user_data.get("q_answer") == "olma"

    ctx.user_data["q_media_type"] = "none"
    ctx.user_data["q_file_id"] = None
    update3 = FakeUpdate(SUPERADMIN_ID, "final")
    await bot._save_q(update3, ctx)
    t = bot.load_topic("english")
    assert len(t["questions"]) == 1
    assert t["questions"][0]["question"] == "apple"
    assert t["questions"][0]["answer"] == "olma"


# ══════════════════════════════════════════════════════
# deletetopic / setprize
# ══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_deletetopic_asks_confirmation(bot, ctx):
    _mk_topic(bot)
    ctx.args = ["english"]
    update = FakeUpdate(SUPERADMIN_ID, "/deletetopic english")
    await bot.cmd_deletetopic(update, ctx)
    assert "o'chirilsinmi" in update.message.replies[0]
    assert update.message.reply_markups[0] is not None


@pytest.mark.asyncio
async def test_deletetopic_nonexistent_topic(bot, ctx):
    ctx.args = ["ghost"]
    update = FakeUpdate(SUPERADMIN_ID, "/deletetopic ghost")
    await bot.cmd_deletetopic(update, ctx)
    assert "mavjud emas" in update.message.replies[0]


@pytest.mark.asyncio
async def test_setprize_starts_waiting_step(bot, ctx):
    _mk_topic(bot)
    ctx.args = ["english"]
    update = FakeUpdate(SUPERADMIN_ID, "/setprize english")
    await bot.cmd_setprize(update, ctx)
    assert ctx.user_data.get("step") == "setprize_waiting"
    assert ctx.user_data.get("topic_name") == "english"


# ══════════════════════════════════════════════════════
# setprice / setchannel
# ══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_setprice_shows_current_prices_with_no_args(bot, ctx):
    update = FakeUpdate(SUPERADMIN_ID, "/setprice")
    await bot.cmd_setprice(update, ctx)
    assert "Tarif narxlari" in update.message.replies[0]


@pytest.mark.asyncio
async def test_setprice_updates_price(bot, ctx):
    ctx.args = ["plus", "77"]
    update = FakeUpdate(SUPERADMIN_ID, "/setprice plus 77")
    await bot.cmd_setprice(update, ctx)
    assert bot.get_tarif_prices()[bot.TARIF_PLUS] == 77
    assert "77" in update.message.replies[0]


@pytest.mark.asyncio
async def test_setprice_rejects_non_numeric(bot, ctx):
    ctx.args = ["plus", "abc"]
    update = FakeUpdate(SUPERADMIN_ID, "/setprice plus abc")
    await bot.cmd_setprice(update, ctx)
    assert "raqam" in update.message.replies[0]
    assert bot.get_tarif_prices()[bot.TARIF_PLUS] != "abc"


@pytest.mark.asyncio
async def test_setchannel_set_and_remove(bot, ctx):
    ctx.args = ["-100555"]
    update = FakeUpdate(SUPERADMIN_ID, "/setchannel -100555")
    await bot.cmd_setchannel(update, ctx)
    assert bot.get_sub_channel() == -100555

    ctx.args = ["0"]
    update2 = FakeUpdate(SUPERADMIN_ID, "/setchannel 0")
    await bot.cmd_setchannel(update2, ctx)
    assert bot.get_sub_channel() is None


# ══════════════════════════════════════════════════════
# broadcast entry point
# ══════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_broadcast_starts_target_selection(bot, ctx):
    update = FakeUpdate(SUPERADMIN_ID, "/broadcast")
    await bot.cmd_broadcast(update, ctx)
    assert ctx.user_data.get("step") == "broadcast_target"
    assert "Kimga" in update.message.replies[0]


@pytest.mark.asyncio
async def test_broadcast_ignored_outside_private_chat(bot, ctx):
    update = FakeUpdate(SUPERADMIN_ID, "/broadcast", chat_type="supergroup")
    await bot.cmd_broadcast(update, ctx)
    assert "step" not in ctx.user_data


# ══════════════════════════════════════════════════════
# do_export / apply_restore_data roundtrip
# ══════════════════════════════════════════════════════

class FakeExportBot:
    def __init__(self):
        self.id = 1
        self.sent_docs = []
        self.pinned = []
        self._next = 4000

    async def create_forum_topic(self, **kw):
        self._next += 1
        return SimpleNamespace(message_thread_id=self._next)

    async def send_document(self, **kw):
        self._next += 1
        self.sent_docs.append(kw)
        return SimpleNamespace(message_id=self._next)

    async def pin_chat_message(self, chat_id, message_id, **kw):
        self.pinned.append((chat_id, message_id))

    async def get_chat(self, chat_id):
        return SimpleNamespace(pinned_message=None, title="Ctrl")

    async def send_message(self, chat_id, text, **kw):
        self._next += 1
        return SimpleNamespace(message_id=self._next)


@pytest.mark.asyncio
async def test_export_then_restore_roundtrip_preserves_data(bot, monkeypatch):
    monkeypatch.setattr(bot.core, "CONTROL_GROUP_ID", -100777)
    monkeypatch.setattr(bot.core, "_SUPERGROUP_ID_CACHE", -100888)
    _mk_topic(bot, questions=[{"question": "x", "answer": "y", "alternatives": []}])
    bot.save_admins({"111": {"added_by": SUPERADMIN_ID}})
    bot.save_users({"222": {"tarif": "vip", "referral_count": 3}})

    fake = FakeExportBot()
    ok = await bot.do_export(fake, to_backup_topic=True)
    assert ok is True
    assert len(fake.sent_docs) == 1

    # Ma'lumotni "wipe" qilamiz (Render disk tozalanishini simulyatsiya)
    import shutil, os
    os.remove("topics/english.json")
    bot.save_admins({})
    bot.save_users({})

    exported_data = json.loads(fake.sent_docs[0]["document"].getvalue().decode("utf-8"))
    ac, cc, tc, uc = await bot.apply_restore_data(exported_data)
    assert tc == 1 and uc == 1
    assert bot.load_topic("english")["questions"][0]["answer"] == "y"
    assert bot.load_users()["222"]["tarif"] == "vip"
    assert "111" in bot.load_admins()
