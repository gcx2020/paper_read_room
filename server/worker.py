from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import threading
import time
from collections import deque

from . import db, research
from .paths import AGENT_MD_PATH, PAPERS_DIR


_started = False


def start_worker() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_single_loop, name="research-worker", daemon=True).start()
    threading.Thread(target=_batch_loop, name="batch-research-worker", daemon=True).start()


def _single_loop() -> None:
    while True:
        try:
            task = next(iter(research.pending_tasks()), None)
            if task:
                _run_single_task(task)
        except Exception as exc:
            if "task" in locals() and task:
                research.fail_task(task["id"], str(exc))
        time.sleep(3)


def _batch_loop() -> None:
    while True:
        item = None
        try:
            item = research.next_queued_item()
            if item:
                _run_batch_item(item)
        except Exception as exc:
            message = f"批量研究 worker 异常：{exc}"
            print(message, flush=True)
            if item:
                research.update_queue_item(item["id"], status="failed", message="worker 异常", error=message)
                research.append_queue_log(item["id"], message, "error")
        time.sleep(5)


def _prompt(paper_name: str, pdf_url: str | None) -> str:
    target = pdf_url or paper_name
    return f"""你是 Codex + GPT-5.5 论文精读研究 agent。请精读这篇论文（PDF 链接或论文名）：{target}

请严格按照本项目 AGENT.md 的规范生成精读 HTML 页面。
要求：
1. 只生成一个 .html 文件，保存到 papers/ 目录下
2. 不要在 papers/ 目录下生成任何非 HTML 文件
3. HTML 文件必须完全自包含（内联 CSS/JS），可离线打开
4. 图表使用 base64 内嵌
5. 复杂架构图、论文 Figure、实验曲线必须优先从 PDF/HTML 原文裁剪为清晰原图；不要手工重绘并称为“论文 Figure”
6. 如果无法抽取清晰原图，不要放模糊图或冒充原图；只能写“未能抽取原图”并用文字解释，或放明确标注“概念示意图 / 非论文原图”的简单说明图
7. 图片插入前必须检查文字可读、线条不乱、无重叠、无乱码；不合格图片不要插入
8. 当前电脑环境为 macOS，运行 agent 组合为 Codex + GPT-5.5，请在最终页面自检中如实记录工具链
9. 生成完成后不要修改项目代码，只保留 papers/ 下的精读 HTML。"""


def _run_codex_research(paper_name: str, pdf_url: str | None, log, target_slug: str | None = None) -> dict:
    codex_bin = shutil.which("codex") or "/Applications/Codex.app/Contents/Resources/codex"
    if not codex_bin or not os.path.exists(codex_bin):
        raise RuntimeError("未找到 Codex CLI。请安装/打开 Codex 桌面版并完成登录后重试 AI 研究功能。")
    before = {p.name: p.stat().st_mtime for p in PAPERS_DIR.glob("*.html")}
    model = os.getenv("PAPER_CODEX_MODEL") or os.getenv("CODEX_MODEL") or "gpt-5.5"
    cmd = [
        codex_bin,
        "exec",
        "-m",
        model,
        "-C",
        str(PAPERS_DIR.parent),
        "--skip-git-repo-check",
        "-s",
        "workspace-write",
        "--color",
        "never",
        _prompt(paper_name, pdf_url),
    ]
    log(f"调用 Codex 生成精读 HTML：agent=codex, model={model}", "generating", "generating")
    log(f"已加载规范文件：{AGENT_MD_PATH.name}", "reading")
    output = _run_codex_process(cmd, log)
    after = sorted(PAPERS_DIR.glob("*.html"), key=lambda p: p.stat().st_mtime, reverse=True)
    new_files = [p for p in after if p.name not in before or p.stat().st_mtime > before.get(p.name, 0)]
    if not new_files:
        html_path = _save_html_from_output(paper_name, output, log)
        if html_path:
            new_files = [html_path]
        else:
            raise RuntimeError("Codex 已结束，但 papers/ 目录没有发现新的 HTML 文件，也没有在输出中发现完整 HTML")
    html_path = new_files[0]
    html = html_path.read_text(encoding="utf-8", errors="replace")
    html = _polish_generated_html(html, log)
    result = db.upsert_from_html(html_path, html, replace_slug=target_slug)
    if target_slug and html_path.name != f"{target_slug}.html" and html_path.exists():
        try:
            html_path.unlink()
            log(f"已用新研究结果替换原版本：{target_slug}.html", "generating")
        except OSError as exc:
            log(f"替换完成，但清理临时版本失败：{exc}", "warning")
    return result


def _run_codex_process(cmd: list[str], log) -> str:
    tail: deque[str] = deque(maxlen=80)
    output_parts: list[str] = []
    output_chars = 0
    output_limit = 2_000_000
    lines: queue.Queue[str | None] = queue.Queue()
    proc = subprocess.Popen(
        cmd,
        cwd=str(PAPERS_DIR.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def read_output() -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.put(line.rstrip())
        finally:
            lines.put(None)

    threading.Thread(target=read_output, name="codex-output-reader", daemon=True).start()
    log(f"Codex CLI 已启动，pid={proc.pid}，等待生成 HTML", "generating", "generating")

    started_at = time.monotonic()
    last_heartbeat = started_at
    reader_done = False
    timeout_seconds = 7200

    while True:
        try:
            line = lines.get(timeout=1)
        except queue.Empty:
            line = None
        if line is None:
            reader_done = reader_done or proc.poll() is not None
        elif line.strip():
            if output_chars < output_limit:
                keep = line[: output_limit - output_chars]
                output_parts.append(keep)
                output_chars += len(keep)
            text = _compact_codex_line(line)
            tail.append(text)
            if _should_surface_codex_line(text):
                log(text, "generating", "generating")

        now = time.monotonic()
        if proc.poll() is None and now - last_heartbeat >= 60:
            minutes = int((now - started_at) // 60)
            log(f"Codex 仍在运行，已等待约 {minutes} 分钟", "generating", "generating")
            last_heartbeat = now
        if proc.poll() is None and now - started_at > timeout_seconds:
            proc.kill()
            raise RuntimeError("Codex 执行超过 120 分钟，已自动终止")
        if proc.poll() is not None and reader_done and lines.empty():
            break

    code = proc.wait()
    log(f"Codex CLI 已结束，退出码 {code}", "generating")
    if code != 0:
        error_tail = "\n".join(tail) or "Codex 执行失败，但没有输出错误详情"
        raise RuntimeError(error_tail[-4000:])
    return "\n".join(output_parts)


def _save_html_from_output(paper_name: str, output: str, log) -> os.PathLike | None:
    html = _extract_html(output)
    if not html:
        return None
    slug = db.slug_from_title(paper_name)
    path = PAPERS_DIR / f"{slug}.html"
    index = 2
    while path.exists():
        path = PAPERS_DIR / f"{slug}-{index}.html"
        index += 1
    path.write_text(html, encoding="utf-8")
    log(f"Codex 未直接写入文件，已从输出兜底保存：{path.name}", "generating")
    return path


def _extract_html(output: str) -> str | None:
    fenced = re.search(r"```html\s*([\s\S]*?</html>)\s*```", output, re.I)
    if fenced:
        return fenced.group(1).strip()
    full = re.search(r"<!doctype html[\s\S]*?</html>", output, re.I)
    if full:
        return full.group(0).strip()
    loose = re.search(r"<html[\s\S]*?</html>", output, re.I)
    if loose:
        return "<!doctype html>\n" + loose.group(0).strip()
    return None


def _polish_generated_html(html: str, log) -> str:
    original = html
    html = re.sub(r"<details\s+open(\s*>\s*<summary>\s*自检记录)", r"<details\1", html, flags=re.I)
    html = re.sub(r"<details\s+open(\s*>\s*<summary>[^<]*(?:解析自检|质量自检)[^<]*</summary>)", r"<details\1", html, flags=re.I)
    html = re.sub(
        r"(对应论文\s+(?:Figure|Fig\.?|Table)\s*[^<。；;，,]*?)的重构图",
        r"概念示意图（非论文原图，依据\1整理）",
        html,
        flags=re.I,
    )
    html = re.sub(
        r"(论文\s+(?:Figure|Fig\.?|Table)\s*[^<。；;，,]*?)重构图",
        r"概念示意图（非论文原图，依据\1整理）",
        html,
        flags=re.I,
    )
    html = re.sub(
        r'(<img[^>]+(?:redrawn|重绘|重构)[^>]*>\s*<div class="caption">)对应论文\s+((?:Figure|Fig\.?|Table)\s*[^：:。；;，,]*)([：:])',
        r"\1概念示意图（非论文原图，依据论文 \2 整理）\3",
        html,
        flags=re.I,
    )
    html = html.replace("重绘实验曲线", "实验结果表格整理")
    if 'data-svg=' in html or 'querySelectorAll("img[data-svg]")' in html or "querySelectorAll('img[data-svg]')" in html:
        log("检测到运行时 SVG 占位图，已记录为质量风险；后续 AGENT 规范已禁止这种输出", "warning")
    if html != original:
        log("已对生成 HTML 做质量兜底：折叠自检区，并标注概念图非论文原图", "generating")
    return html


def _compact_codex_line(line: str) -> str:
    text = " ".join(line.strip().split())
    return text[:600]


def _should_surface_codex_line(line: str) -> bool:
    if not line:
        return False
    lower = line.lower()
    noisy = ("tokens used", "working", "thinking", "session id")
    return not any(part in lower for part in noisy)


def _run_single_task(task: dict) -> None:
    task_id = task["id"]
    def log(text: str, typ: str = "info", status: str | None = None):
        research.log_task(task_id, text, typ, status)

    try:
        log("开始搜索和读取论文资料", "search", "researching")
        result = _run_codex_research(task["paper_name"], task.get("pdf_url"), log, task.get("target_slug"))
        research.complete_task(task_id, result)
    except Exception as exc:
        research.fail_task(task_id, str(exc))


def _run_batch_item(item: dict) -> None:
    task_id = item["id"]
    def log(text: str, typ: str = "info", status: str | None = None):
        research.append_queue_log(task_id, text, typ, status)

    try:
        current = research.queue_item(task_id)
        if current and current.get("cancel_requested"):
            research.update_queue_item(task_id, status="cancelled", message="已停止")
            return
        research.update_queue_item(task_id, status="researching", message="准备开始")
        target_slug = item.get("target_slug")
        log("开始批量重研任务，将替换原研究 HTML" if target_slug else "开始批量研究任务", "system", "researching")
        result = _run_codex_research(item["paper_name"], item.get("pdf_url"), log, target_slug)
        current = research.queue_item(task_id)
        if current and current.get("cancel_requested"):
            research.update_queue_item(task_id, status="cancelled", message="已停止")
            log("任务已停止，忽略本次 Codex 结果", "system")
            return
        with db.connect() as con:
            folder = con.execute("SELECT id FROM folders WHERE name='AI研究论文'").fetchone()
            if not folder:
                stamp = db.now_iso()
                order = con.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM folders").fetchone()[0]
                con.execute(
                    "INSERT INTO folders(name,color,parent_id,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                    ("AI研究论文", "#8b5cf6", None, order, stamp, stamp),
                )
                folder_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            else:
                folder_id = folder["id"]
        paper = db.get_paper(result["paper_id"])
        if paper:
            db.update_paper(paper["id"], {"tags": db.clean_tags((paper.get("tags") or []) + ["AI研究"]), "folder_id": folder_id})
        research.update_queue_item(task_id, status="completed", message="研究完成", result=_json_result(result))
        research.append_queue_log(task_id, "研究完成，已替换原 HTML 并归档到 AI研究论文" if target_slug else "研究完成，已自动归档到 AI研究论文", "system")
    except Exception as exc:
        research.update_queue_item(task_id, status="failed", message="研究失败", error=str(exc))
        research.append_queue_log(task_id, str(exc), "error")


def _json_result(result: dict) -> str:
    import json

    return json.dumps(result, ensure_ascii=False)
