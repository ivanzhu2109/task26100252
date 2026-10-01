# 匿名举报受理库

纯 Python 库，由上层直接调用。受理记录存 SQLite，附件字节存独立本地文件。

## 用法

```python
from report_intake import Attachment, IntakeService, Storage, Submission

storage = Storage("intake.sqlite", "attachments/")
service = IntakeService(storage)

result = service.submit(
    Submission(
        idempotency_key="caller-chosen-key",
        description="……",
        event_date="2026-09-30",
        attachments=[Attachment("证据.bin", b"...")],
    ),
    now=1_700_000_000,  # 调用方提供的 UTC 整数秒，不回退
)
if result.accepted:
    view = service.get_report(result.tracking_code)
```

## 规则

- 跟踪码由 `secrets.token_urlsafe(32)` 生成，唯一且不可预测；仅凭码可读对应举报及完整附件（逐字节还原）。
- 同一幂等键重试相同完整内容（描述、事件日期、附件名称/顺序/字节，不含调用时间）返回首次跟踪码；同键不同内容永久拒绝。
- 不同键、相同描述+事件日期：距最近一次成功受理不足 86400 秒拒绝且不泄露已有信息；满 86400 秒可受理为新举报。
- 拒绝不占用幂等键、不延长去重窗口；幂等重试优先于内容去重。

## 测试

```
python3 -m unittest discover -s tests
```
