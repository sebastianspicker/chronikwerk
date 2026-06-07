from __future__ import annotations

import os
import sys
from typing import Any, cast

from scripts.ops.verify_pdf_certs import (
    add_trust_certs,
    iter_cert_files,
    load_certs_from_paths,
    split_paths,
    validation_context_kwargs,
)

__all__ = [
    "add_trust_certs",
    "iter_cert_files",
    "load_certs_from_paths",
    "load_validation_tools",
    "split_paths",
    "validate_embedded_signatures",
    "validation_context_kwargs",
]


def load_validation_tools() -> tuple[Any, Any, Any] | None:
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


def signature_location(embedded_sig: object) -> str:
    field_name = getattr(embedded_sig, "field_name", None)
    return f" (field {field_name})" if field_name else ""


def print_signature_details(idx: int, embedded_sig: object, status: object) -> None:
    print(f"Signature #{idx}{signature_location(embedded_sig)}:", file=sys.stderr)
    status_details = cast(Any, status)
    try:
        print(status_details.pretty_print_details(), file=sys.stderr)
    except Exception:
        print(str(status), file=sys.stderr)


def validate_embedded_signature(
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
            f"Signature #{idx}{signature_location(embedded_sig)}: validation error: {exc}",
            file=sys.stderr,
        )
        return False

    bottom_line = bool(getattr(status, "bottom_line", False))
    if show_details or not bottom_line:
        print_signature_details(idx, embedded_sig, status)
    return bottom_line


def validate_embedded_signatures(
    embedded_sigs: list[object], *, validate_pdf_signature: Any, vc: Any
) -> bool:
    show_details = os.environ.get("VERIFY_PDF_SHOW_DETAILS", "") == "1"
    ok = True
    for idx, embedded_sig in enumerate(embedded_sigs, start=1):
        bottom_line = validate_embedded_signature(
            idx,
            embedded_sig,
            validate_pdf_signature=validate_pdf_signature,
            vc=vc,
            show_details=show_details,
        )
        ok = ok and bottom_line
    return ok
