from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Start episode", callback_data="menu:start_episode"), InlineKeyboardButton("Due now", callback_data="menu:due")],
            [InlineKeyboardButton("Adherence", callback_data="menu:adherence"), InlineKeyboardButton("Heal episode", callback_data="menu:heal")],
            [InlineKeyboardButton("Relapse episode", callback_data="menu:relapse"), InlineKeyboardButton("Locations", callback_data="menu:locations")],
            [InlineKeyboardButton("Subjects", callback_data="menu:subjects")],
        ]
    )


def main_menu_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("Start episode"), KeyboardButton("Due now")],
            [KeyboardButton("Adherence"), KeyboardButton("Heal episode")],
            [KeyboardButton("Relapse episode"), KeyboardButton("Locations")],
            [KeyboardButton("Subjects")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def open_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Open menu", callback_data="menu:open")]])


def due_keyboard(due_items: list[dict], *, allow_writes: bool) -> InlineKeyboardMarkup | None:
    rows = []
    if not allow_writes:
        return open_menu_keyboard()
    rows = [[InlineKeyboardButton(f"Log episode {item['episode_id']}", callback_data=f"due:log:{item['episode_id']}")] for item in due_items[:10]]
    rows.append([InlineKeyboardButton("Open menu", callback_data="menu:open")])
    return InlineKeyboardMarkup(rows) if rows else None


def due_prompt_keyboard(episode_id: int, *, allow_writes: bool) -> InlineKeyboardMarkup:
    rows = []
    if allow_writes:
        rows.append([InlineKeyboardButton("Done", callback_data=f"due:log:{episode_id}")])
    rows.append([InlineKeyboardButton("Open menu", callback_data="menu:open")])
    return InlineKeyboardMarkup(rows)


def logged_application_keyboard(application_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Undo", callback_data=f"due:undo:{application_id}")],
            [InlineKeyboardButton("Open menu", callback_data="menu:open")],
        ]
    )


def subjects_keyboard(*, allow_writes: bool) -> InlineKeyboardMarkup | None:
    if not allow_writes:
        return open_menu_keyboard()
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Create subject", callback_data="subject:create")],
            [InlineKeyboardButton("Delete subject", callback_data="subject:delete")],
            [InlineKeyboardButton("Open menu", callback_data="menu:open")],
        ]
    )


def subject_delete_select_keyboard(subjects: list[dict]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(subject["display_name"], callback_data=f"subject:delete_select:{subject['id']}")] for subject in subjects[:10]]
    rows.append([InlineKeyboardButton("Cancel", callback_data="subject:delete_cancel")])
    return InlineKeyboardMarkup(rows)


def subject_delete_confirm_keyboard(subject_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Confirm delete", callback_data=f"subject:delete_confirm:{subject_id}")],
            [InlineKeyboardButton("Cancel", callback_data="subject:delete_cancel")],
        ]
    )


def subject_delete_recovery_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Subjects", callback_data="menu:subjects")],
            [InlineKeyboardButton("Open menu", callback_data="menu:open")],
        ]
    )


def locations_keyboard(locations: list[dict], *, allow_writes: bool) -> InlineKeyboardMarkup | None:
    rows = [[InlineKeyboardButton(location["display_name"], callback_data=f"loc:select:{location['id']}")] for location in locations[:10]]
    if allow_writes:
        rows.append([InlineKeyboardButton("Create location", callback_data="loc:create")])
    return InlineKeyboardMarkup(rows) if rows else None


def location_actions_keyboard(location_id: int, *, allow_writes: bool) -> InlineKeyboardMarkup | None:
    if not allow_writes:
        return None
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Set image / Replace image", callback_data=f"loc:image:{location_id}")],
            [InlineKeyboardButton("Back to locations", callback_data="menu:locations")],
        ]
    )


def location_image_prompt_keyboard(location_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Set image", callback_data=f"loc:image:{location_id}")],
            [InlineKeyboardButton("Skip image", callback_data="menu:locations")],
        ]
    )


def start_subject_keyboard(subjects: list[dict], *, allow_writes: bool) -> InlineKeyboardMarkup | None:
    rows = [[InlineKeyboardButton(subject["display_name"], callback_data=f"epstart:subject:{subject['id']}")] for subject in subjects[:10]]
    if allow_writes:
        rows.append([InlineKeyboardButton("Create new subject", callback_data="epstart:subject_new")])
    return InlineKeyboardMarkup(rows) if rows else None


def start_location_keyboard(locations: list[dict], *, allow_writes: bool) -> InlineKeyboardMarkup | None:
    rows = [[InlineKeyboardButton(location["display_name"], callback_data=f"epstart:loc:{location['id']}")] for location in locations[:10]]
    if allow_writes:
        rows.append([InlineKeyboardButton("Create new location", callback_data="epstart:loc_new")])
    return InlineKeyboardMarkup(rows) if rows else None


def start_duplicate_location_keyboard(location_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Use existing location", callback_data=f"epstart:loc:{location_id}")],
            [InlineKeyboardButton("Choose another location", callback_data="epstart:loc_new")],
            [InlineKeyboardButton("Cancel", callback_data="epstart:cancel")],
        ]
    )


def start_location_conflict_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Choose another location", callback_data="epstart:locations")],
            [InlineKeyboardButton("Create new location", callback_data="epstart:loc_new")],
            [InlineKeyboardButton("Open menu", callback_data="menu:open")],
        ]
    )


def start_image_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Add/Replace location image", callback_data="epstart:image")],
            [InlineKeyboardButton("Skip image", callback_data="epstart:skip_image")],
        ]
    )


def start_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Create episode", callback_data="epstart:confirm")],
            [InlineKeyboardButton("Cancel", callback_data="epstart:cancel")],
        ]
    )


def episode_select_keyboard(prefix: str, episodes: list[dict]) -> InlineKeyboardMarkup | None:
    rows = [
        [
            InlineKeyboardButton(
                episode.get("telegram_label") or f"Episode {episode['id']} · {episode.get('status', 'unknown')}",
                callback_data=f"{prefix}:select:{episode['id']}",
            )
        ]
        for episode in episodes[:10]
    ]
    rows.append([InlineKeyboardButton("Cancel", callback_data=f"{prefix}:cancel")])
    return InlineKeyboardMarkup(rows)


def confirm_episode_action_keyboard(prefix: str, episode_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Confirm", callback_data=f"{prefix}:confirm:{episode_id}")],
            [InlineKeyboardButton("Cancel", callback_data=f"{prefix}:cancel")],
        ]
    )


def adherence_keyboard(*, allow_rebuild: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("Summary 7 days", callback_data="adh:summary:7"),
            InlineKeyboardButton("Summary 30 days", callback_data="adh:summary:30"),
        ],
        [InlineKeyboardButton("Summary 90 days", callback_data="adh:summary:90")],
        [
            InlineKeyboardButton("Calendar 30 days", callback_data="adh:calendar:30"),
            InlineKeyboardButton("Missed 30 days", callback_data="adh:missed:30"),
        ],
    ]
    if allow_rebuild:
        rows.append([InlineKeyboardButton("Rebuild snapshots", callback_data="adh:rebuild")])
    return InlineKeyboardMarkup(rows)


def adherence_rebuild_range_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("7 days", callback_data="adh:rebuild:range:7"), InlineKeyboardButton("30 days", callback_data="adh:rebuild:range:30")],
            [InlineKeyboardButton("90 days", callback_data="adh:rebuild:range:90"), InlineKeyboardButton("Cancel", callback_data="adh:rebuild:cancel")],
        ]
    )


def adherence_rebuild_confirm_keyboard(days: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Confirm rebuild", callback_data=f"adh:rebuild:confirm:{days}")],
            [InlineKeyboardButton("Cancel", callback_data="adh:rebuild:cancel")],
        ]
    )
