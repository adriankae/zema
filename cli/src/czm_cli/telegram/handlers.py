from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import re
import shlex
from zoneinfo import ZoneInfo

from telegram.error import BadRequest

from czm_cli.errors import CzmError, EXIT_AUTH, EXIT_NOT_FOUND, EXIT_TRANSPORT, EXIT_USAGE
from czm_cli.telegram import formatting
from czm_cli.telegram.heatmap import build_heatmap_grid, render_heatmap_png
from czm_cli.telegram.commands import TelegramCommandContext
from czm_cli.telegram.keyboards import (
    adherence_rebuild_confirm_keyboard,
    adherence_rebuild_range_keyboard,
    adherence_keyboard,
    confirm_episode_action_keyboard,
    due_prompt_keyboard,
    episode_select_keyboard,
    location_actions_keyboard,
    location_image_prompt_keyboard,
    locations_keyboard,
    main_menu_keyboard,
    main_menu_reply_keyboard,
    start_confirm_keyboard,
    start_duplicate_location_keyboard,
    start_image_keyboard,
    start_location_conflict_keyboard,
    start_location_keyboard,
    start_subject_keyboard,
    subject_delete_confirm_keyboard,
    subject_delete_recovery_keyboard,
    subject_delete_select_keyboard,
    subjects_keyboard,
)
from czm_cli.telegram.reminders import SnoozeStore
from czm_cli.telegram.security import ensure_allowed, ensure_rebuild_allowed, ensure_writes_allowed, identity_from_update
from czm_cli.telegram.state import ConversationStore, EXPIRED_STATE_MESSAGE
from czm_cli.time_utils import local_today


@dataclass(slots=True)
class TelegramHandlerContext:
    command_context: TelegramCommandContext
    state: ConversationStore
    snoozes: SnoozeStore | None = None


async def send_menu(message) -> None:
    await message.reply_text(formatting.menu_text(), reply_markup=main_menu_keyboard())


async def handle_callback(update, context, handler_ctx: TelegramHandlerContext) -> None:
    query = update.callback_query
    await _maybe_await(query.answer())
    try:
        ensure_allowed(handler_ctx.command_context.config.telegram, identity_from_update(update))
        if (query.data or "") in {"menu:due", "menu:log_treatment"}:
            await _send_due_prompts(update, context, handler_ctx)
            return
        if (query.data or "").startswith("adh:summary:"):
            await _send_adherence_summary_heatmap(update, handler_ctx)
            return
        if (query.data or "").startswith(("heal:select:", "relapse:select:")):
            ensure_writes_allowed(handler_ctx.command_context.config.telegram)
            await _send_episode_action_confirmation(update, context, handler_ctx, query.data or "")
            return
        if (query.data or "") == "epstart:confirm":
            ensure_writes_allowed(handler_ctx.command_context.config.telegram)
            await _confirm_start_episode(update, handler_ctx)
            return
        text, keyboard = _dispatch_callback(query.data or "", handler_ctx, update)
    except CzmError as exc:
        text, keyboard = (exc.message if exc.exit_code == EXIT_AUTH else formatting.backend_error_message(exc.message)), None
    except Exception:
        text, keyboard = "Zema request failed.", None
    await safe_edit_callback_message(query, text, reply_markup=keyboard)


def _dispatch_callback(data: str, handler_ctx: TelegramHandlerContext, update) -> tuple[str, object | None]:
    config = handler_ctx.command_context.config
    client = handler_ctx.command_context.client
    if data == "menu:open":
        return formatting.menu_text(), main_menu_keyboard()
    if data.startswith("rem:snooze:"):
        episode_id = int(data.rsplit(":", 1)[1])
        identity = identity_from_update(update)
        if identity.chat_id is None:
            raise CzmError("Telegram chat identity is missing", exit_code=EXIT_AUTH)
        if handler_ctx.snoozes is not None:
            handler_ctx.snoozes.snooze(identity.chat_id, episode_id)
        return f"Snoozed episode {episode_id}.", main_menu_keyboard()
    if data.startswith("due:log:"):
        ensure_writes_allowed(config.telegram)
        episode_id = int(data.rsplit(":", 1)[1])
        due_item = _current_due_item_for_episode(handler_ctx, episode_id)
        if due_item is None:
            return "This due item is no longer due or was already handled.", None
        label = due_item.get("telegram_location_name") or due_item.get("telegram_label") or f"episode {episode_id}"
        client.post("/applications", json={"episode_id": episode_id})
        return f"Logged application for '{label}'", None
    if data == "menu:subjects":
        return formatting.format_subjects(client.get("/subjects")), subjects_keyboard(allow_writes=config.telegram.allow_writes)
    if data == "subject:create":
        ensure_writes_allowed(config.telegram)
        _set_state(update, handler_ctx, "create_subject")
        return "Send the subject display name.", None
    if data == "subject:delete":
        ensure_writes_allowed(config.telegram)
        subjects = client.get("/subjects").get("subjects", [])
        if not subjects:
            return "No subjects to delete.", main_menu_keyboard()
        return "Choose a subject to delete.", subject_delete_select_keyboard(subjects)
    if data.startswith("subject:delete_select:"):
        ensure_writes_allowed(config.telegram)
        subject_id = int(data.rsplit(":", 1)[1])
        subject = _find_by_id(client.get("/subjects").get("subjects", []), subject_id)
        return (
            "\n".join(
                [
                    f'Delete subject "{subject.get("display_name", f"subject {subject_id}")}"?',
                    "",
                    "This may also affect related episodes/data depending on backend behavior.",
                ]
            ),
            subject_delete_confirm_keyboard(subject_id),
        )
    if data.startswith("subject:delete_confirm:"):
        ensure_writes_allowed(config.telegram)
        subject_id = int(data.rsplit(":", 1)[1])
        subject_name = _subject_name_for_id(handler_ctx, subject_id)
        try:
            client.delete(f"/subjects/{subject_id}")
        except CzmError as exc:
            _clear_state(handler_ctx, identity_from_update(update))
            return _format_subject_delete_error(exc), subject_delete_recovery_keyboard()
        _clear_state(handler_ctx, identity_from_update(update))
        return f"Deleted subject: {subject_name}.", main_menu_keyboard()
    if data == "subject:delete_cancel":
        return "Subject deletion cancelled.", main_menu_keyboard()
    if data == "menu:locations":
        payload = client.get("/locations")
        return formatting.format_locations(payload), locations_keyboard(payload.get("locations", []), allow_writes=config.telegram.allow_writes)
    if data == "loc:create":
        ensure_writes_allowed(config.telegram)
        _set_state(update, handler_ctx, "create_location_display")
        return "Send the location display name.", None
    if data.startswith("loc:select:"):
        location_id = int(data.rsplit(":", 1)[1])
        return f"Location {location_id}", location_actions_keyboard(location_id, allow_writes=config.telegram.allow_writes)
    if data.startswith("loc:image:"):
        ensure_writes_allowed(config.telegram)
        location_id = int(data.rsplit(":", 1)[1])
        _set_state(update, handler_ctx, "waiting_location_photo", {"location_id": location_id})
        return f"Send a photo for location {location_id}.", None
    if data == "menu:adherence":
        return "Choose adherence view:", adherence_keyboard(allow_rebuild=config.telegram.allow_adherence_rebuild)
    if data.startswith("adh:"):
        result = _handle_adherence_callback(data, handler_ctx)
        return result if isinstance(result, tuple) else (result, None)
    if data == "menu:start_episode":
        ensure_writes_allowed(config.telegram)
        return _start_episode_subject_step(handler_ctx, update)
    if data.startswith("epstart:"):
        ensure_writes_allowed(config.telegram)
        return _handle_start_episode_callback(data, handler_ctx, update)
    if data == "menu:heal":
        ensure_writes_allowed(config.telegram)
        return _episode_action_list(handler_ctx, "heal")
    if data.startswith("heal:"):
        ensure_writes_allowed(config.telegram)
        return _handle_episode_action_callback(data, handler_ctx, "heal")
    if data == "menu:relapse":
        ensure_writes_allowed(config.telegram)
        return _episode_action_list(handler_ctx, "relapse")
    if data.startswith("relapse:"):
        ensure_writes_allowed(config.telegram)
        return _handle_episode_action_callback(data, handler_ctx, "relapse")
    return "Unknown or stale button. Tap /menu to start again.", None


def _handle_adherence_callback(data: str, handler_ctx: TelegramHandlerContext) -> str | tuple[str, object | None]:
    if data == "adh:rebuild":
        ensure_rebuild_allowed(handler_ctx.command_context.config.telegram)
        return "Choose rebuild range for active episodes:", adherence_rebuild_range_keyboard()
    if data == "adh:rebuild:cancel":
        return "Adherence rebuild cancelled.", None
    if data.startswith("adh:rebuild:range:"):
        ensure_rebuild_allowed(handler_ctx.command_context.config.telegram)
        days = int(data.rsplit(":", 1)[1])
        return f"Rebuild adherence snapshots for active episodes over the last {days} days?", adherence_rebuild_confirm_keyboard(days)
    if data.startswith("adh:rebuild:confirm:"):
        ensure_rebuild_allowed(handler_ctx.command_context.config.telegram)
        days = int(data.rsplit(":", 1)[1])
        today = local_today(handler_ctx.command_context.config.timezone)
        from_date = (today - timedelta(days=days - 1)).isoformat()
        to_date = today.isoformat()
        payload = handler_ctx.command_context.client.post(
            "/adherence/rebuild",
            json={"from": from_date, "to": to_date, "active_only": True, "source": "rebuild"},
        )
        return formatting.format_adherence_rebuild(payload), None
    _, mode, days_text = data.split(":")
    days = int(days_text)
    params = _adherence_range_params(handler_ctx, days)
    if mode == "summary":
        return formatting.format_adherence_summary(handler_ctx.command_context.client.get("/adherence/summary", params=params))
    if mode == "calendar":
        return formatting.format_adherence_days(handler_ctx.command_context.client.get("/adherence/calendar", params=params), title="Adherence calendar")
    if mode == "missed":
        return formatting.format_adherence_days(handler_ctx.command_context.client.get("/adherence/missed", params=params), title="Missed adherence days")
    raise CzmError("Unsupported adherence action", exit_code=EXIT_USAGE)


def _adherence_range_params(handler_ctx: TelegramHandlerContext, days: int) -> dict[str, str]:
    today = local_today(handler_ctx.command_context.config.timezone)
    from_date = (today - timedelta(days=days - 1)).isoformat()
    return {"from": from_date, "to": today.isoformat()}


async def _send_adherence_summary_heatmap(update, handler_ctx: TelegramHandlerContext) -> None:
    query = update.callback_query
    data = query.data or ""
    days = int(data.rsplit(":", 1)[1])
    params = _adherence_range_params(handler_ctx, days)
    client = handler_ctx.command_context.client
    summary = client.get("/adherence/summary", params=params)
    await safe_edit_callback_message(query, formatting.format_adherence_summary(summary))
    try:
        calendar = client.get("/adherence/calendar", params=params)
        subjects = client.get("/subjects")
        locations = client.get("/locations")
        grid = build_heatmap_grid(
            calendar,
            subjects,
            locations,
            from_date=date.fromisoformat(params["from"]),
            to_date=date.fromisoformat(params["to"]),
        )
        image = BytesIO(render_heatmap_png(grid))
        image.name = f"zema-adherence-heatmap-{days}d.png"
        await query.message.reply_photo(photo=image, caption=f"Adherence heatmap - last {days} days")
    except Exception:
        pass
    await _send_terminal_success(update, "Done.")


def _start_episode_subject_step(handler_ctx: TelegramHandlerContext, update) -> tuple[str, object | None]:
    _set_state(update, handler_ctx, "start_episode", {})
    payload = handler_ctx.command_context.client.get("/subjects")
    subjects = payload.get("subjects", [])
    if len(subjects) == 1:
        subject = subjects[0]
        flow = {"subject_id": subject["id"], "subject_name": subject.get("display_name", f"subject {subject['id']}")}
        _set_state(update, handler_ctx, "start_episode", flow)
        location_text, keyboard = _start_episode_location_step(handler_ctx, update, flow)
        return f"Using subject: {flow['subject_name']}.\n{location_text}", keyboard
    if not subjects:
        return "Start episode: create a subject first.", start_subject_keyboard(subjects, allow_writes=handler_ctx.command_context.config.telegram.allow_writes)
    return "Start episode: choose a subject.", start_subject_keyboard(subjects, allow_writes=handler_ctx.command_context.config.telegram.allow_writes)


def _handle_start_episode_callback(data: str, handler_ctx: TelegramHandlerContext, update) -> tuple[str, object | None]:
    identity = identity_from_update(update)
    state, expired = _get_state(handler_ctx, identity)
    if expired or state is None or state.name != "start_episode":
        return EXPIRED_STATE_MESSAGE, None
    flow = dict(state.data)
    if data == "epstart:cancel":
        _clear_state(handler_ctx, identity)
        return "Start episode cancelled.", None
    if data == "epstart:subject_new":
        _set_state(update, handler_ctx, "start_episode_subject_text", flow)
        return "Send the new subject display name.", None
    if data.startswith("epstart:subject:"):
        subject_id = int(data.rsplit(":", 1)[1])
        subject = _find_by_id(handler_ctx.command_context.client.get("/subjects").get("subjects", []), subject_id)
        flow.update({"subject_id": subject_id, "subject_name": subject.get("display_name", f"subject {subject_id}")})
        _set_state(update, handler_ctx, "start_episode", flow)
        return _start_episode_location_step(handler_ctx, update, flow)
    if data == "epstart:locations":
        _set_state(update, handler_ctx, "start_episode", flow)
        return _start_episode_location_step(handler_ctx, update, flow)
    if data == "epstart:loc_new":
        _set_state(update, handler_ctx, "start_episode_location_display", flow)
        return "Send the new location display name.", None
    if data.startswith("epstart:loc:"):
        location_id = int(data.rsplit(":", 1)[1])
        location = _find_by_id(handler_ctx.command_context.client.get("/locations").get("locations", []), location_id)
        if _active_episode_for_start_location(handler_ctx, flow, location_id) is not None:
            return (
                "\n".join(
                    [
                        f"There is already an active episode for {location.get('display_name', f'location {location_id}')}.",
                        "Use Due now, Heal, or Relapse for the existing episode, or choose/create a different location.",
                    ]
                ),
                start_location_conflict_keyboard(),
            )
        flow.update(
            {
                "location_id": location_id,
                "location_name": location.get("display_name", f"location {location_id}"),
                "location_has_image": bool(location.get("image")),
            }
        )
        _set_state(update, handler_ctx, "start_episode", flow)
        return _start_episode_image_step(flow)
    if data == "epstart:image":
        if "location_id" not in flow:
            return EXPIRED_STATE_MESSAGE, None
        _set_state(update, handler_ctx, "start_episode_waiting_photo", flow)
        return f"Send a photo for {flow.get('location_name', 'this location')}.", None
    if data == "epstart:skip_image":
        return _start_episode_confirm_step(flow)
    return "Unknown or stale button. Tap /menu to start again.", None


async def _confirm_start_episode(update, handler_ctx: TelegramHandlerContext) -> None:
    query = update.callback_query
    identity = identity_from_update(update)
    state, expired = _get_state(handler_ctx, identity)
    if expired or state is None or state.name != "start_episode":
        await safe_edit_callback_message(query, EXPIRED_STATE_MESSAGE)
        return
    flow = dict(state.data)
    if "subject_id" not in flow or "location_id" not in flow:
        await safe_edit_callback_message(query, EXPIRED_STATE_MESSAGE)
        return
    payload = handler_ctx.command_context.client.post(
        "/episodes",
        json={"subject_id": flow["subject_id"], "location_id": flow["location_id"], "protocol_version": "v1"},
    )
    _clear_state(handler_ctx, identity)
    await safe_edit_callback_message(query, "Episode created.", reply_markup=None)
    await _send_terminal_success(update, formatting.format_episode_created(payload))


async def _send_terminal_success(update, text: str) -> None:
    await update.callback_query.message.reply_text(text, reply_markup=_reply_keyboard_for_update(update))


def _start_episode_location_step(handler_ctx: TelegramHandlerContext, update=None, flow: dict | None = None) -> tuple[str, object | None]:
    payload = handler_ctx.command_context.client.get("/locations")
    locations = payload.get("locations", [])
    available_locations = _available_start_locations(handler_ctx, flow or {}, locations)
    if not available_locations:
        if update is not None:
            _set_state(update, handler_ctx, "start_episode_location_display", flow or {})
        if locations:
            return "All existing locations already have active episodes. Send the new location display name.", None
        return "No locations exist yet. Send the new location display name.", None
    return "Choose a location.", start_location_keyboard(available_locations, allow_writes=handler_ctx.command_context.config.telegram.allow_writes)


def _available_start_locations(handler_ctx: TelegramHandlerContext, flow: dict, locations: list[dict]) -> list[dict]:
    return [location for location in locations if _active_episode_for_start_location(handler_ctx, flow, int(location["id"])) is None]


def _active_episode_for_start_location(handler_ctx: TelegramHandlerContext, flow: dict, location_id: int) -> dict | None:
    subject_id = flow.get("subject_id")
    if subject_id is None:
        return None
    episodes = handler_ctx.command_context.client.get("/episodes").get("episodes", [])
    for episode in episodes:
        if int(episode.get("subject_id", -1)) != int(subject_id) or int(episode.get("location_id", -1)) != int(location_id):
            continue
        if _episode_blocks_new_start(episode):
            return episode
    return None


def _episode_blocks_new_start(episode: dict) -> bool:
    status = episode.get("status")
    if status:
        return status not in {"obsolete", "healed"}
    if episode.get("obsolete_at"):
        return False
    if episode.get("healed_at"):
        return False
    return True


def _location_code_from_display_name(display_name: str) -> str | None:
    value = display_name.strip().lower()
    value = value.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or None


def _find_location_by_code(handler_ctx: TelegramHandlerContext, code: str) -> dict | None:
    locations = handler_ctx.command_context.client.get("/locations").get("locations", [])
    return next((location for location in locations if location.get("code") == code), None)


def _duplicate_location_message(code: str, location: dict) -> str:
    return (
        f'A location with code {code} already exists: {location.get("display_name", code)}.\n'
        "Please use the existing location or choose a different display name."
    )


def _start_episode_image_step(flow: dict) -> tuple[str, object | None]:
    return f"Location selected: {flow.get('location_name')}.\nAdd or replace its image?", start_image_keyboard()


def _start_episode_confirm_step(flow: dict) -> tuple[str, object | None]:
    image_text = "yes" if flow.get("location_has_image") else "no"
    return (
        "\n".join(
            [
                "Create episode?",
                "",
                f"Subject: {flow.get('subject_name', flow.get('subject_id'))}",
                f"Location: {flow.get('location_name', flow.get('location_id'))}",
                f"Image: {image_text}",
            ]
        ),
        start_confirm_keyboard(),
    )


def _episode_action_list(handler_ctx: TelegramHandlerContext, action: str) -> tuple[str, object | None]:
    episodes = handler_ctx.command_context.client.get("/episodes").get("episodes", [])
    subjects = handler_ctx.command_context.client.get("/subjects").get("subjects", [])
    locations = handler_ctx.command_context.client.get("/locations").get("locations", [])
    if action == "heal":
        eligible = [episode for episode in episodes if episode.get("status") not in {"obsolete", "in_taper"} and not episode.get("obsolete_at")]
        title = "Choose an episode to heal."
    else:
        healed = [episode for episode in episodes if episode.get("healed_at") and not episode.get("obsolete_at")]
        eligible = healed or [episode for episode in episodes if episode.get("status") != "obsolete" and not episode.get("obsolete_at")]
        title = "Choose an episode to relapse."
    if not eligible:
        return f"No episodes available to {action}.", None
    eligible = _with_episode_labels(eligible, subjects, locations)
    return title, episode_select_keyboard(action, eligible)


def _handle_episode_action_callback(data: str, handler_ctx: TelegramHandlerContext, action: str) -> tuple[str, object | None]:
    if data == f"{action}:cancel":
        return f"{action.title()} cancelled.", None
    if data.startswith(f"{action}:select:"):
        episode_id = int(data.rsplit(":", 1)[1])
        return f"{action.title()} episode {episode_id}?", confirm_episode_action_keyboard(action, episode_id)
    if data.startswith(f"{action}:confirm:"):
        episode_id = int(data.rsplit(":", 1)[1])
        if action == "heal":
            payload = handler_ctx.command_context.client.post(f"/episodes/{episode_id}/heal", json=None)
            return formatting.format_episode_action_success("Healed", payload), None
        payload = handler_ctx.command_context.client.post(f"/episodes/{episode_id}/relapse", json={"reason": "relapse"})
        return formatting.format_episode_action_success("Relapsed", payload), None
    return "Unknown or stale button. Tap /menu to start again.", None


async def _send_due_prompts(update, context, handler_ctx: TelegramHandlerContext) -> None:
    del context
    query = update.callback_query
    message = query.message
    try:
        due_items = _enriched_due_items(handler_ctx)
    except CzmError as exc:
        await safe_edit_callback_message(query, _format_due_failure(exc))
        return
    if not due_items:
        await safe_edit_callback_message(query, "No treatments are due right now.")
        return
    limit = formatting.MAX_ROWS
    shown = due_items[:limit]
    await _send_due_prompt_messages(message, handler_ctx, shown)
    if len(due_items) > limit:
        await message.reply_text(f"Showing {limit} of {len(due_items)} due items.", reply_markup=main_menu_keyboard())


async def _send_due_prompts_from_message(message, handler_ctx: TelegramHandlerContext) -> None:
    try:
        due_items = _enriched_due_items(handler_ctx)
    except CzmError as exc:
        await message.reply_text(_format_due_failure(exc))
        return
    if not due_items:
        await message.reply_text("No treatments are due right now.")
        return
    limit = formatting.MAX_ROWS
    shown = due_items[:limit]
    await _send_due_prompt_messages(message, handler_ctx, shown)
    if len(due_items) > limit:
        await message.reply_text(f"Showing {limit} of {len(due_items)} due items.", reply_markup=main_menu_keyboard())


async def _send_due_prompt_messages(message, handler_ctx: TelegramHandlerContext, due_items: list[dict]) -> None:
    for item in due_items:
        text = _format_due_prompt(item)
        keyboard = due_prompt_keyboard(int(item["episode_id"]), allow_writes=handler_ctx.command_context.config.telegram.allow_writes)
        image = _safe_location_image(handler_ctx, item.get("location_id"))
        if image is not None and hasattr(message, "reply_photo"):
            await message.reply_photo(photo=image[0], caption=text, reply_markup=keyboard)
        else:
            await message.reply_text(text, reply_markup=keyboard)


def _format_due_prompt(item: dict) -> str:
    lines = [
        item.get("telegram_label") or f"Episode {item.get('episode_id')}",
    ]
    if item.get("telegram_include_subject"):
        lines.append(f"Subject: {item.get('telegram_subject_name') or item.get('subject_id')}")
    lines.append(f"Phase: {item.get('current_phase_number')}")
    next_phase_change = _format_due_next_phase_change(item.get("telegram_phase_due_end_at"), ZoneInfo(item.get("telegram_timezone") or "UTC"))
    if next_phase_change is not None:
        lines.append(f"Next phase change: {next_phase_change}")
    status = item.get("status")
    if status:
        lines.append(f"Status: {status}")
    return "\n".join(lines)


def _format_due_next_phase_change(value, tzinfo: ZoneInfo) -> str | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return f"{parsed.astimezone(tzinfo):%d.%m.}"


def _format_subject_delete_error(exc: CzmError) -> str:
    status_code = getattr(exc, "status_code", None)
    if status_code == 404 or exc.exit_code == EXIT_NOT_FOUND:
        return "Subject not found. It may already have been deleted."
    return formatting.backend_error_message(exc.message)


def _format_due_failure(exc: CzmError) -> str:
    status_code = getattr(exc, "status_code", None)
    if exc.message == "Zema returned an unreadable due response.":
        return exc.message
    if exc.exit_code == EXIT_AUTH:
        return "Zema authentication failed. Tracking is not active."
    if status_code is not None and status_code >= 500:
        return "Zema backend failed while checking due treatments."
    if exc.exit_code == EXIT_TRANSPORT:
        return "Zema backend did not respond. Do not assume nothing is due."
    return formatting.backend_error_message(exc.message)


def _current_due_item_for_episode(handler_ctx: TelegramHandlerContext, episode_id: int) -> dict | None:
    for item in _enriched_due_items(handler_ctx):
        if int(item.get("episode_id")) == episode_id:
            return item
    return None


def _subject_name_for_id(handler_ctx: TelegramHandlerContext, subject_id: int) -> str:
    try:
        subject = _find_by_id(handler_ctx.command_context.client.get("/subjects").get("subjects", []), subject_id)
        return subject.get("display_name", f"subject {subject_id}")
    except Exception:
        return f"subject {subject_id}"


def _enriched_due_items(handler_ctx: TelegramHandlerContext) -> list[dict]:
    due_items = _raw_due_items(handler_ctx)
    subjects = handler_ctx.command_context.client.get("/subjects").get("subjects", [])
    locations = handler_ctx.command_context.client.get("/locations").get("locations", [])
    episodes = handler_ctx.command_context.client.get("/episodes").get("episodes", [])
    subject_names = {item.get("id"): item.get("display_name") for item in subjects}
    location_names = {item.get("id"): item.get("display_name") for item in locations}
    episodes_by_id = {item.get("id"): item for item in episodes}
    include_subject = len(subjects) > 1
    timezone_name = handler_ctx.command_context.config.timezone
    location_counts: dict[int, int] = {}
    for item in due_items:
        location_id = item.get("location_id")
        location_counts[location_id] = location_counts.get(location_id, 0) + 1

    base_labels: list[str] = []
    for item in due_items:
        location_name = location_names.get(item.get("location_id")) or f"Location {item.get('location_id')}"
        subject_name = subject_names.get(item.get("subject_id")) or f"Subject {item.get('subject_id')}"
        base_labels.append(f"{location_name} — {subject_name}" if location_counts.get(item.get("location_id"), 0) > 1 else location_name)
    base_counts = {label: base_labels.count(label) for label in set(base_labels)}

    enriched = []
    for item, base_label in zip(due_items, base_labels, strict=False):
        copied = dict(item)
        episode = episodes_by_id.get(item.get("episode_id"), {})
        if base_counts.get(base_label, 0) > 1:
            label = f"{base_label} · phase {item.get('current_phase_number')} · #{item.get('episode_id')}"
        else:
            label = base_label
        copied["telegram_label"] = label
        copied["telegram_location_name"] = location_names.get(item.get("location_id")) or f"Location {item.get('location_id')}"
        copied["telegram_subject_name"] = subject_names.get(item.get("subject_id")) or f"Subject {item.get('subject_id')}"
        copied["telegram_include_subject"] = include_subject
        copied["telegram_phase_due_end_at"] = item.get("phase_due_end_at") or episode.get("phase_due_end_at")
        copied["telegram_timezone"] = timezone_name
        enriched.append(copied)
    return enriched


def _raw_due_items(handler_ctx: TelegramHandlerContext) -> list[dict]:
    payload = handler_ctx.command_context.client.get("/episodes/due")
    if not isinstance(payload, dict):
        raise CzmError("Zema returned an unreadable due response.", exit_code=EXIT_TRANSPORT)
    due_items = payload.get("due")
    if not isinstance(due_items, list):
        raise CzmError("Zema returned an unreadable due response.", exit_code=EXIT_TRANSPORT)
    if not all(isinstance(item, dict) for item in due_items):
        raise CzmError("Zema returned an unreadable due response.", exit_code=EXIT_TRANSPORT)
    return due_items


async def handle_guided_text(update, context, handler_ctx: TelegramHandlerContext) -> bool:
    message = update.effective_message
    identity = identity_from_update(update)
    try:
        ensure_allowed(handler_ctx.command_context.config.telegram, identity)
        state, expired = _get_state(handler_ctx, identity)
        if expired:
            await message.reply_text(EXPIRED_STATE_MESSAGE)
            return True
        if state is None:
            return False
        ensure_writes_allowed(handler_ctx.command_context.config.telegram)
        text = (getattr(message, "text", "") or "").strip()
        if not text:
            await message.reply_text("Send text, or tap /menu to start again.")
            return True
        if state.name == "create_subject":
            payload = handler_ctx.command_context.client.post("/subjects", json={"display_name": text})
            _clear_state(handler_ctx, identity)
            await message.reply_text(formatting.format_subject_created(payload))
            return True
        if state.name == "create_location_display":
            code = _location_code_from_display_name(text)
            if code is None:
                await message.reply_text("Location display name must include at least one letter or number.")
                return True
            duplicate = _find_location_by_code(handler_ctx, code)
            if duplicate is not None:
                _clear_state(handler_ctx, identity)
                locations = handler_ctx.command_context.client.get("/locations").get("locations", [])
                await message.reply_text(_duplicate_location_message(code, duplicate), reply_markup=locations_keyboard(locations, allow_writes=True))
                return True
            payload = handler_ctx.command_context.client.post("/locations", json={"code": code, "display_name": text})
            location_id = payload["location"]["id"]
            handler_ctx.state.set(identity.chat_id, identity.user_id, "created_location", {"location_id": location_id})
            await message.reply_text(formatting.format_location_created(payload) + "\nAdd an image?", reply_markup=location_image_prompt_keyboard(location_id))
            return True
        if state.name == "start_episode_subject_text":
            payload = handler_ctx.command_context.client.post("/subjects", json={"display_name": text})
            subject = payload
            flow = dict(state.data)
            flow.update({"subject_id": subject["id"], "subject_name": subject["display_name"]})
            handler_ctx.state.set(identity.chat_id, identity.user_id, "start_episode", flow)
            location_text, keyboard = _start_episode_location_step(handler_ctx, update, flow)
            await message.reply_text(location_text, reply_markup=keyboard)
            return True
        if state.name == "start_episode_location_display":
            flow = dict(state.data)
            code = _location_code_from_display_name(text)
            if code is None:
                await message.reply_text("Location display name must include at least one letter or number.")
                return True
            duplicate = _find_location_by_code(handler_ctx, code)
            if duplicate is not None:
                handler_ctx.state.set(identity.chat_id, identity.user_id, "start_episode", flow)
                await message.reply_text(_duplicate_location_message(code, duplicate), reply_markup=start_duplicate_location_keyboard(int(duplicate["id"])))
                return True
            payload = handler_ctx.command_context.client.post("/locations", json={"code": code, "display_name": text})
            location = payload["location"]
            flow.update({"location_id": location["id"], "location_name": location["display_name"], "location_has_image": bool(location.get("image"))})
            handler_ctx.state.set(identity.chat_id, identity.user_id, "start_episode", flow)
            image_text, keyboard = _start_episode_image_step(flow)
            await message.reply_text(image_text, reply_markup=keyboard)
            return True
        if state.name == "waiting_location_photo":
            await message.reply_text("Please send a Telegram photo for this location, or tap /menu to cancel.")
            return True
        if state.name == "start_episode_waiting_photo":
            await message.reply_text("Please send a Telegram photo for this location, or tap /menu to cancel.")
            return True
        return False
    except CzmError as exc:
        await message.reply_text(exc.message if exc.exit_code == EXIT_AUTH else formatting.backend_error_message(exc.message))
        return True


async def handle_text_message(update, context, handler_ctx: TelegramHandlerContext) -> None:
    message = update.effective_message
    text = (getattr(message, "text", "") or "").strip()
    mapping = {
        "Start episode": "menu:start_episode",
        "Due now": "menu:due",
        "Due today": "menu:due",
        "Adherence": "menu:adherence",
        "Heal episode": "menu:heal",
        "Relapse episode": "menu:relapse",
        "Locations": "menu:locations",
        "Subjects": "menu:subjects",
    }
    if text == "Log treatment":
        try:
            ensure_allowed(handler_ctx.command_context.config.telegram, identity_from_update(update))
            await message.reply_text("Log treatment moved to Due now.", reply_markup=_reply_keyboard_for_update(update))
            await _send_due_prompts_from_message(message, handler_ctx)
        except CzmError as exc:
            reply = exc.message if exc.exit_code == EXIT_AUTH else formatting.backend_error_message(exc.message)
            await message.reply_text(reply, reply_markup=_reply_keyboard_for_update(update))
        except Exception:
            await message.reply_text("Zema request failed.", reply_markup=_reply_keyboard_for_update(update))
        return
    if text in mapping:
        try:
            ensure_allowed(handler_ctx.command_context.config.telegram, identity_from_update(update))
            if text in {"Due now", "Due today", "Log treatment"}:
                await _send_due_prompts_from_message(message, handler_ctx)
                return
            reply, keyboard = _dispatch_callback(mapping[text], handler_ctx, update)
        except CzmError as exc:
            reply, keyboard = (exc.message if exc.exit_code == EXIT_AUTH else formatting.backend_error_message(exc.message)), None
        except Exception:
            reply, keyboard = "Zema request failed.", None
        await message.reply_text(reply, reply_markup=keyboard or _reply_keyboard_for_update(update))
        return
    handled = await handle_guided_text(update, context, handler_ctx)
    if handled:
        return


async def handle_location_image_set_text(update, context, handler_ctx: TelegramHandlerContext) -> str:
    del context
    identity = identity_from_update(update)
    ensure_allowed(handler_ctx.command_context.config.telegram, identity)
    ensure_writes_allowed(handler_ctx.command_context.config.telegram)
    text = getattr(update.effective_message, "text", "") or ""
    try:
        parts = shlex.split(text)
    except ValueError as exc:
        raise CzmError("Usage: /location_image_set left_elbow", exit_code=EXIT_USAGE) from exc
    if len(parts) != 2:
        raise CzmError("Usage: /location_image_set left_elbow", exit_code=EXIT_USAGE)
    reference = parts[1]
    location_id = _resolve_location_id(handler_ctx, reference)
    if identity.chat_id is None or identity.user_id is None:
        raise CzmError("Telegram chat/user identity is missing", exit_code=EXIT_AUTH)
    handler_ctx.state.set(identity.chat_id, identity.user_id, "waiting_location_photo", {"location_id": location_id})
    return f"Send a photo for location {location_id}."


async def handle_photo(update, context, handler_ctx: TelegramHandlerContext) -> None:
    message = update.effective_message
    identity = identity_from_update(update)
    try:
        ensure_allowed(handler_ctx.command_context.config.telegram, identity)
        ensure_writes_allowed(handler_ctx.command_context.config.telegram)
        state, expired = _get_state(handler_ctx, identity)
        if expired:
            await message.reply_text(EXPIRED_STATE_MESSAGE)
            return
        if state is None or state.name not in {"waiting_location_photo", "created_location", "start_episode_waiting_photo"}:
            await message.reply_text(EXPIRED_STATE_MESSAGE)
            return
        location_id = int(state.data["location_id"])
        photo = sorted(getattr(message, "photo", []) or [], key=lambda item: getattr(item, "file_size", 0) or 0)[-1]
        file_obj = await photo.get_file()
        content = bytes(await file_obj.download_as_bytearray())
        handler_ctx.command_context.client.upload_bytes(
            f"/locations/{location_id}/image",
            field_name="image",
            filename=f"telegram-location-{location_id}.jpg",
            content=content,
            content_type="image/jpeg",
        )
        if state.name == "start_episode_waiting_photo":
            flow = dict(state.data)
            flow["location_has_image"] = True
            handler_ctx.state.set(identity.chat_id, identity.user_id, "start_episode", flow)
            text, keyboard = _start_episode_confirm_step(flow)
            await message.reply_text(f"Location image updated for location {location_id}.\n\n{text}", reply_markup=keyboard)
            return
        _clear_state(handler_ctx, identity)
        await message.reply_text(f"Location image updated for location {location_id}.")
    except IndexError:
        await message.reply_text("Please send a Telegram photo.")
    except CzmError as exc:
        await message.reply_text(exc.message if exc.exit_code == EXIT_AUTH else formatting.backend_error_message(exc.message))
    except Exception:
        await message.reply_text("Zema request failed.")


def _set_state(update, handler_ctx: TelegramHandlerContext, name: str, data: dict | None = None) -> None:
    identity = identity_from_update(update)
    if identity.chat_id is None or identity.user_id is None:
        raise CzmError("Telegram chat/user identity is missing", exit_code=EXIT_AUTH)
    handler_ctx.state.set(identity.chat_id, identity.user_id, name, data)


def _get_state(handler_ctx: TelegramHandlerContext, identity):
    if identity.chat_id is None or identity.user_id is None:
        return None, False
    return handler_ctx.state.get_with_expiry(identity.chat_id, identity.user_id)


def _clear_state(handler_ctx: TelegramHandlerContext, identity) -> None:
    if identity.chat_id is not None and identity.user_id is not None:
        handler_ctx.state.clear(identity.chat_id, identity.user_id)


def _find_by_id(items: list[dict], item_id: int) -> dict:
    for item in items:
        if item.get("id") == item_id:
            return item
    return {"id": item_id}


def _with_episode_labels(episodes: list[dict], subjects: list[dict], locations: list[dict]) -> list[dict]:
    subject_names = {item.get("id"): item.get("display_name") for item in subjects}
    location_names = {item.get("id"): item.get("display_name") for item in locations}
    location_counts: dict[int, int] = {}
    for episode in episodes:
        location_id = episode.get("location_id")
        location_counts[location_id] = location_counts.get(location_id, 0) + 1
    labels_seen: dict[str, int] = {}
    enriched = []
    for episode in episodes:
        copied = dict(episode)
        location_name = location_names.get(episode.get("location_id")) or f"Location {episode.get('location_id')}"
        subject_name = subject_names.get(episode.get("subject_id")) or f"Subject {episode.get('subject_id')}"
        label = location_name
        if location_counts.get(episode.get("location_id"), 0) > 1:
            label = f"{location_name} — {subject_name}"
        labels_seen[label] = labels_seen.get(label, 0) + 1
        if labels_seen[label] > 1:
            label = f"{label} · phase {episode.get('current_phase_number')} · #{episode.get('id')}"
        copied["telegram_label"] = label
        copied["telegram_location_name"] = location_name
        copied["telegram_subject_name"] = subject_name
        enriched.append(copied)
    return enriched


async def _send_episode_action_confirmation(update, context, handler_ctx: TelegramHandlerContext, data: str) -> None:
    action = data.split(":", 1)[0]
    episode_id = int(data.rsplit(":", 1)[1])
    episodes = handler_ctx.command_context.client.get("/episodes").get("episodes", [])
    subjects = handler_ctx.command_context.client.get("/subjects").get("subjects", [])
    locations = handler_ctx.command_context.client.get("/locations").get("locations", [])
    episode = _find_by_id(_with_episode_labels(episodes, subjects, locations), episode_id)
    location_name = episode.get("telegram_location_name") or f"location {episode.get('location_id')}"
    verb = "healed" if action == "heal" else "relapsed"
    text = f"Mark {location_name} as {verb}?"
    keyboard = confirm_episode_action_keyboard(action, episode_id)
    image = _safe_location_image(handler_ctx, episode.get("location_id"))
    query = update.callback_query
    if image is not None and hasattr(query.message, "reply_photo"):
        await query.message.reply_photo(photo=image[0], caption=text, reply_markup=keyboard)
        return
    await safe_edit_callback_message(query, text, reply_markup=keyboard)


def _safe_location_image(handler_ctx: TelegramHandlerContext, location_id) -> tuple[bytes, str | None] | None:
    if location_id is None:
        return None
    try:
        return handler_ctx.command_context.client.download_file(f"/locations/{int(location_id)}/image")
    except Exception:
        return None


def _reply_keyboard_for_update(update):
    chat = getattr(update, "effective_chat", None)
    if getattr(chat, "type", None) == "private":
        return main_menu_reply_keyboard()
    return main_menu_keyboard()


def _resolve_location_id(handler_ctx: TelegramHandlerContext, reference: str) -> int:
    locations = handler_ctx.command_context.client.get("/locations").get("locations", [])
    try:
        numeric = int(reference)
    except ValueError:
        numeric = None
    if numeric is not None and any(item.get("id") == numeric for item in locations):
        return numeric
    lowered = reference.lower()
    matches = [
        item["id"]
        for item in locations
        if any((item.get(field) or "").lower() == lowered for field in ("code", "display_name"))
    ]
    if len(matches) == 1:
        return matches[0]
    matches = [
        item["id"]
        for item in locations
        if any(lowered in (item.get(field) or "").lower() for field in ("code", "display_name"))
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise CzmError(f"location not found: {reference}", exit_code=EXIT_USAGE)
    raise CzmError(f"location reference is ambiguous: {reference}", exit_code=EXIT_USAGE)


async def safe_edit_callback_message(query, text: str, reply_markup=None) -> None:
    message = getattr(query, "message", None)
    if _is_caption_message(message) and hasattr(query, "edit_message_caption"):
        try:
            await query.edit_message_caption(caption=text, reply_markup=reply_markup)
            return
        except BadRequest as exc:
            if "There is no caption in the message to edit" not in str(exc):
                raise
    if hasattr(query, "edit_message_text"):
        try:
            await query.edit_message_text(text, reply_markup=reply_markup)
            return
        except BadRequest as exc:
            if "There is no text in the message to edit" not in str(exc):
                raise
    if message is not None:
        await message.reply_text(text, reply_markup=reply_markup)


def _is_caption_message(message) -> bool:
    if message is None:
        return False
    if getattr(message, "caption", None) is not None:
        return True
    return bool(getattr(message, "photo", None) or getattr(message, "document", None))


async def _maybe_await(value):
    if hasattr(value, "__await__"):
        return await value
    return value
