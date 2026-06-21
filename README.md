# Paper Reading Manager

个人论文精读管理工具。支持论文元信息管理、精读 HTML 导入/导出、嵌套文件夹与手动标签、阅读主题、单篇和批量 AI 研究任务。

当前默认面向 macOS + Codex + GPT-5.5。AI 研究完成后会生成自包含 HTML 精读报告，并自动导入 `papers/` 与 SQLite 数据库。

## 核心功能

- 论文卡片管理：状态、年份、搜索、排序、分页、批量选择。
- 文件夹与标签：文件夹支持嵌套和预设颜色；标签由用户手动维护，批量 AI 研究统一追加 `AI研究` 标签。
- 精读 HTML：上传、拖拽导入、导出、iframe 预览、独立 `/read/{slug}` 阅读页。
- 阅读主题：管理页和精读页共享主题，内置云白蓝、清墨绿、GitHub Light、GitHub Dark、深夜蓝。
- AI 研究：单篇研究、批量队列、进度日志、关闭弹窗后继续后台运行。
- 批量重研：已有论文点“重研”会进入批量队列，完成后替换原 HTML。
- 单报告约束：同一篇论文只保留一条数据库记录和一份 `papers/<slug>.html`。

## macOS 开发启动

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

访问 <http://localhost:8000>。

也可以双击 `start.command`，但首次使用前仍需安装依赖。

## AI 研究配置

AI 研究功能依赖本机 Codex CLI。当前默认适配 macOS + Codex + GPT-5.5：

```bash
codex exec -m gpt-5.5 -C . "请按 AGENT.md 精读论文并在 papers/ 目录生成 HTML"
```

如需覆盖模型：

```bash
export PAPER_CODEX_MODEL=gpt-5.5
```

如果未安装或未登录 Codex，普通论文管理、HTML 上传、同步和导出仍可使用；AI 任务会在进度日志中显示明确失败原因。

## 去重与同步策略

系统会优先使用 `arXiv ID` 判断同一篇论文；没有 arXiv ID 时，使用归一化后的 `标题 + 年份` 判断。上传、同步、AI 生成、批量重研都会复用已有 slug，覆盖原 HTML，不再生成 `-2/-3` 副本。

同步文件系统：

```bash
curl -X POST http://127.0.0.1:8000/api/sync
```

同步会先清理重复数据库记录和孤立重复 HTML，再导入缺失文件。健康状态应满足：

- `papers/*.html` 数量等于数据库 `papers` 记录数
- 没有重复 `arXiv ID`
- 没有未被数据库引用的孤立 HTML

## 目录

- `server/`: FastAPI 后端、SQLite 数据库、worker
- `frontend/`: 无框架 SPA
- `papers/`: 精读 HTML 文件
- `data/`: SQLite 数据库
- `AGENT.md`: AI 精读 HTML 生成规范
- `DESIGN.md`: 当前架构、API、数据模型和交互设计说明

## 验证

```bash
python3 -m compileall server run.py main.py
node --check frontend/app.js
.venv/bin/python -m uvicorn server.server:app --host 127.0.0.1 --port 8000
```

服务启动后可检查：

```bash
curl http://127.0.0.1:8000/api/stats
curl http://127.0.0.1:8000/api/papers
```

去重一致性检查：

```bash
python3 - <<'PY'
from collections import Counter
from server import db
from server.paths import PAPERS_DIR
papers = db.list_papers({"limit": 200})[0]
db_slugs = {p["slug"] for p in papers}
fs_slugs = {p.stem for p in PAPERS_DIR.glob("*.html")}
print("db_count", len(db_slugs))
print("file_count", len(fs_slugs))
print("missing_files", sorted(db_slugs - fs_slugs))
print("orphan_files", sorted(fs_slugs - db_slugs))
print("duplicate_arxiv", {k:v for k,v in Counter(p.get("arxiv_id") for p in papers if p.get("arxiv_id")).items() if v > 1})
PY
```
