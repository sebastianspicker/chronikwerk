from __future__ import annotations

from asn1crypto import tsp

from zammad_pdf_archiver.domain.errors import PermanentError


def validate_timestamp_status(tsa_resp: tsp.TimeStampResp, *, log) -> None:  # noqa: ANN001
    try:
        status_info = tsa_resp["status"]
        status_value = status_info["status"].native
    except Exception as exc:
        raise PermanentError("RFC3161 TSA response missing status field") from exc
    accepted_statuses = {"granted", "granted_with_mods"}
    if status_value in accepted_statuses:
        return

    status_string = ""
    try:
        status_string = status_info["status_string"].native or ""
    except Exception as exc:
        log.debug("tsa_status_string_unavailable", exc_info=exc)
    raise PermanentError(
        f"RFC3161 TSA rejected the request: status={status_value!r}"
        f"{f' ({status_string})' if status_string else ''}"
    )


def validate_timestamp_nonce(req: tsp.TimeStampReq, tsa_resp: tsp.TimeStampResp) -> None:
    try:
        req_nonce = req["nonce"].native if req["nonce"].contents is not None else None
        token_content = tsa_resp["time_stamp_token"]["content"]
        tst_info = token_content["encap_content_info"]["content"].parsed
        resp_nonce = tst_info["nonce"].native if tst_info["nonce"].contents is not None else None
    except Exception as exc:
        raise PermanentError("RFC3161 TSA response missing timestamp token fields") from exc
    if req_nonce is not None and resp_nonce is None:
        raise PermanentError("RFC3161 TSA response missing nonce")
    if req_nonce is not None and req_nonce != resp_nonce:
        raise PermanentError(
            f"RFC3161 TSA response nonce mismatch: expected {req_nonce}, got {resp_nonce}"
        )
