# Paper Reading Manager

个人论文精读管理工具。支持论文元信息管理、精读 HTML 导入/导出、文件夹与标签、单篇和批量 AI 研究任务。

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

## 目录

- `server/`: FastAPI 后端、SQLite 数据库、worker
- `frontend/`: 无框架 SPA
- `papers/`: 精读 HTML 文件
- `data/`: SQLite 数据库
- `AGENT.md`: AI 精读 HTML 生成规范

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
