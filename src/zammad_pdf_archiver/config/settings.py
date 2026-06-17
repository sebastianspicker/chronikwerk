from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic.networks import AnyHttpUrl
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from zammad_pdf_archiver.config.env_aliases import get_flat_env_settings_source


class _BaseSection(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServerSettings(_BaseSection):
    # 0.0.0.0 is the standard bind address for containerized services so the
    # process is reachable from outside the container.  A reverse proxy (e.g.
    # nginx, Traefik, cloud load balancer) should handle external access,
    # TLS termination, and IP filtering.
    host: str = "0.0.0.0"
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
    locale: str = "de_DE"
    timezone: str = "Europe/Berlin"
    max_articles: int = Field(default=250, ge=0)
    # fail = fail the ticket; cap_and_continue = truncate and warn.
    article_limit_mode: str = "fail"

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
    # 0 disables the limit.
    max_bytes: int = Field(default=1024 * 1024, ge=0)


class WebhookHardeningSettings(_BaseSection):
    # When enabled, /ingest requires X-Zammad-Delivery replay TTL > 0.
    require_delivery_id: bool = False


class TransportHardeningSettings(_BaseSection):
    # When true, allow httpx to read HTTP_PROXY/HTTPS_PROXY/NO_PROXY.
    trust_env: bool = False
    # Allow outbound upstreams to target loopback / link-local addresses.


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
        return (
            env_settings,
            get_flat_env_settings_source(),
            init_settings,
            dotenv_settings,
            file_secret_settings,
        )
