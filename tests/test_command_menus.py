import asyncio, importlib, sys
from types import SimpleNamespace
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


class FakeBot:
    def __init__(self):
        self.calls = []

    async def set_my_commands(self, commands, scope=None):
        self.calls.append((scope, [c.command for c in commands]))


@pytest.mark.asyncio
async def test_sync_bot_commands_sets_short_default_and_full_admin_lists(bot):
    fb = FakeBot()
    bot.save_admins({"111": {"added_by": SUPERADMIN_ID}})
    await bot.sync_bot_commands(fb)

    default_calls = [c for c in fb.calls if isinstance(c[0], bot.BotCommandScopeDefault)]
    assert len(default_calls) == 1
    assert "broadcast" not in default_calls[0][1]
    assert "newgame" in default_calls[0][1]

    chat_calls = {c[0].chat_id: c[1] for c in fb.calls if isinstance(c[0], bot.BotCommandScopeChat)}
    assert SUPERADMIN_ID in chat_calls
    assert 111 in chat_calls
    assert "broadcast" in chat_calls[SUPERADMIN_ID]
    assert "broadcast" in chat_calls[111]


@pytest.mark.asyncio
async def test_save_admins_auto_resyncs_commands(bot, monkeypatch):
    fb = FakeBot()
    monkeypatch.setattr(bot.core, "_BOT_REF", fb)
    bot.save_admins({"222": {"added_by": SUPERADMIN_ID}})
    await asyncio.sleep(0.05)  # background task tugashini kutamiz
    chat_targets = {c[0].chat_id for c in fb.calls if isinstance(c[0], bot.BotCommandScopeChat)}
    assert 222 in chat_targets
