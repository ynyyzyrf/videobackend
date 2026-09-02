# Weekly Report Backend

This backend is the middle layer for `ppt2video`:

```text
DaoStore frontend
  -> Weekly Report Backend
  -> Dify AI-only app + Video Composer + FFmpeg
```

## Responsibility Split

DaoStore frontend:
- Collect reporter, period, date, and raw weekly report text.
- Render and edit `deck_json` and `speaker_notes`.
- Start video generation.
- Poll job progress and show `final_video_url`.

This backend:
- Persist reports, deck versions, video jobs, and per-slide video tasks.
- Call Dify for AI-only work: raw text to `deck_json`, revisions, and `speaker_notes`.
- Submit each slide to Video Composer.
- Poll Video Composer, retry failed slide tasks, preserve slide order.
- Call FFmpeg after all slide videos succeed.
- Return stable job status to the frontend.

Dify:
- AI-only. It should not poll video tasks or run FFmpeg in the production split.

## API

```http
POST /reports
GET /reports/{report_id}
PATCH /reports/{report_id}
POST /reports/{report_id}/video
GET /video-jobs/{job_id}
```

## Local Run

```powershell
cd C:\Users\ynyyzyrf\Desktop\video\_backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy .env.example .env
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8010
```

## Zeabur

Deploy from the GitHub repository. The service command is defined in `Procfile`:

```text
uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8010}
```

Zeabur should expose the platform-provided HTTP port. The app listens on `PORT`
inside the container and falls back to `8010` only for local/manual runs.

Required environment variables:

```text
DATABASE_URL=
BACKEND_API_KEY=
DIFY_API_KEY=
VIDEO_COMPOSER_API_KEY=
FFMPEG_API_KEY=
```

## MySQL

This backend is configured for MySQL, not SQLite.

```sql
CREATE DATABASE weekly_report CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'weekly_report'@'%' IDENTIFIED BY 'change_me';
GRANT ALL PRIVILEGES ON weekly_report.* TO 'weekly_report'@'%';
FLUSH PRIVILEGES;
```

Set `DATABASE_URL` in `.env`:

```text
DATABASE_URL=mysql+pymysql://weekly_report:change_me@127.0.0.1:3306/weekly_report?charset=utf8mb4
```

## API Key Auth

All business endpoints require:

```text
X-API-Key: <BACKEND_API_KEY>
```

`GET /health` is intentionally left open for deployment health checks.

Without API keys, `POST /reports` can still create a report if `deck_json` is provided directly, but Dify/video generation calls will fail clearly instead of pretending success.
