"""Reject unusable local signing material through the public signer."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from chronikwerk.documents.signing import sign_pdf_with_provenance
from chronikwerk.failures import PermanentError
from tests.unit.documents.test_signing_boundaries import _signing_options


@pytest.mark.parametrize("material", ["missing", "corrupt", "password", "expired", "future"])
def test_signer_rejects_missing_corrupt_or_invalid_certificate(tmp_path, material) -> None:
    path = tmp_path / "synthetic.pfx"
    options = replace(_signing_options(enabled=True), pfx_path=path)
    if material == "missing":
        expected = "PFX file not found"
    elif material == "corrupt":
        path.write_bytes(b"not a PKCS12 file")
        expected = "corrupted file"
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic test")])
        now = datetime.now(UTC)
        before = now + timedelta(days=1) if material == "future" else now - timedelta(days=2)
        after = now - timedelta(days=1) if material == "expired" else now + timedelta(days=2)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(before)
            .not_valid_after(after)
            .sign(key, hashes.SHA256())
        )
        path.write_bytes(
            pkcs12.serialize_key_and_certificates(
                b"test",
                key,
                cert,
                None,
                serialization.BestAvailableEncryption(b"synthetic-password"),
            )
        )
        options = replace(options, pfx_password="synthetic-password")
        if material == "password":
            options = replace(options, pfx_password="incorrect")
            expected = "wrong password"
        else:
            expected = "expired" if material == "expired" else "not valid before"
    with pytest.raises(PermanentError, match=expected):
        sign_pdf_with_provenance(b"synthetic PDF", options)
