from __future__ import annotations

import importlib.machinery
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from test.support.checks import check


def _load_verify_pdf_module() -> types.ModuleType:
    script = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "verify-pdf.py"
    loader = importlib.machinery.SourceFileLoader("verify_pdf_script", str(script))
    module = types.ModuleType(loader.name)
    module.__file__ = str(script)
    loader.exec_module(module)
    return module


def _install_fake_pyhanko(
    monkeypatch: pytest.MonkeyPatch,
    captured_validation_kwargs: dict[str, Any],
) -> None:
    pyhanko = types.ModuleType("pyhanko")
    pyhanko.__path__ = []
    pdf_utils = types.ModuleType("pyhanko.pdf_utils")
    pdf_utils.__path__ = []
    reader_mod = types.ModuleType("pyhanko.pdf_utils.reader")
    sign_mod = types.ModuleType("pyhanko.sign")
    sign_mod.__path__ = []
    validation_mod = types.ModuleType("pyhanko.sign.validation")
    certvalidator_mod = types.ModuleType("pyhanko_certvalidator")

    class _PdfFileReader:
        def __init__(self, _doc: object) -> None:
            self.embedded_signatures = [object()]

    class _ValidationStatus:
        bottom_line = True

    class _ValidationContext:
        def __init__(self, **kwargs: Any) -> None:
            captured_validation_kwargs.update(kwargs)

    def _validate_pdf_signature(_embedded_sig: object, _vc: object) -> _ValidationStatus:
        return _ValidationStatus()

    reader_any: Any = reader_mod
    validation_any: Any = validation_mod
    certvalidator_any: Any = certvalidator_mod
    reader_any.PdfFileReader = _PdfFileReader
    validation_any.validate_pdf_signature = _validate_pdf_signature
    certvalidator_any.ValidationContext = _ValidationContext

    monkeypatch.setitem(sys.modules, "pyhanko", pyhanko)
    monkeypatch.setitem(sys.modules, "pyhanko.pdf_utils", pdf_utils)
    monkeypatch.setitem(sys.modules, "pyhanko.pdf_utils.reader", reader_mod)
    monkeypatch.setitem(sys.modules, "pyhanko.sign", sign_mod)
    monkeypatch.setitem(sys.modules, "pyhanko.sign.validation", validation_mod)
    monkeypatch.setitem(sys.modules, "pyhanko_certvalidator", certvalidator_mod)


def test_verify_pdf_passes_retroactive_revinfo_to_validation_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_validation_kwargs: dict[str, Any] = {}
    _install_fake_pyhanko(monkeypatch, captured_validation_kwargs)

    pdf_path = tmp_path / "signed.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")
    monkeypatch.setenv("VERIFY_PDF_RETROACTIVE_REVINFO", "1")
    monkeypatch.setattr(sys, "argv", ["verify-pdf.py", str(pdf_path)])

    module = _load_verify_pdf_module()

    check(not not module.main() == 0, "assertion failed")
    check(not captured_validation_kwargs["allow_fetching"] is not True, "assertion failed")
    check(not captured_validation_kwargs["retroactive_revinfo"] is not True, "assertion failed")
