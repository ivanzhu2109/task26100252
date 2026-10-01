"""匿名举报受理库的自动化测试。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from report_intake import (
    Attachment,
    ReportIntake,
    ReportNotFound,
    Submission,
    SubmissionRejected,
)

T0 = 1_700_000_000
EVENT_DATE = date(2026, 9, 30)


@pytest.fixture()
def intake(tmp_path: Path) -> ReportIntake:
    instance = ReportIntake(tmp_path / "intake.db", tmp_path / "files")
    yield instance
    instance.close()


def make_submission(
    key: str = "key-1",
    description: str = "食堂卫生问题",
    attachments: tuple[Attachment, ...] = (
        Attachment("photo1.jpg", b"\xff\xd8photo-one"),
        Attachment("photo2.jpg", b"\xff\xd8photo-two"),
    ),
) -> Submission:
    return Submission(
        idempotency_key=key,
        description=description,
        event_date=EVENT_DATE,
        attachments=attachments,
    )


def count_files(files_dir: Path) -> int:
    return len(list(files_dir.iterdir()))


class TestIdempotentRetry:
    def test_same_key_same_content_returns_original_code(
        self, intake: ReportIntake, tmp_path: Path
    ) -> None:
        submission = make_submission()
        code = intake.submit(submission, T0)

        retry = intake.submit(submission, T0 + 10)
        assert retry == code

        # 窗口到期后重试仍返回原跟踪码
        after_window = intake.submit(submission, T0 + 86400 * 7)
        assert after_window == code

        # 重试不新增举报、不重复保存附件、不改变首次受理时间
        assert count_files(tmp_path / "files") == 2
        report = intake.read(code)
        assert report.accepted_at == T0
        assert [a.name for a in report.attachments] == ["photo1.jpg", "photo2.jpg"]
        assert [a.content for a in report.attachments] == [
            b"\xff\xd8photo-one",
            b"\xff\xd8photo-two",
        ]

    def test_same_key_different_attachment_content_rejected(
        self, intake: ReportIntake
    ) -> None:
        code = intake.submit(make_submission(), T0)

        altered = make_submission(
            attachments=(
                Attachment("photo1.jpg", b"\xff\xd8photo-one-CHANGED"),
                Attachment("photo2.jpg", b"\xff\xd8photo-two"),
            )
        )
        with pytest.raises(SubmissionRejected):
            intake.submit(altered, T0 + 1)

        # 绑定无时间窗：窗口到期后同键不同内容仍被拒绝
        with pytest.raises(SubmissionRejected):
            intake.submit(altered, T0 + 86400 * 30)

        # 原举报不受影响，仍可读取
        assert intake.read(code).description == "食堂卫生问题"

    def test_same_key_different_attachment_name_or_order_rejected(
        self, intake: ReportIntake
    ) -> None:
        intake.submit(make_submission(), T0)

        renamed = make_submission(
            attachments=(
                Attachment("renamed.jpg", b"\xff\xd8photo-one"),
                Attachment("photo2.jpg", b"\xff\xd8photo-two"),
            )
        )
        with pytest.raises(SubmissionRejected):
            intake.submit(renamed, T0 + 1)

        reordered = make_submission(
            attachments=(
                Attachment("photo2.jpg", b"\xff\xd8photo-two"),
                Attachment("photo1.jpg", b"\xff\xd8photo-one"),
            )
        )
        with pytest.raises(SubmissionRejected):
            intake.submit(reordered, T0 + 1)


class TestContentDedup:
    def test_rejected_at_86399_and_accepted_at_86400(
        self, intake: ReportIntake, tmp_path: Path
    ) -> None:
        first = make_submission(key="key-a")
        code_a = intake.submit(first, T0)

        # 不同键、相同描述和事件日期：附件差异不影响内容去重
        second = make_submission(
            key="key-b",
            attachments=(Attachment("other.png", b"different-bytes"),),
        )
        with pytest.raises(SubmissionRejected):
            intake.submit(second, T0 + 86399)

        # 拒绝不泄露原跟踪码：第二次提交的键未被占用，材料不可读
        with pytest.raises(ReportNotFound):
            intake.read("key-b")
        assert count_files(tmp_path / "files") == 2

        # 恰好满 86400 秒可以生成新举报和新跟踪码
        code_b = intake.submit(second, T0 + 86400)
        assert code_b != code_a

        # 两份举报的附件均可逐字节完整读取
        report_a = intake.read(code_a)
        assert [a.content for a in report_a.attachments] == [
            b"\xff\xd8photo-one",
            b"\xff\xd8photo-two",
        ]
        assert report_a.accepted_at == T0

        report_b = intake.read(code_b)
        assert [a.name for a in report_b.attachments] == ["other.png"]
        assert [a.content for a in report_b.attachments] == [b"different-bytes"]
        assert report_b.accepted_at == T0 + 86400

    def test_rejection_does_not_occupy_key_or_extend_window(
        self, intake: ReportIntake
    ) -> None:
        code_a = intake.submit(make_submission(key="key-a"), T0)

        rejected_submission = make_submission(key="key-b")
        with pytest.raises(SubmissionRejected):
            intake.submit(rejected_submission, T0 + 100)

        # 窗口以最近一次成功受理为准，被拒绝的提交不延长窗口
        code_b = intake.submit(rejected_submission, T0 + 86400)
        assert code_b != code_a

    def test_idempotent_retry_takes_precedence_over_dedup(
        self, intake: ReportIntake
    ) -> None:
        code = intake.submit(make_submission(key="key-a"), T0)
        # 窗口内用同一幂等键重试，优先命中幂等而非内容去重
        assert intake.submit(make_submission(key="key-a"), T0 + 100) == code


class TestReadAccess:
    def test_unknown_tracking_code_not_found(self, intake: ReportIntake) -> None:
        with pytest.raises(ReportNotFound):
            intake.read("no-such-code")

    def test_tracking_codes_unique_and_unpredictable(
        self, intake: ReportIntake
    ) -> None:
        codes = set()
        for index in range(5):
            code = intake.submit(
                make_submission(key=f"key-{index}", description=f"事件{index}"),
                T0 + index,
            )
            codes.add(code)
        assert len(codes) == 5
        assert all(len(code) >= 32 for code in codes)

    def test_persistence_across_reopen(self, tmp_path: Path) -> None:
        db_path = tmp_path / "intake.db"
        files_dir = tmp_path / "files"

        first = ReportIntake(db_path, files_dir)
        code = first.submit(make_submission(), T0)
        first.close()

        reopened = ReportIntake(db_path, files_dir)
        try:
            # 重开后幂等重试仍返回原跟踪码
            assert reopened.submit(make_submission(), T0 + 86400 * 30) == code
            report = reopened.read(code)
            assert report.accepted_at == T0
            assert [a.content for a in report.attachments] == [
                b"\xff\xd8photo-one",
                b"\xff\xd8photo-two",
            ]
        finally:
            reopened.close()
