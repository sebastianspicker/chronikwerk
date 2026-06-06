from __future__ import annotations

import httpx
import structlog
from asn1crypto import tsp
from pyhanko.sign.timestamps.api import TimeStamper
from pyhanko.sign.timestamps.common_utils import set_tsp_headers

from zammad_pdf_archiver.adapters.http_util import timeouts_for
from zammad_pdf_archiver.adapters.signing.tsa_response_validation import (
    validate_timestamp_nonce,
    validate_timestamp_status,
)
from zammad_pdf_archiver.config.settings import SigningSettings
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError

log = structlog.get_logger(__name__)


class _HttpxRFC3161TimeStamper(TimeStamper):
    def __init__(self, signing: SigningSettings, *, trust_env: bool = False):
        super().__init__()
        rfc3161 = signing.timestamp.rfc3161

        tsa_url = rfc3161.tsa_url
        if tsa_url is None:
            raise PermanentError("Timestamping is enabled but TSA URL is missing")

        self._url = str(tsa_url)
        self._timeout_seconds = float(rfc3161.timeout_seconds)
        self._ca_bundle_path = rfc3161.ca_bundle_path
        self._trust_env = trust_env

        user = rfc3161.user
        password: str | None = (
            rfc3161.password.get_secret_value() if rfc3161.password is not None else None
        )

        self._auth: tuple[str, str] | None
        if user or password:
            if not user or not password:
                raise PermanentError("TSA basic auth requires both user and password in settings")
            self._auth = (user, password)
        else:
            self._auth = None

    async def async_request_tsa_response(self, req: tsp.TimeStampReq) -> tsp.TimeStampResp:
        headers = set_tsp_headers({})
        verify: bool | str = True
        if self._ca_bundle_path is not None:
            verify = str(self._ca_bundle_path)

        try:
            auth: tuple[str | bytes, str | bytes] | None = self._auth

            async with httpx.AsyncClient(
                timeout=timeouts_for(self._timeout_seconds),
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
                verify=verify,
                trust_env=self._trust_env,
                follow_redirects=False,
                auth=auth,
            ) as client:
                response = await client.post(
                    self._url,
                    content=req.dump(),
                    headers=headers,
                )
        except httpx.RequestError as exc:
            raise TransientError("Error communicating with RFC3161 TSA") from exc

        tsa_resp = _load_timestamp_response(response)
        validate_timestamp_status(tsa_resp, log=log)
        validate_timestamp_nonce(req, tsa_resp)
        return tsa_resp


def _load_timestamp_response(response: httpx.Response) -> tsp.TimeStampResp:
    if 500 <= response.status_code <= 599:
        raise TransientError(f"RFC3161 TSA returned HTTP {response.status_code}")

    if response.status_code != 200:
        raise PermanentError(f"RFC3161 TSA returned HTTP {response.status_code}")

    content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip()
    if content_type.lower() != "application/timestamp-reply":
        raise PermanentError("RFC3161 TSA response is malformed (unexpected Content-Type)")

    try:
        return tsp.TimeStampResp.load(response.content)
    except Exception as exc:  # noqa: BLE001 - parse errors are not retryable
        raise PermanentError("RFC3161 TSA response is not a valid TimeStampResp") from exc


def build_timestamper(signing: SigningSettings, *, trust_env: bool = False) -> TimeStamper:
    """
    Build a pyHanko-compatible RFC3161 timestamper.

    Supports optional HTTP basic auth via TSA_USER/TSA_PASS.

    Raises:
      - PermanentError for misconfiguration or non-retryable TSA responses.
      - TransientError for network issues and HTTP 5xx responses.
    """
    return _HttpxRFC3161TimeStamper(signing, trust_env=trust_env)
