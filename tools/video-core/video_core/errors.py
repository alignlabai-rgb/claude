"""Structured errors. Every rejection carries machine-readable issue codes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Issue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {"code": self.code, "path": self.path, "message": self.message}


class VideoCoreError(Exception):
    """Base class. Raised only *before* any process is launched or file created
    unless a subclass documents otherwise."""

    kind = "error"

    def __init__(self, message: str, issues: list[Issue] | tuple[Issue, ...] = ()):
        super().__init__(message)
        self.message = message
        self.issues = list(issues)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "message": self.message,
            "issues": [i.to_dict() for i in self.issues],
        }


class ValidationFailed(VideoCoreError):
    kind = "validation_failed"


class UnsafePath(VideoCoreError):
    kind = "unsafe_path"


class ToolUnavailable(VideoCoreError):
    kind = "tool_unavailable"


def single(code: str, path: str, message: str, cls=ValidationFailed) -> VideoCoreError:
    return cls(message, [Issue(code, path, message)])
