from typing import Any, Literal

from pydantic import BaseModel, Field


class ReportCreate(BaseModel):
    reporter_name: str = Field(min_length=1)
    report_period: str = Field(min_length=1)
    report_date: str = Field(min_length=1)
    raw_content: str = ""
    deck_json: dict[str, Any] | None = None
    preview_images: list[dict[str, Any]] = Field(default_factory=list)


class ReportPatch(BaseModel):
    deck_json: dict[str, Any]
    preview_images: list[dict[str, Any]] = Field(default_factory=list)
    source: Literal["frontend_edit", "dify_revision"] = "frontend_edit"


class ReportVersionOut(BaseModel):
    id: str
    version: int
    deck_json: dict[str, Any]
    source: str
    created_at: str


class ReportOut(BaseModel):
    id: str
    reporter_name: str
    report_period: str
    report_date: str
    raw_content: str
    current_version: ReportVersionOut | None
    created_at: str
    updated_at: str


class VideoJobCreate(BaseModel):
    report_version_id: str | None = None


class VideoJobOut(BaseModel):
    id: str
    report_id: str
    report_version_id: str
    status: str
    progress: int
    total: int
    completed: int
    final_video_url: str | None
    error: str | None


class VideoJobItemOut(BaseModel):
    slide_index: int
    slide_type: str
    status: str
    task_id: str | None
    video_url: str | None
    error: str | None
    attempts: int


class VideoJobDetailOut(VideoJobOut):
    items: list[VideoJobItemOut]
