from __future__ import annotations


class ArchiveUserInputError(ValueError):
    """User-fixable archive configuration or ticket-field error."""

    def __init__(self, message: str, *, code: str, hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


def missing_archive_path_error() -> ArchiveUserInputError:
    return ArchiveUserInputError(
        "custom_fields.archive_path is missing",
        code="missing_archive_path",
        hint="Set custom_fields.archive_path on the ticket.",
    )


def path_not_allowed_error() -> ArchiveUserInputError:
    return ArchiveUserInputError(
        "archive_path is not allowed by allow_prefixes policy",
        code="path_not_allowed",
        hint="Check allow_prefixes; archive_path must match a prefix.",
    )


def invalid_filename_error(message: str) -> ArchiveUserInputError:
    return ArchiveUserInputError(
        message,
        code="invalid_filename",
        hint="Check filename_pattern and path policy (no ., .., separators).",
    )
