"""Process-local admin sessions and request authentication."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from fastapi import Request
from pydantic import SecretStr

from zammad_pdf_archiver.i18n import normalize_locale

SESSION_COOKIE = "zpa_admin_session"


@dataclass
class AdminSession:
    session_id: str
    csrf_token: str
    created_at: float
    last_seen_at: float
    locale: str


class AdminSessionStore:
    """Bounded process-local session store; all sessions vanish on restart."""

    def __init__(
        self, *, idle_seconds: int, absolute_seconds: int, max_sessions: int = 100
    ) -> None:
        self._idle_seconds = idle_seconds
        self._absolute_seconds = absolute_seconds
        self._max_sessions = max_sessions
        self._sessions: dict[str, AdminSession] = {}

    def create(self, *, locale: str) -> AdminSession:
        now = time.time()
        self.prune(now=now)
        if len(self._sessions) >= self._max_sessions:
            oldest = min(self._sessions.values(), key=lambda item: item.last_seen_at)
            self._sessions.pop(oldest.session_id, None)
        session = AdminSession(
            session_id=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            last_seen_at=now,
            locale=normalize_locale(locale),
        )
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str | None, *, touch: bool = True) -> AdminSession | None:
        if not session_id:
            return None
        session = self._sessions.get(session_id)
        if session is None:
            return None
        now = time.time()
        if self._expired(session, now=now):
            self._sessions.pop(session_id, None)
            return None
        if touch:
            session.last_seen_at = now
        return session

    def delete(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)

    def prune(self, *, now: float | None = None) -> None:
        check_time = time.time() if now is None else now
        expired = [
            session_id
            for session_id, session in self._sessions.items()
            if self._expired(session, now=check_time)
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)

    def _expired(self, session: AdminSession, *, now: float) -> bool:
        return (
            now - session.last_seen_at > self._idle_seconds
            or now - session.created_at > self._absolute_seconds
        )


def access_token_matches(provided: str, expected: SecretStr | None) -> bool:
    expected_value = expected.get_secret_value() if expected is not None else ""
    expected_hash = hashlib.sha256(expected_value.encode("utf-8")).digest()
    provided_hash = hashlib.sha256(provided.encode("utf-8")).digest()
    return bool(expected_value) and hmac.compare_digest(expected_hash, provided_hash)


def session_from_request(request: Request, *, touch: bool = True) -> AdminSession | None:
    store: AdminSessionStore = request.app.state.admin_sessions
    return store.get(request.cookies.get(SESSION_COOKIE), touch=touch)


def csrf_matches(request: Request, session: AdminSession) -> bool:
    return csrf_token_matches(request.headers.get("X-CSRF-Token"), session)


def csrf_token_matches(provided: str | None, session: AdminSession) -> bool:
    return hmac.compare_digest(
        (provided or "").encode("utf-8"),
        session.csrf_token.encode("utf-8"),
    )
