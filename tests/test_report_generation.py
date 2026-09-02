import asyncio

from fastapi import BackgroundTasks

from app import main
from app.schemas import ReportCreate, ReportOut, ReportPatch, VideoJobCreate, VideoJobOut


def _payload() -> ReportCreate:
    return ReportCreate(
        reporter_name="MAG",
        report_period="2026/08/24 - 2026/08/30",
        report_date="2026/08/27",
        raw_content="我是mag",
    )


def test_create_report_without_deck_returns_before_dify(monkeypatch):
    inserted: dict[str, object] = {}

    async def fail_if_called(**_kwargs):
        raise AssertionError("Dify should run in a background task.")

    def fake_insert_report_shell(report_id, payload, now, status):
        inserted["report_id"] = report_id
        inserted["payload"] = payload
        inserted["status"] = status

    def fake_get_report(report_id, version_id=None):
        return ReportOut(
            id=report_id,
            reporter_name="MAG",
            report_period="2026/08/24 - 2026/08/30",
            report_date="2026/08/27",
            raw_content="我是mag",
            status="generating",
            error=None,
            current_version=None,
            created_at="2026-09-02 00:00:00",
            updated_at="2026-09-02 00:00:00",
        )

    monkeypatch.setattr(main, "create_deck_from_dify", fail_if_called)
    monkeypatch.setattr(main, "_insert_report_shell", fake_insert_report_shell)
    monkeypatch.setattr(main, "_get_report_or_404", fake_get_report)

    background_tasks = BackgroundTasks()
    report = asyncio.run(main.create_report(_payload(), background_tasks))

    assert report.status == "generating"
    assert report.current_version is None
    assert inserted["status"] == "generating"
    assert len(background_tasks.tasks) == 1


def test_generate_report_deck_marks_report_ready(monkeypatch):
    calls: list[tuple[str, object]] = []

    async def fake_create_deck_from_dify(**_kwargs):
        calls.append(("dify", None))
        return {"slides": [{"slide_type": "cover", "speaker_notes": "ok"}]}

    async def fake_persist_preview_assets(deck_json, preview_images, report_id):
        calls.append(("persist", report_id))
        return deck_json, preview_images

    def fake_insert_report_version(report_id, deck_json, source):
        calls.append(("version", source))
        return "rv_1"

    def fake_mark_report_generation(report_id, status, error, version_id):
        calls.append(("mark", (report_id, status, error, version_id)))

    monkeypatch.setattr(main, "create_deck_from_dify", fake_create_deck_from_dify)
    monkeypatch.setattr(main, "persist_preview_assets", fake_persist_preview_assets)
    monkeypatch.setattr(main, "_insert_report_version", fake_insert_report_version)
    monkeypatch.setattr(main, "_mark_report_generation", fake_mark_report_generation)

    asyncio.run(main.generate_report_deck("report_1", _payload()))

    assert calls == [
        ("dify", None),
        ("persist", "report_1"),
        ("version", "dify"),
        ("mark", ("report_1", "ready", None, "rv_1")),
    ]


def test_generate_report_deck_marks_report_failed(monkeypatch):
    marks: list[tuple[str, str, str | None, str | None]] = []

    async def fake_create_deck_from_dify(**_kwargs):
        raise RuntimeError("Dify timeout")

    def fake_mark_report_generation(report_id, status, error, version_id):
        marks.append((report_id, status, error, version_id))

    monkeypatch.setattr(main, "create_deck_from_dify", fake_create_deck_from_dify)
    monkeypatch.setattr(main, "_mark_report_generation", fake_mark_report_generation)

    asyncio.run(main.generate_report_deck("report_1", _payload()))

    assert marks == [("report_1", "failed", "Dify timeout", None)]


def test_report_patch_accepts_legacy_frontend_source():
    patch = ReportPatch(
        deck_json={"slides": []},
        source="ppt2video_frontend_editor",
    )

    assert patch.source == "ppt2video_frontend_editor"


def test_start_video_job_ensures_slide_images(monkeypatch):
    calls: list[tuple[str, object]] = []

    def fake_report_row(report_id):
        return {"id": report_id, "current_version_id": "rv_1"}

    def fake_version_row(version_id):
        return {
            "id": version_id,
            "deck_json": {"slides": [{"slide_type": "cover", "speaker_notes": "ok"}]},
        }

    def fake_ensure_slide_images(deck_json, report_id):
        calls.append(("ensure", report_id))
        return {
            "slides": [
                {
                    "slide_type": "cover",
                    "speaker_notes": "ok",
                    "image_url": "https://backend.example/assets/previews/report_1/slide_0_generated.png",
                }
            ]
        }

    def fake_create_video_job(report_id, version_id, deck_json):
        calls.append(("create", deck_json["slides"][0]["image_url"]))
        return "job_1"

    def fake_get_video_job(job_id):
        return VideoJobOut(
            id=job_id,
            report_id="report_1",
            report_version_id="rv_1",
            status="queued",
            progress=0,
            total=1,
            completed=0,
            final_video_url=None,
            error=None,
        )

    monkeypatch.setattr(main, "_report_row", fake_report_row)
    monkeypatch.setattr(main, "_version_row", fake_version_row)
    monkeypatch.setattr(main, "ensure_slide_images", fake_ensure_slide_images)
    monkeypatch.setattr(main.db, "loads", lambda value: value)
    monkeypatch.setattr(main, "create_video_job", fake_create_video_job)
    monkeypatch.setattr(main, "_get_video_job_or_404", fake_get_video_job)

    job = main.start_video_job("report_1", VideoJobCreate(), BackgroundTasks())

    assert job.id == "job_1"
    assert calls == [
        ("ensure", "report_1"),
        ("create", "https://backend.example/assets/previews/report_1/slide_0_generated.png"),
    ]
