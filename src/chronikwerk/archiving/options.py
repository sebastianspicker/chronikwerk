"""Immutable runtime values for the archive use case."""

from __future__ import annotations

from dataclasses import dataclass

from chronikwerk.documents.options import DocumentOptions
from chronikwerk.storage.options import ArchiveStorageOptions


@dataclass(frozen=True, slots=True)
class ArchiveWorkflowOptions:
    """Configure workflow tags and the ticket fields that shape archive paths."""

    trigger_tag: str
    require_trigger_tag: bool
    acknowledge_on_success: bool
    archive_path_field_name: str
    archive_user_mode_field_name: str
    archive_user_field_name: str


@dataclass(frozen=True, slots=True)
class ArchiveRuntimeOptions:
    """All narrow runtime dependencies required by the archive use case."""

    workflow: ArchiveWorkflowOptions
    documents: DocumentOptions
    storage: ArchiveStorageOptions
