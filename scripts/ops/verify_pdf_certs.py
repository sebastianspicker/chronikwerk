from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


def split_paths(value: str) -> list[Path]:
    parts = [p for p in value.split(":") if p]
    return [Path(p).expanduser() for p in parts]


def iter_cert_files(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.is_file():
                    out.append(child)
        else:
            out.append(p)
    return out


def load_certs_from_paths(paths: list[Path]) -> list[object]:
    # Cert objects are asn1crypto.x509.Certificate instances; keep typing loose to
    # avoid importing pyHanko at module import time.
    from pyhanko.keys import load_certs_from_pemder_data

    certs: list[object] = []
    for cert_file in iter_cert_files(paths):
        try:
            data = cert_file.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"Failed to read certificate file: {cert_file}") from exc

        try:
            certs.extend(list(load_certs_from_pemder_data(data)))
        except Exception as exc:  # noqa: BLE001 - report parsing issues clearly for ops
            raise RuntimeError(f"Failed to parse certificate(s) in: {cert_file}") from exc
    return certs


def add_trust_certs(vc_kwargs: dict[str, Any], trust_certs: list[object]) -> None:
    if not trust_certs:
        return
    if os.environ.get("VERIFY_PDF_TRUST_REPLACE", "") == "1":
        vc_kwargs["trust_roots"] = trust_certs
    else:
        vc_kwargs["extra_trust_roots"] = trust_certs


def validation_context_kwargs() -> dict[str, Any] | None:
    trust_paths = (
        split_paths(os.environ.get("VERIFY_PDF_TRUST", ""))
        if os.environ.get("VERIFY_PDF_TRUST")
        else []
    )
    other_paths = (
        split_paths(os.environ.get("VERIFY_PDF_OTHER_CERTS", ""))
        if os.environ.get("VERIFY_PDF_OTHER_CERTS")
        else []
    )

    try:
        trust_certs = load_certs_from_paths(trust_paths) if trust_paths else []
        other_certs = load_certs_from_paths(other_paths) if other_paths else []
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return None

    vc_kwargs: dict[str, Any] = {
        "allow_fetching": True,
        "retroactive_revinfo": os.environ.get("VERIFY_PDF_RETROACTIVE_REVINFO", "") == "1",
    }
    add_trust_certs(vc_kwargs, trust_certs)
    if other_certs:
        vc_kwargs["other_certs"] = other_certs
    return vc_kwargs
