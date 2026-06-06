#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.ops.verify_pdf_support import (
    load_validation_tools,
    validate_embedded_signatures,
    validation_context_kwargs,
)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: scripts/ops/verify-pdf.py /path/to/file.pdf", file=sys.stderr)
        return 2

    pdf_path = Path(sys.argv[1])
    if not pdf_path.is_file():
        print(f"PDF not found (or not a regular file): {pdf_path}", file=sys.stderr)
        return 1

    tools = load_validation_tools()
    if tools is None:
        return 2
    PdfFileReader, validate_pdf_signature, ValidationContext = tools

    vc_kwargs = validation_context_kwargs()
    if vc_kwargs is None:
        return 1

    vc = ValidationContext(**cast(Any, vc_kwargs))

    with pdf_path.open("rb") as doc:
        reader = PdfFileReader(doc)
        embedded_sigs = list(getattr(reader, "embedded_signatures", []) or [])
        if not embedded_sigs:
            print("No embedded PDF signatures found.", file=sys.stderr)
            return 1

        ok = validate_embedded_signatures(
            embedded_sigs,
            validate_pdf_signature=validate_pdf_signature,
            vc=vc,
        )

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
