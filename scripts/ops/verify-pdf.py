#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, cast


def _split_paths(value: str) -> list[Path]:
    parts = [p for p in value.split(":") if p]
    return [Path(p).expanduser() for p in parts]


def _iter_cert_files(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.is_file():
                    out.append(child)
        else:
            out.append(p)
    return out


def _load_certs_from_paths(paths: list[Path]) -> list[object]:
    # Cert objects are asn1crypto.x509.Certificate instances; keep typing loose to
    # avoid importing pyHanko at module import time.
    from pyhanko.keys import load_certs_from_pemder_data

    certs: list[object] = []
    for cert_file in _iter_cert_files(paths):
        try:
            data = cert_file.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"Failed to read certificate file: {cert_file}") from exc

        try:
            certs.extend(list(load_certs_from_pemder_data(data)))
        except Exception as exc:  # noqa: BLE001 - report parsing issues clearly for ops
            raise RuntimeError(f"Failed to parse certificate(s) in: {cert_file}") from exc
    return certs


def _load_validation_tools() -> tuple[Any, Any, Any] | None:
    try:
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature
        from pyhanko_certvalidator import ValidationContext
    except Exception as exc:  # noqa: BLE001 - missing deps / import errors
        print(
            "pyHanko validation libraries are not available in this Python environment.",
            file=sys.stderr,
        )
        print(f"Import error: {exc}", file=sys.stderr)
        return None
    return PdfFileReader, validate_pdf_signature, ValidationContext


def _add_trust_certs(vc_kwargs: dict[str, Any], trust_certs: list[object]) -> None:
    if not trust_certs:
        return
    if os.environ.get("VERIFY_PDF_TRUST_REPLACE", "") == "1":
        vc_kwargs["trust_roots"] = trust_certs
    else:
        vc_kwargs["extra_trust_roots"] = trust_certs


def _validation_context_kwargs() -> dict[str, Any] | None:
    trust_paths = (
        _split_paths(os.environ.get("VERIFY_PDF_TRUST", ""))
        if os.environ.get("VERIFY_PDF_TRUST")
        else []
    )
    other_paths = (
        _split_paths(os.environ.get("VERIFY_PDF_OTHER_CERTS", ""))
        if os.environ.get("VERIFY_PDF_OTHER_CERTS")
        else []
    )

    try:
        trust_certs = _load_certs_from_paths(trust_paths) if trust_paths else []
        other_certs = _load_certs_from_paths(other_paths) if other_paths else []
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return None

    vc_kwargs: dict[str, Any] = {
        "allow_fetching": True,
        "retroactive_revinfo": os.environ.get("VERIFY_PDF_RETROACTIVE_REVINFO", "") == "1",
    }
    _add_trust_certs(vc_kwargs, trust_certs)
    if other_certs:
        vc_kwargs["other_certs"] = other_certs
    return vc_kwargs


def _signature_location(embedded_sig: object) -> str:
    field_name = getattr(embedded_sig, "field_name", None)
    return f" (field {field_name})" if field_name else ""


def _print_signature_details(idx: int, embedded_sig: object, status: object) -> None:
    print(f"Signature #{idx}{_signature_location(embedded_sig)}:", file=sys.stderr)
    status_details = cast(Any, status)
    try:
        print(status_details.pretty_print_details(), file=sys.stderr)
    except Exception:
        print(str(status), file=sys.stderr)


def _validate_embedded_signature(
    idx: int,
    embedded_sig: object,
    *,
    validate_pdf_signature: Any,
    vc: Any,
    show_details: bool,
) -> bool:
    try:
        status = validate_pdf_signature(embedded_sig, vc)
    except Exception as exc:  # noqa: BLE001 - report validation error and fail
        print(
            f"Signature #{idx}{_signature_location(embedded_sig)}: validation error: {exc}",
            file=sys.stderr,
        )
        return False

    bottom_line = bool(getattr(status, "bottom_line", False))
    if show_details or not bottom_line:
        _print_signature_details(idx, embedded_sig, status)
    return bottom_line


def _validate_embedded_signatures(
    embedded_sigs: list[object], *, validate_pdf_signature: Any, vc: Any
) -> bool:
    show_details = os.environ.get("VERIFY_PDF_SHOW_DETAILS", "") == "1"
    ok = True
    for idx, embedded_sig in enumerate(embedded_sigs, start=1):
        bottom_line = _validate_embedded_signature(
            idx,
            embedded_sig,
            validate_pdf_signature=validate_pdf_signature,
            vc=vc,
            show_details=show_details,
        )
        ok = ok and bottom_line
    return ok


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: scripts/ops/verify-pdf.py /path/to/file.pdf", file=sys.stderr)
        return 2

    pdf_path = Path(sys.argv[1])
    if not pdf_path.is_file():
        print(f"PDF not found (or not a regular file): {pdf_path}", file=sys.stderr)
        return 1

    tools = _load_validation_tools()
    if tools is None:
        return 2
    PdfFileReader, validate_pdf_signature, ValidationContext = tools

    vc_kwargs = _validation_context_kwargs()
    if vc_kwargs is None:
        return 1

    vc = ValidationContext(**cast(Any, vc_kwargs))

    with pdf_path.open("rb") as doc:
        reader = PdfFileReader(doc)
        embedded_sigs = list(getattr(reader, "embedded_signatures", []) or [])
        if not embedded_sigs:
            print("No embedded PDF signatures found.", file=sys.stderr)
            return 1

        ok = _validate_embedded_signatures(
            embedded_sigs,
            validate_pdf_signature=validate_pdf_signature,
            vc=vc,
        )

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
