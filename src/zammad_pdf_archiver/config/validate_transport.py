from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

from zammad_pdf_archiver.config.settings import Settings, TransportHardeningSettings
from zammad_pdf_archiver.config.validation_issues import ConfigValidationIssue


def is_local_upstream_host(host: str) -> bool:
    normalized = host.strip().lower().rstrip(".")
    if normalized in {"localhost", "localhost.localdomain"}:
        return True

    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return False

    return ip.is_loopback or ip.is_link_local or ip.is_unspecified


def validate_primary_transport(
    settings: Settings,
    *,
    transport: TransportHardeningSettings,
    issues: list[ConfigValidationIssue],
) -> None:
    validate_url_security(
        url=str(settings.zammad.base_url),
        path="zammad.base_url",
        transport=transport,
        issues=issues,
        insecure_message=(
            "Plain HTTP upstream is not allowed by default. "
            "Use https:// or set hardening.transport.allow_insecure_http=true."
        ),
    )

    if not settings.zammad.verify_tls and not transport.allow_insecure_tls:
        issues.append(
            ConfigValidationIssue(
                path="zammad.verify_tls",
                message=(
                    "Disabling TLS verification is not allowed by default. "
                    "Set hardening.transport.allow_insecure_tls=true to override (not recommended)."
                ),
            )
        )


def validate_tsa_transport(
    settings: Settings,
    *,
    transport: TransportHardeningSettings,
    issues: list[ConfigValidationIssue],
) -> None:
    # If timestamping is enabled, enforce secure transport for the TSA as well.
    if settings.signing.timestamp.enabled:
        tsa_url = settings.signing.timestamp.rfc3161.tsa_url
        if tsa_url is None:
            issues.append(
                ConfigValidationIssue(
                    path="signing.timestamp.rfc3161.tsa_url",
                    message="signing.timestamp.enabled=true requires rfc3161.tsa_url to be set.",
                )
            )
        if tsa_url is not None:
            validate_url_security(
                url=str(tsa_url),
                path="signing.timestamp.rfc3161.tsa_url",
                transport=transport,
                issues=issues,
                insecure_message=(
                    "Plain HTTP TSA URL is not allowed by default. "
                    "Use https:// or set hardening.transport.allow_insecure_http=true."
                ),
            )
        ca_bundle_path = settings.signing.timestamp.rfc3161.ca_bundle_path
        if ca_bundle_path is not None and not ca_bundle_path.is_file():
            issues.append(
                ConfigValidationIssue(
                    path="signing.timestamp.rfc3161.ca_bundle_path",
                    message="TSA CA bundle path must point to a readable file.",
                )
            )


def validate_url_security(
    *,
    url: str,
    path: str,
    transport: TransportHardeningSettings,
    issues: list[ConfigValidationIssue],
    insecure_message: str,
) -> None:
    if url.lower().startswith("http://") and not transport.allow_insecure_http:
        issues.append(
            ConfigValidationIssue(
                path=path,
                message=insecure_message,
            )
        )

    validate_upstream_host(
        url=url,
        path=path,
        allow_local_upstreams=transport.allow_local_upstreams,
        issues=issues,
    )


def validate_upstream_host(
    *,
    url: str,
    path: str,
    allow_local_upstreams: bool,
    issues: list[ConfigValidationIssue],
) -> None:
    if allow_local_upstreams:
        return

    host = urlsplit(url).hostname
    if host and is_local_upstream_host(host):
        issues.append(
            ConfigValidationIssue(
                path=path,
                message=(
                    "Loopback/link-local upstream hosts are blocked by default. "
                    "Set hardening.transport.allow_local_upstreams=true to override."
                ),
            )
        )
