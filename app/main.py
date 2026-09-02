import uuid
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles

from . import db
from .asset_store import ensure_slide_images, persist_preview_assets
from .config import get_settings
from .dify_client import create_deck_from_dify, revise_deck_from_dify
from .orchestrator import create_video_job, run_video_job, utc_now
from .schemas import (
    ReportCreate,
    ReportOut,
    ReportPatch,
    ReportRevisionCreate,
    ReportVersionOut,
    VideoJobCreate,
    VideoJobDetailOut,
    VideoJobItemOut,
    VideoJobOut,
)

app = FastAPI(title="Weekly Report Backend", version="0.1.0")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
startup_db_error: str | None = None
Path(get_settings().asset_storage_dir).mkdir(parents=True, exist_ok=True)
app.mount("/assets", StaticFiles(directory=get_settings().asset_storage_dir), name="assets")


def ensure_db_ready() -> None:
    try:
        db.init_db()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database is not ready.") from exc


def require_api_key(api_key: str | None = Depends(api_key_header)) -> None:
    expected = get_settings().backend_api_key
    if not expected:
        raise HTTPException(status_code=503, detail="BACKEND_API_KEY is not configured.")
    if api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key.")


@app.on_event("startup")
def startup() -> None:
    global startup_db_error
    try:
        db.init_db()
        startup_db_error = None
    except Exception as exc:
        startup_db_error = str(exc)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "database": "ok" if startup_db_error is None else "not_ready"}


@app.post(
    "/reports",
    response_model=ReportOut,
    dependencies=[Depends(require_api_key), Depends(ensure_db_ready)],
)
async def create_report(payload: ReportCreate, background_tasks: BackgroundTasks) -> ReportOut:
    report_id = "report_" + uuid.uuid4().hex
    now = utc_now()
    deck_json = payload.deck_json

    if deck_json is None:
        _insert_report_shell(report_id, payload, now, "generating")
        background_tasks.add_task(generate_report_deck, report_id, payload)
        return _get_report_or_404(report_id)

    try:
        deck_json, _ = await persist_preview_assets(deck_json, payload.preview_images, report_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Preview asset persistence failed.") from exc
    version_id = _insert_report_with_version(report_id, payload, deck_json, "frontend", now)
    return _get_report_or_404(report_id, version_id)


async def generate_report_deck(report_id: str, payload: ReportCreate) -> None:
    try:
        deck_json = await create_deck_from_dify(
            reporter_name=payload.reporter_name,
            report_period=payload.report_period,
            report_date=payload.report_date,
            raw_content=payload.raw_content,
            user_id=report_id,
        )
        deck_json, _ = await persist_preview_assets(deck_json, payload.preview_images, report_id)
        version_id = _insert_report_version(report_id, deck_json, "dify")
        _mark_report_generation(report_id, "ready", None, version_id)
    except Exception as exc:
        _mark_report_generation(report_id, "failed", str(exc), None)


@app.get(
    "/reports/{report_id}",
    response_model=ReportOut,
    dependencies=[Depends(require_api_key), Depends(ensure_db_ready)],
)
def get_report(report_id: str) -> ReportOut:
    return _get_report_or_404(report_id)


@app.patch(
    "/reports/{report_id}",
    response_model=ReportOut,
    dependencies=[Depends(require_api_key), Depends(ensure_db_ready)],
)
async def patch_report(report_id: str, payload: ReportPatch) -> ReportOut:
    report = db.fetchone("SELECT * FROM reports WHERE id = %s", (report_id,))
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")

    row = db.fetchone(
        "SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM report_versions WHERE report_id = %s",
        (report_id,),
    )
    version = int((row or {}).get("next_version") or 1)
    version_id = "rv_" + uuid.uuid4().hex
    try:
        deck_json, _ = await persist_preview_assets(payload.deck_json, payload.preview_images, report_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Preview asset persistence failed.") from exc

    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO report_versions (id, report_id, version, deck_json, source)
                VALUES (%s, %s, %s, CAST(%s AS JSON), %s)
                """,
                (version_id, report_id, version, db.dumps(deck_json), payload.source),
            )
            cursor.execute(
                "UPDATE reports SET current_version_id = %s, updated_at = %s WHERE id = %s",
                (version_id, utc_now(), report_id),
            )

    return _get_report_or_404(report_id, version_id)


@app.post(
    "/reports/{report_id}/revision",
    response_model=ReportOut,
    dependencies=[Depends(require_api_key), Depends(ensure_db_ready)],
)
def revise_report(
    report_id: str,
    payload: ReportRevisionCreate,
    background_tasks: BackgroundTasks,
) -> ReportOut:
    report = _report_row(report_id)
    version_id = report["current_version_id"]
    if not version_id:
        raise HTTPException(status_code=409, detail="Report deck is still generating.")

    base_deck_json = payload.deck_json
    if base_deck_json is None:
        version = _version_row(str(version_id))
        base_deck_json = db.loads(version["deck_json"])

    _mark_report_generation(report_id, "revising", None, None)
    background_tasks.add_task(
        generate_report_revision,
        report_id,
        payload,
        base_deck_json,
    )
    return _get_report_or_404(report_id)


async def generate_report_revision(
    report_id: str,
    payload: ReportRevisionCreate,
    base_deck_json: dict[str, Any],
) -> None:
    try:
        report = _report_row(report_id)
        deck_json = await revise_deck_from_dify(
            reporter_name=str(report["reporter_name"]),
            report_period=str(report["report_period"]),
            report_date=str(report["report_date"]),
            current_deck_json=base_deck_json,
            revision_note=payload.revision_note,
            user_id=report_id,
        )
        deck_json, _ = await persist_preview_assets(deck_json, payload.preview_images, report_id)
        version_id = _insert_report_version(report_id, deck_json, "dify_revision")
        _mark_report_generation(report_id, "ready", None, version_id)
    except Exception as exc:
        _mark_report_generation(report_id, "failed", str(exc), None)


@app.post(
    "/reports/{report_id}/video",
    response_model=VideoJobOut,
    dependencies=[Depends(require_api_key), Depends(ensure_db_ready)],
)
def start_video_job(
    report_id: str,
    payload: VideoJobCreate,
    background_tasks: BackgroundTasks,
) -> VideoJobOut:
    report = _report_row(report_id)
    version_id = payload.report_version_id or report["current_version_id"]
    if not version_id:
        raise HTTPException(status_code=409, detail="Report deck is still generating.")
    version = _version_row(str(version_id))
    deck_json = ensure_slide_images(db.loads(version["deck_json"]), report_id)

    try:
        job_id = create_video_job(report_id, str(version_id), deck_json)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    background_tasks.add_task(run_video_job, job_id)
    return _get_video_job_or_404(job_id)


@app.get(
    "/video-jobs/{job_id}",
    response_model=VideoJobDetailOut,
    dependencies=[Depends(require_api_key), Depends(ensure_db_ready)],
)
def get_video_job(job_id: str) -> VideoJobDetailOut:
    return _get_video_job_or_404(job_id, include_items=True)


def _insert_report_with_version(
    report_id: str,
    payload: ReportCreate,
    deck_json: dict[str, Any],
    source: str,
    now: str,
) -> str:
    version_id = "rv_" + uuid.uuid4().hex
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO reports
                (
                    id,
                    reporter_name,
                    report_period,
                    report_date,
                    raw_content,
                    current_version_id,
                    generation_status,
                    generation_error,
                    created_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'ready', NULL, %s, %s)
                """,
                (
                    report_id,
                    payload.reporter_name,
                    payload.report_period,
                    payload.report_date,
                    payload.raw_content,
                    version_id,
                    now,
                    now,
                ),
            )
            cursor.execute(
                """
                INSERT INTO report_versions (id, report_id, version, deck_json, source, created_at)
                VALUES (%s, %s, 1, CAST(%s AS JSON), %s, %s)
                """,
                (version_id, report_id, db.dumps(deck_json), source, now),
            )
    return version_id


def _insert_report_shell(
    report_id: str,
    payload: ReportCreate,
    now: str,
    status: str,
) -> None:
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO reports
                (
                    id,
                    reporter_name,
                    report_period,
                    report_date,
                    raw_content,
                    current_version_id,
                    generation_status,
                    generation_error,
                    created_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, NULL, %s, NULL, %s, %s)
                """,
                (
                    report_id,
                    payload.reporter_name,
                    payload.report_period,
                    payload.report_date,
                    payload.raw_content,
                    status,
                    now,
                    now,
                ),
            )


def _insert_report_version(report_id: str, deck_json: dict[str, Any], source: str) -> str:
    row = db.fetchone(
        "SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM report_versions WHERE report_id = %s",
        (report_id,),
    )
    version = int((row or {}).get("next_version") or 1)
    version_id = "rv_" + uuid.uuid4().hex
    with db.connect() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO report_versions (id, report_id, version, deck_json, source, created_at)
                VALUES (%s, %s, %s, CAST(%s AS JSON), %s, %s)
                """,
                (version_id, report_id, version, db.dumps(deck_json), source, utc_now()),
            )
    return version_id


def _mark_report_generation(
    report_id: str,
    status: str,
    error: str | None,
    version_id: str | None,
) -> None:
    if version_id:
        db.execute(
            """
            UPDATE reports
            SET generation_status = %s,
                generation_error = %s,
                current_version_id = %s,
                updated_at = %s
            WHERE id = %s
            """,
            (status, error, version_id, utc_now(), report_id),
        )
        return
    db.execute(
        """
        UPDATE reports
        SET generation_status = %s,
            generation_error = %s,
            updated_at = %s
        WHERE id = %s
        """,
        (status, error, utc_now(), report_id),
    )


def _report_row(report_id: str) -> dict[str, Any]:
    report = db.fetchone("SELECT * FROM reports WHERE id = %s", (report_id,))
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")
    return report


def _version_row(version_id: str) -> dict[str, Any]:
    version = db.fetchone("SELECT * FROM report_versions WHERE id = %s", (version_id,))
    if not version:
        raise HTTPException(status_code=404, detail="Report version not found.")
    return version


def _get_report_or_404(report_id: str, version_id: str | None = None) -> ReportOut:
    report = db.fetchone("SELECT * FROM reports WHERE id = %s", (report_id,))
    if not report:
        raise HTTPException(status_code=404, detail="Report not found.")

    target_version_id = version_id or report["current_version_id"]
    version = (
        db.fetchone("SELECT * FROM report_versions WHERE id = %s", (str(target_version_id),))
        if target_version_id
        else None
    )

    current_version = None
    if version:
        current_version = ReportVersionOut(
            id=version["id"],
            version=int(version["version"]),
            deck_json=db.loads(version["deck_json"]),
            source=version["source"],
            created_at=str(version["created_at"]),
        )
    return ReportOut(
        id=report["id"],
        reporter_name=report["reporter_name"],
        report_period=report["report_period"],
        report_date=report["report_date"],
        raw_content=report["raw_content"],
        status=report.get("generation_status") or "ready",
        error=report.get("generation_error"),
        current_version=current_version,
        created_at=str(report["created_at"]),
        updated_at=str(report["updated_at"]),
    )


def _get_video_job_or_404(job_id: str, include_items: bool = False) -> Any:
    job = db.fetchone("SELECT * FROM video_jobs WHERE id = %s", (job_id,))
    if not job:
        raise HTTPException(status_code=404, detail="Video job not found.")

    base = {
        "id": job["id"],
        "report_id": job["report_id"],
        "report_version_id": job["report_version_id"],
        "status": job["status"],
        "progress": int(job["progress"]),
        "total": int(job["total"]),
        "completed": int(job["completed"]),
        "final_video_url": job["final_video_url"],
        "error": job["error"],
    }
    if not include_items:
        return VideoJobOut(**base)

    rows = db.fetchall(
        "SELECT * FROM video_job_items WHERE job_id = %s ORDER BY slide_index",
        (job_id,),
    )
    return VideoJobDetailOut(
        **base,
        items=[
            VideoJobItemOut(
                slide_index=int(row["slide_index"]),
                slide_type=row["slide_type"],
                status=row["status"],
                task_id=row["task_id"],
                video_url=row["video_url"],
                error=row["error"],
                attempts=int(row["attempts"]),
            )
            for row in rows
        ],
    )
