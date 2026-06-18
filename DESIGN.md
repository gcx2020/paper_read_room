# Paper Reading Manager — 完整设计文档

## 1. 项目概述

**Paper Reading Manager** 是一个个人论文精读管理工具，提供 Web 界面浏览、批量管理论文元信息，并集成了 AI 自动研究论文功能（通过 opencode CLI 调用 GLM 5.1）。系统生成的每篇精读 HTML 均为完全自包含单文件，支持离线浏览、MathJax 公式渲染和 base64 内嵌图表。

**核心价值**：输入论文名/PDF 链接 → AI 自动搜索、阅读、生成精读 HTML → 自动导入管理 → 离线浏览与分享。

---

## 2. 技术栈

| 层       | 技术                   | 说明                                          |
| -------- | ---------------------- | --------------------------------------------- |
| 后端     | FastAPI + Uvicorn      | Python 3.10+，异步 Web 框架                   |
| 数据库   | SQLite (WAL mode)      | 单文件数据库，位于 `data/papers.db`         |
| 前端     | 纯 HTML/CSS/JS SPA     | 无框架，单页应用                              |
| AI 引擎  | opencode CLI + GLM 5.1 | subprocess 调用，按 AGENT.md 规范生成         |
| PDF 解析 | PyMuPDF (fitz)         | 图表裁剪（worker 端未直接使用，由 AI 侧完成） |
| 精读页面 | MathJax + 自包含 HTML  | 内联 CSS/JS，base64 图片                      |

---

## 3. 项目结构

```
paper/
├── main.py               # 打包入口 (PyInstaller frozen 检测)
├── run.py                # 开发入口
├── start.bat             # Windows 一键启动
├── requirements.txt      # Python 依赖
├── AGENT.md              # AI 生成精读 HTML 的完整规范（629行）
├── README.md
│
├── server/               # 后端
│   ├── __init__.py       # 空
│   ├── paths.py          # 路径常量 (APP_ROOT, PAPERS_DIR, DATA_DIR, FRONTEND_DIR, DB_PATH 等)
│   ├── server.py         # FastAPI 路由定义 (~1167行)
│   ├── db.py             # SQLite 数据库操作 (~608行)
│   ├── models.py         # Pydantic 数据模型 (~211行)
│   ├── research.py       # 研究任务管理 (内存 + SQLite 双存储) (~570行)
│   └── worker.py         # 后台工作线程 (opencode 子进程) (~446行)
│
├── frontend/             # 前端 SPA
│   ├── index.html        # 主页面结构
│   ├── style.css         # 全局样式 (~879行)
│   └── app.js            # 前端逻辑 (~1931行)
│
├── papers/               # 精读 HTML 存储目录 (扁平，每篇一个 <year>-<slug>.html)
│   └── ...
│
└── data/                 # 运行时数据 (gitignore)
    └── papers.db
```

---

## 4. 数据库设计

### 4.1 papers 表

```sql
CREATE TABLE papers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slug            TEXT UNIQUE NOT NULL,          -- 如 "2026-chainflow-vla"
    title           TEXT NOT NULL,
    authors         TEXT NOT NULL DEFAULT '',
    institution     TEXT NOT NULL DEFAULT '',
    year            INTEGER,
    arxiv_id        TEXT,
    pdf_url         TEXT,
    arxiv_url       TEXT,
    github_url      TEXT,
    project_url     TEXT,
    openreview_url  TEXT,
    conference      TEXT,
    accept_status   TEXT,
    one_line_summary TEXT,
    research_question TEXT,
    core_method     TEXT,
    main_result     TEXT,
    target_audience TEXT,
    tags            TEXT NOT NULL DEFAULT '[]',      -- JSON 数组字符串
    status          TEXT NOT NULL DEFAULT 'reading'
                    CHECK(status IN ('reading','read','archived','todo')),
    rating          INTEGER CHECK(rating IS NULL OR (rating >= 1 AND rating <= 5)),
    notes           TEXT,
    date_added      TEXT NOT NULL,
    date_updated    TEXT NOT NULL,
    date_published  TEXT,
    summary_html_exists INTEGER NOT NULL DEFAULT 0,
    html_enriched   INTEGER NOT NULL DEFAULT 0,     -- 标记是否已从 HTML 解析元数据
    folder_id       INTEGER REFERENCES folders(id) ON DELETE SET NULL
);

CREATE INDEX idx_papers_slug ON papers(slug);
CREATE INDEX idx_papers_status ON papers(status);
CREATE INDEX idx_papers_year ON papers(year);
CREATE INDEX idx_papers_date_added ON papers(date_added);
CREATE INDEX idx_papers_folder ON papers(folder_id);
```

### 4.2 tags 表

```sql
CREATE TABLE tags (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT UNIQUE NOT NULL,
    category TEXT NOT NULL DEFAULT 'topic'
);
```

### 4.3 paper_tags 关联表

```sql
CREATE TABLE paper_tags (
    paper_id INTEGER NOT NULL,
    tag_id   INTEGER NOT NULL,
    PRIMARY KEY (paper_id, tag_id),
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id)   REFERENCES tags(id)   ON DELETE CASCADE
);
```

### 4.4 folders 表

```sql
CREATE TABLE folders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    color      TEXT DEFAULT '#6366f1',
    parent_id  INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

### 4.5 batch_queue 表

```sql
CREATE TABLE batch_queue (
    id               TEXT PRIMARY KEY,            -- UUID 前8位
    paper_name       TEXT NOT NULL,
    pdf_url          TEXT,
    status           TEXT NOT NULL DEFAULT 'queued'
                     CHECK(status IN ('queued','pending','researching','generating',
                                      'completed','failed','cancelled')),
    sort_order       INTEGER NOT NULL DEFAULT 0,
    message          TEXT NOT NULL DEFAULT '',
    error            TEXT,
    result           TEXT,                         -- JSON 字符串
    logs             TEXT NOT NULL DEFAULT '[]',   -- JSON 数组字符串
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
```

### 4.6 _migrations 表

```sql
CREATE TABLE _migrations (
    key        TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
```

已执行迁移：

- `clear_auto_tags_v1`: 清空所有自动标签
- `clear_garbage_tags_v2`: 清除垃圾标签（MathJax 残留、特殊字符等）

---

## 5. 后端 API 设计

基础路径: `/api`

### 5.1 论文 CRUD

| 方法   | 路径                 | 说明                           | 请求体          | 响应                                               |
| ------ | -------------------- | ------------------------------ | --------------- | -------------------------------------------------- |
| GET    | `/api/stats`       | 统计信息                       | —              | `{ total, by_status, by_year, recent, unfiled }` |
| GET    | `/api/papers`      | 论文列表（筛选/搜索/分页）     | Query params    | `{ items, total, limit, offset }`                |
| GET    | `/api/papers/{id}` | 单篇详情                       | —              | `PaperResponse`                                  |
| POST   | `/api/papers`      | 添加论文                       | `PaperCreate` | `PaperResponse`                                  |
| PUT    | `/api/papers/{id}` | 更新论文                       | `PaperUpdate` | `PaperResponse`                                  |
| DELETE | `/api/papers/{id}` | 删除论文（同时删除 HTML 文件） | —              | `{ ok: true }`                                   |

**GET /api/papers 查询参数**：

- `status`: 状态筛选 (`reading`/`read`/`archived`/`todo`)
- `tag`: 标签筛选（精确匹配 tag name）
- `year`: 年份筛选
- `folder_id`: 文件夹筛选（0=未分类）
- `search`: 全文搜索（标题、作者、摘要、arXiv ID，LIKE 匹配）
- `sort`: 排序字段 (`date_added`/`date_updated`/`title`/`year`/`rating`)
- `order`: 排序方向 (`asc`/`desc`)
- `limit`: 每页数量 (1-200, 默认50)
- `offset`: 偏移量

### 5.2 批量操作

| 方法 | 路径                         | 说明         | 请求体                          |
| ---- | ---------------------------- | ------------ | ------------------------------- |
| POST | `/api/papers/batch/status` | 批量修改状态 | `{ ids: [int], status: str }` |
| POST | `/api/papers/batch/delete` | 批量删除     | `{ ids: [int] }`              |
| POST | `/api/papers/batch/tags`   | 批量添加标签 | `{ ids: [int], tags: [str] }` |

### 5.3 文件夹

| 方法   | 路径                  | 说明                                           |
| ------ | --------------------- | ---------------------------------------------- |
| GET    | `/api/folders`      | 文件夹列表（含 paper_count）                   |
| POST   | `/api/folders`      | 创建文件夹                                     |
| PUT    | `/api/folders/{id}` | 更新文件夹                                     |
| DELETE | `/api/folders/{id}` | 删除文件夹（级联删除子文件夹，论文移至未分类） |
| POST   | `/api/folders/move` | 移动论文到文件夹                               |

### 5.4 精读 HTML

| 方法 | 路径                             | 说明                                                  |
| ---- | -------------------------------- | ----------------------------------------------------- |
| GET  | `/api/papers/{id}/summary`     | 获取精读 HTML 内容                                    |
| PUT  | `/api/papers/{id}/summary`     | 上传精读 HTML (file upload)                           |
| GET  | `/api/papers/{id}/export-html` | 导出精读 HTML（清理"返回主界面"按钮，返回附件下载）   |
| POST | `/api/upload-summary`          | 上传并自动导入（解析 HTML 元数据，自动创建/更新论文） |
| POST | `/api/scan-orphan-html`        | 扫描根目录孤立的 HTML，自动移入 papers/ 并导入        |

### 5.5 文件同步

| 方法 | 路径          | 说明                                                 |
| ---- | ------------- | ---------------------------------------------------- |
| POST | `/api/sync` | 同步 papers/ 目录（扫描 .html 文件，自动导入缺失的） |

### 5.6 元数据

| 方法 | 路径           | 说明                 |
| ---- | -------------- | -------------------- |
| GET  | `/api/tags`  | 所有标签（含 count） |
| GET  | `/api/years` | 所有年份             |

### 5.7 单篇 AI 研究

| 方法 | 路径                                 | 说明              |
| ---- | ------------------------------------ | ----------------- |
| POST | `/api/research/start`              | 创建研究任务      |
| GET  | `/api/research/{task_id}`          | 查询研究进度      |
| POST | `/api/research/{task_id}/complete` | 研究完成提交 HTML |
| POST | `/api/research/{task_id}/log`      | 上报进度日志      |
| POST | `/api/research/{task_id}/fail`     | 标记研究失败      |
| GET  | `/api/research/pending`            | 待处理任务列表    |
| GET  | `/api/research/active`             | 活跃任务列表      |

**单篇研究约束**：同一时间只允许一个活跃任务（内存存储 `_research_tasks` dict）。

### 5.8 批量 AI 研究

| 方法 | 路径                                                       | 说明                |
| ---- | ---------------------------------------------------------- | ------------------- |
| POST | `/api/research/batch/start`                              | 创建批量研究队列    |
| GET  | `/api/research/batch/{batch_id}`                         | 查询批量研究状态    |
| GET  | `/api/research/batch/active`                             | 活跃批量任务        |
| GET  | `/api/research/batch/latest`                             | 最新批量状态        |
| POST | `/api/research/batch/{batch_id}/cancel`                  | 取消批量研究        |
| POST | `/api/research/batch/{batch_id}/add`                     | 追加论文到队列      |
| POST | `/api/research/batch/{batch_id}/items/{task_id}/remove`  | 移除单个任务        |
| POST | `/api/research/batch/{batch_id}/items/{task_id}/reorder` | 调整顺序 (up/down)  |
| POST | `/api/research/batch/{batch_id}/items/{task_id}/stop`    | 停止单个任务        |
| POST | `/api/research/batch/{batch_id}/items/{task_id}/retry`   | 重试失败/取消的任务 |

**批量研究存储**：SQLite `batch_queue` 表，全局单队列 `QUEUE_ID = "default"`。

### 5.9 精读页面访问

| 方法 | 路径             | 说明                                                                 |
| ---- | ---------------- | -------------------------------------------------------------------- |
| GET  | `/read/{slug}` | 渲染精读 HTML（注入"返回主界面"按钮；`?embed=1` 时移除左侧导航栏） |
| GET  | `/`            | 主界面 (index.html)                                                  |

---

## 6. Pydantic 数据模型

### PaperCreate

```
slug: str (pattern: ^[a-z0-9][a-z0-9\-]*$)       # 必填，如 "2026-chainflow-vla"
title: str                                        # 必填
authors: str = ""
institution: str = ""
year: int | None
arxiv_id: str | None
pdf_url: str | None
arxiv_url: str | None
github_url: str | None
project_url: str | None
openreview_url: str | None
conference: str | None
accept_status: str | None
one_line_summary: str | None
research_question: str | None
core_method: str | None
main_result: str | None
target_audience: str | None
tags: list[str] = []
status: str = "reading"
rating: int | None (1-5)
notes: str | None
date_published: str | None
```

### PaperUpdate

同 PaperCreate，所有字段 Optional。

### PaperResponse

```
id, slug, title, authors, institution, year,
arxiv_id, pdf_url, arxiv_url, github_url, project_url, openreview_url,
conference, accept_status, one_line_summary,
research_question, core_method, main_result, target_audience,
tags: list[str], status, rating, notes, folder_id,
date_added, date_updated, date_published,
summary_html_exists: int, html_enriched: int
```

### ResearchRequest

```
paper_name: str (min_length=1)
pdf_url: str | None
```

### ResearchLogRequest

```
text: str (min_length=1)
type: str = "info" (info|search|reading|generating|system|error)
status: str | None (pending|researching|generating|completed|failed)
```

### FolderCreate / FolderUpdate

```
name: str (1-50 chars)
color: str (hex color, default "#6366f1")
parent_id: int | None
```

### BatchResearchRequest

```
papers: list[BatchResearchItem] (1-50)
  BatchResearchItem: { paper_name: str, pdf_url: str | None }
```

---

## 7. HTML 元数据自动解析

**核心函数**: `_parse_html_metadata(html_content: str) -> dict`

从精读 HTML 中自动提取以下元数据，用于补全论文记录：

| 字段              | 提取策略                                                                                                                |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------- |
| title             | `<title>` 标签，去除"论文精读"后缀；fallback `<h1>`                                                                 |
| authors           | `<meta name="citation_author">`, `<div class="authors">`, `<strong>` 名字模式, `.meta-line` 模式                |
| year              | `citation_date`, `citation_publication_date`, `article:published_time` meta；arXiv ID 前缀推算；正则匹配 4 位年份 |
| arxiv_id          | `citation_arxiv_id` meta；正文前 20000 字符正则 `\d{4}\.\d{4,5}`                                                    |
| pdf_url           | `citation_pdf_url` meta；`.pdf` 链接正则                                                                            |
| arxiv_url         | 由 arxiv_id 推导 `https://arxiv.org/abs/{id}`                                                                         |
| github_url        | `github.com/xxx/yyy` 正则                                                                                             |
| project_url       | `xxx.github.io/yyy` 或 `/projects/` URL 正则                                                                        |
| conference        | `citation_journal_title` meta；表格"会议/期刊"行                                                                      |
| one_line_summary  | `description` meta；"一句话总结" callout/card/summary-box 多种模板                                                    |
| institution       | `citation_author_institution` meta；"机构：" `<strong>` 标签；`.authors` 管道分割；`.affiliations` div          |
| date_published    | "arXiv 首次提交日期"表格行；中文日期/ISO 日期正则                                                                       |
| research_question | `.label/.value`, `.meta-label/.meta-value`, `<strong>研究问题</strong>`, `<tr>` 表格行                          |
| core_method       | 同上模式                                                                                                                |
| main_result       | 同上模式                                                                                                                |
| target_audience   | 同上模式 +`.card-title` "适合什么读者？" 模式                                                                         |
| section_titles    | `<nav>` 内链接文本提取                                                                                                |

**触发时机**：

1. 列表查询时，对 `html_enriched=0` 且 `summary_html_exists=1` 的论文自动解析
2. 单篇详情查询时，同上
3. 上传精读 HTML 后强制解析
4. AI 研究完成后强制解析
5. 文件同步后强制解析

---

## 8. AI 研究工作流

### 8.1 单篇研究

```
用户输入论文名 + 可选 PDF URL
        ↓
POST /api/research/start → 返回 task_id
        ↓
worker 线程轮询 (3秒间隔)
        ↓
取出 pending 任务
        ↓
subprocess 调用 opencode CLI:
  opencode run "<prompt>" -f AGENT_MD_PATH -m huawei/glm5.1 --dangerously-skip-permissions
        ↓
等待 opencode 完成 (超时 7200秒)
        ↓
检测 papers/ 目录新增 HTML 文件
        ↓
解析 HTML 元数据 → 重命名为 <slug>.html
        ↓
complete_research_task → 写入 papers/<slug>.html
        ↓
自动创建/更新 papers 数据库记录 + HTML enrich
```

**opencode 命令**：

```bash
opencode run "请精读这篇论文（PDF 链接）：{pdf_url or paper_name}

请严格按照附带的 AGENT.md 规范生成精读HTML页面。
要求：
1. 只生成一个 .html 文件，保存到 papers/ 目录下
2. 不要在 papers/ 目录下生成任何非 HTML 文件
3. HTML 文件必须完全自包含（内联 CSS/JS），可离线打开
4. 图表使用 base64 内嵌" \
  -f AGENT_MD_PATH \
  -m huawei/glm5.1 \
  --dangerously-skip-permissions
```

### 8.2 批量研究

```
用户添加多篇论文到列表
        ↓
POST /api/research/batch/start → 创建 batch_queue 行 (status=queued)
        ↓
batch worker 线程轮询 (5秒间隔)
        ↓
取出下一个 queued 任务 → 设为 pending
        ↓
同单篇流程调用 opencode
        ↓
完成后 → batch_queue_complete_task
        ↓
自动创建 papers 记录 + 打 "🔍AI研究" 标签 + 移入 "AI研究论文" 文件夹
        ↓
处理下一个 queued 任务
```

**批量研究特性**：

- 全局单队列 (`QUEUE_ID = "default"`)
- 支持追加 (`/batch/{id}/add`)
- 支持单个任务停止/重试/移除/排序
- 服务重启后自动恢复中断的任务（`pending/researching/generating` → `queued`）
- 完成的论文自动放入 "AI研究论文" 文件夹（紫色 #8b5cf6）

---

## 9. 前端设计

### 9.1 页面布局

```
┌──────────────────────────────────────────────────────────┐
│                     .app-layout (flex)                    │
├─────────────┬────────────────────────────────────────────┤
│             │            .main-content                    │
│  .sidebar   │  ┌──────────────────────────────────────┐  │
│  (fixed,    │  │  .topbar (sticky, blur backdrop)     │  │
│  260px,     │  │  [☰] [🔍 搜索框]  [年份▼] [排序▼]   │  │
│  resizable) │  │       [上传] [添加论文] [AI研究] [批量]│  │
│             │  ├──────────────────────────────────────┤  │
│ ┌─────────┐ │  │  .content-area (max-width: 1200px)  │  │
│ │ LOGO    │ │  │                                      │  │
│ │ Paper   │ │  │  ┌─ .stats-row ──────────────────┐  │  │
│ │ Manager │ │  │  │ 全部 │ 在读 │ 已读 │ 待读      │  │  │
│ └─────────┘ │  │  └───────────────────────────────┘  │  │
│             │  │                                      │  │
│ .sidebar-   │  │  ┌─ .selected-bar ────────────────┐  │  │
│   nav       │  │  │ 已选 N 篇 [已读][在读][删除]... │  │  │
│             │  │  └────────────────────────────────┘  │  │
│ ┌─────────┐ │  │                                      │  │
│ │ 状态     │ │  │  ┌─ .paper-grid ─────────────────┐  │  │
│ │ 全部  N  │ │  │  │  ┌─ .paper-card ───────────┐  │  │
│ │ 在读  N  │ │  │  │  │ ☐ 标题                    │  │  │
│ │ 已读  N  │ │  │  │  │ 作者 · 2026 · 会议        │  │  │
│ │ 待读  N  │ │  │  │  │ 一句话总结...             │  │  │
│ │ 归档  N  │ │  │  │  │ [在读] [核心方法] [tag]   │  │  │
│ └─────────┘ │  │  │  │ [PDF][arXiv] [打开][导出] │  │  │
│ ┌─────────┐ │  │  │  └──────────────────────────┘  │  │
│ │ 文件夹   │ │  │  │  ┌─ .paper-card ───────────┐  │  │
│ │ 未分类 N │ │  │  │  │ ...                        │  │  │
│ │ 📁  AI  │ │  │  │  └──────────────────────────┘  │  │
│ │ 📁 自动  │ │  │  └────────────────────────────────┘  │  │
│ └─────────┘ │  │                                      │  │
│ ┌─────────┐ │  │  ┌─ .pagination ──────────────────┐  │  │
│ │ 标签     │ │  │  │ « 1 2 3 » 共 N 篇              │  │  │
│ │ 🔍AI研究 │ │  │  └────────────────────────────────┘  │  │
│ │ VLA      │ │  │                                      │  │
│ │ ...      │ │  └──────────────────────────────────────┘  │
│ └─────────┘ │                                            │
│             │  ┌─ .detail-view (隐藏) ─────────────────┐  │
│ ┌─────────┐ │  │  [← 返回列表] [编辑] [导出] [打开]   │  │
│ │ 上传HTML │ │  │  标题                                │  │
│ │ AI研究   │ │  │  作者 · 机构                          │  │
│ │ 批量研究 │ │  │  一句话总结 callout                   │  │
│ │ 自动归档 │ │  │  元数据网格 (arXiv/年份/会议/...)     │  │
│ │ 同步文件 │ │  │  资源链接 [PDF][arXiv][GitHub]       │  │
│ └─────────┘ │  │  标签                                 │  │
│             │  │  ┌─ iframe (精读预览) ──────────────┐ │  │
│ ┌─────────┐ │  │  │                                    │ │  │
│ │ repo链接 │ │  │  │  /read/{slug}?embed=1             │ │  │
│ └─────────┘ │  │  │                                    │ │  │
│             │  │  └────────────────────────────────────┘ │  │
│             │  └──────────────────────────────────────────┘  │
└─────────────┴────────────────────────────────────────────────┘
```

### 9.2 侧边栏 (Sidebar)

**固定定位**，宽度 260px，可拖拽调整 (200-420px)。

组成部分：

1. **Header**: 渐变紫色 logo + "Paper Manager" / "论文精读与管理"
2. **Nav 区域** (可滚动)：
   - **状态区**: 全部/在读/已读/待读/归档 + 计数徽章
   - **文件夹区**: 未分类 + 树形文件夹（可展开/折叠），每个文件夹有右键菜单（新建子文件夹/重命名/删除）
   - **标签区**: 显示前15个标签（过滤垃圾标签），带计数
3. **Footer 按钮**:
   - 上传精读 HTML
   - AI 研究论文
   - 批量 AI 研究
   - 自动归档精读
   - 同步文件系统
4. **底部**: 仓库链接 + 反馈链接

**移动端**：侧边栏隐藏，点击 ☰ 按钮滑出 + 遮罩层。

### 9.3 顶部工具栏 (Topbar)

**Sticky 定位**，毛玻璃背景 (`backdrop-filter: blur(12px)`)。

布局（左→右）：

1. 移动端菜单按钮 (☰)
2. 搜索框（实时搜索，300ms 防抖，搜索标题/作者/摘要/arXiv ID）
3. 年份筛选下拉
4. 排序下拉（最近添加/最近更新/标题A-Z/年份新→旧/评分高→低）
5. 上传按钮
6. 添加论文按钮
7. AI 研究按钮（紫色）
8. 批量研究按钮（深紫色）
9. 研究中徽章（动态显示）

### 9.4 统计卡片行 (Stats Row)

4 列网格：全部 / 在读(紫色) / 已读(绿色) / 待读(橙色)

### 9.5 论文卡片网格 (Paper Grid)

`grid-template-columns: repeat(auto-fill, minmax(340px, 1fr))`

**单张卡片结构**：

```
┌─────────────────────────────────────┐
│ ☐  论文标题（最多2行）               │
│ 作者前3名 et al. · 2026 · CVPR      │
│ 一句话总结（最多2行）                 │
│ [在读] [Diffusion Policy] [VLA] [RL] │
│ ─────────────────────────────────── │
│ [PDF] [arXiv]     [📄][⬇][›][✏]     │
└─────────────────────────────────────┘
```

卡片元素：

- **Checkbox**: 左上角，用于批量选择
- **标题**: 加粗，2行截断
- **元信息**: 作者(前3+et al.) / 年份 / 发布日期 / 会议 / 星级评分
- **一句话总结**: 灰色，2行截断
- **标签区**: 状态标签(彩色) + 核心方法标签(紫色) + 普通标签(蓝色) + AI研究标签(紫色渐变)
- **Footer**: 资源链接 + 操作按钮
  - 📄 打开精读页 (`/read/{slug}`)
  - ⬇ 导出 HTML 分享
  - › 查看详情
  - ✏ 编辑

**交互**：

- Hover: 边框高亮 + 阴影 + 上移 1px
- Selected: 蓝色背景 + 蓝色边框

### 9.6 详情视图 (Detail View)

点击卡片 › 按钮进入，替换列表视图。

布局：

```
[← 返回列表]                    [在读] ★★★★ [编辑] [导出HTML] [打开精读页]
标题
作者 · 机构

┌ 一句话总结 ──────────────────────────────────────────┐
│ 本文提出...                                           │
└───────────────────────────────────────────────────────┘

元数据网格 (2-4列自适应):
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ arXiv ID │ │ 年份     │ │ 发布日期 │ │ 会议     │
│ 2605.xxx │ │ 2026     │ │ 2026-05  │ │ CVPR     │
└──────────┘ └──────────┘ └──────────┘ └──────────┘
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│ 研究问题 │ │ 核心方法 │ │ 主要结果 │ │ 适合读者 │
│ ...      │ │ ...      │ │ ...      │ │ ...      │
└──────────┘ └──────────┘ └──────────┘ └──────────┘

[PDF] [arXiv] [GitHub] [Project] [OpenReview]

标签: [在读] [VLA] [🔍AI研究]

精读预览:
┌───────────────────────────────────────────────────────┐
│                                                       │
│  <iframe src="/read/{slug}?embed=1" />                │
│  高度: calc(100vh - 200px)                            │
│                                                       │
└───────────────────────────────────────────────────────┘
```

**iframe embed 模式**：`?embed=1` 时，后端自动移除精读 HTML 中的 `<nav class="nav-sidebar">` 及其 CSS 样式，让内容在 iframe 中无侧边栏显示。

### 9.7 批量选择操作栏 (Selected Bar)

选中论文后出现在统计卡片下方：

```
┌─ .selected-bar ────────────────────────────────────────────────────┐
│ 已选 3 篇  [标记已读] [标记在读] [标记待读] [归档] [删除] [添加标签] [移至文件夹] [导出HTML] [取消选择] │
└────────────────────────────────────────────────────────────────────┘
```

### 9.8 分页 (Pagination)

居中显示，每页 30 条，显示当前页前后各2页的页码。

### 9.9 模态框 (Modals)

#### 添加论文模态框

```
┌─ 添加论文 ────────────────────── × ─┐
│                                     │
│ 目录名 (slug)*  │  年份            │
│ 标题*                              │
│ 作者                                │
│ 机构                                │
│ arXiv ID     │  状态 ▼             │
│ PDF URL                             │
│ GitHub URL                          │
│ 一句话总结                          │
│ 标签 (逗号分隔)                     │
│ 备注                                │
│                                     │
│              [取消]  [添加]         │
└─────────────────────────────────────┘
```

#### 编辑论文模态框

同添加，预填数据，底部多一个"删除"按钮。

#### 批量添加标签模态框

简单：标签输入框（逗号分隔）。

#### 新建文件夹模态框

```
┌─ 新建文件夹 ──────────────────── × ─┐
│                                     │
│ 文件夹名称                          │
│ 颜色: ● ● ● ● ● ● ● ● ● ●         │
│       (10色选择器)                   │
│ 父文件夹: [根目录 ▼]               │
│                                     │
│              [取消]  [创建]         │
└─────────────────────────────────────┘
```

#### 移至文件夹模态框

树形文件夹列表，可点击选择目标文件夹，含"未分类"选项。

#### AI 研究论文模态框

```
┌─ 🔮 AI 研究论文 ──────────────── × ─┐
│                                     │
│ ℹ 输入论文名，创建研究任务。opencode  │
│ 将按照 AGENT.md 规范生成精读 HTML。 │
│ 一次只能运行一个研究任务。           │
│                                     │
│ 论文名称*                           │
│ PDF URL (可选)                      │
│                                     │
│ ┌─ 研究进度面板 ──────────────────┐ │
│ │ 🔄 正在研究...     任务ID: xxx  │ │
│ │ ┌─────────────────────────────┐ │ │
│ │ │ 12:30:01 🔍 开始研究...     │ │ │
│ │ │ 12:30:05 🔍 调用 opencode...│ │ │
│ │ │ 12:35:22 ✅ HTML 已生成     │ │ │
│ │ │ 12:35:23 ✅ 研究完成        │ │ │
│ │ └─────────────────────────────┘ │ │
│ └─────────────────────────────────┘ │
│                                     │
│  [关闭(后台继续)]  [🔮 开始研究]    │
└─────────────────────────────────────┘
```

**研究进度面板**：

- Spinner + 状态文本
- 日志列表（滚动，最新在底部）
- 日志条目：时间 + 图标(按type) + 文本
- type 颜色：system=灰, search=蓝, reading=紫, generating=橙, info=蓝, error=红
- 完成后：绿色勾 + "打开精读页"链接，2秒后自动关闭
- 失败后：红色叉 + 错误信息

**研究中徽章**：关闭模态框后，顶部工具栏显示旋转 spinner + "AI 研究中" 按钮，点击可重新打开模态框。

**持久化**：activeResearch 存 localStorage，页面刷新后恢复。

#### 批量 AI 研究模态框

```
┌─ 🔮 批量 AI 研究 ─────────────── × ─┐
│                                     │
│ ℹ 添加多篇论文到研究队列，系统将    │
│ 按顺序逐一调用 opencode 生成精读。 │
│ 完成的论文自动打 "🔍AI研究" 标签   │
│ 并放入 "AI研究论文" 文件夹。       │
│                                     │
│ [论文名称输入]  [PDF URL]  [➕添加] │
│                                     │
│ ┌─ 待研究列表 ──────────────────┐   │
│ │ 1  ProgVLA: Progress-Aware... ×│   │
│ │ 2  Cosmos: World Model...    ×│   │
│ └──────────────────────────────┘   │
│                                     │
│ ┌─ 队列进度 ──────────────────────┐ │
│ │ 研究进行中  完成 2 · 失败 0    │ │
│ │ ━━━━━━━━━━━━━░░░░░ 40%         │ │
│ │                                 │ │
│ │ ✅ 1  ChainFlow-VLA     打开→  │ │
│ │ 🔄 2  ProgVLA          正在.. │ │
│ │ ⏳ 3  Cosmos            排队中 │ │
│ │ ⏳ 4  NaVILA             排队中 │ │
│ └─────────────────────────────────┘ │
│                                     │
│  [关闭]  [➕ 添加到队列]  [⏹ 停止全部] │
└─────────────────────────────────────┘
```

**队列中每个任务行**：

- 排队中(⏳): 上移/下移/移除按钮
- 进行中(🔄): 停止按钮
- 完成(✅): 打开精读页链接 + 移除按钮
- 失败(❌): 重试 + 移除按钮

**批量研究中徽章**：工具栏显示旋转 spinner + "批量研究中" 按钮。

### 9.10 拖拽上传

整个页面支持 HTML 文件拖拽导入：

- `dragenter`: 显示半透明蓝色遮罩层
- `drop`: 自动调用 uploadFiles API
- 支持多文件同时上传

### 9.11 Toast 通知

右下角弹出通知，3.5秒自动消失，三种样式：

- success (绿色背景)
- error (红色背景)
- info (紫色背景)

---

## 10. 精读页面规范 (AGENT.md)

精读页面由 AI 按 AGENT.md 规范生成，关键要求：

### 10.1 页面结构 (14节)

1. **论文概览** — 标题、作者、机构、一句话总结、研究问题、核心方法、主要结果、适合读者、资源按钮
2. **投稿与资源信息** — arXiv 日期、PDF/arXiv/Project/GitHub/OpenReview/Conference 链接
3. **背景与动机** — 方向背景、现有 pipeline、瓶颈、motivation figure
4. **核心问题** — 输入/输出、baseline 失败模式、建模假设
5. **方法总览** — pipeline 全景图、数据流、训练/推理阶段
6. **核心方法精读** — 按模块拆解（动机→机制→输入输出→训练目标→公式→图表证据→局限）
7. **实验设置** — benchmark、split、metric 含义、如何读指标
8. **主要实验结果** — 按结论组织（非按表格编号）
9. **消融分析** — 按验证问题组织（非按 Table 编号）
10. **可视化与失败案例** — 定性结果、失败模式分析
11. **优点、不足与隐含假设** — 方法/实验/工程层面
12. **对后续研究/工程的启发** — 研究方向 + 工程落地
13. **总结** — 核心贡献、最有力证据、最大局限、后续方向
14. **附录: PDF 解析自检** — 折叠区域，不干扰正文

### 10.2 页面风格

- 左侧固定导航栏 (nav-sidebar)
- 正文居中（导航栏右侧区域水平居中）
- MathJax 公式渲染
- 图表 base64 内嵌（离线可用）
- callout/card/summary box 强调关键结论
- 表格 HTML 复刻优先，复杂大表可图片裁剪

### 10.3 进度日志上报

AI 研究过程中通过 `POST /api/research/{task_id}/log` 上报进度：

- type: `system`/`search`/`reading`/`generating`/`info`/`error`
- status: `pending`/`researching`/`generating`/`completed`/`failed`

建议上报时机：开始搜索→找到PDF→开始读取→分析结构→提取图表→开始生成→生成完毕

---

## 11. 关键业务流程

### 11.1 上传精读 HTML 自动导入

```
用户拖拽/选择 .html 文件
        ↓
POST /api/upload-summary (FormData)
        ↓
读取 HTML 内容 → _parse_html_metadata()
        ↓
生成 slug = <year>-<normalized-title>
        ↓
检查 slug 是否已存在:
  已存在 → 更新 HTML + enrich
  不存在 → 写入 papers/<slug>.html
           + 创建 papers 记录 (status=reading)
           + enrich
        ↓
返回 { ok, paper_id, slug, title, action, parsed }
```

### 11.2 精读页面渲染

```
GET /read/{slug}?embed=0
        ↓
读取 papers/<slug>.html
        ↓
if embed=1:
  移除 <nav class="nav-sidebar"> 及其 CSS
  移除 margin-left: var(--nav-width)
        ↓
注入 "返回主界面" 按钮:
  固定定位，左上角，紫色渐变，z-index: 99999
  <a href="/">← 返回主界面</a>
        ↓
返回 HTMLResponse
```

### 11.3 导出 HTML

```
GET /api/papers/{id}/export-html
        ↓
读取 HTML → _clean_html_for_sharing()
  (移除 "返回主界面" 按钮)
        ↓
返回附件下载: Content-Disposition: attachment; filename="<slug>.html"
```

### 11.4 slug 生成规则

```python
def _slug_from_title(title, year=None):
    t = title.lower()
    # 移除 "论文精读/Paper Reading/Summary" 后缀
    t = re.sub(r'[-–—]\s*(论文精读|精读|paper\s*reading|summary)\s*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'[^\w\s-]', '', t)           # 移除非字母数字
    t = re.sub(r'[\s_]+', '-', t).strip('-') # 空格→连字符
    t = re.sub(r'-+', '-', t)                # 合并连续连字符
    t = t[:60].rstrip('-')                   # 截断60字符
    prefix = f"{year}-" if year else ""
    return f"{prefix}{t}"
```

示例：`"ChainFlow-VLA: ..."` + year=2026 → `"2026-chainflow-vla"`

---

## 12. 样式设计规范

### 12.1 CSS 变量

```css
:root {
  --bg: #ffffff;
  --bg-alt: #f8fafc;
  --bg-hover: #f1f5f9;
  --text: #1e293b;
  --text-secondary: #64748b;
  --text-muted: #94a3b8;
  --primary: #3b82f6;
  --primary-hover: #2563eb;
  --primary-light: #dbeafe;
  --primary-bg: #eff6ff;
  --border: #e2e8f0;
  --success: #10b981;
  --danger: #ef4444;
  --warning: #f59e0b;
  --info: #6366f1;
  --radius: 8px;
  --radius-lg: 12px;
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans SC", sans-serif;
  --sidebar-w: 260px;
  --topbar-h: 52px;
}
```

### 12.2 标签颜色方案

| 标签类型        | 背景                                 | 文字                    |
| --------------- | ------------------------------------ | ----------------------- |
| topic           | `--primary-bg` (#eff6ff)           | `--primary` (#3b82f6) |
| method          | `--info-bg` (#e0e7ff)              | `--info` (#6366f1)    |
| status-reading  | `--info-bg`                        | `--info`              |
| status-read     | `--success-bg` (#d1fae5)           | `--success` (#10b981) |
| status-todo     | `--warning-bg` (#fef3c7)           | `--warning` (#f59e0b) |
| status-archived | `--bg-alt`                         | `--text-muted`        |
| AI研究          | 渐变 #f5f3ff→#ede9fe + 边框 #c4b5fd | #6d28d9                 |

### 12.3 响应式断点

- `≤900px`: 侧边栏隐藏(滑出)，卡片单列，统计2列，表单单列
- `901-1200px`: 卡片 `minmax(300px, 1fr)`
- `>1200px`: 卡片 `minmax(340px, 1fr)`

---

## 13. 环境变量

| 变量             | 默认值                                   | 说明         |
| ---------------- | ---------------------------------------- | ------------ |
| `PAPER_HOST`   | `0.0.0.0`                              | 监听地址     |
| `PAPER_PORT`   | `8000` (开发) / `8080` (打包)        | 监听端口     |
| `GLM_API_KEY`  | —                                       | GLM API 密钥 |
| `GLM_API_BASE` | `https://open.bigmodel.cn/api/paas/v4` | API 基础 URL |
| `GLM_MODEL`    | `glm5.1`                               | 使用的模型   |

---

## 14. 打包与部署

### 14.1 PyInstaller 打包

- 入口: `main.py`
- frozen 检测: `getattr(sys, "frozen", False)`
- 打包模式: `APP_ROOT = dirname(sys.argv[0])`，前端在 `_internal/frontend/`
- 数据库/论文存储在 `APP_ROOT` 下（非 `_internal`）

### 14.2 开发模式

- 入口: `run.py` 或 `python -m server.server`
- `APP_ROOT = dirname(dirname(__file__))`
- 前端在 `frontend/`
- 默认端口 8000

### 14.3 启动流程

```
1. os.makedirs("data/", exist_ok=True)
2. os.makedirs("papers/", exist_ok=True)
3. 验证 FRONTEND_DIR 存在
4. db.init_db() → 建表 + 迁移
5. worker.start_worker() → 启动两个守护线程:
   - research-worker: 轮询单篇研究任务 (3秒)
   - batch-research-worker: 轮询批量任务 (5秒)
6. uvicorn.run(app, host, port)
7. webbrowser.open(url) (尝试自动打开)
```

---

## 15. 复现指南

### 15.1 环境准备

```bash
# Python 3.10+
pip install fastapi>=0.100.0 uvicorn>=0.23.0 python-multipart>=0.0.6 \
            aiofiles>=23.0 pydantic>=2.0 pymupdf>=1.24.0 httpx>=0.25.0

# 可选: 安装 opencode CLI (AI 研究功能依赖)
# 须配置 GLM_API_KEY 环境变量
```

### 15.2 目录结构创建

```bash
mkdir -p server frontend papers data
```

### 15.3 后端文件

按以下顺序创建：

1. `server/paths.py` — 路径常量
2. `server/__init__.py` — 空
3. `server/models.py` — Pydantic 模型
4. `server/db.py` — 数据库操作
5. `server/research.py` — 研究任务管理
6. `server/worker.py` — 工作线程
7. `server/server.py` — FastAPI 路由（依赖上述所有模块）

### 15.4 前端文件

1. `frontend/index.html` — 主页面骨架
2. `frontend/style.css` — 全局样式
3. `frontend/app.js` — SPA 逻辑

### 15.5 启动

```bash
python run.py
# 访问 http://localhost:8000
```

### 15.6 验证清单

- [ ] 首页加载，侧边栏显示空状态
- [ ] 添加论文 (slug + title 必填)
- [ ] 编辑论文 (所有字段可修改)
- [ ] 删除论文 (确认对话框)
- [ ] 状态筛选 (在读/已读/待读/归档)
- [ ] 标签筛选
- [ ] 年份筛选
- [ ] 文件夹筛选
- [ ] 全文搜索 (防抖)
- [ ] 排序 (5种排序+正逆序)
- [ ] 分页
- [ ] 批量选择 + 批量操作
- [ ] 上传 HTML 自动导入 + 元数据解析
- [ ] 拖拽上传
- [ ] 精读页 iframe 预览 (embed 模式)
- [ ] 精读页独立打开 (`/read/{slug}`)
- [ ] 导出 HTML (清理返回按钮)
- [ ] 文件夹 CRUD (树形+颜色)
- [ ] 文件夹移动论文
- [ ] 同步文件系统
- [ ] 自动归档精读 HTML
- [ ] AI 研究 (单篇 + 批量)
- [ ] 研究进度实时展示
- [ ] 研究中徽章
- [ ] 页面刷新后恢复研究状态