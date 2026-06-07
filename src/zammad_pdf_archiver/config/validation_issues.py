from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import ValidationError


@dataclass(frozen=True)
class ConfigValidationIssue:
    path: str
    message: str


class ConfigValidationError(ValueError):
    def __init__(self, issues: Iterable[ConfigValidationIssue]):
        self.issues = list(issues)
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        lines = ["Configuration is invalid:"]
        for issue in self.issues:
            lines.append(f"- {issue.path}: {issue.message}")
        return "\n".join(lines)


def issues_from_pydantic_error(error: ValidationError) -> list[ConfigValidationIssue]:
    """Convert a Pydantic ValidationError into a list of ConfigValidationIssue instances."""
    issues: list[ConfigValidationIssue] = []
    for item in error.errors(include_url=False):
        loc = ".".join(str(part) for part in item.get("loc", ())) or "<root>"
        msg = item.get("msg", "Invalid value")
        issues.append(ConfigValidationIssue(path=loc, message=msg))
    return issues
