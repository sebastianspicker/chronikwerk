"""Define validated, typed configuration sections and their defaults."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from ipaddress import ip_address
from math import isfinite
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic.networks import AnyHttpUrl
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from chronikwerk.i18n import normalize_locale

ZAMMAD_CONNECTION_CONTRACT_VERSION = 2
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class _BaseSection(BaseModel):
    model_config = ConfigDict(extra="forbid")


def canonicalize_zammad_origin(value: str, *, allow_insecure_http: bool = False) -> str:
    """Return the credential-free origin used for Zammad API requests."""
    parsed, port = _parse_zammad_origin(value)
    _validate_zammad_origin_parts(parsed, allow_insecure_http=allow_insecure_http)
    host = _canonicalize_zammad_host(parsed.hostname)
    if port == 0:
        raise ValueError("Zammad origin port must be between 1 and 65535")
    rendered_host = f"[{host}]" if ":" in host else host
    rendered_port = f":{port}" if port is not None else ""
    return f"{parsed.scheme.lower()}://{rendered_host}{rendered_port}"


def _parse_zammad_origin(value: str) -> tuple[Any, int | None]:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Zammad origin must be a valid HTTPS origin") from exc
    return parsed, port


def _validate_zammad_origin_parts(parsed: Any, *, allow_insecure_http: bool) -> None:
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Zammad origin must not include credentials")
    if (
        parsed.scheme.lower() not in ({"https", "http"} if allow_insecure_http else {"https"})
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Zammad origin must be an HTTPS scheme, host, and optional port only")


def _canonicalize_zammad_host(value: str) -> str:
    host = value.rstrip(".").lower()
    if not host:
        raise ValueError("Zammad origin must include a host")
    try:
        return ip_address(host).compressed
    except ValueError:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Zammad origin host is invalid") from exc
        labels = host.split(".")
        if (
            len(host) > 253
            or any(_HOST_LABEL.fullmatch(label) is None for label in labels)
            or (len(labels) == 4 and all(label.isdigit() for label in labels))
        ):
            raise ValueError("Zammad origin host is invalid") from None
        return host


@dataclass(frozen=True, slots=True)
class ZammadConnection:
    """Immutable runtime boundary for one authenticated Zammad API connection."""

    origin: str
    api_token: SecretStr
    timeout_seconds: float = 10.0
    allow_private_origin: bool = False
    trust_environment: bool = False
    allow_insecure_http: bool = False

    def __post_init__(self) -> None:
        _validate_zammad_boolean("allow_insecure_http", self.allow_insecure_http)
        object.__setattr__(
            self,
            "origin",
            canonicalize_zammad_origin(
                self.origin,
                allow_insecure_http=self.allow_insecure_http,
            ),
        )
        _validate_zammad_api_token(self.api_token)
        object.__setattr__(self, "timeout_seconds", _validate_zammad_timeout(self.timeout_seconds))
        _validate_zammad_boolean("allow_private_origin", self.allow_private_origin)
        _validate_zammad_boolean("trust_environment", self.trust_environment)

    @property
    def api_root(self) -> str:
        """Return the fixed Zammad REST API root for this origin."""
        return f"{self.origin}/api/v1"


def _validate_zammad_api_token(value: SecretStr) -> None:
    if not isinstance(value, SecretStr):
        raise TypeError("Zammad connection api_token must be a SecretStr")
    token = value.get_secret_value()
    if not token or any(character.isspace() for character in token):
        raise ValueError("Zammad connection api_token must be non-empty and contain no whitespace")


def _validate_zammad_timeout(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value <= 0
    ):
        raise ValueError("Zammad connection timeout_seconds must be a finite number greater than 0")
    return float(value)


def _validate_zammad_boolean(name: str, value: bool) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"Zammad connection {name} must be a boolean")


class ServerSettings(_BaseSection):
    """Configure the ASGI bind address and port."""

    # 0.0.0.0 is the standard bind address for containerized services so the
    # process is reachable from outside the container.  A reverse proxy (e.g.
    # nginx, Traefik, cloud load balancer) should handle external access,
    # TLS termination, and IP filtering.
    # Container bind; proxy/firewall owns exposure.
    host: str = "0.0.0.0"  # nosec B104
    port: int = Field(default=8080, ge=1, le=65535)


class ZammadSettings(_BaseSection):
    """Configure authenticated outbound access to the Zammad instance."""

    base_url: AnyHttpUrl
    api_token: SecretStr
    webhook_hmac_secret: SecretStr | None = None
    timeout_seconds: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    verify_tls: bool = True


class WorkflowSettings(_BaseSection):
    """Configure which ticket state changes trigger archival."""

    trigger_tag: str = "pdf:sign"
    require_tag: bool = True
    acknowledge_on_success: bool = True
    delivery_id_ttl_seconds: int = Field(default=3600, ge=0)


class FieldsSettings(_BaseSection):
    """Configure Zammad custom fields that influence archive ownership."""

    archive_path: str = "archive_path"
    archive_user_mode: str = "archive_user_mode"
    # Custom field name for archive_user in fixed mode (Bug #1/#6).
    archive_user: str = "archive_user"


class StorageSettings(_BaseSection):
    """Configure durable filesystem storage for produced PDFs."""

    root: Path
    fsync: bool = True
    filename_pattern: str = "Ticket-{ticket_number}_{timestamp_utc}.pdf"

    @field_validator("root")
    @classmethod
    def _expand_root(cls, value: Path) -> Path:
        return value.expanduser()


class PdfSettings(_BaseSection):
    """Configure localization and article limits for PDF rendering."""

    locale: str = "de-DE"
    timezone: str = "Europe/Berlin"
    max_articles: int = Field(default=250, ge=0)
    # fail = fail the ticket; cap_and_continue = truncate and warn.
    article_limit_mode: str = "fail"

    @field_validator("locale")
    @classmethod
    def _normalize_locale(cls, value: str) -> str:
        return normalize_locale(value)

    @field_validator("article_limit_mode")
    @classmethod
    def _validate_article_limit_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized in {"fail", "cap_and_continue"}:
            return normalized
        raise ValueError("pdf.article_limit_mode must be 'fail' or 'cap_and_continue'")


class SigningPadesSettings(_BaseSection):
    """Configure visible metadata embedded in PAdES signatures."""

    reason: str = "Ticket Archivierung"
    location: str = "Datacenter"


class SigningTimestampRfc3161Settings(_BaseSection):
    """Configure the optional RFC 3161 timestamp authority."""

    tsa_url: AnyHttpUrl | None = None
    timeout_seconds: float = Field(default=10.0, gt=0)
    ca_bundle_path: Path | None = None
    user: str | None = None
    password: SecretStr | None = None


class SigningTimestampSettings(_BaseSection):
    """Configure optional timestamping for signed documents."""

    enabled: bool = False
    rfc3161: SigningTimestampRfc3161Settings = Field(
        default_factory=SigningTimestampRfc3161Settings
    )


class SigningSettings(_BaseSection):
    """Configure optional PAdES signing and its required credentials."""

    enabled: bool = False
    # PKCS#12/PFX bundle with signer cert + private key.
    pfx_path: Path | None = None
    pfx_password: SecretStr | None = None
    pades: SigningPadesSettings = Field(default_factory=SigningPadesSettings)
    timestamp: SigningTimestampSettings = Field(default_factory=SigningTimestampSettings)

    @model_validator(mode="after")
    def _require_material_if_enabled(self) -> SigningSettings:
        if self.enabled and self.pfx_path is None:
            raise ValueError(
                "Signing is enabled but signing.pfx_path is missing. "
                "The current implementation requires a PKCS#12/PFX bundle."
            )

        if self.timestamp.enabled and not self.enabled:
            raise ValueError("Timestamping requires signing.enabled")

        if self.timestamp.enabled and self.timestamp.rfc3161.tsa_url is None:
            raise ValueError(
                "Timestamping is enabled but signing.timestamp.rfc3161.tsa_url is missing"
            )

        return self


class ObservabilitySettings(_BaseSection):
    """Configure logging, metrics, and optional operator diagnostics."""

    log_level: str = "INFO"
    log_format: str | None = None  # json|human
    metrics_enabled: bool = False
    # When set, GET /metrics requires Authorization: Bearer <this token> (constant-time compare).
    metrics_bearer_token: SecretStr | None = None
    history_enabled: bool = False
    history_bearer_token: SecretStr | None = None
    # When true, GET /healthz omits version and service name (reduces fingerprinting).
    healthz_omit_version: bool = False

    @field_validator("log_format")
    @classmethod
    def _validate_log_format(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized in {"json", "human"}:
            return normalized
        raise ValueError("observability.log_format must be 'json' or 'human'")


class RateLimitSettings(_BaseSection):
    """Configure request-rate limits and trusted client identity headers."""

    enabled: bool = True
    rps: float = Field(default=5.0, ge=0, le=10_000)
    burst: int = Field(default=10, ge=1, le=10_000)
    include_metrics: bool = False
    # When set (e.g. "X-Forwarded-For"), rate limit key is taken from this header (first value).
    # Trust proxy to set it; use with care.
    client_key_header: str | None = None


class BodySizeLimitSettings(_BaseSection):
    """Configure the maximum accepted HTTP request body size."""

    # 0 selects the middleware's non-disableable absolute safety cap.
    max_bytes: int = Field(default=1024 * 1024, ge=0)
    # Whole-body deadline, including slow/chunked uploads.
    timeout_seconds: float = Field(default=10.0, gt=0, le=300)


class AdmissionSettings(_BaseSection):
    """Bounds for process-local background ticket work."""

    max_pending: int = Field(default=100, ge=0, le=10_000)
    max_running: int = Field(default=4, ge=1, le=1_000)
    shutdown_timeout_seconds: float = Field(default=5.0, gt=0, le=300)


class AdminSettings(_BaseSection):
    """Feature-flagged, single-user administration application."""

    enabled: bool = False
    access_token: SecretStr | None = None
    state_dir: Path = Path("/var/lib/chronikwerk/admin")
    session_idle_seconds: int = Field(default=1800, ge=60, le=86_400)
    session_absolute_seconds: int = Field(default=28_800, ge=300, le=604_800)
    cookie_secure: bool = True
    default_locale: str = "de-DE"

    @field_validator("state_dir")
    @classmethod
    def _expand_state_dir(cls, value: Path) -> Path:
        return value.expanduser()

    @field_validator("default_locale")
    @classmethod
    def _normalize_default_locale(cls, value: str) -> str:
        return normalize_locale(value)

    @model_validator(mode="after")
    def _validate_session_lifetimes(self) -> AdminSettings:
        if self.session_idle_seconds > self.session_absolute_seconds:
            raise ValueError("admin.session_idle_seconds must not exceed session_absolute_seconds")
        return self


class WebhookHardeningSettings(_BaseSection):
    """Configure mandatory webhook authentication controls."""

    # When enabled, /ingest requires X-Zammad-Delivery replay TTL > 0.
    require_delivery_id: bool = False


class TransportHardeningSettings(_BaseSection):
    """Configure outbound HTTPS and proxy-trust restrictions."""

    # When true, allow httpx to read HTTP_PROXY/HTTPS_PROXY/NO_PROXY.
    trust_env: bool = False
    allow_insecure_http: bool = False
    allow_private_networks: bool = False


class HardeningSettings(_BaseSection):
    """Group runtime hardening controls applied during startup."""

    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    body_size_limit: BodySizeLimitSettings = Field(default_factory=BodySizeLimitSettings)
    webhook: WebhookHardeningSettings = Field(default_factory=WebhookHardeningSettings)
    transport: TransportHardeningSettings = Field(default_factory=TransportHardeningSettings)


class Settings(BaseSettings):
    """Aggregate all validated service configuration sections."""

    model_config = SettingsConfigDict(
        env_prefix="",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    server: ServerSettings = Field(default_factory=ServerSettings)
    zammad: ZammadSettings
    workflow: WorkflowSettings = Field(default_factory=WorkflowSettings)
    fields: FieldsSettings = Field(default_factory=FieldsSettings)
    storage: StorageSettings
    pdf: PdfSettings = Field(default_factory=PdfSettings)
    signing: SigningSettings = Field(default_factory=SigningSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    hardening: HardeningSettings = Field(default_factory=HardeningSettings)
    admission: AdmissionSettings = Field(default_factory=AdmissionSettings)
    admin: AdminSettings = Field(default_factory=AdminSettings)
    retry_bearer_token: SecretStr | None = None

    @property
    def zammad_connection(self) -> ZammadConnection:
        """Build the fixed-safe runtime connection from legacy configuration fields."""
        return ZammadConnection(
            origin=str(self.zammad.base_url),
            api_token=self.zammad.api_token,
            timeout_seconds=self.zammad.timeout_seconds,
            allow_private_origin=self.hardening.transport.allow_private_networks,
            trust_environment=self.hardening.transport.trust_env,
            allow_insecure_http=self.hardening.transport.allow_insecure_http,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Settings:
        """
        Construct Settings from a mapping without reading environment variables.

        Useful in tests where we want to pass nested dicts and keep mypy happy.
        """

        class _InitOnlySettings(Settings):
            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls: type[BaseSettings],
                init_settings: PydanticBaseSettingsSource,
                env_settings: PydanticBaseSettingsSource,
                dotenv_settings: PydanticBaseSettingsSource,
                file_secret_settings: PydanticBaseSettingsSource,
            ) -> tuple[PydanticBaseSettingsSource, ...]:
                """Use only the supplied mapping so ambient settings cannot affect callers."""
                return (init_settings,)

        return _InitOnlySettings(**dict(data))

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource | Any, ...]:
        """Order settings sources so explicit environment values override managed configuration."""
        # Keep this order explicit: process environment, constructor/YAML
        # values, dotenv, file secrets, then Pydantic defaults.
        return (
            env_settings,
            init_settings,
            dotenv_settings,
            file_secret_settings,
        )
