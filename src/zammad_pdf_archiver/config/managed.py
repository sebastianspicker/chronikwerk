"""Allowlisted, atomic, non-secret managed configuration revisions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.config.validate import ConfigValidationError, validate_settings

_REVISION_RE = re.compile(r"^[a-f0-9]{64}$")
_MAX_MANAGED_FILE_BYTES = 256 * 1024
_SECRET_PATHS = {
    "admin.access_token",
    "retry_bearer_token",
    "zammad.api_token",
    "zammad.webhook_hmac_secret",
    "signing.pfx_password",
    "signing.timestamp.rfc3161.password",
    "observability.metrics_bearer_token",
    "observability.history_bearer_token",
}


@dataclass(frozen=True)
class ManagedField:
    path: str
    group: str
    kind: str
    choices: tuple[str, ...] = ()
    security_acknowledgement: bool = False


MANAGED_FIELDS: tuple[ManagedField, ...] = (
    ManagedField("workflow.trigger_tag", "workflow", "string"),
    ManagedField("workflow.require_tag", "workflow", "boolean"),
    ManagedField("workflow.acknowledge_on_success", "workflow", "boolean"),
    ManagedField("workflow.delivery_id_ttl_seconds", "workflow", "integer"),
    ManagedField("pdf.locale", "pdf", "choice", ("de-DE", "en-GB")),
    ManagedField("pdf.timezone", "pdf", "string"),
    ManagedField("pdf.max_articles", "pdf", "integer"),
    ManagedField(
        "pdf.article_limit_mode",
        "pdf",
        "choice",
        ("fail", "cap_and_continue"),
    ),
    ManagedField("storage.fsync", "storage", "boolean"),
    ManagedField("storage.filename_pattern", "storage", "string"),
    ManagedField("zammad.timeout_seconds", "zammad", "number"),
    ManagedField("observability.log_level", "observability", "string"),
    ManagedField("observability.healthz_omit_version", "observability", "boolean"),
    ManagedField("admission.max_pending", "admission", "integer"),
    ManagedField("admission.max_running", "admission", "integer"),
    ManagedField("admission.shutdown_timeout_seconds", "admission", "number"),
    ManagedField("signing.pades.reason", "signing", "string"),
    ManagedField("signing.pades.location", "signing", "string"),
    ManagedField(
        "hardening.transport.trust_env",
        "security",
        "boolean",
        security_acknowledgement=True,
    ),
    ManagedField(
        "hardening.transport.allow_insecure_http",
        "security",
        "boolean",
        security_acknowledgement=True,
    ),
    ManagedField(
        "hardening.transport.allow_private_networks",
        "security",
        "boolean",
        security_acknowledgement=True,
    ),
)

_FIELD_BY_PATH = {field.path: field for field in MANAGED_FIELDS}


class ManagedConfigError(ValueError):
    pass


class RevisionConflict(ManagedConfigError):
    pass


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def flatten_mapping(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            flattened.update(flatten_mapping(item, path))
        else:
            flattened[path] = item
    return flattened


def overlay_from_flat(values: dict[str, Any]) -> dict[str, Any]:
    overlay: dict[str, Any] = {}
    for path, value in values.items():
        if path not in _FIELD_BY_PATH:
            if path in _SECRET_PATHS:
                raise ManagedConfigError(f"Secret field is not manageable: {path}")
            raise ManagedConfigError(f"Unknown or external-only field: {path}")
        cursor = overlay
        parts = path.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    return overlay


def validate_overlay_paths(overlay: dict[str, Any]) -> None:
    overlay_from_flat(flatten_mapping(overlay))


def revision_for(overlay: dict[str, Any]) -> str:
    payload = json.dumps(overlay, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def environment_owns(path: str) -> bool:
    return path.upper().replace(".", "__") in os.environ


def get_path(mapping: dict[str, Any], path: str) -> Any:
    current: Any = mapping
    for part in path.split("."):
        current = current[part]
    return current


def config_read_model(settings: Settings, overlay: dict[str, Any]) -> list[dict[str, Any]]:
    effective = settings.model_dump(mode="json")
    managed = flatten_mapping(overlay)
    return [
        {
            **asdict(field),
            "value": get_path(effective, field.path),
            "source": (
                "environment"
                if environment_owns(field.path)
                else "managed"
                if field.path in managed
                else "base_or_default"
            ),
            "editable": not environment_owns(field.path),
        }
        for field in MANAGED_FIELDS
    ]


def secret_presence(settings: Settings) -> dict[str, bool]:
    values = settings.model_dump()
    presence: dict[str, bool] = {}
    for path in sorted(_SECRET_PATHS):
        current: Any = values
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if hasattr(current, "get_secret_value"):
            current = current.get_secret_value()
        presence[path] = bool(current)
    return presence


def validate_candidate(
    settings: Settings, overlay: dict[str, Any]
) -> tuple[Settings, dict[str, Any]]:
    validate_overlay_paths(overlay)
    base = settings.model_dump()
    candidate = Settings.from_mapping(deep_merge(base, overlay))
    validate_settings(candidate)
    normalized = candidate.model_dump(mode="json")
    normalized_flat = {path: get_path(normalized, path) for path in flatten_mapping(overlay)}
    return candidate, overlay_from_flat(normalized_flat)


class ManagedConfigStore:
    """Atomic current overlay and bounded immutable revision files."""

    def __init__(self, state_dir: Path, *, keep_revisions: int = 20) -> None:
        self.state_dir = state_dir
        self.keep_revisions = keep_revisions
        self.overlay_path = state_dir / "managed-config.json"
        self.revisions_dir = state_dir / "revisions"
        self._lock = threading.Lock()
        self._ensure_directory(state_dir)
        self._ensure_directory(self.revisions_dir)

    @staticmethod
    def _ensure_directory(path: Path) -> None:
        if path.is_symlink():
            raise ManagedConfigError(f"Managed configuration path must not be a symlink: {path}")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not path.is_dir():
            raise ManagedConfigError(f"Managed configuration path is not a directory: {path}")

    def load(self) -> dict[str, Any]:
        overlay, _revision = self._read_current()
        return overlay

    def _read_current(self) -> tuple[dict[str, Any], str]:
        if not self.overlay_path.exists():
            return {}, revision_for({})
        if self.overlay_path.is_symlink():
            raise ManagedConfigError("Managed configuration file must not be a symlink")
        if self.overlay_path.stat().st_size > _MAX_MANAGED_FILE_BYTES:
            raise ManagedConfigError("Managed configuration file exceeds 256 KiB")
        data = json.loads(self.overlay_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ManagedConfigError("Managed configuration must be a JSON object")
        if set(data) == {"revision", "overlay"}:
            revision = data.get("revision")
            overlay = data.get("overlay")
            if not isinstance(revision, str) or not _REVISION_RE.fullmatch(revision):
                raise ManagedConfigError("Managed configuration revision is invalid")
            if not isinstance(overlay, dict):
                raise ManagedConfigError("Managed configuration overlay is invalid")
            validate_overlay_paths(overlay)
            return overlay, revision
        validate_overlay_paths(data)
        return data, revision_for(data)

    def current_revision(self) -> str:
        _overlay, revision = self._read_current()
        return revision

    def stage(
        self,
        overlay: dict[str, Any],
        *,
        expected_revision: str,
        request_id: str,
    ) -> dict[str, Any]:
        validate_overlay_paths(overlay)
        with self._lock:
            current_overlay, current_revision = self._read_current()
            if expected_revision != current_revision:
                raise RevisionConflict("Managed configuration revision changed")
            timestamp = datetime.now(UTC).isoformat()
            new_revision = revision_for(
                {
                    "overlay": overlay,
                    "previous_revision": current_revision,
                    "request_id": request_id,
                    "created_at": timestamp,
                }
            )
            current_flat = flatten_mapping(current_overlay)
            new_flat = flatten_mapping(overlay)
            changed_paths = sorted(
                path
                for path in set(current_flat) | set(new_flat)
                if current_flat.get(path) != new_flat.get(path)
            )
            metadata = {
                "revision": new_revision,
                "previous_revision": current_revision,
                "created_at": timestamp,
                "request_id": request_id,
                "changed_paths": changed_paths,
            }
            revision_path = self.revisions_dir / f"{new_revision}.json"
            revision_value = {"metadata": metadata, "overlay": overlay}
            current_value = {"revision": new_revision, "overlay": overlay}
            # Validate every payload before the first durable mutation. Otherwise an
            # overlay that fits the inbound request limit can create a revision or
            # current pointer that our bounded readers subsequently refuse to open.
            self._payload_bytes(revision_value)
            self._payload_bytes(current_value)
            self._atomic_write(
                revision_path,
                revision_value,
            )
            try:
                self._atomic_write(
                    self.overlay_path,
                    current_value,
                )
            except Exception:
                revision_path.unlink(missing_ok=True)
                self._fsync_directory(self.revisions_dir)
                raise
            self._prune_revisions()
            return metadata

    def list_revisions(self) -> list[dict[str, Any]]:
        return [data["metadata"] for _path, data in self._revision_chain()[: self.keep_revisions]]

    def restore(
        self,
        revision: str,
        *,
        expected_revision: str,
        request_id: str,
    ) -> dict[str, Any]:
        data = self._read_revision(revision)
        return self.stage(
            data["overlay"],
            expected_revision=expected_revision,
            request_id=request_id,
        )

    def revision_overlay(self, revision: str) -> dict[str, Any]:
        """Return a validated non-secret overlay for route-level review."""
        return deepcopy(self._read_revision(revision)["overlay"])

    def _read_revision(self, revision: str) -> dict[str, Any]:
        if not _REVISION_RE.fullmatch(revision):
            raise ManagedConfigError("Invalid revision identifier")
        return self._read_revision_file(self.revisions_dir / f"{revision}.json")

    def _read_revision_file(self, path: Path) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file():
            raise ManagedConfigError("Revision not found or unsafe")
        if path.stat().st_size > _MAX_MANAGED_FILE_BYTES:
            raise ManagedConfigError("Revision file exceeds 256 KiB")
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("overlay"), dict)
            or not isinstance(data.get("metadata"), dict)
        ):
            raise ManagedConfigError("Invalid revision file")
        revision = data["metadata"].get("revision")
        previous = data["metadata"].get("previous_revision")
        if (
            not isinstance(revision, str)
            or not _REVISION_RE.fullmatch(revision)
            or not isinstance(previous, str)
            or not _REVISION_RE.fullmatch(previous)
            or path.stem != revision
        ):
            raise ManagedConfigError("Invalid revision metadata")
        validate_overlay_paths(data["overlay"])
        return data

    def _revision_chain(self) -> list[tuple[Path, dict[str, Any]]]:
        chain: list[tuple[Path, dict[str, Any]]] = []
        revision = self.current_revision()
        seen: set[str] = set()
        while revision not in seen:
            seen.add(revision)
            path = self.revisions_dir / f"{revision}.json"
            if not path.is_file() or path.is_symlink():
                break
            data = self._read_revision_file(path)
            chain.append((path, data))
            revision = str(data["metadata"]["previous_revision"])
        return chain

    def _prune_revisions(self) -> None:
        keep = {path for path, _data in self._revision_chain()[: self.keep_revisions]}
        for path in self.revisions_dir.glob("*.json"):
            if path not in keep and not path.is_symlink():
                path.unlink()
        self._fsync_directory(self.revisions_dir)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        directory_fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    @staticmethod
    def _payload_bytes(value: dict[str, Any]) -> bytes:
        payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
        if len(payload) > _MAX_MANAGED_FILE_BYTES:
            raise ManagedConfigError("Managed configuration payload exceeds 256 KiB")
        return payload

    @staticmethod
    def _atomic_write(path: Path, value: dict[str, Any]) -> None:
        if path.is_symlink():
            raise ManagedConfigError(f"Refusing to replace symlink: {path}")
        payload = ManagedConfigStore._payload_bytes(value)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            ManagedConfigStore._fsync_directory(path.parent)
        finally:
            temp_path.unlink(missing_ok=True)


def validation_errors(exc: Exception) -> list[dict[str, str]]:
    if isinstance(exc, ConfigValidationError):
        return [{"path": issue.path, "message": issue.message} for issue in exc.issues]
    return [{"path": "<root>", "message": str(exc)}]
