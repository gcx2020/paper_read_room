# Paper Read Room

一个本地论文阅读室项目，用来管理论文精读 HTML、上传已有解读文件，并通过 Codex 按照当前仓库的 `AGENTS.md` 要求研究论文。研究结果统一落在 `papers/` 目录下，前端可以直接预览和检索。

## 功能

- 论文库管理：展示标题、索引号、论文类型、分类、关键词、阅读状态、更新时间和备注。
- 阅读管理：支持未读、阅读中、已读、归档状态，支持阅读备注、标签、优先级和批注。
- 分类检索：按标题、索引号、备注、论文类型、分类和关键词检索。
- HTML 解读上传：上传完整的离线 `.html` 论文解读文件，自动保存到 `papers/<slug>/index.html`。
- 新增论文研究任务：填写论文名称、索引号或 PDF/Web 链接，后端调用 Codex 读取 `AGENTS.md` 并生成完整论文精读页面。
- 前端预览：在工作台中直接预览 `papers/` 下的 HTML 页面。
- 任务队列：查看 Codex 研究任务状态、输出路径、错误信息和最近日志。
- Python 后端：只依赖 Python 标准库，无需安装数据库或前端构建工具。

## 项目目录

```text
paper_read_room/
  AGENTS.md              # Codex 研究论文时必须遵守的精读要求
  README.md              # 项目说明
  package.json           # 便捷启动脚本
  data/
    papers.json          # 论文元数据
    jobs.json            # 任务队列状态，运行后自动生成
  papers/
    <paper-slug>/
      index.html         # 最终离线论文解读页面
      assets/            # 裁剪图表、局部截图等资源
  public/
    index.html           # 前端工作台
    styles.css           # 页面样式
    app.js               # 前端交互逻辑
  server/
    app.py               # Python HTTP 服务、API、JSON 存储和 Codex 调用
```

## 快速开始

```bash
npm start
```

也可以直接运行 Python 后端：

```bash
python3 server/app.py
```

启动后打开：

```text
http://localhost:5173
```

如果端口被占用，可以指定端口：

```bash
PORT=5180 npm start
```

## Codex 研究任务

前端“Codex 研究”表单支持任意一种输入：

- 论文名称
- 索引号，例如 arXiv ID、OpenReview ID、DOI 或内部编号
- PDF / Web 链接

提交后，后端会拼接当前仓库 `AGENTS.md` 的完整要求，并调用：

```bash
codex exec "<研究提示词>"
```

Codex 需要在任务中完成 PDF 获取、正文解析、图表抽取、公式检查、资源信息核验，并把最终 HTML 写入：

```text
papers/<paper-slug>/index.html
```

如果你的本机 Codex 命令不是 `codex`，可以通过环境变量指定命令前缀：

```bash
CODEX_COMMAND="codex" npm start
```

## API

### 获取论文库

```http
GET /api/papers
```

支持查询参数：

- `q` / `search`：全文搜索标题、索引号、分类、关键词和备注
- `readingStatus`：`unread`、`reading`、`read`、`archived`
- `type`：论文类型
- `category`：分类
- `keyword`：关键词

返回论文列表、最近任务、统计信息和分类索引。

### 手动新增论文元数据

```http
POST /api/papers
Content-Type: application/json

{
  "title": "Paper title",
  "indexId": "arXiv:xxxx.xxxxx",
  "pdfUrl": "https://...",
  "paperType": "Method",
  "categories": ["LLM", "Agent"],
  "keywords": ["planning", "memory"],
  "readingStatus": "unread",
  "notes": "optional notes"
}
```

### 获取 / 更新 / 删除论文

```http
GET /api/papers/{id}
PATCH /api/papers/{id}
DELETE /api/papers/{id}
```

`PATCH` 可更新阅读状态、论文类型、分类、关键词、标签和备注等字段。

### 上传 HTML 解读

```http
POST /api/upload
Content-Type: multipart/form-data
```

字段：

- `title`：论文标题，必填
- `indexId`：索引号，可选
- `pdfUrl`：PDF 或网页链接，可选
- `paperType`：论文类型，可选
- `categories`：分类，逗号分隔，可选
- `keywords`：关键词，逗号分隔，可选
- `notes`：备注，可选
- `file`：完整 HTML 文件，必填

### 创建 Codex 研究任务

```http
POST /api/research
Content-Type: application/json

{
  "title": "Paper title",
  "indexId": "1706.03762",
  "pdfUrl": "https://arxiv.org/pdf/1706.03762",
  "paperType": "Method",
  "categories": ["Transformer", "Sequence Modeling"],
  "keywords": ["attention"],
  "notes": "重点关注方法和消融"
}
```

### 批注接口

```http
GET /api/papers/{id}/annotations
POST /api/papers/{id}/annotations
PATCH /api/papers/{id}/annotations/{annotationId}
DELETE /api/papers/{id}/annotations/{annotationId}
```

新增批注示例：

```json
{
  "section": "Section 4.2",
  "page": "7",
  "quote": "关键原文或图表编号",
  "text": "我的理解、疑问或后续动作"
}
```

### 分类索引和统计

```http
GET /api/taxonomy
GET /api/stats
```

### 查看任务

```http
GET /api/jobs
```

## 验证

```bash
npm run check
```

该命令会检查 Python 后端语法。

## 说明

- `papers/` 是论文解读的最终交付目录，建议将每篇论文的图片、表格裁剪和补充资源放在对应 `assets/` 下。
- 上传 HTML 时，系统只负责入库和保存，不会改写文件内容。
- Codex 研究任务是否能成功取决于本机是否可用 `codex` 命令，以及 Codex 是否能访问论文 PDF 或相关网页。
- `AGENTS.md` 当前包含严格的论文精读生成规范，尤其要求不要在 PDF 正文和图表未成功读取时生成伪完整报告。
