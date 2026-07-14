from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic.networks import AnyHttpUrl
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from zammad_pdf_archiver.i18n import normalize_locale


class _BaseSection(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerSettings(_BaseSection):
    # 0.0.0.0 is the standard bind address for containerized services so the
    # process is reachable from outside the container.  A reverse proxy (e.g.
    # nginx, Traefik, cloud load balancer) should handle external access,
    # TLS termination, and IP filtering.
    # Container bind; proxy/firewall owns exposure.
    host: str = "0.0.0.0"  # nosec B104
    port: int = Field(default=8080, ge=1, le=65535)


class ZammadSettings(_BaseSection):
    base_url: AnyHttpUrl
    api_token: SecretStr
    webhook_hmac_secret: SecretStr | None = None
    timeout_seconds: float = Field(default=10.0, gt=0)
    verify_tls: bool = True


class WorkflowSettings(_BaseSection):
    trigger_tag: str = "pdf:sign"
    require_tag: bool = True
    acknowledge_on_success: bool = True
    delivery_id_ttl_seconds: int = Field(default=3600, ge=0)


class FieldsSettings(_BaseSection):
    archive_path: str = "archive_path"
    archive_user_mode: str = "archive_user_mode"
    # Custom field name for archive_user in fixed mode (Bug #1/#6).
    archive_user: str = "archive_user"


class StorageSettings(_BaseSection):
    root: Path
    fsync: bool = True
    filename_pattern: str = "Ticket-{ticket_number}_{timestamp_utc}.pdf"

    @field_validator("root")
    @classmethod
    def _expand_root(cls, value: Path) -> Path:
        return value.expanduser()


class PdfSettings(_BaseSection):
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
    reason: str = "Ticket Archivierung"
    location: str = "Datacenter"


class SigningTimestampRfc3161Settings(_BaseSection):
    tsa_url: AnyHttpUrl | None = None
    timeout_seconds: float = Field(default=10.0, gt=0)
    ca_bundle_path: Path | None = None
    user: str | None = None
    password: SecretStr | None = None


class SigningTimestampSettings(_BaseSection):
    enabled: bool = False
    rfc3161: SigningTimestampRfc3161Settings = Field(
        default_factory=SigningTimestampRfc3161Settings
    )


class SigningSettings(_BaseSection):
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
    enabled: bool = True
    rps: float = Field(default=5.0, ge=0, le=10_000)
    burst: int = Field(default=10, ge=1, le=10_000)
    include_metrics: bool = False
    # When set (e.g. "X-Forwarded-For"), rate limit key is taken from this header (first value).
    # Trust proxy to set it; use with care.
    client_key_header: str | None = None


class BodySizeLimitSettings(_BaseSection):
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
    state_dir: Path = Path("/var/lib/zammad-pdf-archiver/admin")
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
    # When enabled, /ingest requires X-Zammad-Delivery replay TTL > 0.
    require_delivery_id: bool = False


class TransportHardeningSettings(_BaseSection):
    # When true, allow httpx to read HTTP_PROXY/HTTPS_PROXY/NO_PROXY.
    trust_env: bool = False
    allow_insecure_http: bool = False
    allow_private_networks: bool = False


class HardeningSettings(_BaseSection):
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    body_size_limit: BodySizeLimitSettings = Field(default_factory=BodySizeLimitSettings)
    webhook: WebhookHardeningSettings = Field(default_factory=WebhookHardeningSettings)
    transport: TransportHardeningSettings = Field(default_factory=TransportHardeningSettings)


class Settings(BaseSettings):
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
        # Keep this order explicit: process environment, constructor/YAML
        # values, dotenv, file secrets, then Pydantic defaults.
        return (
            env_settings,
            init_settings,
            dotenv_settings,
            file_secret_settings,
        )
