"""SQLite 记录与本地附件文件的持久化。"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import date
from pathlib import Path

from .models import Attachment, Report

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracking_code TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    event_date TEXT NOT NULL,
    accepted_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports (id),
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    file_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    report_id INTEGER NOT NULL REFERENCES reports (id)
);
CREATE INDEX IF NOT EXISTS idx_reports_content
    ON reports (description, event_date, accepted_at);
"""


class Storage:
    """负责举报记录（SQLite）与附件内容（独立本地文件）的读写。"""

    def __init__(self, db_path: str | Path, files_dir: str | Path) -> None:
        self._files_dir = Path(files_dir)
        self._files_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def find_idempotency(self, key: str) -> tuple[str, str] | None:
        """返回 (content_hash, tracking_code)；键不存在返回 None。"""
        row = self._conn.execute(
            """
            SELECT k.content_hash, r.tracking_code
            FROM idempotency_keys k
            JOIN reports r ON r.id = k.report_id
            WHERE k.key = ?
            """,
            (key,),
        ).fetchone()
        return (row[0], row[1]) if row else None

    def latest_accepted_at(self, description: str, event_date: date) -> int | None:
        """同一描述与事件日期最近一次成功受理时间；无记录返回 None。"""
        row = self._conn.execute(
            """
            SELECT MAX(accepted_at) FROM reports
            WHERE description = ? AND event_date = ?
            """,
            (description, event_date.isoformat()),
        ).fetchone()
        return row[0]

    def create_report(
        self,
        tracking_code: str,
        description: str,
        event_date: date,
        accepted_at: int,
        attachments: tuple[Attachment, ...],
        idempotency_key: str,
        content_hash: str,
    ) -> None:
        """写入附件文件与全部数据库记录，任一步失败不留半成品。"""
        written: list[Path] = []
        file_names: list[str] = []
        try:
            for attachment in attachments:
                file_name = secrets.token_hex(16)
                path = self._files_dir / file_name
                path.write_bytes(attachment.content)
                written.append(path)
                file_names.append(file_name)
            with self._conn:
                cursor = self._conn.execute(
                    """
                    INSERT INTO reports (tracking_code, description, event_date, accepted_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (tracking_code, description, event_date.isoformat(), accepted_at),
                )
                report_id = cursor.lastrowid
                self._conn.executemany(
                    """
                    INSERT INTO attachments (report_id, position, name, file_name)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (report_id, position, attachment.name, file_name)
                        for position, (attachment, file_name) in enumerate(
                            zip(attachments, file_names)
                        )
                    ],
                )
                self._conn.execute(
                    "INSERT INTO idempotency_keys (key, content_hash, report_id) VALUES (?, ?, ?)",
                    (idempotency_key, content_hash, report_id),
                )
        except BaseException:
            for path in written:
                path.unlink(missing_ok=True)
            raise

    def get_report(self, tracking_code: str) -> Report | None:
        """按跟踪码读取举报及附件；不存在返回 None。"""
        row = self._conn.execute(
            """
            SELECT id, description, event_date, accepted_at
            FROM reports WHERE tracking_code = ?
            """,
            (tracking_code,),
        ).fetchone()
        if row is None:
            return None
        report_id, description, event_date_text, accepted_at = row
        attachment_rows = self._conn.execute(
            """
            SELECT name, file_name FROM attachments
            WHERE report_id = ? ORDER BY position
            """,
            (report_id,),
        ).fetchall()
        attachments = tuple(
            Attachment(name=name, content=(self._files_dir / file_name).read_bytes())
            for name, file_name in attachment_rows
        )
        return Report(
            tracking_code=tracking_code,
            description=description,
            event_date=date.fromisoformat(event_date_text),
            accepted_at=accepted_at,
            attachments=attachments,
        )
