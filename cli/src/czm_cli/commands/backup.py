from __future__ import annotations

import argparse
from pathlib import Path

from ..errors import CzmError, EXIT_USAGE
from ._common import emit


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], parent: argparse.ArgumentParser) -> None:
    parser = subparsers.add_parser("backup", parents=[parent], help="Import and export account backups")
    backup_subparsers = parser.add_subparsers(dest="backup_command", required=True)

    export = backup_subparsers.add_parser("export", parents=[parent], help="Download a JSON backup")
    export.add_argument("--output", required=True)
    export.set_defaults(handler=handle_export)

    import_parser = backup_subparsers.add_parser("import", parents=[parent], help="Replace tracking data from a JSON backup")
    import_parser.add_argument("path")
    import_parser.add_argument("--yes", action="store_true", help="Confirm replacement of current tracking data")
    import_parser.set_defaults(handler=handle_import)


def handle_export(ctx, args) -> int:
    if ctx.json_output:
        raise CzmError("backup export writes bytes to --output; JSON output is not supported", exit_code=EXIT_USAGE)
    content, _ = ctx.client.download_file("/export")
    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(content)
    if not ctx.quiet:
        print(f"Wrote backup to {output_path}")
    return 0


def handle_import(ctx, args) -> int:
    if not args.yes:
        raise CzmError("backup import replaces current tracking data; rerun with --yes to confirm", exit_code=EXIT_USAGE)
    input_path = Path(args.path).expanduser()
    if not input_path.exists() or not input_path.is_file():
        raise CzmError(f"backup file not found: {args.path}", exit_code=EXIT_USAGE)
    payload = ctx.client.upload_file("/import", field_name="file", file_path=input_path, content_type="application/json")
    emit(ctx, payload, _format_import_result)
    return 0


def _format_import_result(payload) -> str:
    imported = payload.get("imported", {}) if isinstance(payload, dict) else {}
    return (
        "Imported backup: "
        f"{imported.get('subjects', 0)} subjects, "
        f"{imported.get('locations', 0)} locations, "
        f"{imported.get('episodes', 0)} episodes, "
        f"{imported.get('applications', 0)} applications."
    )
