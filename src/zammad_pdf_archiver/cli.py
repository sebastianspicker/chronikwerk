"""CLI entry point for config validation and diagnostics."""
from __future__ import annotations

import argparse
import json
import sys

from zammad_pdf_archiver.config.load import load_settings
from zammad_pdf_archiver.config.redact import redact_settings_dict
from zammad_pdf_archiver.config.validate import ConfigValidationError


def _missing_config_path_from_error(error: ConfigValidationError) -> str | None:
    for issue in error.issues:
        if issue.path != "CONFIG_PATH":
            continue
        prefix = "Config file not found:"
        if issue.message.startswith(prefix):
            return issue.message.removeprefix(prefix).strip()
    return None


def cmd_validate_config(args: argparse.Namespace) -> int:
    """Validate current configuration."""
    try:
        load_settings(config_path=args.config)
        print("✓ Configuration valid")
        return 0
    except ConfigValidationError as exc:
        missing_path = _missing_config_path_from_error(exc)
        if missing_path:
            print(f"✗ Config not found: {missing_path}", file=sys.stderr)
            return 1
        print(f"✗ Configuration invalid: {exc}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(f"✗ Configuration invalid: {exc}", file=sys.stderr)
        return 1


def cmd_dump_config(_args: argparse.Namespace) -> int:
    """Dump current configuration JSON with secrets redacted."""
    try:
        settings = load_settings()
        data = settings.model_dump(mode="json")
        redacted = redact_settings_dict(data)
        print(json.dumps(redacted, indent=2, default=str))
        return 0
    except (ConfigValidationError, ValueError, OSError) as exc:
        print(f"✗ Failed to load configuration: {exc}", file=sys.stderr)
        return 1


def _add_basic_commands(subparsers: argparse._SubParsersAction) -> None:
    validate_parser = subparsers.add_parser("validate-config", help="Validate configuration")
    validate_parser.add_argument("--config", default=None, help="Path to YAML config file")
    validate_parser.set_defaults(func=cmd_validate_config)

    dump_parser = subparsers.add_parser("dump-config", help="Dump redacted config JSON")
    dump_parser.set_defaults(func=cmd_dump_config)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zammad-pdf-archiver",
        description="Zammad PDF Archiver CLI utilities",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    _add_basic_commands(subparsers)
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
