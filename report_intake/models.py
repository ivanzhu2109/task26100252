"""公共类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Attachment:
    """一次提交中的单个附件。"""

    name: str
    content: bytes


@dataclass(frozen=True)
class Submission:
    """一次举报提交。attachments 的顺序属于请求内容的一部分。"""

    idempotency_key: str
    description: str
    event_date: date
    attachments: tuple[Attachment, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Report:
    """凭跟踪码读回的举报及其完整附件。"""

    tracking_code: str
    description: str
    event_date: date
    accepted_at: int
    attachments: tuple[Attachment, ...]


class IntakeError(Exception):
    """受理库基础异常。"""


class SubmissionRejected(IntakeError):
    """提交未被受理（内容重复或幂等键冲突）。"""


class ReportNotFound(IntakeError):
    """跟踪码不存在或对应举报未成功受理。"""
