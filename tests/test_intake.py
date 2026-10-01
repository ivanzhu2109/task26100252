"""匿名举报受理库的自动化测试。"""

import os
import shutil
import tempfile
import unittest

from report_intake import (
    Attachment,
    IntakeService,
    Storage,
    Submission,
    TrackingCodeNotFound,
)

T0 = 1_700_000_000
WINDOW = 86400


def make_submission(key, description="欺诈线索", event_date="2026-09-30",
                    attachments=None):
    if attachments is None:
        attachments = [Attachment("证据一.bin", b"\x00\x01\x02binary"),
                       Attachment("说明.txt", "文本内容".encode("utf-8"))]
    return Submission(
        idempotency_key=key,
        description=description,
        event_date=event_date,
        attachments=attachments,
    )


class IntakeServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        storage = Storage(
            os.path.join(self.tmpdir, "intake.sqlite"),
            os.path.join(self.tmpdir, "attachments"),
        )
        self.addCleanup(storage.close)
        self.service = IntakeService(storage)

    # ---- 幂等键 ----

    def test_retry_same_key_same_content_returns_original_code(self):
        result = self.service.submit(make_submission("k1"), T0)
        self.assertTrue(result.accepted)
        code = result.tracking_code
        self.assertIsNotNone(code)

        # 窗口内重试
        retry = self.service.submit(make_submission("k1"), T0 + 100)
        self.assertTrue(retry.accepted)
        self.assertEqual(retry.tracking_code, code)

        # 窗口到期后重试仍返回原跟踪码（幂等优先于内容去重）
        retry_late = self.service.submit(make_submission("k1"), T0 + WINDOW * 3)
        self.assertTrue(retry_late.accepted)
        self.assertEqual(retry_late.tracking_code, code)

    def test_retry_does_not_change_accepted_at_or_duplicate_report(self):
        first = self.service.submit(make_submission("k1"), T0)
        self.service.submit(make_submission("k1"), T0 + 5000)
        view = self.service.get_report(first.tracking_code)
        self.assertEqual(view.accepted_at, T0)

    def test_same_key_different_attachment_content_rejected(self):
        first = self.service.submit(make_submission("k1"), T0)
        self.assertTrue(first.accepted)

        tampered = make_submission(
            "k1",
            attachments=[Attachment("证据一.bin", b"\x00\x01\x02changed"),
                         Attachment("说明.txt", "文本内容".encode("utf-8"))],
        )
        conflict = self.service.submit(tampered, T0 + 1)
        self.assertFalse(conflict.accepted)
        self.assertIsNone(conflict.tracking_code)

        # 窗口到期后依然拒绝：键绑定无时间窗
        conflict_late = self.service.submit(tampered, T0 + WINDOW * 3)
        self.assertFalse(conflict_late.accepted)

    def test_same_key_different_attachment_order_or_name_rejected(self):
        self.service.submit(make_submission("k1"), T0)
        reordered = make_submission(
            "k1",
            attachments=[Attachment("说明.txt", "文本内容".encode("utf-8")),
                         Attachment("证据一.bin", b"\x00\x01\x02binary")],
        )
        self.assertFalse(self.service.submit(reordered, T0 + 1).accepted)
        renamed = make_submission(
            "k1",
            attachments=[Attachment("改名.bin", b"\x00\x01\x02binary"),
                         Attachment("说明.txt", "文本内容".encode("utf-8"))],
        )
        self.assertFalse(self.service.submit(renamed, T0 + 1).accepted)

    # ---- 内容去重窗口 ----

    def test_duplicate_content_within_window_rejected_without_leaking(self):
        first = self.service.submit(make_submission("k1"), T0)
        self.assertTrue(first.accepted)

        # 不同键、相同描述+事件日期（附件不同不影响去重），86399 秒时被拒
        other = make_submission(
            "k2",
            attachments=[Attachment("别的附件.dat", b"totally different")],
        )
        rejected = self.service.submit(other, T0 + WINDOW - 1)
        self.assertFalse(rejected.accepted)
        self.assertIsNone(rejected.tracking_code)

        # 原举报人的跟踪码不受影响，仍可读
        view = self.service.get_report(first.tracking_code)
        self.assertEqual(view.description, "欺诈线索")

    def test_duplicate_content_at_exact_window_boundary_accepted(self):
        first = self.service.submit(make_submission("k1"), T0)
        self.assertTrue(first.accepted)

        other = make_submission(
            "k2",
            attachments=[Attachment("另一份.pdf", b"\xff\xfe pdf-bytes")],
        )
        second = self.service.submit(other, T0 + WINDOW)
        self.assertTrue(second.accepted)
        self.assertIsNotNone(second.tracking_code)
        self.assertNotEqual(second.tracking_code, first.tracking_code)

        # 两份举报的附件都完整可读、逐字节还原
        view1 = self.service.get_report(first.tracking_code)
        self.assertEqual(
            view1.attachments,
            [Attachment("证据一.bin", b"\x00\x01\x02binary"),
             Attachment("说明.txt", "文本内容".encode("utf-8"))],
        )
        view2 = self.service.get_report(second.tracking_code)
        self.assertEqual(view2.attachments,
                         [Attachment("另一份.pdf", b"\xff\xfe pdf-bytes")])

    def test_rejection_does_not_occupy_key_or_extend_window(self):
        self.service.submit(make_submission("k1"), T0)

        # k2 在窗口内被拒
        rejected = self.service.submit(make_submission("k2"), T0 + 100)
        self.assertFalse(rejected.accepted)

        # 窗口以首次成功受理为准：T0+86400 时 k2 同一请求可成功
        retry = self.service.submit(make_submission("k2"), T0 + WINDOW)
        self.assertTrue(retry.accepted)
        self.assertIsNotNone(retry.tracking_code)

        # 被拒期间 k2 未占用：若窗口内换成不同内容也不应报键冲突，
        # 只会因内容去重被拒
        # （用另一个此前同样被拒过的键验证，避免干扰上面 k2 的绑定）
        rejected2 = self.service.submit(make_submission("k9"), T0 + 150)
        self.assertFalse(rejected2.accepted)
        other_content = make_submission("k9", description="另一件事",
                                        event_date="2026-09-30")
        fresh = self.service.submit(other_content, T0 + 200)
        self.assertTrue(fresh.accepted)

    def test_window_uses_latest_successful_acceptance(self):
        self.service.submit(make_submission("k1"), T0)
        second = self.service.submit(make_submission("k2"), T0 + WINDOW)
        self.assertTrue(second.accepted)
        # 新窗口从最近一次成功受理起算
        rejected = self.service.submit(make_submission("k3"),
                                       T0 + WINDOW + WINDOW - 1)
        self.assertFalse(rejected.accepted)
        third = self.service.submit(make_submission("k3"), T0 + WINDOW * 2)
        self.assertTrue(third.accepted)

    # ---- 读取 ----

    def test_unknown_tracking_code_raises(self):
        with self.assertRaises(TrackingCodeNotFound):
            self.service.get_report("no-such-code")

    def test_tracking_codes_unpredictable_and_unique(self):
        codes = set()
        for index in range(8):
            result = self.service.submit(
                make_submission(f"k{index}", description=f"事件{index}"), T0
            )
            self.assertTrue(result.accepted)
            codes.add(result.tracking_code)
        self.assertEqual(len(codes), 8)
        for code in codes:
            self.assertGreaterEqual(len(code), 32)

    def test_empty_attachments_roundtrip(self):
        result = self.service.submit(
            make_submission("k1", attachments=[]), T0
        )
        self.assertTrue(result.accepted)
        view = self.service.get_report(result.tracking_code)
        self.assertEqual(view.attachments, [])


if __name__ == "__main__":
    unittest.main()
