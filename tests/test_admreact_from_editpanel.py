import importlib
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


class FakeQ:
    def __init__(self, uid, data):
        self.from_user = SimpleNamespace(id=uid)
        self.data = data
        self.message = SimpleNamespace(chat=SimpleNamespace(id=1))
        self.edits = []
        self.markups = []

    async def answer(self, *a, **kw):
        pass

    async def edit_message_text(self, text, reply_markup=None, **kw):
        self.edits.append(text)
        self.markups.append(reply_markup)


class FUpdateCB:
    def __init__(self, uid, data):
        self.callback_query = FakeQ(uid, data)
        self.effective_user = SimpleNamespace(id=uid)


class FakeMsg:
    def __init__(self, text=None):
        self.text = text
        self.replies = []
        self.reply_markups = []

    async def reply_text(self, text, reply_markup=None, **kw):
        self.replies.append(text)
        self.reply_markups.append(reply_markup)


class FUpdateMsg:
    def __init__(self, uid, text):
        self.effective_user = SimpleNamespace(id=uid)
        self.effective_chat = SimpleNamespace(type="private", id=uid)
        self.message = FakeMsg(text)


@pytest.fixture
def ctx():
    c = SimpleNamespace()
    c.bot = MagicMock()
    c.args = []
    c.user_data = {}
    return c


def _flat_buttons(kb):
    return [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]


@pytest.mark.asyncio
async def test_editadmin_panel_has_reaction_button(bot, ctx):
    bot.save_admins({"333": {"topic_limit": 2, "max_questions": 100, "added_by": SUPERADMIN_ID}})
    update = FUpdateCB(SUPERADMIN_ID, "edit_adm:333")
    await bot.callback_handler(update, ctx)
    kb = update.callback_query.markups[-1]
    buttons = _flat_buttons(kb)
    assert any("Reaksiya" in t and cb == "admreact_page:333:0" for t, cb in buttons)


@pytest.mark.asyncio
async def test_reaction_picker_reachable_for_existing_admin(bot, ctx):
    bot.save_admins({"333": {"topic_limit": 2, "max_questions": 100, "added_by": SUPERADMIN_ID}})
    update = FUpdateCB(SUPERADMIN_ID, "admreact_page:333:0")
    await bot.callback_handler(update, ctx)
    assert "reaksiya" in update.callback_query.edits[-1].lower()
    kb = update.callback_query.markups[-1]
    buttons = _flat_buttons(kb)
    assert any("ID bilan" in t for t, _ in buttons)
    assert any("O'tkazib" in t for t, _ in buttons)


@pytest.mark.asyncio
async def test_pick_standard_emoji_for_existing_admin_and_returns_to_panel(bot, ctx):
    bot.save_admins({"333": {"topic_limit": 2, "max_questions": 100, "added_by": SUPERADMIN_ID}})
    update = FUpdateCB(SUPERADMIN_ID, "admreact:333:🔥")
    await bot.callback_handler(update, ctx)
    adm = bot.load_admins()
    assert adm["333"]["reaction_emoji"] == "🔥"
    kb = update.callback_query.markups[-1]
    assert _flat_buttons(kb) == [("⬅️ Admin sozlamalariga", "edit_adm:333")]


@pytest.mark.asyncio
async def test_pick_custom_id_for_existing_admin_via_text_step(bot, ctx):
    bot.save_admins({"333": {"topic_limit": 2, "max_questions": 100, "added_by": SUPERADMIN_ID}})
    cb_update = FUpdateCB(SUPERADMIN_ID, "admreact_custom:333")
    await bot.callback_handler(cb_update, ctx)
    assert ctx.user_data.get("step") == "admreact_custom_wait"
    assert ctx.user_data.get("ar_target") == 333

    msg_update = FUpdateMsg(SUPERADMIN_ID, "5368324170671202286")
    await bot.handle_text(msg_update, ctx)
    adm = bot.load_admins()
    assert adm["333"]["reaction_custom_emoji_id"] == "5368324170671202286"
    assert msg_update.message.reply_markups[-1] is not None


@pytest.mark.asyncio
async def test_skip_reaction_for_existing_admin_clears_custom_id(bot, ctx):
    bot.save_admins({"333": {"topic_limit": 2, "max_questions": 100, "added_by": SUPERADMIN_ID,
                              "reaction_custom_emoji_id": "111"}})
    update = FUpdateCB(SUPERADMIN_ID, "admreact_skip:333")
    await bot.callback_handler(update, ctx)
    adm = bot.load_admins()
    assert "reaction_custom_emoji_id" not in adm["333"]
