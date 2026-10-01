# task26100252

匿名举报受理库（`report_intake`），由上层直接实例化调用。
受理记录存 SQLite，附件内容存独立本地文件。

## 用法

```python
from datetime import date
from report_intake import Attachment, ReportIntake, Submission

intake = ReportIntake("intake.db", "attachment_files")

submission = Submission(
    idempotency_key="caller-chosen-key",
    description="事件描述",
    event_date=date(2026, 9, 30),
    attachments=(Attachment("photo.jpg", b"..."),),
)
tracking_code = intake.submit(submission, now=1_700_000_000)  # 调用方提供 UTC 秒
report = intake.read(tracking_code)  # 凭跟踪码读回举报及完整附件
```

- 同一幂等键重试相同完整内容返回首次跟踪码；同键不同内容抛出 `SubmissionRejected`。
- 不同键提交相同描述与事件日期，距最近一次成功受理不足 86400 秒抛出
  `SubmissionRejected`，且不泄露已有跟踪码。
- 跟踪码不存在时 `read` 抛出 `ReportNotFound`。

## 测试

```sh
python3 -m pytest tests/ -q
```
