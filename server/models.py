from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


Status = Literal["reading", "read", "archived", "todo"]
TaskStatus = Literal["pending", "researching", "generating", "completed", "failed"]
QueueStatus = Literal["queued", "pending", "researching", "generating", "completed", "failed", "cancelled"]


class PaperBase(BaseModel):
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9\-]*$")
    title: str | None = None
    authors: str | None = ""
    institution: str | None = ""
    year: int | None = None
    arxiv_id: str | None = None
    pdf_url: str | None = None
    arxiv_url: str | None = None
    github_url: str | None = None
    project_url: str | None = None
    openreview_url: str | None = None
    conference: str | None = None
    accept_status: str | None = None
    one_line_summary: str | None = None
    research_question: str | None = None
    core_method: str | None = None
    main_result: str | None = None
    target_audience: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: Status | None = "reading"
    rating: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = None
    date_published: str | None = None
    folder_id: int | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def normalize_tags(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [x.strip() for x in value.split(",") if x.strip()]
        return value


class PaperCreate(PaperBase):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]*$")
    title: str
    status: Status = "reading"


class PaperUpdate(PaperBase):
    pass


class PaperResponse(PaperBase):
    id: int
    slug: str
    title: str
    authors: str = ""
    institution: str = ""
    tags: list[str] = Field(default_factory=list)
    status: Status = "reading"
    date_added: str
    date_updated: str
    summary_html_exists: int = 0
    html_enriched: int = 0


class PaperListResponse(BaseModel):
    items: list[PaperResponse]
    total: int
    limit: int
    offset: int


class IdsRequest(BaseModel):
    ids: list[int]


class BatchStatusRequest(IdsRequest):
    status: Status


class BatchTagsRequest(IdsRequest):
    tags: list[str]


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    color: str = Field(default="#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")
    parent_id: int | None = None


class FolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    parent_id: int | None = None


class FolderMoveRequest(BaseModel):
    ids: list[int]
    folder_id: int | None = None


class ResearchRequest(BaseModel):
    paper_name: str = Field(min_length=1)
    pdf_url: str | None = None


class ResearchLogRequest(BaseModel):
    text: str = Field(min_length=1)
    type: Literal["info", "search", "reading", "generating", "system", "error"] = "info"
    status: TaskStatus | None = None


class ResearchCompleteRequest(BaseModel):
    html: str
    slug: str | None = None
    title: str | None = None


class ResearchFailRequest(BaseModel):
    error: str


class BatchResearchItem(BaseModel):
    paper_name: str = Field(min_length=1)
    pdf_url: str | None = None


class BatchResearchRequest(BaseModel):
    papers: list[BatchResearchItem] = Field(min_length=1, max_length=50)


class ReorderRequest(BaseModel):
    direction: Literal["up", "down"]
