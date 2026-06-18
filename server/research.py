from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from . import db


_research_tasks: dict[str, dict[str, Any]] = {}
QUEUE_ID = "default"


def _stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def create_task(paper_name: str, pdf_url: str | None = None) -> dict[str, Any]:
    active = active_task()
    if active:
        raise RuntimeError("同一时间只允许一个活跃研究任务")
    task_id = uuid.uuid4().hex[:8]
    task = {
        "id": task_id,
        "paper_name": paper_name,
        "pdf_url": pdf_url,
        "status": "pending",
        "message": "任务已创建",
        "error": None,
        "result": None,
        "logs": [{"time": _stamp(), "type": "system", "text": "任务已创建，等待 worker 处理"}],
        "created_at": db.now_iso(),
        "updated_at": db.now_iso(),
        "cancel_requested": False,
    }
    _research_tasks[task_id] = task
    return task


def get_task(task_id: str) -> dict[str, Any] | None:
    return _research_tasks.get(task_id)


def pending_tasks() -> list[dict[str, Any]]:
    return [t for t in _research_tasks.values() if t["status"] == "pending"]


def active_task() -> dict[str, Any] | None:
    for task in _research_tasks.values():
        if task["status"] in {"pending", "researching", "generating"}:
            return task
    return None


def active_tasks() -> list[dict[str, Any]]:
    return [t for t in _research_tasks.values() if t["status"] in {"pending", "researching", "generating"}]


def log_task(task_id: str, text: str, typ: str = "info", status: str | None = None) -> dict[str, Any] | None:
    task = get_task(task_id)
    if not task:
        return None
    if status:
        task["status"] = status
    task["message"] = text
    task["updated_at"] = db.now_iso()
    task["logs"].append({"time": _stamp(), "type": typ, "text": text})
    return task


def complete_task(task_id: str, result: dict[str, Any]) -> dict[str, Any] | None:
    task = get_task(task_id)
    if not task:
        return None
    task["status"] = "completed"
    task["message"] = "研究完成"
    task["result"] = result
    task["updated_at"] = db.now_iso()
    task["logs"].append({"time": _stamp(), "type": "system", "text": "研究完成，已导入论文库"})
    return task


def fail_task(task_id: str, error: str) -> dict[str, Any] | None:
    task = get_task(task_id)
    if not task:
        return None
    task["status"] = "failed"
    task["message"] = "研究失败"
    task["error"] = error
    task["updated_at"] = db.now_iso()
    task["logs"].append({"time": _stamp(), "type": "error", "text": error})
    return task


def start_batch(items: list[dict[str, Any]]) -> dict[str, Any]:
    with db.connect() as con:
        max_order = con.execute("SELECT COALESCE(MAX(sort_order),0) FROM batch_queue").fetchone()[0]
        created = []
        for offset, item in enumerate(items, start=1):
            task_id = uuid.uuid4().hex[:8]
            stamp = db.now_iso()
            logs = [{"time": _stamp(), "type": "system", "text": "已加入批量研究队列"}]
            con.execute(
                """
                INSERT INTO batch_queue(id,paper_name,pdf_url,status,sort_order,message,logs,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (task_id, item["paper_name"], item.get("pdf_url"), "queued", max_order + offset, "排队中", json.dumps(logs, ensure_ascii=False), stamp, stamp),
            )
            created.append(task_id)
    return get_batch()


def get_batch() -> dict[str, Any]:
    with db.connect() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM batch_queue ORDER BY sort_order ASC, created_at ASC")]
    for row in rows:
        row["logs"] = json.loads(row.get("logs") or "[]")
        row["result"] = json.loads(row["result"]) if row.get("result") else None
    total = len(rows)
    completed = sum(1 for r in rows if r["status"] == "completed")
    failed = sum(1 for r in rows if r["status"] == "failed")
    active = any(r["status"] in {"queued", "pending", "researching", "generating"} for r in rows)
    return {"id": QUEUE_ID, "items": rows, "total": total, "completed": completed, "failed": failed, "active": active}


def latest_batch() -> dict[str, Any]:
    return get_batch()


def active_batch() -> dict[str, Any]:
    batch = get_batch()
    batch["items"] = [i for i in batch["items"] if i["status"] in {"queued", "pending", "researching", "generating"}]
    batch["active"] = bool(batch["items"])
    return batch


def update_queue_item(task_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = db.now_iso()
    with db.connect() as con:
        con.execute(
            f"UPDATE batch_queue SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
            [fields[k] for k in fields] + [task_id],
        )


def append_queue_log(task_id: str, text: str, typ: str = "info", status: str | None = None) -> None:
    with db.connect() as con:
        row = con.execute("SELECT logs FROM batch_queue WHERE id=?", (task_id,)).fetchone()
        if not row:
            return
        logs = json.loads(row["logs"] or "[]")
        logs.append({"time": _stamp(), "type": typ, "text": text})
        fields: dict[str, Any] = {"logs": json.dumps(logs, ensure_ascii=False), "message": text, "updated_at": db.now_iso()}
        if status:
            fields["status"] = status
        con.execute(
            f"UPDATE batch_queue SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
            [fields[k] for k in fields] + [task_id],
        )


def next_queued_item() -> dict[str, Any] | None:
    with db.connect() as con:
        running = con.execute("SELECT 1 FROM batch_queue WHERE status IN ('pending','researching','generating')").fetchone()
        if running:
            return None
        row = con.execute("SELECT * FROM batch_queue WHERE status='queued' AND cancel_requested=0 ORDER BY sort_order ASC, created_at ASC LIMIT 1").fetchone()
        return dict(row) if row else None


def cancel_batch() -> dict[str, Any]:
    with db.connect() as con:
        con.execute("UPDATE batch_queue SET cancel_requested=1, status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END, message='已请求停止', updated_at=? WHERE status IN ('queued','pending','researching','generating')", (db.now_iso(),))
    return get_batch()


def remove_item(task_id: str) -> dict[str, Any]:
    with db.connect() as con:
        con.execute("DELETE FROM batch_queue WHERE id=? AND status NOT IN ('researching','generating')", (task_id,))
    return get_batch()


def stop_item(task_id: str) -> dict[str, Any]:
    update_queue_item(task_id, cancel_requested=1, status="cancelled", message="已停止")
    append_queue_log(task_id, "任务已停止", "system")
    return get_batch()


def retry_item(task_id: str) -> dict[str, Any]:
    update_queue_item(task_id, cancel_requested=0, status="queued", error=None, message="重新排队", result=None)
    append_queue_log(task_id, "已重新加入队列", "system")
    return get_batch()


def reorder_item(task_id: str, direction: str) -> dict[str, Any]:
    with db.connect() as con:
        row = con.execute("SELECT id,sort_order FROM batch_queue WHERE id=?", (task_id,)).fetchone()
        if not row:
            return get_batch()
        op = "<" if direction == "up" else ">"
        order = "DESC" if direction == "up" else "ASC"
        other = con.execute(f"SELECT id,sort_order FROM batch_queue WHERE sort_order {op} ? ORDER BY sort_order {order} LIMIT 1", (row["sort_order"],)).fetchone()
        if other:
            con.execute("UPDATE batch_queue SET sort_order=? WHERE id=?", (other["sort_order"], row["id"]))
            con.execute("UPDATE batch_queue SET sort_order=? WHERE id=?", (row["sort_order"], other["id"]))
    return get_batch()
