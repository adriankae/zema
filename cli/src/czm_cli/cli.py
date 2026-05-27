from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .client import CzmClient
from .config import resolve_runtime_config
from .context import CommandContext
from .errors import (
    CzmError,
    EXIT_AMBIGUOUS,
    EXIT_AUTH,
    EXIT_CONFLICT,
    EXIT_INTERNAL,
    EXIT_NOT_FOUND,
    EXIT_TRANSPORT,
    EXIT_USAGE,
)
from .commands.application import register as register_application_commands
from .commands.adherence import register as register_adherence_commands
from .commands.backup import register as register_backup_commands
from .commands.config import register as register_config_commands
from .commands.setup import register as register_setup_commands
from .commands.due import register as register_due_commands
from .commands.events import register as register_events_commands
from .commands.episode import register as register_episode_commands
from .commands.location import register as register_location_commands
from .commands.subject import register as register_subject_commands
from .commands.telegram import register as register_telegram_commands


def build_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    parent.add_argument("--base-url", default=argparse.SUPPRESS)
    parent.add_argument("--api-key", default=argparse.SUPPRESS)
    parent.add_argument("--timezone", default=argparse.SUPPRESS)
    parent.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    parent.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS)
    parent.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(prog="zema", description="zema CLI client", parents=[parent])
    subparsers = parser.add_subparsers(dest="command", required=True)
    register_setup_commands(subparsers, parent)
    register_config_commands(subparsers, parent)
    register_telegram_commands(subparsers, parent)
    register_subject_commands(subparsers, parent)
    register_location_commands(subparsers, parent)
    register_episode_commands(subparsers, parent)
    register_application_commands(subparsers, parent)
    register_due_commands(subparsers, parent)
    register_adherence_commands(subparsers, parent)
    register_events_commands(subparsers, parent)
    register_backup_commands(subparsers, parent)
    return parser


def _error_code(error: CzmError) -> str:
    mapping = {
        EXIT_USAGE: "invalid_request",
        EXIT_NOT_FOUND: "not_found",
        EXIT_AMBIGUOUS: "ambiguous_reference",
        EXIT_AUTH: "unauthorized",
        EXIT_CONFLICT: "conflict",
        EXIT_TRANSPORT: "transport_error",
        EXIT_INTERNAL: "internal_error",
    }
    return mapping.get(error.exit_code, "internal_error")


def _print_error(error: CzmError, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"error": {"code": _error_code(error), "message": error.message}}, ensure_ascii=False))
    else:
        print(error.message, file=sys.stderr)


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if getattr(args, "command", None) in {"setup", "config", "telegram"}:
            return args.handler(None, args)
        config = resolve_runtime_config(
            base_url=getattr(args, "base_url", None),
            api_key=getattr(args, "api_key", None),
            timezone=getattr(args, "timezone", None),
            config_path=getattr(args, "config", None),
        )
        client = CzmClient(config.normalized_base_url(), config.api_key)
        ctx = CommandContext(
            config=config,
            client=client,
            json_output=bool(getattr(args, "json", False)),
            quiet=bool(getattr(args, "quiet", False)),
            no_color=bool(getattr(args, "no_color", False)),
        )
        try:
            exit_code = args.handler(ctx, args)
        finally:
            client.close()
        return exit_code
    except CzmError as exc:
        _print_error(exc, json_output=bool(getattr(args, "json", False)))
        return exc.exit_code
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - defensive guard
        error = CzmError(f"unexpected failure: {exc}", exit_code=EXIT_INTERNAL)
        _print_error(error, json_output=bool(getattr(args, "json", False)))
        return error.exit_code


def main(argv: list[str] | None = None) -> int:
    return run(argv)
