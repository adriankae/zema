from __future__ import annotations

import asyncio

from telegram import InlineKeyboardMarkup, ReplyKeyboardMarkup

from czm_cli.config import AppConfig, TelegramConfig
from czm_cli.errors import ApiError, CzmError, EXIT_AUTH, EXIT_CONFLICT, EXIT_NOT_FOUND, EXIT_TRANSPORT
from czm_cli.telegram.commands import TelegramCommandContext
from czm_cli.telegram import handlers as handlers_module
from czm_cli.telegram.handlers import TelegramHandlerContext, handle_callback, handle_guided_text, handle_text_message
from czm_cli.telegram.runtime import TelegramRuntime, build_application
from czm_cli.telegram.state import ConversationStore


class FakeClient:
    def __init__(
        self,
        *,
        allow_empty=False,
        image: bytes | None = b"image-bytes",
        empty_after_log=False,
        delete_error: CzmError | None = None,
        due_items: list[dict] | None = None,
        due_payload=None,
        due_error: CzmError | None = None,
        locations: list[dict] | None = None,
        subjects: list[dict] | None = None,
        episodes: list[dict] | None = None,
    ):
        self.requests = []
        self.allow_empty = allow_empty
        self.image = image
        self.empty_after_log = empty_after_log
        self.logged = False
        self.delete_error = delete_error
        self.due_items = due_items
        self.due_payload = due_payload
        self.due_error = due_error
        self.subjects = subjects or [{"id": 1, "display_name": "Child A"}]
        self.locations = locations or [{"id": 2, "code": "left_elbow", "display_name": "Left elbow"}]
        self.episodes = episodes or [
            {
                "id": 12,
                "subject_id": 1,
                "location_id": 2,
                "status": "active_flare",
                "current_phase_number": 1,
                "phase_due_end_at": "2026-05-03T00:00:00Z",
                "healed_at": None,
                "obsolete_at": None,
            }
        ]

    def get(self, path, params=None):
        self.requests.append(("GET", path, params))
        if path == "/episodes/due":
            if self.due_error is not None:
                raise self.due_error
            if self.due_payload is not None:
                return self.due_payload
            if self.allow_empty or (self.empty_after_log and self.logged):
                return {"due": []}
            if self.due_items is not None:
                return {"due": self.due_items}
            return {"due": [{"episode_id": 12, "subject_id": 1, "location_id": 2, "current_phase_number": 1, "treatment_due_today": True}]}
        if path == "/subjects":
            return {"subjects": self.subjects}
        if path == "/locations":
            return {"locations": self.locations}
        if path == "/episodes":
            return {"episodes": self.episodes}
        if path == "/adherence/summary":
            return {"from": "2026-04-01", "to": "2026-04-30", "expected_applications": 10, "credited_applications": 8, "adherence_score": 0.8, "missed_days": 2}
        if path == "/adherence/calendar":
            return {
                "days": [
                    {
                        "date": "2026-04-01",
                        "episode_id": 12,
                        "subject_id": 1,
                        "location_id": 2,
                        "phase_number": 1,
                        "expected_applications": 1,
                        "completed_applications": 1,
                        "credited_applications": 1,
                        "status": "completed",
                    }
                ]
            }
        raise AssertionError(path)

    def delete(self, path, json=None, params=None):
        self.requests.append(("DELETE", path, json))
        if self.delete_error is not None:
            raise self.delete_error
        if path == "/subjects/1":
            return {"id": 1, "display_name": "Child A"}
        raise AssertionError(path)

    def post(self, path, json=None, params=None):
        self.requests.append(("POST", path, json))
        if path == "/applications":
            self.logged = True
            return {"application": {"id": 1, "episode_id": json["episode_id"]}}
        if path == "/subjects":
            return {"id": 3, "display_name": json["display_name"]}
        if path == "/locations":
            return {"location": {"id": 4, "code": json["code"], "display_name": json["display_name"]}}
        raise AssertionError(path)

    def download_file(self, path):
        self.requests.append(("DOWNLOAD", path, None))
        if self.image is None:
            raise RuntimeError("not found")
        return self.image, "image/jpeg"


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs.get("reply_markup")))

    async def reply_photo(self, **kwargs):
        self.replies.append((kwargs.get("caption"), kwargs.get("reply_markup"), kwargs.get("photo")))


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


class Obj:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def run(coro):
    return asyncio.run(coro)


def make_handler(
    *,
    allow_writes=True,
    chat_id=123,
    user_id=1,
    allow_empty=False,
    image=b"image-bytes",
    empty_after_log=False,
    delete_error=None,
    due_items=None,
    due_payload=None,
    due_error=None,
    locations=None,
    subjects=None,
    episodes=None,
    chat_type=None,
):
    config = AppConfig(timezone="UTC", telegram=TelegramConfig(bot_token="t", allowed_chat_ids=[123], allow_writes=allow_writes))
    client = FakeClient(
        allow_empty=allow_empty,
        image=image,
        empty_after_log=empty_after_log,
        delete_error=delete_error,
        due_items=due_items,
        due_payload=due_payload,
        due_error=due_error,
        locations=locations,
        subjects=subjects,
        episodes=episodes,
    )
    ctx = TelegramHandlerContext(TelegramCommandContext(config, client), ConversationStore())
    update = Obj(effective_chat=Obj(id=chat_id, type=chat_type), effective_user=Obj(id=user_id))
    return ctx, client, update


def test_menu_returns_main_keyboard():
    app = build_application(TelegramRuntime(config=AppConfig(telegram=TelegramConfig(bot_token="t", allowed_chat_ids=[123])), client=FakeClient()))
    handler = app.handlers[0][0].callback
    message = FakeMessage("/menu")
    update = Obj(effective_chat=Obj(id=123), effective_user=Obj(id=1), effective_message=message)
    run(handler(update, None))
    assert message.replies[0][0].startswith("Zema")
    assert message.replies[0][1] is not None
    labels = [button.text for row in message.replies[0][1].inline_keyboard for button in row]
    assert "Log treatment" not in labels
    assert "Due now" in labels
    assert "Due today" not in labels


def test_persistent_reply_keyboard_does_not_include_log_treatment():
    app = build_application(TelegramRuntime(config=AppConfig(telegram=TelegramConfig(bot_token="t", allowed_chat_ids=[123])), client=FakeClient()))
    handler = app.handlers[0][0].callback
    message = FakeMessage("/menu")
    update = Obj(effective_chat=Obj(id=123, type="private"), effective_user=Obj(id=1), effective_message=message)
    run(handler(update, None))
    labels = [button.text for row in message.replies[0][1].keyboard for button in row]
    assert "Log treatment" not in labels
    assert "Due now" in labels
    assert "Due today" not in labels


def test_due_callback_includes_log_buttons_when_writes_enabled():
    ctx, client, update = make_handler(allow_writes=True)
    query = FakeQuery("menu:due")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert all(edit[0] != "Due prompts below." for edit in query.edits)
    assert "Left elbow" in query.message.replies[0][0]
    assert "Subject:" not in query.message.replies[0][0]
    assert "Phase: 1" in query.message.replies[0][0]
    assert "Next phase change: 03.05." in query.message.replies[0][0]
    assert query.message.replies[0][2] == b"image-bytes"
    labels = [button.text for row in query.message.replies[0][1].inline_keyboard for button in row]
    assert labels == ["Log application", "Open menu"]
    assert ("DOWNLOAD", "/locations/2/image", None) in client.requests


def test_due_callback_hides_log_buttons_when_writes_disabled():
    ctx, client, update = make_handler(allow_writes=False)
    query = FakeQuery("menu:due")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    labels = [button.text for row in query.message.replies[0][1].inline_keyboard for button in row]
    assert labels == ["Open menu"]


def test_due_callback_falls_back_to_text_when_image_missing():
    ctx, client, update = make_handler(allow_writes=True, image=None)
    query = FakeQuery("menu:due")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert "Left elbow" in query.message.replies[0][0]
    assert len(query.message.replies[0]) == 2


def test_due_callback_includes_subject_when_multiple_subjects():
    ctx, _client, update = make_handler(
        allow_writes=True,
        image=None,
        subjects=[{"id": 1, "display_name": "Child A"}, {"id": 2, "display_name": "Child B"}],
    )
    query = FakeQuery("menu:due")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert "Subject: Child A" in query.message.replies[0][0]


def test_due_callback_omits_next_phase_change_when_unavailable():
    ctx, _client, update = make_handler(allow_writes=True, image=None, episodes=[{"id": 12, "phase_due_end_at": None}])
    query = FakeQuery("menu:due")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert "Next phase change:" not in query.message.replies[0][0]


def test_due_callback_renders_all_backend_due_items_location_first():
    due_items = [
        {"episode_id": 1, "subject_id": 1, "location_id": 1, "current_phase_number": 1, "treatment_due_today": True, "due_slot": "evening"},
        {"episode_id": 2, "subject_id": 1, "location_id": 2, "current_phase_number": 1, "treatment_due_today": True, "due_slot": "evening"},
        {"episode_id": 3, "subject_id": 1, "location_id": 3, "current_phase_number": 1, "treatment_due_today": True, "due_slot": "evening"},
    ]
    locations = [
        {"id": 1, "code": "hinterkopf_links", "display_name": "Hinterkopf links"},
        {"id": 2, "code": "kotelette_rechts", "display_name": "Kotelette rechts"},
        {"id": 3, "code": "mundwinkel_rechts", "display_name": "Mundwinkel rechts"},
    ]
    ctx, _client, update = make_handler(allow_writes=True, due_items=due_items, locations=locations)
    query = FakeQuery("menu:due")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    captions = [reply[0] for reply in query.message.replies]
    assert all(edit[0] != "Due prompts below." for edit in query.edits)
    assert len(captions) == 3
    assert any("Hinterkopf links" in caption for caption in captions)
    assert any("Kotelette rechts" in caption for caption in captions)
    assert any("Mundwinkel rechts" in caption for caption in captions)
    assert all("Episode" not in caption for caption in captions)


def test_log_treatment_uses_location_first_due_prompts():
    ctx, client, update = make_handler(allow_writes=True)
    query = FakeQuery("menu:log_treatment")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert all(edit[0] != "Due prompts below." for edit in query.edits)
    assert "Left elbow" in query.message.replies[0][0]
    assert "Episode 12" not in query.message.replies[0][0]


def test_reply_keyboard_due_today_uses_location_first_due_prompts():
    ctx, client, _update = make_handler(allow_writes=True)
    message = FakeMessage("Due today")
    update = Obj(effective_chat=Obj(id=123, type="private"), effective_user=Obj(id=1), effective_message=message)
    run(handle_text_message(update, None, ctx))
    assert all(reply[0] != "Due prompts below." for reply in message.replies)
    assert "Left elbow" in message.replies[0][0]
    assert "Subject:" not in message.replies[0][0]
    assert "Episode 12" not in message.replies[0][0]
    assert message.replies[0][2] == b"image-bytes"
    assert ("DOWNLOAD", "/locations/2/image", None) in client.requests


def test_reply_keyboard_due_now_sends_due_prompts_without_preamble():
    ctx, client, _update = make_handler(allow_writes=True)
    message = FakeMessage("Due now")
    update = Obj(effective_chat=Obj(id=123, type="private"), effective_user=Obj(id=1), effective_message=message)
    run(handle_text_message(update, None, ctx))
    assert all(reply[0] != "Due prompts below." for reply in message.replies)
    assert len(message.replies) == 1
    assert "Left elbow" in message.replies[0][0]
    assert "Episode 12" not in message.replies[0][0]
    assert all(getattr(markup, "remove_keyboard", None) is not True for _text, markup, *_rest in message.replies)
    assert ("DOWNLOAD", "/locations/2/image", None) in client.requests


def test_reply_keyboard_log_treatment_reuses_due_prompts():
    ctx, _client, _update = make_handler(allow_writes=True)
    message = FakeMessage("Log treatment")
    update = Obj(effective_chat=Obj(id=123, type="private"), effective_user=Obj(id=1), effective_message=message)
    run(handle_text_message(update, None, ctx))
    assert message.replies[0][0] == "Log treatment moved to Due now."
    assert all(reply[0] != "Due prompts below." for reply in message.replies)
    assert "Left elbow" in message.replies[1][0]
    assert "Episode 12" not in message.replies[1][0]


def test_reply_keyboard_due_today_empty_state():
    ctx, _client, _update = make_handler(allow_empty=True)
    message = FakeMessage("Due today")
    update = Obj(effective_chat=Obj(id=123, type="private"), effective_user=Obj(id=1), effective_message=message)
    run(handle_text_message(update, None, ctx))
    assert message.replies == [("No treatments are due right now.", None)]
    assert all(getattr(markup, "remove_keyboard", None) is not True for _text, markup in message.replies)


def test_due_callback_empty_state():
    ctx, client, update = make_handler(allow_empty=True)
    query = FakeQuery("menu:due")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert query.edits == [("No treatments are due right now.", None)]
    assert query.message.replies == []
    assert all(getattr(markup, "remove_keyboard", None) is not True for _text, markup in query.edits)


def test_due_callback_transport_error_is_not_empty_state():
    ctx, _client, update = make_handler(due_error=CzmError("timeout", exit_code=EXIT_TRANSPORT))
    query = FakeQuery("menu:due")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert query.edits == [("Zema backend did not respond. Do not assume nothing is due.", None)]


def test_due_callback_backend_error_is_not_empty_state():
    error = ApiError("server exploded", exit_code=EXIT_TRANSPORT, status_code=500)
    ctx, _client, update = make_handler(due_error=error)
    query = FakeQuery("menu:due")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert query.edits == [("Zema backend failed while checking due treatments.", None)]


def test_due_callback_auth_error_is_not_empty_state():
    ctx, _client, update = make_handler(due_error=CzmError("unauthorized", exit_code=EXIT_AUTH))
    query = FakeQuery("menu:due")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert query.edits == [("Zema authentication failed. Tracking is not active.", None)]


def test_due_callback_malformed_response_is_not_empty_state():
    ctx, _client, update = make_handler(due_payload={"due": "not-a-list"})
    query = FakeQuery("menu:due")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert query.edits == [("Zema returned an unreadable due response.", None)]


def test_quick_log_button_posts_application():
    ctx, client, update = make_handler(allow_writes=True)
    query = FakeQuery("due:log:12")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert query.answered is True
    assert ("POST", "/applications", {"episode_id": 12}) in client.requests
    assert query.edits == [("Logged application for 'Left elbow'", None)]
    assert query.message.replies == []
    assert all(getattr(markup, "remove_keyboard", None) is not True for _text, markup in query.edits)


def test_due_today_fetches_fresh_backend_state_after_logging():
    ctx, client, update = make_handler(allow_writes=True, empty_after_log=True)
    query = FakeQuery("menu:due")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert "Left elbow" in query.message.replies[0][0]

    query = FakeQuery("due:log:12")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert ("POST", "/applications", {"episode_id": 12}) in client.requests

    query = FakeQuery("menu:due")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert query.edits[0][0] == "No treatments are due right now."


def test_quick_log_button_rejects_stale_due_item():
    ctx, client, update = make_handler(allow_empty=True)
    query = FakeQuery("due:log:12")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert ("POST", "/applications", {"episode_id": 12}) not in client.requests
    assert query.edits == [("This due item is no longer due or was already handled.", None)]


def test_write_callback_rejected_when_disabled():
    ctx, client, update = make_handler(allow_writes=False)
    query = FakeQuery("due:log:12")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert "disabled" in query.edits[0][0]


def test_adherence_menu_includes_summary_90_days():
    ctx, _client, update = make_handler()
    query = FakeQuery("menu:adherence")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    labels = [button.text for row in query.edits[0][1].inline_keyboard for button in row]
    assert "Summary 90 days" in labels


def test_adherence_summary_sends_text_and_heatmap(monkeypatch):
    ctx, client, update = make_handler(chat_type="private")
    monkeypatch.setattr(handlers_module, "render_heatmap_png", lambda grid: b"png-bytes")
    query = FakeQuery("adh:summary:30")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert "Adherence" in query.edits[0][0]
    assert query.message.replies[0][0] == "Adherence heatmap - last 30 days"
    assert query.message.replies[0][2].getvalue() == b"png-bytes"
    assert query.message.replies[1][0] == "Done."
    assert isinstance(query.message.replies[1][1], ReplyKeyboardMarkup)
    labels = [button.text for row in query.message.replies[1][1].keyboard for button in row]
    assert "Due now" in labels
    assert ("GET", "/adherence/summary", {"from": "2026-03-28", "to": "2026-04-26"}) in client.requests
    assert any(request[0:2] == ("GET", "/adherence/calendar") for request in client.requests)


def test_adherence_summary_restores_inline_menu_in_group(monkeypatch):
    ctx, _client, update = make_handler(chat_type="group")
    monkeypatch.setattr(handlers_module, "render_heatmap_png", lambda grid: b"png-bytes")
    query = FakeQuery("adh:summary:30")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert query.message.replies[1][0] == "Done."
    assert isinstance(query.message.replies[1][1], InlineKeyboardMarkup)
    labels = [button.text for row in query.message.replies[1][1].inline_keyboard for button in row]
    assert "Due now" in labels


def test_adherence_summary_still_sends_text_when_heatmap_render_fails(monkeypatch):
    ctx, _client, update = make_handler(chat_type="private")
    def fail_render(grid):
        raise RuntimeError("boom")
    monkeypatch.setattr(handlers_module, "render_heatmap_png", fail_render)
    query = FakeQuery("adh:summary:30")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert "Adherence" in query.edits[0][0]
    assert query.message.replies[0][0] == "Done."
    assert isinstance(query.message.replies[0][1], ReplyKeyboardMarkup)


def test_adherence_summary_still_sends_text_when_photo_send_fails(monkeypatch):
    class FailingPhotoMessage(FakeMessage):
        async def reply_photo(self, **kwargs):
            raise RuntimeError("telegram failed")

    ctx, _client, update = make_handler(chat_type="private")
    monkeypatch.setattr(handlers_module, "render_heatmap_png", lambda grid: b"png-bytes")
    query = FakeQuery("adh:summary:30")
    query.message = FailingPhotoMessage()
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert "Adherence" in query.edits[0][0]
    assert query.message.replies[0][0] == "Done."
    assert isinstance(query.message.replies[0][1], ReplyKeyboardMarkup)


def test_subject_create_guided_flow():
    ctx, client, update = make_handler(allow_writes=True)
    query = FakeQuery("subject:create")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    message = FakeMessage("New Child")
    text_update = Obj(effective_chat=Obj(id=123), effective_user=Obj(id=1), effective_message=message)
    assert run(handle_guided_text(text_update, None, ctx)) is True
    assert ("POST", "/subjects", {"display_name": "New Child"}) in client.requests


def test_subjects_menu_shows_delete_when_writes_enabled():
    ctx, _client, update = make_handler(allow_writes=True)
    query = FakeQuery("menu:subjects")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    labels = [button.text for row in query.edits[0][1].inline_keyboard for button in row]
    assert "Create subject" in labels
    assert "Delete subject" in labels
    assert "Open menu" in labels


def test_subjects_menu_hides_delete_when_writes_disabled():
    ctx, _client, update = make_handler(allow_writes=False)
    query = FakeQuery("menu:subjects")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    labels = [button.text for row in query.edits[0][1].inline_keyboard for button in row]
    assert "Delete subject" not in labels
    assert labels == ["Open menu"]


def test_subject_delete_callback_rejected_when_writes_disabled():
    ctx, client, update = make_handler(allow_writes=False)
    query = FakeQuery("subject:delete")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert "disabled" in query.edits[0][0]
    assert not any(request[0] == "DELETE" for request in client.requests)


def test_subject_delete_flow_confirms_and_deletes():
    ctx, client, update = make_handler(allow_writes=True)
    query = FakeQuery("subject:delete")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert query.edits[0][0] == "Choose a subject to delete."
    labels = [button.text for row in query.edits[0][1].inline_keyboard for button in row]
    assert "Child A" in labels

    query = FakeQuery("subject:delete_select:1")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert 'Delete subject "Child A"?' in query.edits[0][0]
    labels = [button.text for row in query.edits[0][1].inline_keyboard for button in row]
    assert labels == ["Confirm delete", "Cancel"]

    query = FakeQuery("subject:delete_confirm:1")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert ("DELETE", "/subjects/1", None) in client.requests
    assert query.edits[0][0] == "Deleted subject: Child A."


def test_subject_delete_cancel_does_not_delete():
    ctx, client, update = make_handler(allow_writes=True)
    query = FakeQuery("subject:delete_cancel")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert query.edits[0][0] == "Subject deletion cancelled."
    assert not any(request[0] == "DELETE" for request in client.requests)


def test_subject_delete_conflict_is_surfaced_with_recovery_actions():
    ctx, client, update = make_handler(allow_writes=True, delete_error=CzmError("unexpected conflict", exit_code=EXIT_CONFLICT))
    query = FakeQuery("subject:delete_confirm:1")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert ("DELETE", "/subjects/1", None) in client.requests
    assert query.edits[0][0] == "Zema request failed: unexpected conflict"
    assert "Subject cannot be deleted because it has related episodes or treatment history." not in query.edits[0][0]
    assert "To preserve medical history" not in query.edits[0][0]
    assert "Traceback" not in query.edits[0][0]
    labels = [button.text for row in query.edits[0][1].inline_keyboard for button in row]
    assert labels == ["Subjects", "Open menu"]


def test_subject_delete_conflict_surfaces_specific_backend_detail():
    ctx, client, update = make_handler(allow_writes=True, delete_error=CzmError("subject is locked by retention policy", exit_code=EXIT_CONFLICT))
    query = FakeQuery("subject:delete_confirm:1")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert ("DELETE", "/subjects/1", None) in client.requests
    assert query.edits[0][0] == "Zema request failed: subject is locked by retention policy"


def test_subject_delete_not_found_is_surfaced_safely():
    ctx, client, update = make_handler(allow_writes=True, delete_error=CzmError("subject not found", exit_code=EXIT_NOT_FOUND))
    query = FakeQuery("subject:delete_confirm:1")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert ("DELETE", "/subjects/1", None) in client.requests
    assert query.edits[0][0] == "Subject not found. It may already have been deleted."


def test_create_location_guided_flow():
    ctx, client, update = make_handler(allow_writes=True)
    query = FakeQuery("loc:create")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert query.edits[0][0] == "Send the location display name."
    msg2 = FakeMessage("Right knee")
    assert run(handle_guided_text(Obj(effective_chat=Obj(id=123), effective_user=Obj(id=1), effective_message=msg2), None, ctx)) is True
    assert ("POST", "/locations", {"code": "right_knee", "display_name": "Right knee"}) in client.requests


def test_create_location_guided_flow_rejects_duplicate_code():
    ctx, client, update = make_handler(allow_writes=True)
    query = FakeQuery("loc:create")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    msg = FakeMessage("Left elbow")
    assert run(handle_guided_text(Obj(effective_chat=Obj(id=123), effective_user=Obj(id=1), effective_message=msg), None, ctx)) is True
    assert "already exists" in msg.replies[0][0]
    assert not any(request == ("POST", "/locations", {"code": "left_elbow", "display_name": "Left elbow"}) for request in client.requests)


def test_create_location_guided_flow_rejects_empty_derived_code():
    ctx, _client, update = make_handler(allow_writes=True)
    query = FakeQuery("loc:create")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    msg = FakeMessage("!!!")
    assert run(handle_guided_text(Obj(effective_chat=Obj(id=123), effective_user=Obj(id=1), effective_message=msg), None, ctx)) is True
    assert "must include at least one letter or number" in msg.replies[0][0]


def test_unknown_chat_callback_rejected():
    ctx, client, update = make_handler(chat_id=999)
    query = FakeQuery("menu:subjects")
    update.callback_query = query
    run(handle_callback(update, None, ctx))
    assert "not allowed" in query.edits[0][0]
