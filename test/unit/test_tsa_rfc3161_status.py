"""RFC3161 TSA response status tests."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
import respx

from test.support.checks import check
from test.support.tsa_helpers import CapturingDebugLog
from test.support.tsa_helpers import make_signing as _make_signing
from test.support.tsa_helpers import mock_status_response as _mock_status_response
from test.support.tsa_helpers import mock_tsa_response as _mock_tsa_response
from test.support.tsa_helpers import tsa_req as _tsa_req
from zammad_pdf_archiver.domain.errors import PermanentError

pytest.importorskip("pyhanko", reason="TSA adapter requires pyHanko")

import zammad_pdf_archiver.adapters.signing.tsa_rfc3161 as tsa_module  # noqa: E402
from zammad_pdf_archiver.adapters.signing.tsa_rfc3161 import build_timestamper  # noqa: E402


def test_tsa_rejection_status_raises_permanent() -> None:
    """TSA response with rejected status raises PermanentError with status in message."""
    tsa_url = "https://tsa.test/rfc3161"
    signing = _make_signing(tsa_url)
    timestamper = build_timestamper(signing)
    mock_resp = _mock_status_response(MagicMock(native="Not authorized"))

    with respx.mock:
        _mock_tsa_response(tsa_url)
        from asn1crypto import tsp as _tsp

        with patch.object(_tsp.TimeStampResp, "load", return_value=mock_resp):
            with pytest.raises(PermanentError, match="rejected"):
                asyncio.run(timestamper.async_request_tsa_response(_tsa_req()))


def test_tsa_status_string_access_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed TSA status detail is logged while preserving PermanentError behavior."""
    class _BrokenStatusString:
        @property
        def native(self) -> str:
            raise ValueError("status string unavailable")

    tsa_url = "https://tsa.test/rfc3161"
    signing = _make_signing(tsa_url)
    timestamper = build_timestamper(signing)
    capture = CapturingDebugLog()
    monkeypatch.setattr(tsa_module, "log", capture)

    mock_resp = _mock_status_response(_BrokenStatusString())

    with respx.mock:
        _mock_tsa_response(tsa_url)
        from asn1crypto import tsp as _tsp

        with patch.object(_tsp.TimeStampResp, "load", return_value=mock_resp):
            with pytest.raises(PermanentError, match="rejection"):
                asyncio.run(timestamper.async_request_tsa_response(_tsa_req()))

    check(not not len(capture.debug_events) == 1, "assertion failed")
    event, fields = capture.debug_events[0]
    check(not not event == "tsa_status_string_unavailable", "assertion failed")
    check(not not set(fields) == {"exc_info"}, "assertion failed")
    check(
        not not isinstance(capture.debug_events[0][1]["exc_info"], ValueError), "assertion failed"
    )
