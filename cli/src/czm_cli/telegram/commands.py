from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

from czm_cli.client import CzmClient
from czm_cli.config import AppConfig
from czm_cli.errors import CzmError, EXIT_TRANSPORT, EXIT_USAGE
from czm_cli.telegram import formatting
from czm_cli.telegram.parser import ParsedTelegramCommand, parse_telegram_command, require_int, require_no_options, require_options
from czm_cli.telegram.security import ensure_rebuild_allowed, ensure_writes_allowed
from czm_cli.time_utils import local_today, parse_local_date


@dataclass(slots=True)
class TelegramCommandContext:
    config: AppConfig
    client: CzmClient


async def handle_text_command(ctx: TelegramCommandContext, text: str) -> str:
    parsed = parse_telegram_command(text)
    handlers: dict[str, Callable[[TelegramCommandContext, ParsedTelegramCommand], str]] = {
        "start": _menu,
        "menu": _menu,
        "help": _help,
        "status": _status,
        "subjects": _subjects,
        "subject_create": _subject_create,
        "locations": _locations,
        "location_create": _location_create,
        "episodes": _episodes,
        "episode": _episode,
        "episode_create": _episode_create,
        "due": _due,
        "log": _log,
        "events": _events,
        "timeline": _timeline,
        "adherence": _adherence,
        "adherence_calendar": _adherence_calendar,
        "adherence_missed": _adherence_missed,
        "adherence_rebuild": _adherence_rebuild,
    }
    return handlers[parsed.command](ctx, parsed)


def _menu(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    require_no_options(parsed)
    return formatting.menu_text()


def _help(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    require_no_options(parsed)
    return formatting.help_text()


def _status(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    require_no_options(parsed)
    ctx.client.get("/health")
    return "Zema backend is reachable."


def _subjects(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    require_no_options(parsed)
    return formatting.format_subjects(ctx.client.get("/subjects"))


def _subject_create(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    ensure_writes_allowed(ctx.config.telegram)
    require_no_options(parsed)
    name = " ".join(parsed.positionals).strip()
    if not name:
        raise CzmError("Usage: /subject_create Child A", exit_code=EXIT_USAGE)
    return formatting.format_subject_created(ctx.client.post("/subjects", json={"display_name": name}))


def _locations(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    require_no_options(parsed)
    return formatting.format_locations(ctx.client.get("/locations"))


def _location_create(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    ensure_writes_allowed(ctx.config.telegram)
    require_no_options(parsed)
    if len(parsed.positionals) < 2:
        raise CzmError("Usage: /location_create left_elbow Left elbow", exit_code=EXIT_USAGE)
    code = parsed.positionals[0]
    display_name = " ".join(parsed.positionals[1:])
    return formatting.format_location_created(ctx.client.post("/locations", json={"code": code, "display_name": display_name}))


def _episodes(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    require_no_options(parsed)
    return formatting.format_episodes(ctx.client.get("/episodes"))


def _episode(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    require_no_options(parsed)
    if len(parsed.positionals) != 1:
        raise CzmError("Usage: /episode 12", exit_code=EXIT_USAGE)
    return formatting.format_episode(ctx.client.get(f"/episodes/{require_int(parsed.positionals[0], 'episode')}"))


def _episode_create(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    ensure_writes_allowed(ctx.config.telegram)
    require_options(parsed, {"subject", "location"})
    if parsed.positionals or "subject" not in parsed.options or "location" not in parsed.options:
        raise CzmError('Usage: /episode_create subject:"Child A" location:left_elbow', exit_code=EXIT_USAGE)
    subject_id = _resolve_subject_id(ctx, parsed.options["subject"])
    location_id = _resolve_location_id(ctx, parsed.options["location"])
    return formatting.format_episode(
        ctx.client.post("/episodes", json={"subject_id": subject_id, "location_id": location_id, "protocol_version": "v1"})
    )


def _due(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    require_no_options(parsed)
    payload = ctx.client.get("/episodes/due")
    if not isinstance(payload, dict) or not isinstance(payload.get("due"), list):
        raise CzmError("Zema returned an unreadable due response.", exit_code=EXIT_TRANSPORT)
    return formatting.format_due(payload)


def _log(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    ensure_writes_allowed(ctx.config.telegram)
    require_options(parsed, {"episode"})
    if parsed.positionals or "episode" not in parsed.options:
        raise CzmError("Usage: /log episode:12", exit_code=EXIT_USAGE)
    episode_id = require_int(parsed.options["episode"], "episode")
    return formatting.format_application_logged(ctx.client.post("/applications", json={"episode_id": episode_id}))


def _events(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    require_options(parsed, {"episode"})
    episode_id = _episode_option(parsed)
    return formatting.format_events(ctx.client.get(f"/episodes/{episode_id}/events"))


def _timeline(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    require_options(parsed, {"episode"})
    episode_id = _episode_option(parsed)
    return formatting.format_events(ctx.client.get(f"/episodes/{episode_id}/timeline"), key="timeline")


def _adherence(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    require_no_options(parsed)
    if len(parsed.positionals) != 1:
        raise CzmError("Usage: /adherence 30", exit_code=EXIT_USAGE)
    from_date, to_date = _last_days(ctx, require_int(parsed.positionals[0], "days"))
    return formatting.format_adherence_summary(ctx.client.get("/adherence/summary", params={"from": from_date, "to": to_date}))


def _adherence_calendar(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    require_options(parsed, {"episode", "days", "from", "to"})
    params = _adherence_params(ctx, parsed)
    return formatting.format_adherence_days(ctx.client.get("/adherence/calendar", params=params), title="Adherence calendar")


def _adherence_missed(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    require_options(parsed, {"episode", "days", "from", "to"})
    params = _adherence_params(ctx, parsed)
    return formatting.format_adherence_days(ctx.client.get("/adherence/missed", params=params), title="Missed adherence days")


def _adherence_rebuild(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> str:
    ensure_rebuild_allowed(ctx.config.telegram)
    require_options(parsed, {"episode", "from", "to"})
    if parsed.positionals or not {"episode", "from", "to"} <= set(parsed.options):
        raise CzmError("Usage: /adherence_rebuild episode:12 from:2026-04-01 to:2026-04-30", exit_code=EXIT_USAGE)
    payload = {
        "episode_id": require_int(parsed.options["episode"], "episode"),
        "from": parse_local_date(parsed.options["from"]).isoformat(),
        "to": parse_local_date(parsed.options["to"]).isoformat(),
        "active_only": True,
        "source": "rebuild",
    }
    result = ctx.client.post("/adherence/rebuild", json=payload)
    return f"Adherence rebuild complete. Episodes processed: {result['episodes_processed']}. Rows persisted: {result['rows_persisted']}."


def _episode_option(parsed: ParsedTelegramCommand) -> int:
    if parsed.positionals or "episode" not in parsed.options:
        raise CzmError("Usage: /events episode:12", exit_code=EXIT_USAGE)
    return require_int(parsed.options["episode"], "episode")


def _last_days(ctx: TelegramCommandContext, days: int) -> tuple[str, str]:
    if days < 1:
        raise CzmError("days must be greater than zero", exit_code=EXIT_USAGE)
    today = local_today(ctx.config.timezone)
    return (today - timedelta(days=days - 1)).isoformat(), today.isoformat()


def _adherence_params(ctx: TelegramCommandContext, parsed: ParsedTelegramCommand) -> dict[str, object]:
    if parsed.positionals:
        raise CzmError("unsupported arguments; send /help", exit_code=EXIT_USAGE)
    params: dict[str, object] = {}
    if "episode" in parsed.options:
        params["episode_id"] = require_int(parsed.options["episode"], "episode")
    if "days" in parsed.options:
        from_date, to_date = _last_days(ctx, require_int(parsed.options["days"], "days"))
    elif "from" in parsed.options and "to" in parsed.options:
        from_date = parse_local_date(parsed.options["from"]).isoformat()
        to_date = parse_local_date(parsed.options["to"]).isoformat()
    else:
        raise CzmError("provide days:<n> or from:<date> to:<date>", exit_code=EXIT_USAGE)
    params["from"] = from_date
    params["to"] = to_date
    return params


def _resolve_subject_id(ctx: TelegramCommandContext, reference: str) -> int:
    subjects = ctx.client.get("/subjects").get("subjects", [])
    return _resolve_reference(reference, [(item["id"], [item["display_name"]]) for item in subjects], "subject")


def _resolve_location_id(ctx: TelegramCommandContext, reference: str) -> int:
    locations = ctx.client.get("/locations").get("locations", [])
    return _resolve_reference(reference, [(item["id"], [item["code"], item["display_name"]]) for item in locations], "location")


def _resolve_reference(reference: str, candidates: list[tuple[int, list[str]]], label: str) -> int:
    try:
        numeric = int(reference)
    except ValueError:
        numeric = None
    if numeric is not None and any(identifier == numeric for identifier, _ in candidates):
        return numeric
    lowered = reference.lower()
    matches = [identifier for identifier, names in candidates if any(name.lower() == lowered for name in names)]
    if len(matches) == 1:
        return matches[0]
    matches = [identifier for identifier, names in candidates if any(lowered in name.lower() for name in names)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise CzmError(f"{label} not found: {reference}", exit_code=EXIT_USAGE)
    raise CzmError(f"{label} reference is ambiguous: {reference}", exit_code=EXIT_USAGE)
