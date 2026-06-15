const statusLabels = {
  unread: "未读",
  reading: "阅读中",
  read: "已读",
  archived: "归档"
};

const state = {
  papers: [],
  jobs: [],
  stats: {},
  selectedPaper: null,
  filters: {
    q: "",
    readingStatus: "",
    type: "",
    category: "",
    keyword: ""
  }
};

const els = {
  paperList: document.querySelector("#paperList"),
  jobList: document.querySelector("#jobList"),
  paperCount: document.querySelector("#paperCount"),
  readyCount: document.querySelector("#readyCount"),
  jobCount: document.querySelector("#jobCount"),
  searchInput: document.querySelector("#searchInput"),
  statusFilter: document.querySelector("#statusFilter"),
  typeFilter: document.querySelector("#typeFilter"),
  categoryFilter: document.querySelector("#categoryFilter"),
  keywordFilter: document.querySelector("#keywordFilter"),
  refreshBtn: document.querySelector("#refreshBtn"),
  uploadForm: document.querySelector("#uploadForm"),
  researchForm: document.querySelector("#researchForm"),
  detailForm: document.querySelector("#detailForm"),
  annotationForm: document.querySelector("#annotationForm"),
  uploadStatus: document.querySelector("#uploadStatus"),
  researchStatus: document.querySelector("#researchStatus"),
  detailStatus: document.querySelector("#detailStatus"),
  annotationStatus: document.querySelector("#annotationStatus"),
  annotationList: document.querySelector("#annotationList"),
  detailTitle: document.querySelector("#detailTitle"),
  previewFrame: document.querySelector("#previewFrame"),
  previewTitle: document.querySelector("#previewTitle"),
  paperTemplate: document.querySelector("#paperCardTemplate")
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

function formatDate(value) {
  if (!value) return "未知时间";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function toList(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value;
  return String(value)
    .split(/[,，;；\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function buildQuery() {
  const params = new URLSearchParams();
  Object.entries(state.filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  const query = params.toString();
  return query ? `?${query}` : "";
}

function renderPapers() {
  els.paperList.innerHTML = "";
  els.paperCount.textContent = String(state.stats.total ?? state.papers.length);
  els.readyCount.textContent = String(state.stats.ready ?? state.papers.filter((paper) => paper.status === "ready").length);
  els.jobCount.textContent = String(state.stats.annotations ?? 0);

  if (!state.papers.length) {
    els.paperList.innerHTML = '<div class="empty">还没有匹配的论文。可以上传 HTML 解读，或创建 Codex 研究任务。</div>';
    return;
  }

  state.papers.forEach((paper) => {
    const card = els.paperTemplate.content.firstElementChild.cloneNode(true);
    const isSelected = state.selectedPaper?.id === paper.id;
    card.classList.toggle("selected", isSelected);
    card.querySelector("h4").textContent = paper.title || paper.slug;
    card.querySelector(".status-pill").textContent = statusLabels[paper.readingStatus] || "未读";
    card.querySelector(".meta").textContent = [
      paper.indexId ? `索引 ${paper.indexId}` : "无索引",
      paper.paperType || "未分类类型",
      `${paper.annotations?.length || 0} 条批注`,
      formatDate(paper.updatedAt)
    ].join(" · ");
    card.querySelector(".notes").textContent = paper.notes || paper.pdfUrl || "暂无备注";
    const tagRow = card.querySelector(".tag-row");
    [...(paper.categories || []), ...(paper.keywords || []).slice(0, 4)].forEach((item) => {
      const tag = document.createElement("span");
      tag.textContent = item;
      tagRow.append(tag);
    });
    card.addEventListener("click", (event) => {
      if (!event.target.closest(".open-button")) selectPaper(paper);
    });
    const button = card.querySelector(".open-button");
    button.disabled = !paper.htmlPath;
    button.textContent = paper.htmlPath ? "预览" : "待生成";
    button.addEventListener("click", () => {
      selectPaper(paper);
      openPreview(paper);
    });
    els.paperList.append(card);
  });
}

function renderJobs() {
  els.jobList.innerHTML = "";
  if (!state.jobs.length) {
    els.jobList.innerHTML = '<div class="empty">暂无研究任务。</div>';
    return;
  }
  state.jobs.forEach((job) => {
    const item = document.createElement("article");
    item.className = "job";
    const log = job.log ? `<pre>${escapeHtml(job.log)}</pre>` : "";
    item.innerHTML = `
      <span class="status-pill">${escapeHtml(job.status)}</span>
      <strong>${escapeHtml(job.title || job.indexId || job.slug || "未命名任务")}</strong>
      <span class="meta">${escapeHtml(job.outputPath || "")} · ${formatDate(job.updatedAt || job.createdAt)}</span>
      ${job.error ? `<p class="notes">${escapeHtml(job.error)}</p>` : ""}
      ${log}
    `;
    els.jobList.append(item);
  });
}

function renderDetail() {
  const paper = state.selectedPaper;
  els.annotationList.innerHTML = "";
  if (!paper) {
    els.detailTitle.textContent = "选择论文后编辑阅读信息";
    els.detailForm.reset();
    els.detailForm.elements.id.value = "";
    els.annotationList.innerHTML = '<div class="empty">选择左侧论文后，可以添加章节批注、摘录和阅读笔记。</div>';
    return;
  }

  els.detailTitle.textContent = paper.title || paper.slug;
  els.detailForm.elements.id.value = paper.id;
  els.detailForm.elements.readingStatus.value = paper.readingStatus || "unread";
  els.detailForm.elements.paperType.value = paper.paperType || "";
  els.detailForm.elements.categories.value = (paper.categories || []).join(", ");
  els.detailForm.elements.keywords.value = (paper.keywords || []).join(", ");
  els.detailForm.elements.tags.value = (paper.tags || []).join(", ");
  els.detailForm.elements.notes.value = paper.notes || "";

  if (!paper.annotations?.length) {
    els.annotationList.innerHTML = '<div class="empty">暂无批注。</div>';
    return;
  }
  paper.annotations.forEach((annotation) => {
    const item = document.createElement("article");
    item.className = "annotation";
    item.innerHTML = `
      <div class="annotation-head">
        <strong>${escapeHtml(annotation.section || "未标注章节")}</strong>
        <button class="text-button" type="button" data-delete="${escapeHtml(annotation.id)}">删除</button>
      </div>
      ${annotation.quote ? `<blockquote>${escapeHtml(annotation.quote)}</blockquote>` : ""}
      <p>${escapeHtml(annotation.text)}</p>
      <span class="meta">${annotation.page ? `页码 ${escapeHtml(annotation.page)} · ` : ""}${formatDate(annotation.updatedAt || annotation.createdAt)}</span>
    `;
    item.querySelector("[data-delete]").addEventListener("click", () => deleteAnnotation(annotation.id));
    els.annotationList.append(item);
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function selectPaper(paper) {
  state.selectedPaper = paper;
  renderPapers();
  renderDetail();
}

function openPreview(paper) {
  if (!paper.htmlPath) return;
  els.previewTitle.textContent = paper.title || paper.slug;
  els.previewFrame.src = `/${paper.htmlPath}`;
  document.querySelector(".preview-panel").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadData() {
  const data = await api(`/api/papers${buildQuery()}`);
  state.papers = data.papers || [];
  state.jobs = data.jobs || [];
  state.stats = data.stats || {};
  if (state.selectedPaper) {
    state.selectedPaper = state.papers.find((paper) => paper.id === state.selectedPaper.id) || null;
  }
  renderPapers();
  renderJobs();
  renderDetail();
}

function syncFilters() {
  state.filters.q = els.searchInput.value.trim();
  state.filters.readingStatus = els.statusFilter.value;
  state.filters.type = els.typeFilter.value.trim();
  state.filters.category = els.categoryFilter.value.trim();
  state.filters.keyword = els.keywordFilter.value.trim();
}

function formPayload(form) {
  const payload = Object.fromEntries(new FormData(form).entries());
  ["categories", "keywords", "tags"].forEach((key) => {
    if (payload[key] !== undefined) payload[key] = toList(payload[key]);
  });
  return payload;
}

async function deleteAnnotation(annotationId) {
  if (!state.selectedPaper) return;
  await api(`/api/papers/${encodeURIComponent(state.selectedPaper.id)}/annotations/${encodeURIComponent(annotationId)}`, {
    method: "DELETE"
  });
  await refreshSelectedPaper();
}

async function refreshSelectedPaper() {
  if (!state.selectedPaper) return;
  const data = await api(`/api/papers/${encodeURIComponent(state.selectedPaper.id)}`);
  state.selectedPaper = data.paper;
  const index = state.papers.findIndex((paper) => paper.id === data.paper.id);
  if (index >= 0) state.papers[index] = data.paper;
  renderPapers();
  renderDetail();
}

[els.searchInput, els.statusFilter, els.typeFilter, els.categoryFilter, els.keywordFilter].forEach((input) => {
  input.addEventListener("input", () => {
    syncFilters();
    loadData().catch((error) => {
      els.detailStatus.textContent = error.message;
    });
  });
});

els.refreshBtn.addEventListener("click", () => {
  loadData().catch((error) => {
    els.researchStatus.textContent = error.message;
  });
});

els.detailForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selectedPaper) {
    els.detailStatus.textContent = "请先选择一篇论文。";
    return;
  }
  els.detailStatus.textContent = "正在保存...";
  try {
    await api(`/api/papers/${encodeURIComponent(state.selectedPaper.id)}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(formPayload(els.detailForm))
    });
    els.detailStatus.textContent = "阅读信息已保存。";
    await loadData();
  } catch (error) {
    els.detailStatus.textContent = error.message;
  }
});

els.annotationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selectedPaper) {
    els.annotationStatus.textContent = "请先选择一篇论文。";
    return;
  }
  els.annotationStatus.textContent = "正在添加批注...";
  try {
    await api(`/api/papers/${encodeURIComponent(state.selectedPaper.id)}/annotations`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(formPayload(els.annotationForm))
    });
    els.annotationForm.reset();
    els.annotationStatus.textContent = "批注已添加。";
    await refreshSelectedPaper();
    await loadData();
  } catch (error) {
    els.annotationStatus.textContent = error.message;
  }
});

els.uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  els.uploadStatus.textContent = "正在上传...";
  try {
    const form = new FormData(els.uploadForm);
    await api("/api/upload", { method: "POST", body: form });
    els.uploadForm.reset();
    els.uploadStatus.textContent = "已上传并加入论文库。";
    await loadData();
  } catch (error) {
    els.uploadStatus.textContent = error.message;
  }
});

els.researchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  els.researchStatus.textContent = "正在创建 Codex 任务...";
  try {
    const result = await api("/api/research", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(formPayload(els.researchForm))
    });
    els.researchForm.reset();
    els.researchStatus.textContent = `任务已创建：${result.job.id}`;
    await loadData();
  } catch (error) {
    els.researchStatus.textContent = error.message;
  }
});

loadData().catch((error) => {
  els.paperList.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
});

setInterval(() => {
  api("/api/jobs")
    .then((data) => {
      state.jobs = data.jobs || [];
      renderJobs();
    })
    .catch(() => {});
}, 5000);
