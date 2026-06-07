from __future__ import annotations


class CapturingWarningLog:
    def __init__(self) -> None:
        self.error_events: list[tuple[str, dict[str, object]]] = []
        self.exception_events: list[tuple[str, dict[str, object]]] = []
        self.warning_events: list[tuple[str, dict[str, object]]] = []

    def error(self, event: str, **kwargs: object) -> None:
        self.error_events.append((event, kwargs))

    def exception(self, event: str, **kwargs: object) -> None:
        self.exception_events.append((event, kwargs))

    def warning(self, event: str, **kwargs: object) -> None:
        self.warning_events.append((event, kwargs))
