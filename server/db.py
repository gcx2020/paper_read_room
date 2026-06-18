from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

from .paths import DB_PATH, PAPERS_DIR, ensure_dirs


PAPER_COLUMNS = [
    "slug", "title", "authors", "institution", "year", "arxiv_id", "pdf_url", "arxiv_url",
    "github_url", "project_url", "openreview_url", "conference", "accept_status",
    "one_line_summary", "research_question", "core_method", "main_result",
    "target_audience", "tags", "status", "rating", "notes", "date_published", "folder_id",
    "summary_html_exists", "html_enriched",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


@contextmanager
def connect():
    ensure_dirs()
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    ensure_dirs()
    with connect() as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS folders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                color TEXT DEFAULT '#6366f1',
                parent_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS papers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                authors TEXT NOT NULL DEFAULT '',
                institution TEXT NOT NULL DEFAULT '',
                year INTEGER,
                arxiv_id TEXT,
                pdf_url TEXT,
                arxiv_url TEXT,
                github_url TEXT,
                project_url TEXT,
                openreview_url TEXT,
                conference TEXT,
                accept_status TEXT,
                one_line_summary TEXT,
                research_question TEXT,
                core_method TEXT,
                main_result TEXT,
                target_audience TEXT,
                tags TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'reading' CHECK(status IN ('reading','read','archived','todo')),
                rating INTEGER CHECK(rating IS NULL OR (rating >= 1 AND rating <= 5)),
                notes TEXT,
                date_added TEXT NOT NULL,
                date_updated TEXT NOT NULL,
                date_published TEXT,
                summary_html_exists INTEGER NOT NULL DEFAULT 0,
                html_enriched INTEGER NOT NULL DEFAULT 0,
                folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_papers_slug ON papers(slug)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_papers_status ON papers(status)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_papers_date_added ON papers(date_added)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_papers_folder ON papers(folder_id)")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                category TEXT NOT NULL DEFAULT 'topic'
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_tags (
                paper_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (paper_id, tag_id),
                FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
                FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_queue (
                id TEXT PRIMARY KEY,
                paper_name TEXT NOT NULL,
                pdf_url TEXT,
                status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','pending','researching','generating','completed','failed','cancelled')),
                sort_order INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '',
                error TEXT,
                result TEXT,
                logs TEXT NOT NULL DEFAULT '[]',
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        con.execute("CREATE TABLE IF NOT EXISTS _migrations (key TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        _run_tag_cleanup_migration(con, "clear_auto_tags_v1")
        _run_tag_cleanup_migration(con, "clear_garbage_tags_v2")
        recover_batch_queue(con)


def _run_tag_cleanup_migration(con: sqlite3.Connection, key: str) -> None:
    if con.execute("SELECT 1 FROM _migrations WHERE key=?", (key,)).fetchone():
        return
    con.execute("DELETE FROM tags WHERE name LIKE '%MathJax%' OR length(name) > 60")
    con.execute("DELETE FROM paper_tags WHERE tag_id NOT IN (SELECT id FROM tags)")
    con.execute("INSERT INTO _migrations(key, applied_at) VALUES (?, ?)", (key, now_iso()))


def row_to_paper(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    try:
        data["tags"] = json.loads(data.get("tags") or "[]")
    except json.JSONDecodeError:
        data["tags"] = []
    return data


def _paper_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload = {k: data.get(k) for k in PAPER_COLUMNS if k in data}
    if "tags" in payload:
        payload["tags"] = json.dumps(clean_tags(payload["tags"]), ensure_ascii=False)
    return payload


def clean_tags(tags: list[str] | str | None) -> list[str]:
    if not tags:
        return []
    if isinstance(tags, str):
        tags = [x.strip() for x in tags.split(",")]
    out: list[str] = []
    for tag in tags:
        tag = str(tag).strip()
        if not tag or len(tag) > 40:
            continue
        if any(x in tag.lower() for x in ["mathjax", "\\(", "\\[", "</", "<script"]):
            continue
        if tag not in out:
            out.append(tag)
    return out[:20]


def sync_tags(con: sqlite3.Connection, paper_id: int, tags: list[str]) -> None:
    con.execute("DELETE FROM paper_tags WHERE paper_id=?", (paper_id,))
    for tag in clean_tags(tags):
        category = "method" if any(x in tag.lower() for x in ["vla", "diffusion", "rl", "transformer", "agent"]) else "topic"
        con.execute("INSERT OR IGNORE INTO tags(name, category) VALUES (?, ?)", (tag, category))
        tag_id = con.execute("SELECT id FROM tags WHERE name=?", (tag,)).fetchone()["id"]
        con.execute("INSERT OR IGNORE INTO paper_tags(paper_id, tag_id) VALUES (?, ?)", (paper_id, tag_id))


def create_paper(data: dict[str, Any]) -> dict[str, Any]:
    payload = _paper_payload(data)
    stamp = now_iso()
    payload.setdefault("authors", "")
    payload.setdefault("institution", "")
    payload.setdefault("status", "reading")
    payload.setdefault("tags", "[]")
    payload["date_added"] = stamp
    payload["date_updated"] = stamp
    payload.setdefault("summary_html_exists", int((PAPERS_DIR / f"{payload['slug']}.html").exists()))
    with connect() as con:
        keys = list(payload)
        con.execute(
            f"INSERT INTO papers({','.join(keys)}) VALUES ({','.join(['?'] * len(keys))})",
            [payload[k] for k in keys],
        )
        paper_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        sync_tags(con, paper_id, json.loads(payload.get("tags", "[]")))
        return get_paper(paper_id, con=con)


def update_paper(paper_id: int, data: dict[str, Any], con: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    own = con is None
    cm = connect() if own else None
    if own:
        con = cm.__enter__()
    assert con is not None
    try:
        payload = _paper_payload(data)
        payload["date_updated"] = now_iso()
        if payload:
            con.execute(
                f"UPDATE papers SET {', '.join(f'{k}=?' for k in payload)} WHERE id=?",
                [payload[k] for k in payload] + [paper_id],
            )
        if "tags" in payload:
            sync_tags(con, paper_id, json.loads(payload.get("tags", "[]")))
        result = get_paper(paper_id, con=con)
    finally:
        if own and cm:
            cm.__exit__(None, None, None)
    return result


def get_paper(paper_id: int, con: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    if con is None:
        with connect() as c:
            return get_paper(paper_id, c)
    return row_to_paper(con.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone())


def get_paper_by_slug(slug: str, con: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    if con is None:
        with connect() as c:
            return get_paper_by_slug(slug, c)
    return row_to_paper(con.execute("SELECT * FROM papers WHERE slug=?", (slug,)).fetchone())


def list_papers(filters: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    where: list[str] = []
    args: list[Any] = []
    if filters.get("status"):
        where.append("status=?")
        args.append(filters["status"])
    if filters.get("year"):
        where.append("year=?")
        args.append(filters["year"])
    if filters.get("folder_id") is not None:
        if int(filters["folder_id"]) == 0:
            where.append("folder_id IS NULL")
        else:
            where.append("folder_id=?")
            args.append(filters["folder_id"])
    if filters.get("tag"):
        where.append("EXISTS (SELECT 1 FROM paper_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.paper_id=papers.id AND t.name=?)")
        args.append(filters["tag"])
    if filters.get("search"):
        q = f"%{filters['search']}%"
        where.append("(title LIKE ? OR authors LIKE ? OR one_line_summary LIKE ? OR arxiv_id LIKE ?)")
        args.extend([q, q, q, q])
    sql_where = " WHERE " + " AND ".join(where) if where else ""
    sort = filters.get("sort") if filters.get("sort") in {"date_added", "date_updated", "title", "year", "rating"} else "date_added"
    order = "ASC" if filters.get("order") == "asc" else "DESC"
    limit = max(1, min(int(filters.get("limit") or 50), 200))
    offset = max(0, int(filters.get("offset") or 0))
    with connect() as con:
        total = con.execute(f"SELECT COUNT(*) FROM papers{sql_where}", args).fetchone()[0]
        rows = con.execute(
            f"SELECT * FROM papers{sql_where} ORDER BY {sort} {order}, id DESC LIMIT ? OFFSET ?",
            args + [limit, offset],
        ).fetchall()
        return [row_to_paper(r) for r in rows], total


def delete_paper(paper_id: int) -> bool:
    with connect() as con:
        row = con.execute("SELECT slug FROM papers WHERE id=?", (paper_id,)).fetchone()
        if not row:
            return False
        con.execute("DELETE FROM papers WHERE id=?", (paper_id,))
    path = PAPERS_DIR / f"{row['slug']}.html"
    if path.exists():
        path.unlink()
    return True


def stats() -> dict[str, Any]:
    with connect() as con:
        total = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        by_status = {r["status"]: r["c"] for r in con.execute("SELECT status, COUNT(*) c FROM papers GROUP BY status")}
        by_year = {str(r["year"]): r["c"] for r in con.execute("SELECT year, COUNT(*) c FROM papers WHERE year IS NOT NULL GROUP BY year ORDER BY year DESC")}
        recent = [row_to_paper(r) for r in con.execute("SELECT * FROM papers ORDER BY date_added DESC LIMIT 5")]
        unfiled = con.execute("SELECT COUNT(*) FROM papers WHERE folder_id IS NULL").fetchone()[0]
        return {"total": total, "by_status": by_status, "by_year": by_year, "recent": recent, "unfiled": unfiled}


def list_tags() -> list[dict[str, Any]]:
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT t.id,t.name,t.category,COUNT(pt.paper_id) count FROM tags t LEFT JOIN paper_tags pt ON pt.tag_id=t.id GROUP BY t.id ORDER BY count DESC,t.name ASC"
        ) if r["count"] > 0]


def list_years() -> list[int]:
    with connect() as con:
        return [r["year"] for r in con.execute("SELECT DISTINCT year FROM papers WHERE year IS NOT NULL ORDER BY year DESC")]


def list_folders() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT f.*, COUNT(p.id) paper_count
            FROM folders f LEFT JOIN papers p ON p.folder_id=f.id
            GROUP BY f.id ORDER BY f.sort_order ASC, f.name ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def create_folder(data: dict[str, Any]) -> dict[str, Any]:
    stamp = now_iso()
    with connect() as con:
        order = con.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM folders").fetchone()[0]
        con.execute(
            "INSERT INTO folders(name,color,parent_id,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (data["name"], data.get("color", "#6366f1"), data.get("parent_id"), order, stamp, stamp),
        )
        return dict(con.execute("SELECT * FROM folders WHERE id=last_insert_rowid()").fetchone())


def update_folder(folder_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    payload = {k: v for k, v in data.items() if k in {"name", "color", "parent_id"}}
    payload["updated_at"] = now_iso()
    with connect() as con:
        if payload.get("parent_id") == folder_id:
            raise ValueError("文件夹不能作为自己的父文件夹")
        parent_id = payload.get("parent_id")
        while parent_id:
            parent = con.execute("SELECT parent_id FROM folders WHERE id=?", (parent_id,)).fetchone()
            if not parent:
                break
            if parent["parent_id"] == folder_id:
                raise ValueError("不能把文件夹移动到自己的子文件夹下")
            parent_id = parent["parent_id"]
        con.execute(f"UPDATE folders SET {', '.join(f'{k}=?' for k in payload)} WHERE id=?", [payload[k] for k in payload] + [folder_id])
        row = con.execute("SELECT * FROM folders WHERE id=?", (folder_id,)).fetchone()
        return dict(row) if row else None


def delete_folder(folder_id: int) -> None:
    with connect() as con:
        con.execute("UPDATE papers SET folder_id=NULL WHERE folder_id=?", (folder_id,))
        con.execute("DELETE FROM folders WHERE id=?", (folder_id,))


def move_papers(ids: list[int], folder_id: int | None) -> int:
    if not ids:
        return 0
    with connect() as con:
        con.executemany("UPDATE papers SET folder_id=?, date_updated=? WHERE id=?", [(folder_id, now_iso(), i) for i in ids])
        return len(ids)


def batch_update_status(ids: list[int], status: str) -> int:
    with connect() as con:
        con.executemany("UPDATE papers SET status=?, date_updated=? WHERE id=?", [(status, now_iso(), i) for i in ids])
        return len(ids)


def batch_add_tags(ids: list[int], tags: list[str]) -> int:
    with connect() as con:
        for paper_id in ids:
            paper = get_paper(paper_id, con)
            if not paper:
                continue
            merged = clean_tags((paper.get("tags") or []) + tags)
            con.execute("UPDATE papers SET tags=?, date_updated=? WHERE id=?", (json.dumps(merged, ensure_ascii=False), now_iso(), paper_id))
            sync_tags(con, paper_id, merged)
        return len(ids)


def upsert_from_html(path: Path, html: str, preferred_slug: str | None = None, folder_id: int | None = None, extra_tags: list[str] | None = None) -> dict[str, Any]:
    parsed = parse_html_metadata(html)
    title = parsed.get("title") or preferred_slug or path.stem
    slug = unique_slug(preferred_slug or slug_from_title(title, parsed.get("year")))
    final_path = PAPERS_DIR / f"{slug}.html"
    final_path.write_text(html, encoding="utf-8")
    with connect() as con:
        existing = con.execute("SELECT * FROM papers WHERE slug=?", (slug,)).fetchone()
        # 精读报告只补全论文信息；标签必须由用户手动设置。
        # 批量 AI 研究可通过 extra_tags 统一加一个固定标签。
        tags = clean_tags(extra_tags or [])
        payload = {
            "slug": slug,
            "title": title,
            "authors": parsed.get("authors") or "",
            "institution": parsed.get("institution") or "",
            "year": parsed.get("year"),
            "arxiv_id": parsed.get("arxiv_id"),
            "pdf_url": parsed.get("pdf_url"),
            "arxiv_url": parsed.get("arxiv_url"),
            "github_url": parsed.get("github_url"),
            "project_url": parsed.get("project_url"),
            "conference": parsed.get("conference"),
            "one_line_summary": parsed.get("one_line_summary"),
            "research_question": parsed.get("research_question"),
            "core_method": parsed.get("core_method"),
            "main_result": parsed.get("main_result"),
            "target_audience": parsed.get("target_audience"),
            "date_published": parsed.get("date_published"),
            "tags": tags,
            "folder_id": folder_id,
            "summary_html_exists": 1,
            "html_enriched": 1,
        }
        if existing:
            paper = update_paper(existing["id"], payload, con)
            action = "updated"
        else:
            stamp = now_iso()
            insert_payload = _paper_payload(payload | {"slug": slug, "title": title})
            insert_payload.setdefault("authors", "")
            insert_payload.setdefault("institution", "")
            insert_payload.setdefault("status", "reading")
            insert_payload.setdefault("tags", "[]")
            insert_payload["date_added"] = stamp
            insert_payload["date_updated"] = stamp
            keys = list(insert_payload)
            con.execute(
                f"INSERT INTO papers({','.join(keys)}) VALUES ({','.join(['?'] * len(keys))})",
                [insert_payload[k] for k in keys],
            )
            paper_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            sync_tags(con, paper_id, json.loads(insert_payload.get("tags", "[]")))
            paper = get_paper(paper_id, con=con)
            action = "created"
        return {"ok": True, "paper_id": paper["id"], "slug": slug, "title": paper["title"], "action": action, "parsed": parsed}


def unique_slug(slug: str) -> str:
    slug = slug or "paper"
    with connect() as con:
        candidate = slug
        i = 2
        while con.execute("SELECT 1 FROM papers WHERE slug=?", (candidate,)).fetchone() or (PAPERS_DIR / f"{candidate}.html").exists():
            candidate = f"{slug}-{i}"
            i += 1
        return candidate


def slug_from_title(title: str, year: int | None = None) -> str:
    text = (title or "paper").lower()
    text = re.sub(r"[-–—]\s*(论文精读|精读|paper\s*reading|summary)\s*$", "", text, flags=re.I)
    text = re.sub(r"[^\w\s-]", "", text, flags=re.U)
    text = re.sub(r"[\s_]+", "-", text).strip("-")
    text = re.sub(r"-+", "-", text)[:60].rstrip("-")
    text = text or "paper"
    return f"{year}-{text}" if year else text


def parse_html_metadata(html: str) -> dict[str, Any]:
    plain = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", " ", plain))
    text = re.sub(r"\s+", " ", text)

    def meta(name: str) -> str | None:
        m = re.search(rf'<meta[^>]+(?:name|property)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        return unescape(m.group(1)).strip() if m else None

    def tag(name: str) -> str | None:
        m = re.search(rf"<{name}[^>]*>([\s\S]*?)</{name}>", html, re.I)
        return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", m.group(1)))).strip() if m else None

    def clean_fragment(value: str) -> str:
        return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", value))).strip(" :：|")

    def labeled(label: str) -> str | None:
        patterns = [
            rf"{label}\s*[:：]\s*([^。；;\n<]{{3,260}})",
            rf"<strong[^>]*>\s*{label}\s*[:：]?\s*</strong>\s*([^<]{{3,260}})",
            rf"<td[^>]*>\s*{label}\s*</td>\s*<td[^>]*>([\s\S]{{3,300}}?)</td>",
            rf"<th[^>]*>\s*{label}\s*</th>\s*<td[^>]*>([\s\S]{{3,300}}?)</td>",
            rf"<[^>]+class=[\"'][^\"']*(?:label|meta-label|card-title)[^\"']*[\"'][^>]*>\s*{label}\s*</[^>]+>\s*<[^>]+class=[\"'][^\"']*(?:value|meta-value|card-body|card-content)[^\"']*[\"'][^>]*>([\s\S]{{3,360}}?)</[^>]+>",
            rf"<h[2-4][^>]*>\s*{label}\s*</h[2-4]>\s*<p[^>]*>([\s\S]{{3,360}}?)</p>",
        ]
        for pat in patterns:
            m = re.search(pat, html, re.I)
            if m:
                return clean_fragment(m.group(1))
        return None

    def first_section_text(*labels: str) -> str | None:
        for label in labels:
            m = re.search(rf"(?:<h[1-4][^>]*>[^<]*{label}[^<]*</h[1-4]>)([\s\S]{{0,1200}}?)(?=<h[1-4]\b|$)", html, re.I)
            if not m:
                continue
            paragraph = re.search(r"<p[^>]*>([\s\S]{8,420}?)</p>", m.group(1), re.I)
            if paragraph:
                return clean_fragment(paragraph.group(1))[:260]
            cleaned = clean_fragment(m.group(1))
            if len(cleaned) >= 8:
                return cleaned[:260]
        return None

    title = meta("citation_title") or tag("title") or tag("h1") or "Untitled Paper"
    title = re.sub(r"\s*[-–—|]\s*(论文精读|精读|Paper Reading|Summary)\s*$", "", title, flags=re.I).strip()
    authors = meta("citation_author") or labeled("作者")
    institution = meta("citation_author_institution") or labeled("机构")
    year = None
    date = meta("citation_date") or meta("citation_publication_date") or meta("article:published_time") or labeled("arXiv 首次提交日期")
    year_match = re.search(r"(19|20)\d{2}", " ".join(x for x in [date, title, text[:5000]] if x))
    if year_match:
        year = int(year_match.group(0))
    arxiv = meta("citation_arxiv_id")
    if not arxiv:
        m = re.search(r"\b(\d{4}\.\d{4,5})(v\d+)?\b", text[:20000])
        arxiv = m.group(1) if m else None
    pdf = meta("citation_pdf_url")
    if not pdf:
        m = re.search(r'https?://[^\s"\'<>]+\.pdf', html)
        pdf = m.group(0) if m else None
    github = None
    m = re.search(r'https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', html)
    if m:
        github = m.group(0)
    project = None
    m = re.search(r'https?://[^\s"\'<>]*(?:github\.io|/project|/projects)[^\s"\'<>]*', html, re.I)
    if m:
        project = m.group(0)
    return {
        "title": title,
        "authors": authors or "",
        "institution": institution or "",
        "year": year,
        "arxiv_id": arxiv,
        "pdf_url": pdf,
        "arxiv_url": f"https://arxiv.org/abs/{arxiv}" if arxiv else None,
        "github_url": github,
        "project_url": project,
        "conference": meta("citation_journal_title") or labeled("会议") or labeled("会议/期刊"),
        "one_line_summary": meta("description") or labeled("一句话总结") or first_section_text("论文概览", "总结"),
        "research_question": labeled("研究问题") or first_section_text("核心问题", "问题"),
        "core_method": labeled("核心方法") or first_section_text("方法总览", "核心方法", "方法"),
        "main_result": labeled("主要结果") or labeled("主要结论") or first_section_text("主要实验结果", "实验结果"),
        "target_audience": labeled("适合读者") or labeled("适合什么读者阅读") or first_section_text("适合读者"),
        "date_published": date,
        "tags": [],
    }


def recover_batch_queue(con: sqlite3.Connection) -> None:
    con.execute(
        "UPDATE batch_queue SET status='queued', message='服务重启后恢复排队', cancel_requested=0, updated_at=? WHERE status IN ('pending','researching','generating')",
        (now_iso(),),
    )
