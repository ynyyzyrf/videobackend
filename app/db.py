import json
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import parse_qs, unquote, urlparse

import pymysql
from pymysql.cursors import DictCursor

from .config import get_settings


@contextmanager
def connect() -> Iterator[pymysql.connections.Connection]:
    conn = pymysql.connect(**_mysql_config())
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _mysql_config() -> dict[str, Any]:
    url = get_settings().database_url
    parsed = urlparse(url)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise RuntimeError("DATABASE_URL must use mysql+pymysql:// for production.")

    query = parse_qs(parsed.query)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": parsed.path.lstrip("/"),
        "charset": query.get("charset", ["utf8mb4"])[0],
        "autocommit": False,
        "cursorclass": DictCursor,
    }


def init_db() -> None:
    with connect() as conn:
        with conn.cursor() as cursor:
            for statement in _schema_statements():
                cursor.execute(statement)
            _ensure_report_generation_columns(cursor)


def _ensure_report_generation_columns(cursor: Any) -> None:
    columns = {
        row["COLUMN_NAME"]
        for row in _fetch_columns(cursor, "reports")
    }
    if "generation_status" not in columns:
        cursor.execute(
            """
            ALTER TABLE reports
            ADD COLUMN generation_status VARCHAR(32) NOT NULL DEFAULT 'ready'
            AFTER current_version_id
            """
        )
    if "generation_error" not in columns:
        cursor.execute(
            """
            ALTER TABLE reports
            ADD COLUMN generation_error TEXT NULL
            AFTER generation_status
            """
        )
    if "dify_conversation_id" not in columns:
        cursor.execute(
            """
            ALTER TABLE reports
            ADD COLUMN dify_conversation_id VARCHAR(255) NULL
            AFTER generation_error
            """
        )


def _fetch_columns(cursor: Any, table_name: str) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT COLUMN_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        """,
        (table_name,),
    )
    return list(cursor.fetchall())


def _schema_statements() -> list[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS reports (
          id VARCHAR(64) PRIMARY KEY,
          reporter_name VARCHAR(255) NOT NULL,
          report_period VARCHAR(255) NOT NULL,
          report_date VARCHAR(64) NOT NULL,
          raw_content MEDIUMTEXT NOT NULL,
          current_version_id VARCHAR(64) NULL,
          generation_status VARCHAR(32) NOT NULL DEFAULT 'ready',
          generation_error TEXT NULL,
          dify_conversation_id VARCHAR(255) NULL,
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX idx_reports_current_version (current_version_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS report_versions (
          id VARCHAR(64) PRIMARY KEY,
          report_id VARCHAR(64) NOT NULL,
          version INT NOT NULL,
          deck_json JSON NOT NULL,
          source VARCHAR(64) NOT NULL,
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE KEY uniq_report_version (report_id, version),
          CONSTRAINT fk_report_versions_report
            FOREIGN KEY (report_id) REFERENCES reports(id)
            ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS video_jobs (
          id VARCHAR(64) PRIMARY KEY,
          report_id VARCHAR(64) NOT NULL,
          report_version_id VARCHAR(64) NOT NULL,
          status VARCHAR(32) NOT NULL,
          progress INT NOT NULL DEFAULT 0,
          total INT NOT NULL DEFAULT 0,
          completed INT NOT NULL DEFAULT 0,
          final_video_url TEXT NULL,
          error TEXT NULL,
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          INDEX idx_video_jobs_report (report_id),
          INDEX idx_video_jobs_version (report_version_id),
          CONSTRAINT fk_video_jobs_report
            FOREIGN KEY (report_id) REFERENCES reports(id)
            ON DELETE CASCADE,
          CONSTRAINT fk_video_jobs_version
            FOREIGN KEY (report_version_id) REFERENCES report_versions(id)
            ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS video_job_items (
          id VARCHAR(64) PRIMARY KEY,
          job_id VARCHAR(64) NOT NULL,
          slide_index INT NOT NULL,
          slide_type VARCHAR(32) NOT NULL,
          image_url TEXT NOT NULL,
          text_content MEDIUMTEXT NOT NULL,
          task_id VARCHAR(255) NULL,
          status VARCHAR(32) NOT NULL,
          video_url TEXT NULL,
          error TEXT NULL,
          attempts INT NOT NULL DEFAULT 0,
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uniq_job_slide (job_id, slide_index),
          INDEX idx_video_job_items_job (job_id),
          INDEX idx_video_job_items_task (task_id),
          CONSTRAINT fk_video_job_items_job
            FOREIGN KEY (job_id) REFERENCES video_jobs(id)
            ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS operation_logs (
          id VARCHAR(64) PRIMARY KEY,
          user_id VARCHAR(255) NOT NULL,
          display_name VARCHAR(255) NOT NULL,
          action VARCHAR(64) NOT NULL,
          resource_type VARCHAR(64) NOT NULL,
          resource_id VARCHAR(64) NULL,
          created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
          INDEX idx_operation_logs_user (user_id),
          INDEX idx_operation_logs_action (action),
          INDEX idx_operation_logs_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
    ]


def fetchone(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()


def fetchall(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            return list(cursor.fetchall())


def execute(query: str, params: tuple[Any, ...] = ()) -> None:
    with connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | bytes | dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return json.loads(value)
