"""Verifies PDF signing invokes TSA support and classifies TSA outages."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import SecretStr

from chronikwerk.adapters.signing.sign_pdf import sign_pdf
from chronikwerk.config.settings import (
    SigningPadesSettings,
    SigningSettings,
    SigningTimestampRfc3161Settings,
    SigningTimestampSettings,
)
from chronikwerk.domain.errors import TransientError
from tests.support.signing_test_helpers import sample_pdf_bytes, write_test_pfx


def _tsa_response_for_request(req_bytes: bytes) -> bytes:
    """Build a deterministic tsa response for request fixture for focused assertions."""
    from asn1crypto import keys, tsp, x509
    from cryptography import x509 as pyca_x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from pyhanko.sign.timestamps.dummy_client import DummyTimeStamper

    req = tsp.TimeStampReq.load(req_bytes)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = pyca_x509.Name([pyca_x509.NameAttribute(NameOID.COMMON_NAME, "Test TSA")])
    now = datetime.now(UTC)
    cert = (
        pyca_x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(pyca_x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(private_key=key, algorithm=hashes.SHA256())
    )

    tsa_cert = x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))
    tsa_key = keys.PrivateKeyInfo.load(
        key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    timestamper = DummyTimeStamper(tsa_cert=tsa_cert, tsa_key=tsa_key)
    resp = timestamper.request_tsa_response(req)
    return resp.dump()


def _make_signing_with_tsa(
    pfx_path: Path, password: str, tsa_url: str, timeout_seconds: float = 10.0
) -> SigningSettings:
    """Build the signing with tsa fixture used by this scenario."""
    return SigningSettings(
        enabled=True,
        pfx_path=pfx_path,
        pfx_password=SecretStr(password),
        pades=SigningPadesSettings(reason="Unit test", location="CI"),
        timestamp=SigningTimestampSettings(
            enabled=True,
            rfc3161=SigningTimestampRfc3161Settings(
                tsa_url=tsa_url,  # type: ignore[arg-type]
                timeout_seconds=timeout_seconds,
            ),
        ),
    )


def test_sign_pdf_with_tsa_enabled_calls_tsa(tmp_path: Path) -> None:
    pfx_path = tmp_path / "test.pfx"
    write_test_pfx(pfx_path, password="secret")

    tsa_url = "https://tsa.test/rfc3161"
    signing = _make_signing_with_tsa(pfx_path, "secret", tsa_url)

    with respx.mock(assert_all_called=False) as router:
        route = router.post(tsa_url)

        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "application/timestamp-reply"},
                content=_tsa_response_for_request(request.content),
            )

        route.mock(side_effect=_handler)

        signed = sign_pdf(sample_pdf_bytes(), signing, allow_private_networks=True)
        assert signed.startswith(b"%PDF-")
        assert route.called


def test_sign_pdf_with_unreachable_tsa_is_transient(tmp_path: Path) -> None:
    pfx_path = tmp_path / "test.pfx"
    write_test_pfx(pfx_path, password="secret")

    tsa_url = "https://tsa.test/rfc3161"
    signing = _make_signing_with_tsa(pfx_path, "secret", tsa_url, timeout_seconds=0.1)

    with respx.mock(assert_all_called=False) as router:
        router.post(tsa_url).mock(side_effect=httpx.ConnectError("boom"))
        with pytest.raises(TransientError):
            sign_pdf(sample_pdf_bytes(), signing, allow_private_networks=True)
