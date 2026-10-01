"""受理逻辑：幂等、内容去重、跟踪码读取。"""

from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

from .models import Report, ReportNotFound, Submission, SubmissionRejected
from .storage import Storage

DEDUP_WINDOW_SECONDS = 86400


def _content_hash(submission: Submission) -> str:
    """对完整请求内容（描述、事件日期、附件名称/顺序/内容）计算摘要。"""
    digest = hashlib.sha256()

    def feed(data: bytes) -> None:
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)

    feed(submission.description.encode("utf-8"))
    feed(submission.event_date.isoformat().encode("ascii"))
    feed(len(submission.attachments).to_bytes(8, "big"))
    for attachment in submission.attachments:
        feed(attachment.name.encode("utf-8"))
        feed(attachment.content)
    return digest.hexdigest()


class ReportIntake:
    """匿名举报受理入口，由上层直接实例化调用。"""

    def __init__(self, db_path: str | Path, files_dir: str | Path) -> None:
        self._storage = Storage(db_path, files_dir)

    def submit(self, submission: Submission, now: int) -> str:
        """受理提交，成功返回跟踪码；未受理抛出 SubmissionRejected。"""
        content_hash = _content_hash(submission)

        bound = self._storage.find_idempotency(submission.idempotency_key)
        if bound is not None:
            bound_hash, tracking_code = bound
            if bound_hash != content_hash:
                raise SubmissionRejected(
                    "幂等键已绑定不同内容，拒绝受理"
                )
            return tracking_code

        latest = self._storage.latest_accepted_at(
            submission.description, submission.event_date
        )
        if latest is not None and now - latest < DEDUP_WINDOW_SECONDS:
            raise SubmissionRejected(
                "相同举报内容在去重窗口内已受理，拒绝重复受理"
            )

        tracking_code = secrets.token_urlsafe(32)
        self._storage.create_report(
            tracking_code=tracking_code,
            description=submission.description,
            event_date=submission.event_date,
            accepted_at=now,
            attachments=submission.attachments,
            idempotency_key=submission.idempotency_key,
            content_hash=content_hash,
        )
        return tracking_code

    def read(self, tracking_code: str) -> Report:
        """凭跟踪码读取举报及附件；不存在抛出 ReportNotFound。"""
        report = self._storage.get_report(tracking_code)
        if report is None:
            raise ReportNotFound("跟踪码不存在")
        return report

    def close(self) -> None:
        self._storage.close()
