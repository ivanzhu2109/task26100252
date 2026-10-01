"""业务层：受理规则（幂等、内容去重窗口、跟踪码读取）。"""

from __future__ import annotations

import hashlib
import secrets

from .models import ReportView, Submission, SubmitResult, TrackingCodeNotFound
from .storage import Storage

DEDUP_WINDOW_SECONDS = 86400

REASON_IDEMPOTENCY_CONFLICT = "idempotency_key_conflict"
REASON_DUPLICATE_CONTENT = "duplicate_content"


def _fingerprint(submission: Submission) -> str:
    """完整请求内容的指纹：描述、事件日期、附件名称/顺序/内容。

    不含幂等键与调用时间；用长度前缀分帧避免拼接歧义。
    """
    digest = hashlib.sha256()

    def feed(data: bytes) -> None:
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)

    feed(submission.description.encode("utf-8"))
    feed(submission.event_date.encode("utf-8"))
    digest.update(len(submission.attachments).to_bytes(8, "big"))
    for attachment in submission.attachments:
        feed(attachment.name.encode("utf-8"))
        feed(attachment.content)
    return digest.hexdigest()


class IntakeService:
    """匿名举报受理服务，由上层直接实例化调用。"""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def submit(self, submission: Submission, now: int) -> SubmitResult:
        """提交举报。now 为调用方提供的 UTC 整数秒（不回退）。"""
        fingerprint = _fingerprint(submission)

        # 1. 幂等键优先：成功使用过的键永远绑定首次内容。
        binding = self._storage.find_idempotency(submission.idempotency_key)
        if binding is not None:
            if binding["fingerprint"] == fingerprint:
                return SubmitResult(accepted=True, tracking_code=binding["tracking_code"])
            return SubmitResult(accepted=False, reason=REASON_IDEMPOTENCY_CONFLICT)

        # 2. 内容去重：同描述+事件日期，距最近一次成功受理不足窗口则拒绝。
        #    拒绝不占用幂等键，也不刷新窗口。
        latest = self._storage.latest_accepted_at(
            submission.description, submission.event_date
        )
        if latest is not None and now - latest < DEDUP_WINDOW_SECONDS:
            return SubmitResult(accepted=False, reason=REASON_DUPLICATE_CONTENT)

        # 3. 受理：生成不可预测跟踪码，落库并绑定幂等键。
        tracking_code = secrets.token_urlsafe(32)
        self._storage.insert_report(
            tracking_code=tracking_code,
            description=submission.description,
            event_date=submission.event_date,
            accepted_at=now,
            attachments=submission.attachments,
        )
        self._storage.bind_idempotency(
            submission.idempotency_key, fingerprint, tracking_code
        )
        return SubmitResult(accepted=True, tracking_code=tracking_code)

    def get_report(self, tracking_code: str) -> ReportView:
        """凭跟踪码读取举报及完整附件；不存在则抛 TrackingCodeNotFound。"""
        report = self._storage.find_report_by_code(tracking_code)
        if report is None:
            raise TrackingCodeNotFound(tracking_code)
        return ReportView(
            tracking_code=report["tracking_code"],
            description=report["description"],
            event_date=report["event_date"],
            accepted_at=report["accepted_at"],
            attachments=self._storage.load_attachments(tracking_code),
        )
