"""CLI entry point for zammad-archiver-cli: config validation, queue management, and diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import sys
from collections.abc import Callable

import structlog

from zammad_pdf_archiver._version import __version__
from zammad_pdf_archiver.app.jobs.history import _history_enabled, read_history
from zammad_pdf_archiver.app.jobs.redis_queue import drain_dlq, get_queue_stats
from zammad_pdf_archiver.config.load import load_settings
from zammad_pdf_archiver.config.redact import redact_settings_dict
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validate import ConfigValidationError

log = structlog.get_logger(__name__)


def _load_settings_for_cli(args: argparse.Namespace) -> Settings:
    config_path = getattr(args, "config", None)
    if config_path is None:
        return load_settings()
    return load_settings(config_path=config_path)


def _missing_config_path_from_error(error: ConfigValidationError) -> str | None:
    for issue in error.issues:
        if issue.path != "CONFIG_PATH":
            continue
        prefix = "Config file not found:"
        if issue.message.startswith(prefix):
            return issue.message.removeprefix(prefix).strip()
    return None


def _cli_command(
    error_prefix: str,
    *,
    catch: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[[argparse.Namespace], int]], Callable[[argparse.Namespace], int]]:
    """Decorator that wraps CLI commands with consistent error handling.

    Args:
        error_prefix: Human-readable prefix for error messages.
        catch: Exception types to catch (exit code 1).
    """

    def decorator(
        fn: Callable[[argparse.Namespace], int],
    ) -> Callable[[argparse.Namespace], int]:
        @functools.wraps(fn)
        def wrapper(args: argparse.Namespace) -> int:
            try:
                return fn(args)
            except catch as e:
                print(f"\u2717 {error_prefix}: {e}", file=sys.stderr)
                return 1

        return wrapper

    return decorator


def cmd_validate_config(args: argparse.Namespace) -> int:
    """Validate configuration and exit with appropriate code.

    Exit codes:
        0: Configuration is valid
        1: Configuration is invalid
        2: Configuration file not found (when CONFIG_PATH is set)
    """
    try:
        settings = _load_settings_for_cli(args)
        print("✓ Configuration is valid")
        print(f"  - Zammad URL: {settings.zammad.base_url}")
        print(f"  - Storage root: {settings.storage.root}")
        print(f"  - Signing enabled: {settings.signing.enabled}")
        print(f"  - Metrics enabled: {settings.observability.metrics_enabled}")
        return 0
    except ConfigValidationError as e:
        if missing_path := _missing_config_path_from_error(e):
            print(f"✗ Configuration file not found: {missing_path}", file=sys.stderr)
            return 2
        print(f"✗ Configuration is invalid: {e}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as e:
        print(f"✗ Configuration is invalid: {e}", file=sys.stderr)
        return 1


@_cli_command("Failed to load configuration", catch=(ConfigValidationError, ValueError, OSError))
def cmd_dump_config(args: argparse.Namespace) -> int:
    """Dump current configuration as JSON (with secrets redacted)."""
    settings = _load_settings_for_cli(args)
    # Convert to dict and redact
    data = settings.model_dump(mode="json")
    redacted = redact_settings_dict(data)
    print(json.dumps(redacted, indent=2, default=str))
    return 0


@_cli_command(
    "Failed to read queue stats", catch=(RuntimeError, ConnectionError, OSError, ValueError)
)
def cmd_queue_stats(args: argparse.Namespace) -> int:
    """Show queue stats as JSON for operational diagnostics."""
    settings = _load_settings_for_cli(args)
    stats = asyncio.run(get_queue_stats(settings))
    print(json.dumps(stats, indent=2, default=str))
    return 0


@_cli_command("Failed to drain DLQ", catch=(RuntimeError, ConnectionError, OSError, ValueError))
def cmd_queue_drain_dlq(args: argparse.Namespace) -> int:
    """Drain dead-letter queue entries (bounded by --limit)."""
    settings = _load_settings_for_cli(args)
    backend = (settings.workflow.execution_backend or "inprocess").strip().lower()
    if backend != "redis_queue":
        print(
            "\u2717 queue-drain-dlq requires workflow.execution_backend=redis_queue",
            file=sys.stderr,
        )
        return 1

    drain_result = asyncio.run(drain_dlq(settings, limit=args.limit))
    status = "partial" if drain_result["not_deleted"] else "ok"
    print(
        json.dumps(
            {"status": status, "drained": drain_result["deleted"], **drain_result},
            indent=2,
        )
    )
    return 0


@_cli_command(
    "Failed to read queue history", catch=(RuntimeError, ConnectionError, OSError, ValueError)
)
def cmd_queue_history(args: argparse.Namespace) -> int:
    """Show queue history events as JSON."""
    settings = _load_settings_for_cli(args)
    if not _history_enabled(settings):
        payload = {"status": "disabled", "available": False, "count": 0, "items": []}
        print(json.dumps(payload, indent=2))
        return 0

    items = asyncio.run(
        read_history(
            settings,
            limit=args.limit,
            ticket_id=getattr(args, "ticket_id", None),
        )
    )
    payload = {"status": "ok", "available": True, "count": len(items), "items": items}
    print(json.dumps(payload, indent=2, default=str))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zammad-pdf-archiver",
        description="Zammad PDF Archiver CLI utilities",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=None,
        help="Path to YAML config file (overrides CONFIG_PATH)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    _add_config_commands(subparsers)
    _add_queue_commands(subparsers)
    return parser


def _add_config_commands(subparsers: argparse._SubParsersAction) -> None:
    validate_parser = subparsers.add_parser(
        "validate-config",
        help="Validate configuration and exit",
    )
    validate_parser.set_defaults(func=cmd_validate_config)

    dump_parser = subparsers.add_parser(
        "dump-config",
        help="Dump configuration as JSON (secrets redacted)",
    )
    dump_parser.set_defaults(func=cmd_dump_config)


def _add_queue_commands(subparsers: argparse._SubParsersAction) -> None:
    queue_stats_parser = subparsers.add_parser(
        "queue-stats",
        help="Show queue stats (redis_queue backend)",
    )
    queue_stats_parser.set_defaults(func=cmd_queue_stats)

    queue_drain_parser = subparsers.add_parser(
        "queue-drain-dlq",
        help="Drain dead-letter queue entries",
    )
    queue_drain_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of DLQ entries to drain (default: 100, max: 1000)",
    )
    queue_drain_parser.set_defaults(func=cmd_queue_drain_dlq)

    queue_history_parser = subparsers.add_parser(
        "queue-history",
        help="Show processing history from Redis stream",
    )
    queue_history_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of entries to return (default: 100)",
    )
    queue_history_parser.add_argument(
        "--ticket-id",
        type=int,
        default=None,
        help="Optional ticket_id filter",
    )
    queue_history_parser.set_defaults(func=cmd_queue_history)


def main() -> int:
    """Main CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
