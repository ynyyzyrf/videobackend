import asyncio

from fastapi import BackgroundTasks

from app import db, main
from app.schemas import OperationActor, ReportCreate, ReportOut, VideoJobCreate, VideoJobOut


def _report(report_id: str) -> ReportOut:
    return ReportOut(
        id=report_id,
        reporter_name="MAG",
        report_period="2026/08/24 - 2026/08/30",
        report_date="2026/08/27",
        raw_content="weekly update",
        status="generating",
        error=None,
        current_version=None,
        created_at="2026-09-03 00:00:00",
        updated_at="2026-09-03 00:00:00",
    )


def test_database_schema_contains_operation_log_table():
    schema = "\n".join(db._schema_statements())

    assert "CREATE TABLE IF NOT EXISTS operation_logs" in schema
    assert "user_id VARCHAR(255) NOT NULL" in schema
    assert "display_name VARCHAR(255) NOT NULL" in schema
    assert "action VARCHAR(64) NOT NULL" in schema


def test_create_report_logs_operator_once_and_uses_user_id_for_dify(monkeypatch):
    logs: list[dict[str, object]] = []
    dify_users: list[str] = []

    payload = ReportCreate(
        reporter_name="MAG",
        report_period="2026/08/24 - 2026/08/30",
        report_date="2026/08/27",
        raw_content="weekly update",
        operator=OperationActor(user_id="user-123", display_name="Mag.H"),
    )

    monkeypatch.setattr(main, "_insert_report_shell", lambda *_args: None)
    monkeypatch.setattr(main, "_get_report_or_404", lambda report_id, version_id=None: _report(report_id))
    monkeypatch.setattr(main, "_record_operation", lambda **kwargs: logs.append(kwargs))

    async def fake_create_deck_from_dify(**kwargs):
        dify_users.append(kwargs["user_id"])
        return {"slides": []}

    async def fake_persist_preview_assets(deck_json, preview_images, report_id):
        return deck_json, preview_images

    monkeypatch.setattr(main, "create_deck_from_dify", fake_create_deck_from_dify)
    monkeypatch.setattr(main, "persist_preview_assets", fake_persist_preview_assets)
    monkeypatch.setattr(main, "_insert_report_version", lambda *_args: "rv_1")
    monkeypatch.setattr(main, "_mark_report_generation", lambda *_args: None)

    tasks = BackgroundTasks()
    report = asyncio.run(main.create_report(payload, tasks))
    asyncio.run(tasks())

    assert report.status == "generating"
    assert dify_users == ["Mag.H [user-123]"]
    assert logs == [
        {
            "operator": payload.operator,
            "action": "create_report",
            "resource_type": "report",
            "resource_id": report.id,
        }
    ]


def test_start_video_logs_one_user_operation(monkeypatch):
    logs: list[dict[str, object]] = []
    operator = OperationActor(user_id="user-123", display_name="Mag.H")

    monkeypatch.setattr(main, "_report_row", lambda report_id: {"id": report_id, "current_version_id": "rv_1"})
    monkeypatch.setattr(main, "_version_row", lambda version_id: {"id": version_id, "deck_json": {"slides": []}})
    monkeypatch.setattr(main.db, "loads", lambda value: value)
    monkeypatch.setattr(main, "ensure_slide_images", lambda deck_json, report_id: deck_json)
    monkeypatch.setattr(main, "create_video_job", lambda *_args: "job_1")
    monkeypatch.setattr(main, "_record_operation", lambda **kwargs: logs.append(kwargs))
    monkeypatch.setattr(
        main,
        "_get_video_job_or_404",
        lambda job_id: VideoJobOut(
            id=job_id,
            report_id="report_1",
            report_version_id="rv_1",
            status="queued",
            progress=0,
            total=0,
            completed=0,
            final_video_url=None,
            error=None,
        ),
    )

    job = main.start_video_job(
        "report_1",
        VideoJobCreate(operator=operator),
        BackgroundTasks(),
    )

    assert logs == [
        {
            "operator": operator,
            "action": "generate_video",
            "resource_type": "video_job",
            "resource_id": job.id,
        }
    ]
