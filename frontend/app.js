const state = {
  papers: [],
  stats: {},
  tags: [],
  folders: [],
  years: [],
  selected: new Set(),
  filters: { status: "", tag: "", year: "", folder_id: null, search: "", sort: "date_added", order: "desc", limit: 30, offset: 0 },
  currentPaper: null,
  activeResearch: JSON.parse(localStorage.getItem("activeResearch") || "null"),
  activeBatch: null,
  batchTimer: null,
  researchTimer: null,
  theme: null,
};

const FOLDER_COLORS = ["#2563eb", "#4f46e5", "#7c3aed", "#db2777", "#dc2626", "#ea580c", "#d97706", "#059669", "#0891b2", "#475569"];
const THEME_PRESETS = {
  default: { name: "云白蓝", hint: "当前管理页风格", primary: "#2563eb", bg: "#fbfcff", panel: "#ffffff", text: "#172033", border: "#dfe6f1", mode: "glow" },
  ink: { name: "清墨绿", hint: "低饱和阅读", primary: "#0f766e", bg: "#f8fafc", panel: "#ffffff", text: "#111827", border: "#d7e2ea", mode: "solid" },
  githubLight: { name: "GitHub Light", hint: "清爽代码阅读", primary: "#0969da", bg: "#f6f8fa", panel: "#ffffff", text: "#1f2328", border: "#d0d7de", mode: "solid" },
  githubDark: { name: "GitHub Dark", hint: "深色护眼阅读", primary: "#2f81f7", bg: "#0d1117", panel: "#161b22", text: "#e6edf3", border: "#30363d", mode: "solid" },
  night: { name: "深夜蓝", hint: "暗色长读", primary: "#60a5fa", bg: "#0f172a", panel: "#111827", text: "#e5edf8", border: "#28364d", mode: "solid" },
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

async function api(path, options = {}) {
  const init = { headers: {}, ...options };
  if (init.body && !(init.body instanceof FormData)) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(init.body);
  }
  const res = await fetch(path, init);
  const type = res.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(data.detail || data.message || data || `HTTP ${res.status}`);
  return data;
}

function toast(text, type = "info") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = text;
  $("#toastStack").append(el);
  setTimeout(() => el.remove(), 3500);
}

function debounce(fn, ms = 300) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

async function loadAll() {
  initTheme();
  await Promise.all([loadStats(), loadFilters(), loadPapers()]);
  loadRepoLink();
  restoreResearchPolling();
}

async function loadRepoLink() {
  try {
    const repo = await api("/api/repo");
    const linkEl = $("#repoLink");
    if (!linkEl) return;
    if (repo.url) {
      linkEl.href = repo.url.replace(/^git@github.com:/, "https://github.com/").replace(/\.git$/, "");
      const label = repo.url.replace(/^https?:\/\//, "").replace(/\.git$/, "");
      const shortLabel = label.replace(/^github\.com\//, "");
      linkEl.innerHTML = `<span class="repo-icon">GH</span><span class="repo-text"><b>GitHub 仓库</b><small>${escapeHtml(shortLabel)}</small></span><span class="repo-arrow">↗</span>`;
      linkEl.title = repo.url;
    } else {
      linkEl.removeAttribute("href");
      linkEl.innerHTML = `<span class="repo-icon">GH</span><span class="repo-text"><b>GitHub 仓库</b><small>未配置 remote</small></span>`;
    }
  } catch {
    const linkEl = $("#repoLink");
    if (linkEl) linkEl.innerHTML = `<span class="repo-icon">GH</span><span class="repo-text"><b>GitHub 仓库</b><small>读取失败</small></span>`;
  }
}

async function loadStats() {
  state.stats = await api("/api/stats");
  const s = state.stats;
  setText("countAll", s.total || 0);
  setText("statAll", s.total || 0);
  setText("countReading", s.by_status?.reading || 0);
  setText("statReading", s.by_status?.reading || 0);
  setText("countRead", s.by_status?.read || 0);
  setText("statRead", s.by_status?.read || 0);
  setText("countTodo", s.by_status?.todo || 0);
  setText("statTodo", s.by_status?.todo || 0);
  setText("countArchived", s.by_status?.archived || 0);
  setText("countUnfiled", s.unfiled || 0);
}

async function loadFilters() {
  const [tags, years, folders] = await Promise.all([api("/api/tags"), api("/api/years"), api("/api/folders")]);
  state.tags = tags;
  state.years = years;
  state.folders = folders;
  renderTags();
  renderYears();
  renderFolders();
}

async function loadPapers() {
  const q = new URLSearchParams();
  Object.entries(state.filters).forEach(([k, v]) => {
    if (v !== "" && v !== null && v !== undefined) q.set(k, v);
  });
  const data = await api(`/api/papers?${q}`);
  state.papers = data.items;
  renderPapers(data.total);
  renderSelectedBar();
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function renderTags() {
  const root = $("#tagList");
  root.innerHTML = "";
  if (!state.tags.length) {
    return;
  }
  state.tags.slice(0, 15).forEach(tag => {
    const btn = document.createElement("button");
    btn.className = `tag-chip ${state.filters.tag === tag.name ? "active" : ""}`;
    btn.textContent = `${tag.name} ${tag.count}`;
    btn.onclick = () => {
      state.filters.tag = state.filters.tag === tag.name ? "" : tag.name;
      state.filters.offset = 0;
      loadPapers();
      renderTags();
    };
    root.append(btn);
  });
}

function renderYears() {
  const select = $("#yearFilter");
  const current = select.value;
  select.innerHTML = `<option value="">全部年份</option>` + state.years.map(y => `<option value="${y}">${y}</option>`).join("");
  select.value = current || state.filters.year || "";
}

function renderFolders() {
  const root = $("#folderList");
  root.innerHTML = "";
  const unfiled = $('[data-folder-id="0"]');
  if (unfiled) unfiled.classList.toggle("active", state.filters.folder_id === 0);
  renderFolderRows(buildFolderTree(state.folders), root, 0);
}

function renderFolderRows(folders, root, depth) {
  folders.forEach(folder => {
    const wrap = document.createElement("div");
    wrap.className = "folder-row";
    wrap.style.setProperty("--depth", depth);
    wrap.innerHTML = `
      <button class="nav-item ${state.filters.folder_id === folder.id ? "active" : ""}" data-folder="${folder.id}">
        <span><i class="folder-branch"></i><i class="folder-dot" style="background:${escapeHtml(folder.color || "#2563eb")}"></i>${escapeHtml(folder.name)}</span>
        <b>${folder.paper_count || 0}</b>
      </button>
      <button class="icon-btn" title="编辑">⋯</button>`;
    $(".nav-item", wrap).onclick = () => {
      state.filters.folder_id = state.filters.folder_id === folder.id ? null : folder.id;
      state.filters.offset = 0;
      loadPapers();
      renderFolders();
    };
    $(".icon-btn", wrap).onclick = () => openFolderModal(folder);
    root.append(wrap);
    if (folder.children?.length) renderFolderRows(folder.children, root, depth + 1);
  });
}

function buildFolderTree(folders) {
  const map = new Map(folders.map(folder => [folder.id, { ...folder, children: [] }]));
  const roots = [];
  map.forEach(folder => {
    const parent = folder.parent_id ? map.get(folder.parent_id) : null;
    if (parent && parent.id !== folder.id) parent.children.push(folder);
    else roots.push(folder);
  });
  const sort = items => {
    items.sort((a, b) => (a.sort_order - b.sort_order) || a.name.localeCompare(b.name, "zh-Hans-CN"));
    items.forEach(item => sort(item.children));
  };
  sort(roots);
  return roots;
}

function renderPapers(total) {
  const grid = $("#paperGrid");
  const empty = $("#emptyState");
  grid.innerHTML = "";
  empty.classList.toggle("hidden", state.papers.length > 0);
  state.papers.forEach(paper => grid.append(renderCard(paper)));
  renderPagination(total);
}

function renderCard(paper) {
  const card = document.createElement("article");
  card.className = `paper-card ${state.selected.has(paper.id) ? "selected" : ""}`;
  const tags = (paper.tags || []).slice(0, 5).map(t => `<span class="chip ${t === "AI研究" ? "ai" : ""}">${escapeHtml(t)}</span>`).join("");
  const status = statusLabel(paper.status);
  card.innerHTML = `
    <div class="card-head">
      <input type="checkbox" ${state.selected.has(paper.id) ? "checked" : ""} aria-label="选择论文" />
      <div>
        <h3 class="card-title">${escapeHtml(paper.title)}</h3>
        <div class="meta">${escapeHtml(authorLine(paper))}</div>
      </div>
    </div>
    <p class="summary">${escapeHtml(paper.one_line_summary || paper.research_question || "暂无摘要，可编辑补充或上传精读 HTML 自动解析。")}</p>
    <div class="chips"><span class="chip status-${paper.status}">${status}</span>${tags}</div>
    <div class="card-footer">
      <div class="links">${link(paper.pdf_url, "PDF")}${link(paper.arxiv_url, "arXiv")}${link(paper.github_url, "GitHub")}</div>
      <div class="ops">
        ${paper.summary_html_exists ? `<a href="${readUrl(paper.slug, false)}" target="_blank" title="打开精读页">📄</a><a href="/api/papers/${paper.id}/export-html" title="导出">⬇</a>` : ""}
        <button data-detail title="查看详情">›</button>
        <button data-edit title="编辑">✎</button>
      </div>
    </div>`;
  $("input", card).onchange = e => {
    e.stopPropagation();
    e.target.checked ? state.selected.add(paper.id) : state.selected.delete(paper.id);
    renderPapers(state.filters.offset + state.papers.length);
    renderSelectedBar();
  };
  $("[data-detail]", card).onclick = () => showDetail(paper.id);
  $("[data-edit]", card).onclick = () => openPaperModal(paper);
  return card;
}

function renderPagination(total) {
  const root = $("#pagination");
  const page = Math.floor(state.filters.offset / state.filters.limit) + 1;
  const pages = Math.max(1, Math.ceil(total / state.filters.limit));
  root.innerHTML = "";
  if (pages <= 1) return;
  const add = (label, target, active = false, disabled = false) => {
    const btn = document.createElement("button");
    btn.textContent = label;
    btn.className = active ? "active" : "";
    btn.disabled = disabled;
    btn.onclick = () => {
      state.filters.offset = (target - 1) * state.filters.limit;
      loadPapers();
    };
    root.append(btn);
  };
  add("‹", Math.max(1, page - 1), false, page === 1);
  for (let i = Math.max(1, page - 2); i <= Math.min(pages, page + 2); i++) add(i, i, i === page);
  add("›", Math.min(pages, page + 1), false, page === pages);
}

function renderSelectedBar() {
  $("#selectedBar").classList.toggle("hidden", state.selected.size === 0);
  $("#selectedCount").textContent = `已选 ${state.selected.size} 篇`;
}

async function showDetail(id) {
  const paper = await api(`/api/papers/${id}`);
  state.currentPaper = paper;
  $("#listView").classList.add("hidden");
  const detail = $("#detailView");
  detail.classList.remove("hidden");
  const meta = [
    ["arXiv ID", paper.arxiv_id],
    ["年份", paper.year],
    ["发布日期", paper.date_published],
    ["会议", paper.conference],
    ["研究问题", paper.research_question],
    ["核心方法", paper.core_method],
    ["主要结果", paper.main_result],
    ["适合读者", paper.target_audience],
  ].map(([k, v]) => `<div class="meta-box"><span>${k}</span>${escapeHtml(v || "未填写")}</div>`).join("");
  detail.innerHTML = `
    <div class="detail-top">
      <button class="toolbar-btn" id="backListBtn">← 返回列表</button>
      <div class="ops">
        <button id="detailEditBtn">编辑</button>
        <button id="detailReresearchBtn">重新研究并替换</button>
        ${paper.summary_html_exists ? `<a href="/api/papers/${paper.id}/export-html">导出HTML</a><a href="${readUrl(paper.slug, false)}" target="_blank">打开精读页</a>` : ""}
      </div>
    </div>
    <h2>${escapeHtml(paper.title)}</h2>
    <div class="meta">${escapeHtml([paper.authors, paper.institution].filter(Boolean).join(" · "))}</div>
    <div class="callout">${escapeHtml(paper.one_line_summary || "暂无一句话总结。")}</div>
    <div class="meta-grid">${meta}</div>
    <div class="chips"><span class="chip status-${paper.status}">${statusLabel(paper.status)}</span>${(paper.tags || []).map(t => `<span class="chip">${escapeHtml(t)}</span>`).join("")}</div>
    ${paper.summary_html_exists ? `<iframe class="summary-frame" src="${readUrl(paper.slug, true)}"></iframe>` : `<div class="empty-state"><h2>暂无精读 HTML</h2><p>可以上传 HTML 或启动 AI 研究生成。</p></div>`}`;
  $("#backListBtn").onclick = () => {
    detail.classList.add("hidden");
    $("#listView").classList.remove("hidden");
  };
  $("#detailEditBtn").onclick = () => openPaperModal(paper);
  $("#detailReresearchBtn").onclick = () => startReplaceResearch(paper);
}

function openPaperModal(paper = null) {
  const isEdit = Boolean(paper);
  openModal(`
    <div class="modal-head"><h2>${isEdit ? "编辑论文" : "添加论文"}</h2><button class="icon-btn" data-close>×</button></div>
    <form class="modal-body" id="paperForm">
      <div class="form-grid">
        ${field("slug", "目录名 (slug)*", paper?.slug || "", "text", !isEdit)}
        ${field("year", "年份", paper?.year || "", "number")}
        ${field("title", "标题*", paper?.title || "", "text", true, "full")}
        ${field("authors", "作者", paper?.authors || "", "text", false, "full")}
        ${field("institution", "机构", paper?.institution || "", "text", false, "full")}
        ${selectField("status", "状态", paper?.status || "reading")}
        ${field("arxiv_id", "arXiv ID", paper?.arxiv_id || "")}
        ${field("pdf_url", "PDF URL", paper?.pdf_url || "", "url", false, "full")}
        ${field("github_url", "GitHub URL", paper?.github_url || "", "url", false, "full")}
        ${field("project_url", "Project URL", paper?.project_url || "", "url", false, "full")}
        ${textField("one_line_summary", "一句话总结", paper?.one_line_summary || "")}
        ${field("tags", "标签 (逗号分隔)", (paper?.tags || []).join(", "), "text", false, "full")}
        ${textField("notes", "备注", paper?.notes || "")}
      </div>
      <div class="modal-actions">
        ${isEdit ? `<button type="button" class="danger" id="deletePaperBtn">删除</button>` : ""}
        <button type="button" data-close>取消</button>
        <button class="primary">${isEdit ? "保存" : "添加"}</button>
      </div>
    </form>`);
  $("#paperForm").onsubmit = async e => {
    e.preventDefault();
    const data = formData(e.target);
    data.year = data.year ? Number(data.year) : null;
    data.tags = data.tags ? data.tags.split(",").map(x => x.trim()).filter(Boolean) : [];
    if (isEdit) await api(`/api/papers/${paper.id}`, { method: "PUT", body: data });
    else await api("/api/papers", { method: "POST", body: data });
    closeModal();
    await refreshAll();
    toast(isEdit ? "论文已更新" : "论文已添加", "success");
  };
  if (isEdit) $("#deletePaperBtn").onclick = async () => {
    if (!confirm("确认删除这篇论文和对应 HTML？")) return;
    await api(`/api/papers/${paper.id}`, { method: "DELETE" });
    closeModal();
    $("#detailView").classList.add("hidden");
    $("#listView").classList.remove("hidden");
    await refreshAll();
    toast("论文已删除", "success");
  };
}

function openFolderModal(folder = null) {
  const currentColor = folder?.color || FOLDER_COLORS[0];
  openModal(`
    <div class="modal-head"><h2>${folder ? "编辑文件夹" : "新建文件夹"}</h2><button class="icon-btn" data-close>×</button></div>
    <form class="modal-body" id="folderForm">
      <div class="form-grid">
        ${field("name", "文件夹名称", folder?.name || "", "text", true, "full")}
        ${folderParentSelect(folder)}
        ${colorPalette(currentColor)}
      </div>
      <div class="modal-actions">
        ${folder ? `<button type="button" class="danger" id="deleteFolderBtn">删除</button>` : ""}
        <button type="button" data-close>取消</button>
        <button class="primary">${folder ? "保存" : "创建"}</button>
      </div>
    </form>`);
  $("#folderForm").onsubmit = async e => {
    e.preventDefault();
    const data = formData(e.target);
    data.parent_id = data.parent_id ? Number(data.parent_id) : null;
    if (folder) await api(`/api/folders/${folder.id}`, { method: "PUT", body: data });
    else await api("/api/folders", { method: "POST", body: data });
    closeModal();
    await refreshAll();
    toast("文件夹已保存", "success");
  };
  if (folder) $("#deleteFolderBtn").onclick = async () => {
    if (!confirm("确认删除文件夹？论文会移动到未分类。")) return;
    await api(`/api/folders/${folder.id}`, { method: "DELETE" });
    closeModal();
    await refreshAll();
  };
}

function openTagsModal() {
  openModal(`
    <div class="modal-head"><h2>批量添加标签</h2><button class="icon-btn" data-close>×</button></div>
    <form class="modal-body" id="tagsForm">
      ${field("tags", "标签 (逗号分隔)", "", "text", true, "full")}
      <div class="modal-actions"><button type="button" data-close>取消</button><button class="primary">添加</button></div>
    </form>`);
  $("#tagsForm").onsubmit = async e => {
    e.preventDefault();
    const tags = formData(e.target).tags.split(",").map(x => x.trim()).filter(Boolean);
    await api("/api/papers/batch/tags", { method: "POST", body: { ids: [...state.selected], tags } });
    closeModal();
    state.selected.clear();
    await refreshAll();
  };
}

function openMoveModal() {
  const rows = [`<button class="nav-item" data-id="">未分类</button>`].concat(
    folderOptionRows(buildFolderTree(state.folders), null, 0).map(f => `<button class="nav-item folder-option" style="--depth:${f.depth}" data-id="${f.id}"><span><i class="folder-dot" style="background:${escapeAttr(f.color)}"></i>${escapeHtml(f.label)}</span></button>`)
  ).join("");
  openModal(`<div class="modal-head"><h2>移至文件夹</h2><button class="icon-btn" data-close>×</button></div><div class="modal-body">${rows}</div>`);
  $$(".modal .nav-item").forEach(btn => btn.onclick = async () => {
    const raw = btn.dataset.id;
    await api("/api/folders/move", { method: "POST", body: { ids: [...state.selected], folder_id: raw ? Number(raw) : null } });
    closeModal();
    state.selected.clear();
    await refreshAll();
  });
}

function openThemeModal() {
  const theme = state.theme || themeFromPreset("default");
  openModal(`
    <div class="modal-head"><h2>阅读主题</h2><button class="icon-btn" data-close>×</button></div>
    <div class="modal-body">
      <p class="callout compact">选择一个预设，统一管理页和论文阅读页的外观。</p>
      <div class="field full">
        <label>预设</label>
        <div class="theme-grid">
          ${Object.entries(THEME_PRESETS).map(([key, item]) => themePresetCard(key, item, theme.preset === key)).join("")}
        </div>
      </div>
      <div class="modal-actions">
        <button type="button" id="resetThemeBtn">恢复默认</button>
        <button type="button" data-close>关闭</button>
      </div>
    </div>`);
  $$(".theme-card").forEach(card => card.onclick = () => {
    const next = themeFromPreset(card.dataset.theme);
    saveTheme(next);
    closeModal();
    refreshDetailFrame();
    toast("主题已应用", "success");
  });
  $("#resetThemeBtn").onclick = () => {
    saveTheme(themeFromPreset("default"));
    closeModal();
    refreshDetailFrame();
  };
}

async function startReplaceResearch(paper) {
  if (!confirm(`确认将《${paper.title}》加入批量重研队列？完成后会替换当前精读 HTML，最终只保留一份报告。`)) return;
  try {
    const batch = await api(`/api/papers/${paper.id}/research/batch-replace`, { method: "POST" });
    state.activeBatch = batch.active ? batch : null;
    updateResearchBadge(batch);
    openBatchModal();
    toast("已加入批量重研队列，完成后替换当前版本", "success");
  } catch (err) {
    toast(err.message, "error");
  }
}

function openResearchModal() {
  const active = state.activeResearch;
  openModal(`
    <div class="modal-head"><h2>AI 研究论文</h2><button class="icon-btn" data-close>×</button></div>
    <div class="modal-body">
      <p class="callout">输入论文名或 PDF URL，系统将调用 Codex，默认使用 agent=codex、model=gpt-5.5，并按 AGENT.md 生成离线精读 HTML。一次只运行一个单篇任务。</p>
      <form id="researchForm" class="form-grid">
        ${field("paper_name", "论文名称*", "", "text", true, "full")}
        ${field("pdf_url", "PDF URL (可选)", "", "url", false, "full")}
        <div class="modal-actions field full"><button type="button" data-close>关闭(后台继续)</button><button class="primary">开始研究</button></div>
      </form>
      <div class="log-panel ${active ? "" : "hidden"}" id="researchLog"></div>
    </div>`);
  $("#researchForm").onsubmit = async e => {
    e.preventDefault();
    const task = await api("/api/research/start", { method: "POST", body: formData(e.target) });
    state.activeResearch = { id: task.id };
    localStorage.setItem("activeResearch", JSON.stringify(state.activeResearch));
    $("#researchLog").classList.remove("hidden");
    pollResearch(task.id);
  };
  if (active) pollResearch(active.id);
}

async function pollResearch(id) {
  clearInterval(state.researchTimer);
  const run = async () => {
    try {
      const task = await api(`/api/research/${id}`);
      updateResearchBadge();
      const panel = $("#researchLog");
      if (panel) panel.innerHTML = task.logs.map(l => `<div class="log-line">${escapeHtml(l.time)} · ${logIcon(l.type)} ${escapeHtml(l.text)}</div>`).join("");
      if (["completed", "failed"].includes(task.status)) {
        clearInterval(state.researchTimer);
        state.activeResearch = null;
        localStorage.removeItem("activeResearch");
        updateResearchBadge();
        await refreshAll();
        toast(task.status === "completed" ? "AI 研究完成" : task.error || "AI 研究失败", task.status === "completed" ? "success" : "error");
      }
    } catch {
      clearInterval(state.researchTimer);
    }
  };
  await run();
  state.researchTimer = setInterval(run, 2500);
}

function openBatchModal() {
  openModal(`
    <div class="modal-head"><h2>批量 AI 研究</h2><button class="icon-btn" data-close>×</button></div>
    <div class="modal-body">
      <p class="callout">队列按顺序调用 Codex + GPT-5.5。新研究会自动归档；已有论文点“重研”会排队执行并覆盖原 HTML，只保留一份报告。</p>
      <form id="batchAddForm" class="form-grid">
        ${field("paper_name", "论文名称*", "", "text", true)}
        ${field("pdf_url", "PDF URL", "", "url")}
        <div class="modal-actions field full"><button class="primary">添加到队列</button><button type="button" id="cancelBatchBtn">停止全部</button></div>
      </form>
      <div id="batchProgress"></div>
      <div class="queue-panel" id="batchPanel"></div>
    </div>`);
  $("#batchAddForm").onsubmit = async e => {
    e.preventDefault();
    await api("/api/research/batch/start", { method: "POST", body: { papers: [formData(e.target)] } });
    e.target.reset();
    pollBatch();
  };
  $("#cancelBatchBtn").onclick = async () => {
    await api("/api/research/batch/default/cancel", { method: "POST" });
    pollBatch();
  };
  pollBatch();
}

async function pollBatch() {
  const batch = await api("/api/research/batch/latest");
  state.activeBatch = batch.active ? batch : null;
  updateResearchBadge(batch);
  const panel = $("#batchPanel");
  const progress = $("#batchProgress");
  if (progress) progress.innerHTML = batchProgress(batch);
  if (panel) {
    panel.innerHTML = batch.items.length ? batch.items.map(item => queueRow(item)).join("") : `<div class="empty-state"><p>队列为空</p></div>`;
    $$(".queue-row button", panel).forEach(btn => btn.onclick = () => {
      if (btn.dataset.action === "replace-research") return startReplaceResearchFromQueue(btn);
      return batchAction(btn.dataset.action, btn.dataset.id);
    });
  }
  clearInterval(state.batchTimer);
  if (batch.active) state.batchTimer = setInterval(() => pollBatch().catch(() => {}), 4000);
}

function updateResearchBadge(batch = state.activeBatch) {
  const badge = $("#researchBadge");
  if (!badge) return;
  if (batch?.active) {
    const progress = batchProgressNumbers(batch);
    badge.textContent = `批量研究中 ${progress.current}/${progress.total}`;
    badge.dataset.mode = "batch";
    badge.classList.remove("hidden");
    return;
  }
  if (state.activeResearch) {
    badge.textContent = "AI 研究中";
    badge.dataset.mode = "single";
    badge.classList.remove("hidden");
    return;
  }
  badge.classList.add("hidden");
  badge.dataset.mode = "";
}

async function openActiveResearchPanel() {
  try {
    const batch = await api("/api/research/batch/latest");
    if (batch.active || batch.items?.length) {
      state.activeBatch = batch.active ? batch : null;
      openBatchModal();
      return;
    }
  } catch {}
  openResearchModal();
}

async function batchAction(action, id) {
  if (action === "open") return;
  if (action === "replace-research") return;
  await api(`/api/research/batch/default/items/${id}/${action}`, { method: "POST", body: action === "reorder" ? { direction: "up" } : undefined });
  pollBatch();
}

async function startReplaceResearchFromQueue(btn) {
  const paperId = Number(btn.dataset.paperId);
  if (!paperId) return toast("未找到对应论文，无法重新研究", "error");
  const title = btn.dataset.title || "这篇论文";
  if (!confirm(`确认将《${title}》加入批量重研队列？完成后会替换当前精读 HTML，只保留一份报告。`)) return;
  try {
    const batch = await api(`/api/papers/${paperId}/research/batch-replace`, { method: "POST" });
    state.activeBatch = batch.active ? batch : null;
    updateResearchBadge(batch);
    pollBatch();
    toast("已加入批量重研队列，完成后替换原 HTML", "success");
  } catch (err) {
    toast(err.message, "error");
  }
}

async function uploadFiles(files) {
  const htmlFiles = [...files].filter(f => /\.html?$/i.test(f.name));
  if (!htmlFiles.length) return toast("请选择 HTML 文件", "error");
  for (const file of htmlFiles) {
    const fd = new FormData();
    fd.append("file", file);
    await api("/api/upload-summary", { method: "POST", body: fd });
  }
  await refreshAll();
  toast(`已导入 ${htmlFiles.length} 个 HTML`, "success");
}

async function refreshAll() {
  await Promise.all([loadStats(), loadFilters(), loadPapers()]);
}

function bindEvents() {
  $("#searchInput").oninput = debounce(e => {
    state.filters.search = e.target.value.trim();
    state.filters.offset = 0;
    loadPapers();
  });
  $("#yearFilter").onchange = e => {
    state.filters.year = e.target.value;
    state.filters.offset = 0;
    loadPapers();
  };
  $("#sortSelect").onchange = e => {
    const [sort, order] = e.target.value.split(":");
    Object.assign(state.filters, { sort, order, offset: 0 });
    loadPapers();
  };
  $$("[data-filter-status]").forEach(btn => btn.onclick = () => {
    state.filters.status = btn.dataset.filterStatus;
    state.filters.offset = 0;
    $$(".nav-item,.stat-card").forEach(x => x.classList.remove("active"));
    $$(`[data-filter-status="${state.filters.status}"]`).forEach(x => x.classList.add("active"));
    loadPapers();
  });
  $('[data-folder-id="0"]').onclick = () => {
    state.filters.folder_id = state.filters.folder_id === 0 ? null : 0;
    state.filters.offset = 0;
    renderFolders();
    loadPapers();
  };
  $("#uploadBtn").onclick = $("#uploadTopBtn").onclick = () => $("#fileInput").click();
  $("#fileInput").onchange = e => uploadFiles(e.target.files).catch(err => toast(err.message, "error"));
  $("#addPaperBtn").onclick = $("#emptyAddBtn").onclick = () => openPaperModal();
  $("#aiBtn").onclick = $("#aiTopBtn").onclick = () => openResearchModal();
  $("#researchBadge").onclick = () => openActiveResearchPanel();
  $("#batchBtn").onclick = $("#batchTopBtn").onclick = () => openBatchModal();
  $("#themeBtn").onclick = () => openThemeModal();
  $("#themeTopBtn").onclick = () => openThemeModal();
  $("#syncBtn").onclick = async () => { await api("/api/sync", { method: "POST" }); await refreshAll(); toast("文件系统已同步", "success"); };
  $("#scanBtn").onclick = async () => { await api("/api/scan-orphan-html", { method: "POST" }); await refreshAll(); toast("孤立 HTML 已扫描", "success"); };
  $("#newFolderBtn").onclick = () => openFolderModal();
  $("#batchTagsBtn").onclick = openTagsModal;
  $("#moveFolderBtn").onclick = openMoveModal;
  $("#clearSelectedBtn").onclick = () => { state.selected.clear(); renderPapers(state.papers.length); };
  $("#batchDeleteBtn").onclick = async () => {
    if (!confirm(`确认删除 ${state.selected.size} 篇论文？`)) return;
    await api("/api/papers/batch/delete", { method: "POST", body: { ids: [...state.selected] } });
    state.selected.clear();
    await refreshAll();
  };
  $$("[data-batch-status]").forEach(btn => btn.onclick = async () => {
    await api("/api/papers/batch/status", { method: "POST", body: { ids: [...state.selected], status: btn.dataset.batchStatus } });
    state.selected.clear();
    await refreshAll();
  });
  $("#menuBtn").onclick = () => { $("#sidebar").classList.add("open"); $("#mobileScrim").classList.add("show"); };
  $("#mobileScrim").onclick = () => { $("#sidebar").classList.remove("open"); $("#mobileScrim").classList.remove("show"); };
  document.addEventListener("dragenter", e => { e.preventDefault(); $("#dropMask").classList.add("show"); });
  document.addEventListener("dragover", e => e.preventDefault());
  document.addEventListener("dragleave", e => { if (e.target === document || e.clientX === 0) $("#dropMask").classList.remove("show"); });
  document.addEventListener("drop", e => {
    e.preventDefault();
    $("#dropMask").classList.remove("show");
    uploadFiles(e.dataTransfer.files).catch(err => toast(err.message, "error"));
  });
  bindSidebarResize();
}

function bindSidebarResize() {
  const handle = $("#sidebarResizer");
  if (!handle) return;
  const saved = Number(localStorage.getItem("sidebarWidth"));
  if (saved) setSidebarWidth(saved);
  let dragging = false;
  handle.addEventListener("pointerdown", e => {
    dragging = true;
    handle.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing-sidebar");
  });
  handle.addEventListener("pointermove", e => {
    if (!dragging) return;
    setSidebarWidth(Math.max(220, Math.min(420, e.clientX)));
  });
  handle.addEventListener("pointerup", e => {
    dragging = false;
    handle.releasePointerCapture(e.pointerId);
    document.body.classList.remove("resizing-sidebar");
    localStorage.setItem("sidebarWidth", getComputedStyle(document.documentElement).getPropertyValue("--sidebar-w").trim().replace("px", ""));
  });
}
function setSidebarWidth(width) {
  document.documentElement.style.setProperty("--sidebar-w", `${Math.round(width)}px`);
}

function initTheme() {
  const saved = JSON.parse(localStorage.getItem("paperTheme") || "null");
  applyTheme(saved || themeFromPreset("default"));
}

function themeFromPreset(key) {
  const preset = THEME_PRESETS[key] || THEME_PRESETS.default;
  return { ...preset, preset: key in THEME_PRESETS ? key : "default" };
}

function saveTheme(theme) {
  applyTheme(theme);
  localStorage.setItem("paperTheme", JSON.stringify(state.theme));
}

function applyTheme(theme) {
  const next = { ...themeFromPreset("default"), ...(theme || {}) };
  state.theme = next;
  const root = document.documentElement;
  root.dataset.theme = next.preset && next.preset !== "custom" ? next.preset : "";
  document.body.classList.toggle("theme-solid", next.mode === "solid" || next.preset === "custom");
  [
    ["--primary", next.primary],
    ["--primary-hover", next.primary],
    ["--primary-bg", mixHex(next.primary, next.bg, 0.12)],
    ["--bg", next.bg],
    ["--panel", next.panel],
    ["--bg-alt", mixHex(next.border, next.bg, 0.34)],
    ["--bg-hover", mixHex(next.primary, next.bg, 0.1)],
    ["--text", next.text],
    ["--text-secondary", mixHex(next.text, next.bg, 0.72)],
    ["--text-muted", mixHex(next.text, next.bg, 0.48)],
    ["--border", next.border],
    ["--info", next.primary],
    ["--info-bg", mixHex(next.primary, next.bg, 0.12)],
  ].forEach(([name, value]) => root.style.setProperty(name, value));
}

function refreshDetailFrame() {
  const frame = $(".summary-frame");
  if (frame && state.currentPaper?.slug) frame.src = readUrl(state.currentPaper.slug, true);
}

function readUrl(slug, embed = false) {
  const url = new URL(`/read/${slug}`, window.location.origin);
  if (embed) url.searchParams.set("embed", "1");
  const theme = state.theme || themeFromPreset("default");
  ["primary", "bg", "panel", "text", "border"].forEach(key => url.searchParams.set(key, theme[key]));
  return `${url.pathname}${url.search}`;
}

function themePresetCard(key, theme, active) {
  return `<button type="button" class="theme-card ${active ? "active" : ""}" data-theme="${key}">
    <span class="theme-preview">
      <i style="background:${escapeAttr(theme.bg)}"></i>
      <i style="background:${escapeAttr(theme.primary)}"></i>
    </span>
    <b>${escapeHtml(theme.name)}</b>
    <span>${escapeHtml(theme.hint)}</span>
  </button>`;
}

function mixHex(a, b, amount) {
  const ar = parseInt(a.slice(1, 3), 16), ag = parseInt(a.slice(3, 5), 16), ab = parseInt(a.slice(5, 7), 16);
  const br = parseInt(b.slice(1, 3), 16), bg = parseInt(b.slice(3, 5), 16), bb = parseInt(b.slice(5, 7), 16);
  const vals = [ar * amount + br * (1 - amount), ag * amount + bg * (1 - amount), ab * amount + bb * (1 - amount)].map(v => Math.round(v).toString(16).padStart(2, "0"));
  return `#${vals.join("")}`;
}

function restoreResearchPolling() {
  if (state.activeResearch?.id) pollResearch(state.activeResearch.id);
  pollBatch().catch(() => updateResearchBadge());
}

function openModal(html) {
  $("#modalRoot").innerHTML = `<div class="modal-backdrop"><div class="modal">${html}</div></div>`;
  $$("[data-close]", $("#modalRoot")).forEach(btn => btn.onclick = closeModal);
}
function closeModal() {
  $("#modalRoot").innerHTML = "";
}

function field(name, label, value = "", type = "text", required = false, cls = "") {
  return `<div class="field ${cls}"><label>${label}</label><input name="${name}" type="${type}" value="${escapeAttr(value)}" ${required ? "required" : ""}></div>`;
}
function textField(name, label, value = "") {
  return `<div class="field full"><label>${label}</label><textarea name="${name}">${escapeHtml(value)}</textarea></div>`;
}
function selectField(name, label, value) {
  const options = [["reading", "在读"], ["read", "已读"], ["todo", "待读"], ["archived", "归档"]];
  return `<div class="field"><label>${label}</label><select name="${name}">${options.map(([v, t]) => `<option value="${v}" ${v === value ? "selected" : ""}>${t}</option>`).join("")}</select></div>`;
}
function folderParentSelect(folder = null) {
  const rows = [`<option value="">根目录</option>`].concat(
    folderOptionRows(buildFolderTree(state.folders), folder?.id, 0).map(item => {
      const selected = folder?.parent_id === item.id ? "selected" : "";
      return `<option value="${item.id}" ${selected}>${escapeHtml(item.label)}</option>`;
    })
  ).join("");
  return `<div class="field full"><label>父文件夹</label><select name="parent_id">${rows}</select></div>`;
}
function folderOptionRows(nodes, excludedId = null, depth = 0) {
  return nodes.flatMap(node => {
    if (node.id === excludedId) return [];
    const row = { id: node.id, label: `${"　".repeat(depth)}${node.name}`, color: node.color || FOLDER_COLORS[0], depth };
    return [row, ...folderOptionRows(node.children || [], excludedId, depth + 1)];
  });
}
function colorPalette(value) {
  return `<div class="field full"><label>颜色</label><div class="color-grid">${FOLDER_COLORS.map(color => `
    <label class="color-swatch ${color === value ? "selected" : ""}" style="background:${color}">
      <input type="radio" name="color" value="${color}" ${color === value ? "checked" : ""}>
    </label>`).join("")}</div></div>`;
}
function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}
function link(url, label) {
  return url ? `<a href="${escapeAttr(url)}" target="_blank" rel="noreferrer">${label}</a>` : "";
}
function authorLine(p) {
  const authors = p.authors ? p.authors.split(/,|;|、/).map(x => x.trim()).filter(Boolean) : [];
  const author = authors.length > 3 ? `${authors.slice(0, 3).join(", ")} et al.` : (p.authors || "未知作者");
  return [author, p.year, p.conference, p.rating ? "★".repeat(p.rating) : ""].filter(Boolean).join(" · ");
}
function statusLabel(status) {
  return { reading: "在读", read: "已读", todo: "待读", archived: "归档" }[status] || status;
}
function logIcon(type) {
  return { system: "•", search: "⌕", reading: "◐", generating: "◇", error: "!" }[type] || "•";
}
function queueRow(item) {
  const icon = { queued: "⏳", pending: "◌", researching: "◐", generating: "◇", completed: "✓", failed: "!", cancelled: "×" }[item.status] || "•";
  const result = item.result?.slug ? `<a href="${readUrl(item.result.slug, false)}" target="_blank">打开</a>` : "";
  const paperId = item.result?.paper_id || item.result?.id || "";
  const rerun = paperId ? `<button data-action="replace-research" data-id="${escapeAttr(item.id)}" data-paper-id="${escapeAttr(paperId)}" data-title="${escapeAttr(item.paper_name)}">重研</button>` : "";
  const actions = item.status === "completed"
    ? `${result}${rerun}<button data-action="remove" data-id="${item.id}">移除</button>`
    : item.status === "failed" || item.status === "cancelled"
      ? `<button data-action="retry" data-id="${item.id}">重试</button><button data-action="remove" data-id="${item.id}">移除</button>`
      : item.status === "queued"
        ? `<button data-action="reorder" data-id="${item.id}">上移</button><button data-action="remove" data-id="${item.id}">移除</button>`
        : `<button data-action="stop" data-id="${item.id}">停止</button>`;
  return `<div class="queue-row"><b>${icon}</b><div>${escapeHtml(item.paper_name)}<small>${escapeHtml(item.message || item.status)}</small></div><div class="ops">${actions}</div></div>`;
}
function batchProgress(batch) {
  const nums = batchProgressNumbers(batch);
  const running = (batch.items || []).find(item => ["pending", "researching", "generating"].includes(item.status));
  const percent = nums.total ? Math.round((nums.current / nums.total) * 100) : 0;
  const statusText = running ? `当前：${running.paper_name}` : (batch.active ? "等待下一个任务" : "队列已停止");
  return `
    <div class="batch-summary">
      <div class="batch-summary-main">
        <strong>${batch.active ? "批量研究进行中" : "批量队列状态"}</strong>
        <span>${escapeHtml(statusText)}</span>
      </div>
      <div class="progress-track"><i style="width:${percent}%"></i></div>
      <b>${nums.current}/${nums.total}</b>
      <div class="progress-meta">
        <span>完成 ${nums.completed}</span>
        <span>失败 ${nums.failed}</span>
        <span>取消 ${nums.cancelled}</span>
        <span>${percent}%</span>
      </div>
    </div>`;
}
function batchProgressNumbers(batch) {
  const items = batch.items || [];
  const total = batch.total || items.length || 0;
  const completed = batch.completed || 0;
  const failed = batch.failed || 0;
  const cancelled = items.filter(item => item.status === "cancelled").length;
  const processed = completed + failed + cancelled;
  const runningIndex = items.findIndex(item => ["pending", "researching", "generating"].includes(item.status));
  const current = runningIndex >= 0 ? Math.max(processed + 1, runningIndex + 1) : processed;
  return { total, completed, failed, cancelled, current: Math.min(current, total) };
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}
function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}

bindEvents();
loadAll().catch(err => toast(err.message, "error"));
