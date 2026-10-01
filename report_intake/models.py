"""类型定义：提交输入、结果与读取视图。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class Attachment:
    """一个附件：名称 + 字节内容。"""

    name: str
    content: bytes


@dataclass(frozen=True)
class Submission:
    """一次提交：幂等键、描述、事件日期（YYYY-MM-DD）、有序附件集合。"""

    idempotency_key: str
    description: str
    event_date: str
    attachments: List[Attachment] = field(default_factory=list)


@dataclass(frozen=True)
class SubmitResult:
    """提交结果。

    accepted=True 时 tracking_code 为新受理（或幂等重试命中）的跟踪码；
    accepted=False 时 reason 说明拒绝原因，不携带任何已有举报信息。
    """

    accepted: bool
    tracking_code: Optional[str] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class ReportView:
    """凭跟踪码读取到的举报视图。"""

    tracking_code: str
    description: str
    event_date: str
    accepted_at: int
    attachments: List[Attachment]


class IntakeError(Exception):
    """库级错误基类。"""


class TrackingCodeNotFound(IntakeError):
    """跟踪码不存在（或对应举报未成功受理）。"""
