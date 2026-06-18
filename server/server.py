from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db, research, worker
from .models import (
    BatchResearchRequest,
    BatchStatusRequest,
    BatchTagsRequest,
    FolderCreate,
    FolderMoveRequest,
    FolderUpdate,
    IdsRequest,
    PaperCreate,
    PaperListResponse,
    PaperResponse,
    PaperUpdate,
    ReorderRequest,
    ResearchCompleteRequest,
    ResearchFailRequest,
    ResearchLogRequest,
    ResearchRequest,
)
from .paths import FRONTEND_DIR, PAPERS_DIR, ensure_dirs


app = FastAPI(title="Paper Reading Manager")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    ensure_dirs()
    db.init_db()
    worker.start_worker()


def _paper_or_404(paper_id: int) -> dict:
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "Paper not found")
    return paper


def _enrich_if_needed(paper: dict) -> dict:
    if paper.get("summary_html_exists") and not paper.get("html_enriched"):
        path = PAPERS_DIR / f"{paper['slug']}.html"
        if path.exists():
            parsed = db.parse_html_metadata(path.read_text(encoding="utf-8", errors="replace"))
            payload = {k: v for k, v in parsed.items() if v and k != "tags"}
            payload["html_enriched"] = 1
            paper = db.update_paper(paper["id"], payload) or paper
    return paper


@app.get("/api/stats")
def api_stats():
    return db.stats()


@app.get("/api/papers", response_model=PaperListResponse)
def api_list_papers(
    status: str | None = None,
    tag: str | None = None,
    year: int | None = None,
    folder_id: int | None = None,
    search: str | None = None,
    sort: str = "date_added",
    order: str = "desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    items, total = db.list_papers(locals())
    items = [_enrich_if_needed(i) for i in items]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/papers/{paper_id}", response_model=PaperResponse)
def api_get_paper(paper_id: int):
    return _enrich_if_needed(_paper_or_404(paper_id))


@app.post("/api/papers", response_model=PaperResponse)
def api_create_paper(payload: PaperCreate):
    try:
        return db.create_paper(payload.model_dump())
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.put("/api/papers/{paper_id}", response_model=PaperResponse)
def api_update_paper(paper_id: int, payload: PaperUpdate):
    paper = db.update_paper(paper_id, payload.model_dump(exclude_unset=True))
    if not paper:
        raise HTTPException(404, "Paper not found")
    return paper


@app.delete("/api/papers/{paper_id}")
def api_delete_paper(paper_id: int):
    if not db.delete_paper(paper_id):
        raise HTTPException(404, "Paper not found")
    return {"ok": True}


@app.post("/api/papers/batch/status")
def api_batch_status(payload: BatchStatusRequest):
    return {"ok": True, "count": db.batch_update_status(payload.ids, payload.status)}


@app.post("/api/papers/batch/delete")
def api_batch_delete(payload: IdsRequest):
    count = 0
    for paper_id in payload.ids:
        count += int(db.delete_paper(paper_id))
    return {"ok": True, "count": count}


@app.post("/api/papers/batch/tags")
def api_batch_tags(payload: BatchTagsRequest):
    return {"ok": True, "count": db.batch_add_tags(payload.ids, payload.tags)}


@app.get("/api/folders")
def api_list_folders():
    return db.list_folders()


@app.post("/api/folders")
def api_create_folder(payload: FolderCreate):
    return db.create_folder(payload.model_dump())


@app.put("/api/folders/{folder_id}")
def api_update_folder(folder_id: int, payload: FolderUpdate):
    try:
        folder = db.update_folder(folder_id, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not folder:
        raise HTTPException(404, "Folder not found")
    return folder


@app.delete("/api/folders/{folder_id}")
def api_delete_folder(folder_id: int):
    db.delete_folder(folder_id)
    return {"ok": True}


@app.post("/api/folders/move")
def api_move_folder(payload: FolderMoveRequest):
    return {"ok": True, "count": db.move_papers(payload.ids, payload.folder_id)}


@app.get("/api/papers/{paper_id}/summary")
def api_get_summary(paper_id: int):
    paper = _paper_or_404(paper_id)
    path = PAPERS_DIR / f"{paper['slug']}.html"
    if not path.exists():
        raise HTTPException(404, "Summary HTML not found")
    return HTMLResponse(path.read_text(encoding="utf-8", errors="replace"))


@app.put("/api/papers/{paper_id}/summary")
async def api_put_summary(paper_id: int, file: UploadFile = File(...)):
    paper = _paper_or_404(paper_id)
    html = (await file.read()).decode("utf-8", errors="replace")
    (PAPERS_DIR / f"{paper['slug']}.html").write_text(html, encoding="utf-8")
    parsed = db.parse_html_metadata(html)
    updated = db.update_paper(paper_id, {k: v for k, v in parsed.items() if v and k != "tags"} | {"summary_html_exists": 1, "html_enriched": 1})
    return {"ok": True, "paper": updated, "parsed": parsed}


@app.get("/api/papers/{paper_id}/export-html")
def api_export_html(paper_id: int):
    paper = _paper_or_404(paper_id)
    path = PAPERS_DIR / f"{paper['slug']}.html"
    if not path.exists():
        raise HTTPException(404, "Summary HTML not found")
    html = _clean_html_for_sharing(path.read_text(encoding="utf-8", errors="replace"))
    return Response(
        html,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{paper["slug"]}.html"'},
    )


@app.post("/api/upload-summary")
async def api_upload_summary(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith((".html", ".htm")):
        raise HTTPException(400, "Only HTML files are supported")
    html = (await file.read()).decode("utf-8", errors="replace")
    preferred = db.slug_from_title(Path(file.filename).stem)
    return db.upsert_from_html(PAPERS_DIR / file.filename, html, preferred_slug=preferred)


@app.post("/api/scan-orphan-html")
def api_scan_orphan_html():
    imported = []
    for path in PAPERS_DIR.parent.glob("*.html"):
        html = path.read_text(encoding="utf-8", errors="replace")
        result = db.upsert_from_html(path, html)
        imported.append(result)
        path.unlink(missing_ok=True)
    return {"ok": True, "imported": imported}


@app.post("/api/sync")
def api_sync():
    imported = []
    with db.connect() as con:
        known = {r["slug"] for r in con.execute("SELECT slug FROM papers")}
    for path in PAPERS_DIR.glob("*.html"):
        if path.stem not in known:
            imported.append(db.upsert_from_html(path, path.read_text(encoding="utf-8", errors="replace"), preferred_slug=path.stem))
        else:
            paper = db.get_paper_by_slug(path.stem)
            if paper:
                db.update_paper(paper["id"], {"summary_html_exists": 1})
    return {"ok": True, "imported": imported}


@app.get("/api/tags")
def api_tags():
    return db.list_tags()


@app.get("/api/years")
def api_years():
    return db.list_years()


@app.get("/api/repo")
def api_repo():
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(PAPERS_DIR.parent),
            capture_output=True,
            text=True,
            timeout=3,
        )
        url = result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        url = ""
    return {"url": url}


@app.post("/api/research/start")
def api_research_start(payload: ResearchRequest):
    try:
        return research.create_task(payload.paper_name, payload.pdf_url)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/research/pending")
def api_research_pending():
    return research.pending_tasks()


@app.get("/api/research/active")
def api_research_active():
    return research.active_tasks()


@app.get("/api/research/{task_id}")
def api_research_get(task_id: str):
    task = research.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@app.post("/api/research/{task_id}/log")
def api_research_log(task_id: str, payload: ResearchLogRequest):
    task = research.log_task(task_id, payload.text, payload.type, payload.status)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@app.post("/api/research/{task_id}/complete")
def api_research_complete(task_id: str, payload: ResearchCompleteRequest):
    html = payload.html
    parsed = db.parse_html_metadata(html)
    slug = payload.slug or db.slug_from_title(payload.title or parsed.get("title") or "paper", parsed.get("year"))
    result = db.upsert_from_html(PAPERS_DIR / f"{slug}.html", html, preferred_slug=slug)
    task = research.complete_task(task_id, result)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@app.post("/api/research/{task_id}/fail")
def api_research_fail(task_id: str, payload: ResearchFailRequest):
    task = research.fail_task(task_id, payload.error)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@app.post("/api/research/batch/start")
def api_batch_start(payload: BatchResearchRequest):
    return research.start_batch([p.model_dump() for p in payload.papers])


@app.get("/api/research/batch/active")
def api_batch_active():
    return research.active_batch()


@app.get("/api/research/batch/latest")
def api_batch_latest():
    return research.latest_batch()


@app.get("/api/research/batch/{batch_id}")
def api_batch_get(batch_id: str):
    if batch_id != research.QUEUE_ID:
        raise HTTPException(404, "Batch not found")
    return research.get_batch()


@app.post("/api/research/batch/{batch_id}/cancel")
def api_batch_cancel(batch_id: str):
    return research.cancel_batch()


@app.post("/api/research/batch/{batch_id}/add")
def api_batch_add(batch_id: str, payload: BatchResearchRequest):
    return research.start_batch([p.model_dump() for p in payload.papers])


@app.post("/api/research/batch/{batch_id}/items/{task_id}/remove")
def api_batch_remove(batch_id: str, task_id: str):
    return research.remove_item(task_id)


@app.post("/api/research/batch/{batch_id}/items/{task_id}/reorder")
def api_batch_reorder(batch_id: str, task_id: str, payload: ReorderRequest):
    return research.reorder_item(task_id, payload.direction)


@app.post("/api/research/batch/{batch_id}/items/{task_id}/stop")
def api_batch_stop(batch_id: str, task_id: str):
    return research.stop_item(task_id)


@app.post("/api/research/batch/{batch_id}/items/{task_id}/retry")
def api_batch_retry(batch_id: str, task_id: str):
    return research.retry_item(task_id)


@app.get("/read/{slug}")
def read_summary(slug: str, embed: int = 0):
    path = PAPERS_DIR / f"{slug}.html"
    if not path.exists():
        raise HTTPException(404, "Summary not found")
    html = path.read_text(encoding="utf-8", errors="replace")
    if embed:
        html = _embed_html(html)
    html = _inject_back_button(html, embed=bool(embed))
    return HTMLResponse(html)


@app.get("/")
def index():
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(500, f"Frontend not found: {FRONTEND_DIR}")
    return FileResponse(index_path)


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


def _inject_back_button(html: str, embed: bool = False) -> str:
    if "data-paper-manager-back" in html:
        return html
    css = "position:fixed;top:14px;left:14px;z-index:99999;padding:9px 13px;border-radius:999px;background:#111827;color:#fff;text-decoration:none;font:14px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;box-shadow:0 10px 30px rgba(15,23,42,.18)"
    label = "返回主界面"
    button = f'<a data-paper-manager-back href="/" style="{css}">← {label}</a>'
    return re.sub(r"(<body[^>]*>)", r"\1" + button, html, count=1, flags=re.I) if re.search(r"<body[^>]*>", html, re.I) else button + html


def _embed_html(html: str) -> str:
    html = re.sub(r"<nav[^>]+class=[\"'][^\"']*nav-sidebar[^\"']*[\"'][\s\S]*?</nav>", "", html, flags=re.I)
    html = re.sub(r"margin-left\s*:\s*var\(--nav-width\)\s*;?", "", html, flags=re.I)
    html = re.sub(r"padding-left\s*:\s*var\(--nav-width\)\s*;?", "", html, flags=re.I)
    return html


def _clean_html_for_sharing(html: str) -> str:
    html = re.sub(r"<a[^>]+data-paper-manager-back[\s\S]*?</a>", "", html, flags=re.I)
    return html
