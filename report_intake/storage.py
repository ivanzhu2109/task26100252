"""持久化层：SQLite 存受理记录与幂等键绑定，附件字节存本地文件。"""

from __future__ import annotations

import os
import sqlite3
from typing import List, Optional

from .models import Attachment

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    tracking_code TEXT PRIMARY KEY,
    description   TEXT NOT NULL,
    event_date    TEXT NOT NULL,
    accepted_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_content
    ON reports (description, event_date, accepted_at);

CREATE TABLE IF NOT EXISTS attachments (
    tracking_code TEXT NOT NULL REFERENCES reports (tracking_code),
    position      INTEGER NOT NULL,
    name          TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    PRIMARY KEY (tracking_code, position)
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    key           TEXT PRIMARY KEY,
    fingerprint   TEXT NOT NULL,
    tracking_code TEXT NOT NULL REFERENCES reports (tracking_code)
);
"""


class Storage:
    """封装 SQLite 与附件目录。

    - db_path: SQLite 文件路径（":memory:" 表示内存库）
    - attachment_dir: 附件字节文件存放目录
    """

    def __init__(self, db_path: str, attachment_dir: str) -> None:
        self._attachment_dir = attachment_dir
        os.makedirs(attachment_dir, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---- 幂等键 ----
    def find_idempotency(self, key: str) -> Optional[dict]:
        """返回该键的绑定记录（含内容指纹与跟踪码），不存在返回 None。"""
        row = self._conn.execute(
            "SELECT key, fingerprint, tracking_code FROM idempotency_keys WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return {"key": row[0], "fingerprint": row[1], "tracking_code": row[2]}

    def bind_idempotency(self, key: str, fingerprint: str, tracking_code: str) -> None:
        self._conn.execute(
            "INSERT INTO idempotency_keys (key, fingerprint, tracking_code)"
            " VALUES (?, ?, ?)",
            (key, fingerprint, tracking_code),
        )
        self._conn.commit()

    # ---- 举报 ----
    def insert_report(
        self,
        tracking_code: str,
        description: str,
        event_date: str,
        accepted_at: int,
        attachments: List[Attachment],
    ) -> None:
        """写入一条举报及其附件（附件字节落盘，元数据入库）。"""
        report_dir = os.path.join(self._attachment_dir, tracking_code)
        os.makedirs(report_dir, exist_ok=False)
        try:
            self._conn.execute(
                "INSERT INTO reports (tracking_code, description, event_date, accepted_at)"
                " VALUES (?, ?, ?, ?)",
                (tracking_code, description, event_date, accepted_at),
            )
            for position, attachment in enumerate(attachments):
                file_path = os.path.join(report_dir, str(position))
                with open(file_path, "wb") as handle:
                    handle.write(attachment.content)
                self._conn.execute(
                    "INSERT INTO attachments (tracking_code, position, name, file_path)"
                    " VALUES (?, ?, ?, ?)",
                    (tracking_code, position, attachment.name, file_path),
                )
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise

    def find_report_by_code(self, tracking_code: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT tracking_code, description, event_date, accepted_at"
            " FROM reports WHERE tracking_code = ?",
            (tracking_code,),
        ).fetchone()
        if row is None:
            return None
        return {
            "tracking_code": row[0],
            "description": row[1],
            "event_date": row[2],
            "accepted_at": row[3],
        }

    def latest_accepted_at(self, description: str, event_date: str) -> Optional[int]:
        """同一描述+事件日期下，最近一次成功受理时间；无则 None。"""
        row = self._conn.execute(
            "SELECT MAX(accepted_at) FROM reports"
            " WHERE description = ? AND event_date = ?",
            (description, event_date),
        ).fetchone()
        return row[0]

    def load_attachments(self, tracking_code: str) -> List[Attachment]:
        """按顺序读回某举报的全部附件（逐字节还原）。"""
        rows = self._conn.execute(
            "SELECT name, file_path FROM attachments"
            " WHERE tracking_code = ? ORDER BY position",
            (tracking_code,),
        ).fetchall()
        result: List[Attachment] = []
        for name, file_path in rows:
            with open(file_path, "rb") as handle:
                result.append(Attachment(name=name, content=handle.read()))
        return result
