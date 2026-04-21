from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
from asn1crypto import tsp  # type: ignore[import-untyped]
from pyhanko.sign.timestamps.api import TimeStamper
from pyhanko.sign.timestamps.common_utils import set_tsp_headers

from zammad_pdf_archiver.adapters.http_util import timeouts_for
from zammad_pdf_archiver.config.settings import SigningSettings
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError


@dataclass(frozen=True)
class _TsaConfig:
    url: str
    timeout_seconds: float
    ca_bundle_path: Path | None
    auth: tuple[str, str] | None
    trust_env: bool


def _load_tsa_config(signing: SigningSettings, *, trust_env: bool = False) -> _TsaConfig:
    rfc3161 = signing.timestamp.rfc3161

    tsa_url = rfc3161.tsa_url
    if tsa_url is None:
        raise PermanentError("Timestamping is enabled but TSA URL is missing")

    timeout_seconds = float(rfc3161.timeout_seconds)
    ca_bundle_path = rfc3161.ca_bundle_path

    user = rfc3161.user
    password: str | None = (
        rfc3161.password.get_secret_value() if rfc3161.password is not None else None
    )

    auth: tuple[str, str] | None
    if user or password:
        if not user or not password:
            raise PermanentError("TSA basic auth requires both user and password in settings")
        auth = (user, password)
    else:
        auth = None

    return _TsaConfig(
        url=str(tsa_url),
        timeout_seconds=timeout_seconds,
        ca_bundle_path=ca_bundle_path,
        auth=auth,
        trust_env=trust_env,
    )


class _HttpxRFC3161TimeStamper(TimeStamper):
    def __init__(self, config: _TsaConfig):
        super().__init__()
        self._config = config

    async def async_request_tsa_response(self, req: tsp.TimeStampReq) -> tsp.TimeStampResp:
        headers = set_tsp_headers({})
        verify: bool | str = True
        if self._config.ca_bundle_path is not None:
            verify = str(self._config.ca_bundle_path)

        try:
            auth: tuple[str | bytes, str | bytes] | None = self._config.auth

            async with httpx.AsyncClient(
                timeout=timeouts_for(self._config.timeout_seconds),
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
                verify=verify,
                trust_env=self._config.trust_env,
                follow_redirects=False,
                auth=auth,
            ) as client:
                response = await client.post(
                    self._config.url,
                    content=req.dump(),
                    headers=headers,
                )
        except httpx.RequestError as exc:
            raise TransientError("Error communicating with RFC3161 TSA") from exc

        if 500 <= response.status_code <= 599:
            raise TransientError(f"RFC3161 TSA returned HTTP {response.status_code}")

        if response.status_code != 200:
            raise PermanentError(f"RFC3161 TSA returned HTTP {response.status_code}")

        content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip()
        if content_type.lower() != "application/timestamp-reply":
            raise PermanentError("RFC3161 TSA response is malformed (unexpected Content-Type)")

        try:
            tsa_resp = tsp.TimeStampResp.load(response.content)
        except Exception as exc:  # noqa: BLE001 - parse errors are not retryable
            raise PermanentError("RFC3161 TSA response is not a valid TimeStampResp") from exc

        # Validate the TSA response status before returning.
        status_info = tsa_resp["status"]
        status_value = status_info["status"].native
        _ACCEPTED_STATUSES = {"granted", "granted_with_mods"}
        if status_value not in _ACCEPTED_STATUSES:
            status_string = ""
            try:
                status_string = status_info["status_string"].native or ""
            except Exception:
                pass
            raise PermanentError(
                f"RFC3161 TSA rejected the request: status={status_value!r}"
                f"{f' ({status_string})' if status_string else ''}"
            )

        # Verify that the nonce in the response matches the one we sent.
        req_nonce = req["nonce"].native if req["nonce"].contents is not None else None
        tst_info = tsa_resp["time_stamp_token"]["content"]["encap_content_info"]["content"].parsed
        resp_nonce = tst_info["nonce"].native if tst_info["nonce"].contents is not None else None
        if req_nonce is not None and req_nonce != resp_nonce:
            raise PermanentError(
                f"RFC3161 TSA response nonce mismatch: expected {req_nonce}, got {resp_nonce}"
            )

        return tsa_resp


def build_timestamper(signing: SigningSettings, *, trust_env: bool = False) -> TimeStamper:
    """
    Build a pyHanko-compatible RFC3161 timestamper.

    Supports optional HTTP basic auth via TSA_USER/TSA_PASS.

    Raises:
      - PermanentError for misconfiguration or non-retryable TSA responses.
      - TransientError for network issues and HTTP 5xx responses.
    """
    config = _load_tsa_config(signing, trust_env=trust_env)
    return _HttpxRFC3161TimeStamper(config)
