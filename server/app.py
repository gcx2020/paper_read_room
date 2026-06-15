from __future__ import annotations

import json
import mimetypes
import os
import re
import shlex
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


ROOT_DIR = Path(__file__).resolve().parent.parent
PUBLIC_DIR = ROOT_DIR / "public"
DATA_DIR = ROOT_DIR / "data"
PAPERS_DIR = ROOT_DIR / "papers"
PAPERS_FILE = DATA_DIR / "papers.json"
JOBS_FILE = DATA_DIR / "jobs.json"

READING_STATUSES = {"unread", "reading", "read", "archived"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    if not PAPERS_FILE.exists():
        write_json(PAPERS_FILE, [])
    if not JOBS_FILE.exists():
        write_json(JOBS_FILE, [])


def read_json(path: Path, fallback):
    ensure_dirs_without_recursion()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def ensure_dirs_without_recursion() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, value) -> None:
    ensure_dirs_without_recursion()
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def slugify(value: str | None) -> str:
    text = (value or "paper").strip().lower()
    text = re.sub(r"[^\w\s.-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-._")
    return text or f"paper-{int(time.time())}"


def listify(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[,，\n;；]+", str(value))
    result: list[str] = []
    seen = set()
    for item in items:
        cleaned = str(item).strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            result.append(cleaned)
            seen.add(key)
    return result


def list_papers() -> list[dict]:
    return read_json(PAPERS_FILE, [])


def save_papers(papers: list[dict]) -> None:
    write_json(PAPERS_FILE, papers)


def list_jobs() -> list[dict]:
    return read_json(JOBS_FILE, [])


def save_jobs(jobs: list[dict]) -> None:
    write_json(JOBS_FILE, jobs)


def unique_slug(title: str) -> str:
    existing = {paper.get("slug") for paper in list_papers()}
    base = slugify(title)
    candidate = base
    index = 2
    while candidate in existing:
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def normalize_paper(payload: dict, existing: dict | None = None) -> dict:
    existing = existing or {}
    title = str(payload.get("title") or existing.get("title") or "").strip()
    slug = slugify(payload.get("slug") or existing.get("slug") or title)
    created_at = existing.get("createdAt") or now_iso()
    reading_status = str(payload.get("readingStatus") or existing.get("readingStatus") or "unread")
    if reading_status not in READING_STATUSES:
        reading_status = "unread"

    return {
        **existing,
        "id": existing.get("id") or slug,
        "slug": slug,
        "title": title,
        "indexId": str(payload.get("indexId") or existing.get("indexId") or "").strip(),
        "pdfUrl": str(payload.get("pdfUrl") or existing.get("pdfUrl") or "").strip(),
        "source": str(payload.get("source") or existing.get("source") or "manual"),
        "status": str(payload.get("status") or existing.get("status") or "draft"),
        "readingStatus": reading_status,
        "paperType": str(payload.get("paperType") or existing.get("paperType") or "").strip(),
        "categories": listify(payload.get("categories", existing.get("categories", []))),
        "keywords": listify(payload.get("keywords", existing.get("keywords", []))),
        "tags": listify(payload.get("tags", existing.get("tags", []))),
        "notes": str(payload.get("notes") if payload.get("notes") is not None else existing.get("notes", "")).strip(),
        "rating": payload.get("rating", existing.get("rating", "")),
        "priority": str(payload.get("priority") or existing.get("priority") or "normal"),
        "htmlPath": str(payload.get("htmlPath") or existing.get("htmlPath") or ""),
        "originalFileName": str(payload.get("originalFileName") or existing.get("originalFileName") or ""),
        "annotations": existing.get("annotations", payload.get("annotations", [])) or [],
        "createdAt": created_at,
        "updatedAt": now_iso(),
        "lastReadAt": payload.get("lastReadAt", existing.get("lastReadAt", "")),
    }


def upsert_paper(payload: dict) -> dict:
    papers = list_papers()
    target_id = payload.get("id") or payload.get("slug")
    target_slug = payload.get("slug")
    index = next(
        (
            i
            for i, paper in enumerate(papers)
            if paper.get("id") == target_id or paper.get("slug") == target_slug
        ),
        -1,
    )
    existing = papers[index] if index >= 0 else None
    paper = normalize_paper(payload, existing)
    if index >= 0:
        papers[index] = paper
    else:
        papers.insert(0, paper)
    save_papers(papers)
    return paper


def find_paper(value: str) -> tuple[int, dict] | tuple[None, None]:
    needle = value.lower()
    for index, paper in enumerate(list_papers()):
        if (
            str(paper.get("id", "")).lower() == needle
            or str(paper.get("slug", "")).lower() == needle
            or str(paper.get("indexId", "")).lower() == needle
        ):
            return index, paper
    return None, None


def filter_papers(params: dict[str, list[str]]) -> list[dict]:
    papers = list_papers()
    query = (params.get("q") or params.get("search") or [""])[0].strip().lower()
    paper_type = (params.get("type") or [""])[0].strip().lower()
    reading_status = (params.get("readingStatus") or [""])[0].strip().lower()
    keyword = (params.get("keyword") or [""])[0].strip().lower()
    category = (params.get("category") or [""])[0].strip().lower()

    def matches(paper: dict) -> bool:
        haystack = " ".join(
            [
                str(paper.get("title", "")),
                str(paper.get("indexId", "")),
                str(paper.get("notes", "")),
                " ".join(paper.get("categories", [])),
                " ".join(paper.get("keywords", [])),
                " ".join(paper.get("tags", [])),
                str(paper.get("paperType", "")),
            ]
        ).lower()
        if query and query not in haystack:
            return False
        if paper_type and str(paper.get("paperType", "")).lower() != paper_type:
            return False
        if reading_status and str(paper.get("readingStatus", "")).lower() != reading_status:
            return False
        if keyword and keyword not in [item.lower() for item in paper.get("keywords", [])]:
            return False
        if category and category not in [item.lower() for item in paper.get("categories", [])]:
            return False
        return True

    return [paper for paper in papers if matches(paper)]


def taxonomy() -> dict:
    papers = list_papers()
    values = {
        "paperTypes": sorted({paper.get("paperType") for paper in papers if paper.get("paperType")}),
        "categories": sorted({item for paper in papers for item in paper.get("categories", [])}),
        "keywords": sorted({item for paper in papers for item in paper.get("keywords", [])}),
        "tags": sorted({item for paper in papers for item in paper.get("tags", [])}),
        "readingStatuses": sorted(READING_STATUSES),
    }
    return values


def stats() -> dict:
    papers = list_papers()
    by_status = {status: 0 for status in sorted(READING_STATUSES)}
    by_type: dict[str, int] = {}
    for paper in papers:
        by_status[paper.get("readingStatus", "unread")] = by_status.get(paper.get("readingStatus", "unread"), 0) + 1
        if paper.get("paperType"):
            by_type[paper["paperType"]] = by_type.get(paper["paperType"], 0) + 1
    return {
        "total": len(papers),
        "ready": len([paper for paper in papers if paper.get("status") == "ready"]),
        "annotations": sum(len(paper.get("annotations", [])) for paper in papers),
        "byReadingStatus": by_status,
        "byType": by_type,
    }


def summarize_jobs(jobs: list[dict], limit: int = 10) -> list[dict]:
    summary = []
    for job in jobs[:limit]:
        summary.append(
            {
                key: value
                for key, value in job.items()
                if key not in {"log"}
            }
        )
    return summary


def add_job(job: dict) -> dict:
    jobs = list_jobs()
    jobs.insert(0, job)
    save_jobs(jobs)
    return job


def update_job(job_id: str, patch: dict) -> dict | None:
    jobs = list_jobs()
    for index, job in enumerate(jobs):
        if job.get("id") == job_id:
            jobs[index] = {**job, **patch, "updatedAt": now_iso()}
            save_jobs(jobs)
            return jobs[index]
    return None


def build_research_prompt(payload: dict, slug: str) -> str:
    agents_path = ROOT_DIR / "AGENTS.md"
    agents = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""
    paper_dir = PAPERS_DIR / slug
    return "\n".join(
        [
            "You are working in this repository as a paper research assistant.",
            "",
            "Follow the repository AGENTS.md requirements exactly. The AGENTS.md content is copied below:",
            "----- AGENTS.md -----",
            agents,
            "----- END AGENTS.md -----",
            "",
            "Research target:",
            f"- Paper title/name: {payload.get('title') or 'Not provided'}",
            f"- Paper index/arXiv/OpenReview/etc.: {payload.get('indexId') or 'Not provided'}",
            f"- PDF or web URL: {payload.get('pdfUrl') or 'Not provided'}",
            f"- Paper type: {payload.get('paperType') or 'Not provided'}",
            f"- Categories: {', '.join(listify(payload.get('categories'))) or 'None'}",
            f"- Keywords: {', '.join(listify(payload.get('keywords'))) or 'None'}",
            f"- User notes: {payload.get('notes') or 'None'}",
            "",
            "Output requirements for this project:",
            f"1. Create the final offline HTML deep-dive at: {paper_dir / 'index.html'}",
            f"2. Put cropped figures/tables/assets under: {paper_dir / 'assets'}",
            "3. Keep all generated paper artifacts under the papers/ directory.",
            "4. Do not invent missing public resources or acceptance information.",
            "5. If the PDF cannot be read or figures cannot be extracted, write a clear failure note instead of a fake final report.",
        ]
    )


def start_codex_research(payload: dict) -> dict:
    slug = unique_slug(payload.get("title") or payload.get("indexId") or "paper")
    paper_dir = PAPERS_DIR / slug
    (paper_dir / "assets").mkdir(parents=True, exist_ok=True)
    job = {
        "id": f"job_{int(time.time() * 1000)}",
        "type": "codex-research",
        "status": "queued",
        "title": payload.get("title", ""),
        "indexId": payload.get("indexId", ""),
        "pdfUrl": payload.get("pdfUrl", ""),
        "slug": slug,
        "outputPath": f"papers/{slug}/index.html",
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "log": "",
    }
    add_job(job)
    thread = threading.Thread(target=run_codex_job, args=(job["id"], payload, slug), daemon=True)
    thread.start()
    return job


def run_codex_job(job_id: str, payload: dict, slug: str) -> None:
    command = os.environ.get("CODEX_COMMAND", "codex")
    args = shlex.split(command) + ["exec", build_research_prompt(payload, slug)]
    update_job(job_id, {"status": "running", "startedAt": now_iso()})
    log_parts: list[str] = []
    try:
        process = subprocess.Popen(
            args,
            cwd=ROOT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        for line in process.stdout:
            log_parts.append(line)
            update_job(job_id, {"log": "".join(log_parts)[-12000:]})
        code = process.wait()
        html_path = PAPERS_DIR / slug / "index.html"
        if code == 0 and html_path.exists():
            upsert_paper(
                {
                    **payload,
                    "id": slug,
                    "slug": slug,
                    "source": "codex",
                    "status": "ready",
                    "htmlPath": f"papers/{slug}/index.html",
                }
            )
            update_job(job_id, {"status": "completed", "finishedAt": now_iso(), "exitCode": code})
        else:
            update_job(
                job_id,
                {
                    "status": "failed",
                    "finishedAt": now_iso(),
                    "exitCode": code,
                    "error": "Codex finished without creating papers/<slug>/index.html.",
                },
            )
    except Exception as error:
        update_job(
            job_id,
            {
                "status": "failed",
                "finishedAt": now_iso(),
                "error": f"Unable to start Codex command: {error}",
                "log": "".join(log_parts)[-12000:],
            },
        )


def parse_multipart(body: bytes, content_type: str) -> dict:
    match = re.search(r"boundary=(?P<boundary>[^;]+)", content_type)
    if not match:
        return {}
    boundary = match.group("boundary").strip('"')
    delimiter = b"--" + boundary.encode()
    result = {}
    for part in body.split(delimiter):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        header_blob, _, content = part.partition(b"\r\n\r\n")
        if not header_blob:
            continue
        headers = header_blob.decode("utf-8", errors="replace")
        name_match = re.search(r'name="([^"]+)"', headers)
        if not name_match:
            continue
        name = name_match.group(1)
        filename_match = re.search(r'filename="([^"]*)"', headers)
        if filename_match:
            result[name] = {
                "filename": Path(filename_match.group(1) or "upload.html").name,
                "content": content.rstrip(b"\r\n"),
            }
        else:
            result[name] = content.decode("utf-8", errors="replace").strip()
    return result


class PaperReadRoomHandler(BaseHTTPRequestHandler):
    server_version = "PaperReadRoomPython/1.0"

    def do_GET(self) -> None:
        self.route()

    def do_POST(self) -> None:
        self.route()

    def do_PATCH(self) -> None:
        self.route()

    def do_DELETE(self) -> None:
        self.route()

    def route(self) -> None:
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith("/api/"):
                self.handle_api(parsed)
                return
            self.serve_static(parsed.path)
        except Exception as error:
            self.send_json(500, {"error": str(error)})

    def handle_api(self, parsed) -> None:
        path = parsed.path
        params = parse_qs(parsed.query)
        method = self.command

        if method == "GET" and path == "/api/papers":
            self.send_json(200, {"papers": filter_papers(params), "jobs": summarize_jobs(list_jobs()), "stats": stats(), "taxonomy": taxonomy()})
            return
        if method == "GET" and path == "/api/jobs":
            self.send_json(200, {"jobs": list_jobs()})
            return
        if method == "GET" and path == "/api/stats":
            self.send_json(200, {"stats": stats()})
            return
        if method == "GET" and path == "/api/taxonomy":
            self.send_json(200, {"taxonomy": taxonomy()})
            return
        if method == "POST" and path == "/api/papers":
            self.create_paper()
            return
        if method == "POST" and path == "/api/upload":
            self.upload_paper()
            return
        if method == "POST" and path == "/api/research":
            self.research_paper()
            return

        paper_match = re.fullmatch(r"/api/papers/([^/]+)", path)
        if paper_match:
            paper_id = unquote(paper_match.group(1))
            if method == "GET":
                self.get_paper(paper_id)
                return
            if method == "PATCH":
                self.update_paper(paper_id)
                return
            if method == "DELETE":
                self.delete_paper(paper_id)
                return

        annotation_match = re.fullmatch(r"/api/papers/([^/]+)/annotations(?:/([^/]+))?", path)
        if annotation_match:
            paper_id = unquote(annotation_match.group(1))
            annotation_id = unquote(annotation_match.group(2)) if annotation_match.group(2) else None
            if method == "GET":
                self.list_annotations(paper_id)
                return
            if method == "POST" and not annotation_id:
                self.create_annotation(paper_id)
                return
            if method == "PATCH" and annotation_id:
                self.update_annotation(paper_id, annotation_id)
                return
            if method == "DELETE" and annotation_id:
                self.delete_annotation(paper_id, annotation_id)
                return

        self.send_json(404, {"error": "API route not found"})

    def create_paper(self) -> None:
        payload = self.read_json_body()
        title = str(payload.get("title", "")).strip()
        if not title:
            self.send_json(400, {"error": "title is required"})
            return
        slug = slugify(payload.get("slug")) if payload.get("slug") else unique_slug(title)
        paper = upsert_paper({**payload, "id": slug, "slug": slug, "source": "manual"})
        self.send_json(201, {"paper": paper})

    def get_paper(self, paper_id: str) -> None:
        _, paper = find_paper(paper_id)
        if not paper:
            self.send_json(404, {"error": "Paper not found"})
            return
        self.send_json(200, {"paper": paper})

    def update_paper(self, paper_id: str) -> None:
        index, paper = find_paper(paper_id)
        if paper is None:
            self.send_json(404, {"error": "Paper not found"})
            return
        payload = self.read_json_body()
        updated = normalize_paper({**paper, **payload}, paper)
        papers = list_papers()
        papers[index] = updated
        save_papers(papers)
        self.send_json(200, {"paper": updated})

    def delete_paper(self, paper_id: str) -> None:
        index, paper = find_paper(paper_id)
        if paper is None:
            self.send_json(404, {"error": "Paper not found"})
            return
        papers = list_papers()
        papers.pop(index)
        save_papers(papers)
        self.send_json(200, {"deleted": paper.get("id")})

    def upload_paper(self) -> None:
        content_type = self.headers.get("content-type", "")
        form = parse_multipart(self.read_body(), content_type)
        title = str(form.get("title", "")).strip()
        file_item = form.get("file")
        if not title or not isinstance(file_item, dict) or not file_item.get("content"):
            self.send_json(400, {"error": "title and html file are required"})
            return
        slug = slugify(form.get("slug")) if form.get("slug") else unique_slug(title)
        paper_dir = PAPERS_DIR / slug
        (paper_dir / "assets").mkdir(parents=True, exist_ok=True)
        (paper_dir / "index.html").write_bytes(file_item["content"])
        paper = upsert_paper(
            {
                **form,
                "id": slug,
                "slug": slug,
                "title": title,
                "source": "upload",
                "status": "ready",
                "htmlPath": f"papers/{slug}/index.html",
                "originalFileName": file_item.get("filename", ""),
            }
        )
        self.send_json(201, {"paper": paper})

    def research_paper(self) -> None:
        payload = self.read_json_body()
        if not payload.get("title") and not payload.get("indexId") and not payload.get("pdfUrl"):
            self.send_json(400, {"error": "Provide at least a paper title, index, or URL."})
            return
        job = start_codex_research(payload)
        self.send_json(202, {"job": job})

    def list_annotations(self, paper_id: str) -> None:
        _, paper = find_paper(paper_id)
        if paper is None:
            self.send_json(404, {"error": "Paper not found"})
            return
        self.send_json(200, {"annotations": paper.get("annotations", [])})

    def create_annotation(self, paper_id: str) -> None:
        index, paper = find_paper(paper_id)
        if paper is None:
            self.send_json(404, {"error": "Paper not found"})
            return
        payload = self.read_json_body()
        text = str(payload.get("text", "")).strip()
        if not text:
            self.send_json(400, {"error": "annotation text is required"})
            return
        annotation = {
            "id": f"ann_{int(time.time() * 1000)}",
            "text": text,
            "quote": str(payload.get("quote", "")).strip(),
            "section": str(payload.get("section", "")).strip(),
            "page": str(payload.get("page", "")).strip(),
            "color": str(payload.get("color", "teal")).strip(),
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
        }
        paper.setdefault("annotations", []).insert(0, annotation)
        paper["updatedAt"] = now_iso()
        papers = list_papers()
        papers[index] = paper
        save_papers(papers)
        self.send_json(201, {"annotation": annotation, "paper": paper})

    def update_annotation(self, paper_id: str, annotation_id: str) -> None:
        index, paper = find_paper(paper_id)
        if paper is None:
            self.send_json(404, {"error": "Paper not found"})
            return
        payload = self.read_json_body()
        for ann_index, annotation in enumerate(paper.get("annotations", [])):
            if annotation.get("id") == annotation_id:
                paper["annotations"][ann_index] = {**annotation, **payload, "updatedAt": now_iso()}
                paper["updatedAt"] = now_iso()
                papers = list_papers()
                papers[index] = paper
                save_papers(papers)
                self.send_json(200, {"annotation": paper["annotations"][ann_index], "paper": paper})
                return
        self.send_json(404, {"error": "Annotation not found"})

    def delete_annotation(self, paper_id: str, annotation_id: str) -> None:
        index, paper = find_paper(paper_id)
        if paper is None:
            self.send_json(404, {"error": "Paper not found"})
            return
        annotations = [item for item in paper.get("annotations", []) if item.get("id") != annotation_id]
        if len(annotations) == len(paper.get("annotations", [])):
            self.send_json(404, {"error": "Annotation not found"})
            return
        paper["annotations"] = annotations
        paper["updatedAt"] = now_iso()
        papers = list_papers()
        papers[index] = paper
        save_papers(papers)
        self.send_json(200, {"deleted": annotation_id, "paper": paper})

    def serve_static(self, raw_path: str) -> None:
        if raw_path.startswith("/papers/"):
            base = ROOT_DIR
            relative = unquote(raw_path.lstrip("/"))
        else:
            base = PUBLIC_DIR
            relative = "index.html" if raw_path == "/" else unquote(raw_path.lstrip("/"))
        file_path = (base / relative).resolve()
        if not str(file_path).startswith(str(base.resolve())) or not file_path.is_file():
            self.send_text(404, "Not found")
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        if file_path.suffix == ".js":
            content_type = "application/javascript"
        if file_path.suffix == ".css":
            content_type = "text/css"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_body(self) -> bytes:
        length = int(self.headers.get("content-length", "0"))
        return self.rfile.read(length) if length else b""

    def read_json_body(self) -> dict:
        body = self.read_body()
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))

    def send_json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_text(self, status: int, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "text/plain; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    ensure_dirs()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5173"))
    server = ThreadingHTTPServer((host, port), PaperReadRoomHandler)
    print(f"Paper Read Room running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
