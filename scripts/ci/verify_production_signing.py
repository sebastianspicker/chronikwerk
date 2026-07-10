#!/usr/bin/env python3
"""Prove that the production image can render, sign, and validate a PDF."""

from __future__ import annotations

import asyncio
import io
import secrets
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from pydantic import SecretStr
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import validation
from pyhanko_certvalidator import ValidationContext

from zammad_pdf_archiver.adapters.pdf.render_pdf import render_pdf
from zammad_pdf_archiver.adapters.signing.sign_pdf import sign_pdf
from zammad_pdf_archiver.config.settings import SigningSettings
from zammad_pdf_archiver.domain.snapshot_models import Snapshot


def _write_ephemeral_pfx(path: Path, password: str) -> x509.Certificate:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Production smoke signer")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(
        pkcs12.serialize_key_and_certificates(
            name=b"production-smoke-signer",
            key=key,
            cert=certificate,
            cas=None,
            encryption_algorithm=serialization.BestAvailableEncryption(password.encode("utf-8")),
        )
    )
    return certificate


def _validate_embedded_signature(pdf_bytes: bytes, certificate: x509.Certificate) -> None:
    reader = PdfFileReader(io.BytesIO(pdf_bytes))
    if len(reader.embedded_signatures) != 1:
        raise RuntimeError("expected one embedded PDF signature")
    trust_root = asn1_x509.Certificate.load(certificate.public_bytes(serialization.Encoding.DER))
    status = validation.validate_pdf_signature(
        reader.embedded_signatures[0],
        signer_validation_context=ValidationContext(trust_roots=[trust_root]),
    )
    if not status.bottom_line:
        raise RuntimeError("embedded PDF signature did not validate")


def main() -> None:
    """Render, sign, and validate a PDF with temporary signing material."""
    password = secrets.token_urlsafe(24)
    with tempfile.TemporaryDirectory(prefix="zta-production-signing-") as directory:
        pfx_path = Path(directory) / "signing.pfx"
        certificate = _write_ephemeral_pfx(pfx_path, password)
        snapshot = Snapshot.model_validate(
            {"ticket": {"id": 1, "number": "VERIFY-1"}, "articles": []}
        )
        unsigned_pdf = asyncio.run(render_pdf(snapshot))
        if not unsigned_pdf.startswith(b"%PDF"):
            raise RuntimeError("rendered document is not a PDF")
        signed_pdf = sign_pdf(
            unsigned_pdf,
            SigningSettings(
                enabled=True,
                pfx_path=pfx_path,
                pfx_password=SecretStr(password),
            ),
        )
        _validate_embedded_signature(signed_pdf, certificate)
    print("production-signing-ok")


if __name__ == "__main__":
    main()
