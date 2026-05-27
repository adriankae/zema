from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from czm_cli.config import AppConfig, TelegramConfig, TelegramReminderConfig
from czm_cli.telegram.commands import TelegramCommandContext
from czm_cli.telegram.handlers import TelegramHandlerContext, handle_callback
from czm_cli.telegram.reminders import SnoozeStore, schedule_reminders, send_due_reminders
from czm_cli.telegram.state import ConversationStore


class FakeClient:
    def __init__(
        self,
        *,
        image: bytes | None = None,
        due_items: list[dict] | None = None,
        subjects: list[dict] | None = None,
        locations: list[dict] | None = None,
        episodes: list[dict] | None = None,
    ):
        self.image = image
        self.requests = []
        self.due_items = due_items
        self.subjects = subjects or [{"id": 1, "display_name": "Child A"}]
        self.locations = locations or [
            {"id": 2, "code": "left_elbow", "display_name": "Left elbow"},
            {"id": 3, "code": "right_knee", "display_name": "Right knee"},
        ]
        self.episodes = episodes or [
            {"id": 12, "phase_due_end_at": "2026-05-03T00:00:00Z"},
            {"id": 13, "phase_due_end_at": "2026-05-10T00:00:00Z"},
        ]
        self.logged = False

    def get(self, path, params=None):
        self.requests.append(("GET", path, params))
        if path == "/episodes/due":
            if self.due_items is not None:
                return {"due": self.due_items}
            return {
                "due": [
                    {
                        "episode_id": 12,
                        "subject_id": 1,
                        "location_id": 2,
                        "current_phase_number": 1,
                        "treatment_due_today": True,
                        "due_slot": "morning",
                    },
                    {
                        "episode_id": 13,
                        "subject_id": 1,
                        "location_id": 3,
                        "current_phase_number": 2,
                        "treatment_due_today": True,
                    },
                ]
            }
        if path == "/subjects":
            return {"subjects": self.subjects}
        if path == "/locations":
            return {"locations": self.locations}
        if path == "/episodes":
            return {"episodes": self.episodes}
        raise AssertionError(path)

    def post(self, path, json=None, params=None):
        self.requests.append(("POST", path, json))
        if path == "/applications":
            self.logged = True
            return {"application": {"id": 1, "episode_id": json["episode_id"]}}
        raise AssertionError(path)

    def delete(self, path, json=None, params=None):
        self.requests.append(("DELETE", path, json))
        if path == "/applications/1":
            return {"application": {"id": 1, "episode_id": 12, "is_deleted": True}}
        raise AssertionError(path)

    def download_file(self, path):
        self.requests.append(("DOWNLOAD", path, None))
        if self.image is None:
            raise RuntimeError("not found")
        return self.image, "image/jpeg"


class FakeBot:
    def __init__(self):
        self.messages = []
        self.photos = []
        self.commands = None

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)

    async def send_photo(self, **kwargs):
        self.photos.append(kwargs)

    async def set_my_commands(self, commands):
        self.commands = commands


class FakeJobQueue:
    def __init__(self):
        self.jobs = []

    def run_daily(self, callback, *, time, name, data):
        self.jobs.append((callback, time, name, data))


class FakeApplication:
    def __init__(self):
        self.job_queue = FakeJobQueue()


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.edits = []
        self.message = FakeMessage()
        self.answered = False

    async def answer(self):
        self.answered = True
        return None

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs.get("reply_markup")))


class FakeMessage:
    async def reply_text(self, text, **kwargs):
        return None


class Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def run(coro):
    return asyncio.run(coro)


def make_ctx(
    *,
    allow_writes=True,
    image=None,
    reminders=None,
    due_items=None,
    subjects=None,
    locations=None,
    episodes=None,
):
    config = AppConfig(
        timezone="Europe/Berlin",
        telegram=TelegramConfig(
            bot_token="t",
            allowed_chat_ids=[123],
            allow_writes=allow_writes,
            reminders=reminders or TelegramReminderConfig(timezone="Europe/Berlin"),
        ),
    )
    client = FakeClient(image=image, due_items=due_items, subjects=subjects, locations=locations, episodes=episodes)
    ctx = TelegramHandlerContext(TelegramCommandContext(config, client), ConversationStore(), SnoozeStore(30))
    return ctx, client


def test_scheduler_registers_morning_and_evening_jobs():
    ctx, _client = make_ctx()
    application = FakeApplication()
    schedule_reminders(application, ctx)
    assert [job[2] for job in application.job_queue.jobs] == ["zema-morning-reminder", "zema-evening-reminder"]
    assert application.job_queue.jobs[0][1].hour == 7
    assert application.job_queue.jobs[1][1].hour == 19
    assert str(application.job_queue.jobs[0][1].tzinfo) == "Europe/Berlin"


def test_disabled_reminders_register_no_jobs():
    ctx, _client = make_ctx(reminders=TelegramReminderConfig(enabled=False))
    application = FakeApplication()
    schedule_reminders(application, ctx)
    assert application.job_queue.jobs == []


def test_morning_reminder_sends_location_image_and_log_button():
    ctx, client = make_ctx(image=b"jpeg-bytes")
    bot = FakeBot()
    run(send_due_reminders(bot, ctx, reminder_kind="morning"))
    assert len(bot.photos) == 2
    assert bot.photos[0]["chat_id"] == 123
    assert bot.photos[0]["photo"] == b"jpeg-bytes"
    assert bot.photos[0]["caption"].startswith("Apply this morning:")
    assert "Location: Left elbow" in bot.photos[0]["caption"]
    assert "Subject:" not in bot.photos[0]["caption"]
    assert "Next phase change: 03.05." in bot.photos[0]["caption"]
    assert "Good morning" not in bot.photos[0]["caption"]
    assert "needs cream" not in bot.photos[0]["caption"]
    labels = [button.text for row in bot.photos[0]["reply_markup"].inline_keyboard for button in row]
    assert "Log application" in labels
    assert "Snooze" in labels
    assert "Open menu" in labels
    assert ("GET", "/episodes/due", None) in client.requests


def test_reminder_falls_back_to_text_when_image_missing():
    ctx, _client = make_ctx(image=None)
    bot = FakeBot()
    run(send_due_reminders(bot, ctx, reminder_kind="morning"))
    assert bot.photos == []
    assert len(bot.messages) == 2
    assert "Location: Left elbow" in bot.messages[0]["text"]


def test_phase_one_evening_reminder_copy():
    due_items = [
        {
            "episode_id": 12,
            "subject_id": 1,
            "location_id": 2,
            "current_phase_number": 1,
            "treatment_due_today": True,
            "due_slot": "evening",
            "phase_due_end_at": "2026-05-03T21:30:00Z",
        }
    ]
    ctx, _client = make_ctx(image=None, due_items=due_items)
    bot = FakeBot()
    run(send_due_reminders(bot, ctx, reminder_kind="evening"))
    assert bot.messages[0]["text"].startswith("Apply this evening:")
    assert "Good evening" not in bot.messages[0]["text"]
    assert "needs cream" not in bot.messages[0]["text"]
    assert "Next phase change: 03.05." in bot.messages[0]["text"]


def test_other_phase_reminder_copy_uses_apply_today():
    due_items = [
        {
            "episode_id": 13,
            "subject_id": 1,
            "location_id": 3,
            "current_phase_number": 2,
            "treatment_due_today": True,
        }
    ]
    ctx, _client = make_ctx(image=None, due_items=due_items)
    bot = FakeBot()
    run(send_due_reminders(bot, ctx, reminder_kind="morning"))
    assert bot.messages[0]["text"].startswith("Apply today:")
    assert "Location: Right knee" in bot.messages[0]["text"]
    assert "Phase: 2" in bot.messages[0]["text"]


def test_evening_reminder_uses_due_source_and_filters_to_phase_one():
    ctx, _client = make_ctx(image=None)
    bot = FakeBot()
    run(send_due_reminders(bot, ctx, reminder_kind="evening"))
    assert len(bot.messages) == 1
    assert "Location: Left elbow" in bot.messages[0]["text"]
    assert "Right knee" not in bot.messages[0]["text"]


def test_log_button_hidden_when_writes_disabled():
    ctx, _client = make_ctx(allow_writes=False, image=None)
    bot = FakeBot()
    run(send_due_reminders(bot, ctx, reminder_kind="morning"))
    labels = [button.text for row in bot.messages[0]["reply_markup"].inline_keyboard for button in row]
    assert "Log application" not in labels
    assert "Snooze" in labels
    assert "Open menu" in labels


def test_subject_line_included_only_when_multiple_subjects():
    due_items = [
        {
            "episode_id": 12,
            "subject_id": 1,
            "location_id": 2,
            "current_phase_number": 1,
            "treatment_due_today": True,
            "due_slot": "morning",
        }
    ]
    ctx, _client = make_ctx(image=None, due_items=due_items, subjects=[{"id": 1, "display_name": "Child A"}])
    bot = FakeBot()
    run(send_due_reminders(bot, ctx, reminder_kind="morning"))
    assert "Subject:" not in bot.messages[0]["text"]

    ctx, _client = make_ctx(
        image=None,
        due_items=due_items,
        subjects=[{"id": 1, "display_name": "Child A"}, {"id": 2, "display_name": "Child B"}],
    )
    bot = FakeBot()
    run(send_due_reminders(bot, ctx, reminder_kind="morning"))
    assert "Subject: Child A" in bot.messages[0]["text"]


def test_next_phase_change_line_omitted_when_unavailable():
    due_items = [
        {
            "episode_id": 12,
            "subject_id": 1,
            "location_id": 2,
            "current_phase_number": 1,
            "treatment_due_today": True,
            "due_slot": "morning",
        }
    ]
    ctx, _client = make_ctx(image=None, due_items=due_items, episodes=[{"id": 12, "phase_due_end_at": None}])
    bot = FakeBot()
    run(send_due_reminders(bot, ctx, reminder_kind="morning"))
    assert "Next phase change:" not in bot.messages[0]["text"]


def test_location_label_falls_back_to_code_then_id():
    due_items = [
        {
            "episode_id": 12,
            "subject_id": 1,
            "location_id": 2,
            "current_phase_number": 1,
            "treatment_due_today": True,
            "due_slot": "morning",
        },
        {
            "episode_id": 13,
            "subject_id": 1,
            "location_id": 3,
            "current_phase_number": 2,
            "treatment_due_today": True,
        },
    ]
    locations = [{"id": 2, "code": "left_elbow"}, {"id": 3}]
    ctx, _client = make_ctx(image=None, due_items=due_items, locations=locations)
    bot = FakeBot()
    run(send_due_reminders(bot, ctx, reminder_kind="morning"))
    assert "Location: left_elbow" in bot.messages[0]["text"]
    assert "Location: Location 3" in bot.messages[1]["text"]


def test_snooze_suppresses_until_expiry():
    now = datetime(2026, 4, 25, 7, 0, tzinfo=timezone.utc)
    store = SnoozeStore(30, clock=lambda: now)
    store.snooze(123, 12)
    assert store.is_snoozed(123, 12) is True
    store.clock = lambda: datetime(2026, 4, 25, 7, 31, tzinfo=timezone.utc)
    assert store.is_snoozed(123, 12) is False


def test_snooze_callback_records_episode():
    ctx, _client = make_ctx()
    query = FakeQuery("rem:snooze:12")
    update = Obj(effective_chat=Obj(id=123), effective_user=Obj(id=1), callback_query=query)
    run(handle_callback(update, None, ctx))
    assert "Snoozed episode 12" in query.edits[0][0]
    assert ctx.snoozes is not None
    assert ctx.snoozes.is_snoozed(123, 12) is True


def test_reminder_log_callback_clears_inline_keyboard_without_opening_menu():
    ctx, client = make_ctx()
    query = FakeQuery("due:log:12")
    update = Obj(effective_chat=Obj(id=123), effective_user=Obj(id=1), callback_query=query)
    run(handle_callback(update, None, ctx))
    assert query.answered is True
    assert ("POST", "/applications", {"episode_id": 12}) in client.requests
    assert query.edits[0][0] == "Logged application for 'Left elbow'"
    assert query.edits[0][1] is not None
    assert client.logged is True
    assert all(getattr(markup, "remove_keyboard", None) is not True for _text, markup in query.edits)
