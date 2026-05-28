from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from czm_cli.config import AppConfig, parse_hhmm


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class SnoozeStore:
    snooze_minutes: int
    clock: Any = _utc_now
    _until: dict[tuple[int, int], datetime] = field(default_factory=dict)

    def snooze(self, chat_id: int, episode_id: int) -> datetime:
        until = self.clock() + timedelta(minutes=self.snooze_minutes)
        self._until[(chat_id, episode_id)] = until
        return until

    def is_snoozed(self, chat_id: int, episode_id: int) -> bool:
        key = (chat_id, episode_id)
        until = self._until.get(key)
        if until is None:
            return False
        if until <= self.clock():
            self._until.pop(key, None)
            return False
        return True


def reminder_timezone(config: AppConfig) -> ZoneInfo:
    return ZoneInfo(config.telegram.reminders.timezone or config.timezone)


def reminder_keyboard(episode_id: int, *, allow_writes: bool) -> InlineKeyboardMarkup:
    rows = []
    if allow_writes:
        rows.append([InlineKeyboardButton("Done", callback_data=f"due:log:{episode_id}")])
    rows.append(
        [
            InlineKeyboardButton("Snooze", callback_data=f"rem:snooze:{episode_id}"),
            InlineKeyboardButton("Open menu", callback_data="menu:open"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def schedule_reminders(application, handler_ctx) -> None:
    config = handler_ctx.command_context.config
    reminders = config.telegram.reminders
    if not reminders.enabled:
        return
    # Access the private slot first so test environments that have not reinstalled
    # the job-queue extra do not emit PTB's warning just by building handlers.
    job_queue = getattr(application, "_job_queue", None) if hasattr(application, "_job_queue") else getattr(application, "job_queue", None)
    if job_queue is None:
        logging.warning("Telegram reminders are enabled, but python-telegram-bot JobQueue is unavailable.")
        return
    tzinfo = reminder_timezone(config)
    morning = _time_with_zone(reminders.morning_time, "telegram.reminders.morning_time", tzinfo)
    evening = _time_with_zone(reminders.evening_time, "telegram.reminders.evening_time", tzinfo)
    job_queue.run_daily(_morning_job, time=morning, name="zema-morning-reminder", data=handler_ctx)
    job_queue.run_daily(_evening_job, time=evening, name="zema-evening-reminder", data=handler_ctx)


async def _morning_job(context) -> None:
    await send_due_reminders(context.bot, context.job.data, reminder_kind="morning")


async def _evening_job(context) -> None:
    await send_due_reminders(context.bot, context.job.data, reminder_kind="evening")


async def send_due_reminders(bot, handler_ctx, *, reminder_kind: str) -> None:
    config = handler_ctx.command_context.config
    payload = handler_ctx.command_context.client.get("/episodes/due")
    due_items = [item for item in payload.get("due", []) if item.get("treatment_due_today")]
    if reminder_kind == "evening":
        due_items = [item for item in due_items if item.get("current_phase_number") == 1]
    if not due_items:
        return
    subjects = handler_ctx.command_context.client.get("/subjects").get("subjects", [])
    locations = handler_ctx.command_context.client.get("/locations").get("locations", [])
    episodes = handler_ctx.command_context.client.get("/episodes").get("episodes", [])
    subject_names = {item.get("id"): item.get("display_name") for item in subjects}
    locations_by_id = {item.get("id"): item for item in locations}
    episodes_by_id = {item.get("id"): item for item in episodes}
    include_subject = len(subjects) > 1
    tzinfo = reminder_timezone(config)
    for chat_id in config.telegram.allowed_chat_ids:
        for item in due_items:
            episode_id = int(item["episode_id"])
            if handler_ctx.snoozes is not None and handler_ctx.snoozes.is_snoozed(chat_id, episode_id):
                continue
            location = locations_by_id.get(item.get("location_id"), {})
            episode = episodes_by_id.get(episode_id, {})
            text = _reminder_text(
                item,
                location_name=_location_label(location, item.get("location_id")),
                subject_name=subject_names.get(item.get("subject_id")) or f"Subject {item.get('subject_id')}",
                include_subject=include_subject,
                phase_due_end_at=item.get("phase_due_end_at") or episode.get("phase_due_end_at"),
                tzinfo=tzinfo,
            )
            keyboard = reminder_keyboard(episode_id, allow_writes=config.telegram.allow_writes)
            image = _location_image(handler_ctx, item.get("location_id")) if config.telegram.reminders.send_location_images else None
            if image is not None:
                await bot.send_photo(chat_id=chat_id, photo=image[0], caption=text, reply_markup=keyboard)
            else:
                await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)


def _time_with_zone(value: str, label: str, tzinfo: ZoneInfo) -> time:
    parsed = parse_hhmm(value, label=label)
    return parsed.replace(tzinfo=tzinfo)


def _location_label(location: dict, location_id: int | None) -> str:
    return location.get("display_name") or location.get("code") or f"Location {location_id}"


def _reminder_header(item: dict) -> str:
    phase = item.get("current_phase_number")
    due_slot = item.get("due_slot")
    if phase == 1 and due_slot == "morning":
        return "Apply this morning:"
    if phase == 1 and due_slot == "evening":
        return "Apply this evening:"
    return "Apply today:"


def _format_next_phase_change(value: Any, tzinfo: ZoneInfo) -> str | None:
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
    local = parsed.astimezone(tzinfo)
    return f"{local:%d.%m.}"


def _reminder_text(
    item: dict,
    *,
    location_name: str,
    subject_name: str,
    include_subject: bool,
    phase_due_end_at: Any = None,
    tzinfo: ZoneInfo,
) -> str:
    lines = [
        _reminder_header(item),
        "",
        f"Location: {location_name}",
    ]
    if include_subject:
        lines.append(f"Subject: {subject_name}")
    lines.append(f"Phase: {item.get('current_phase_number')}")
    next_phase_change = _format_next_phase_change(phase_due_end_at, tzinfo)
    if next_phase_change is not None:
        lines.append(f"Next phase change: {next_phase_change}")
    return "\n".join(lines)


def _location_image(handler_ctx, location_id) -> tuple[bytes, str | None] | None:
    if location_id is None:
        return None
    try:
        return handler_ctx.command_context.client.download_file(f"/locations/{int(location_id)}/image")
    except Exception:
        return None
