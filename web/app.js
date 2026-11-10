/* Nota MVP front-end — no build step, no framework. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    user: localStorage.getItem("nota.user") || "demo",
    jobId: null,
    source: null, // EventSource
    recorder: null,
    chunks: [],
    recording: false,
  };

  // ------------------------------------------------------------------ helpers
  function headers(extra) {
    return Object.assign({ "X-User-Id": state.user }, extra || {});
  }

  async function api(path, options) {
    const opts = Object.assign({ headers: headers() }, options || {});
    if (opts.body && typeof opts.body !== "string" && !(opts.body instanceof FormData)) {
      opts.body = JSON.stringify(opts.body);
      opts.headers = headers({ "Content-Type": "application/json" });
    }
    const res = await fetch(path, opts);
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { raw: text }; }
    if (!res.ok) {
      const detail = (data && data.detail) || data || {};
      const err = new Error(detail.message || detail.error || res.statusText);
      err.status = res.status;
      err.detail = detail;
      throw err;
    }
    return data;
  }

  function toast(message, isError) {
    const el = document.createElement("div");
    el.className = "toast" + (isError ? " error" : "");
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3200);
  }

  function fmtTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // ------------------------------------------------------------------ quota
  async function refreshQuota() {
    try {
      const q = await api("/api/quota");
      const limit = q.limit === null ? null : q.limit;
      const bar = document.querySelector(".quota-bar");
      const pct = limit ? Math.min(100, (q.used / limit) * 100) : 12;
      $("quota-fill").style.width = pct + "%";
      bar.classList.toggle("full", limit !== null && q.used >= limit);
      $("quota-text").textContent = limit === null
        ? `${q.used} / 不限`
        : `${q.used} / ${limit}`;
      $("quota").title = limit === null
        ? "Pro 用户不限额"
        : `剩余 ${q.remaining} 次，按本机时区的自然日重置`;
    } catch (e) {
      $("quota-text").textContent = "额度不可用";
    }
  }

  // ------------------------------------------------------------------ notes
  async function refreshNotes() {
    const list = $("notes");
    list.innerHTML = "";
    try {
      const notes = await api("/api/notes?limit=20");
      if (!notes.length) {
        list.appendChild(el("li", "", "还没有记录"));
        return;
      }
      for (const n of notes) {
        const li = el("li");
        const cls = n.state === "failed" ? " failed"
          : n.state === "cancelled" ? " cancelled" : "";
        const tag = el("span", "n-state" + cls, stateLabel(n.state || "?"));
        li.appendChild(tag);
        li.appendChild(document.createTextNode(n.title || n.transcript || n.note_id));
        // The list already carries the note id; no need to re-fetch the list.
        li.addEventListener("click", () => loadJobForNote(n.note_id));
        list.appendChild(li);
      }
    } catch (e) {
      list.appendChild(el("li", "", "加载失败：" + e.message));
    }
  }

  async function loadJobForNote(noteId) {
    // The notes endpoint does not expose job ids; find the newest job for the
    // note by asking the job endpoint through the trace of the notes list.
    try {
      const res = await fetch(`/api/notes/${encodeURIComponent(noteId)}/job`, { headers: headers() });
      if (!res.ok) throw new Error("no job");
      const job = await res.json();
      state.jobId = job.job_id;
      renderJob(job);
      await loadSteps(job.job_id);
      hideApprovals();
    } catch (e) {
      // fall back: nothing selected
    }
  }

  // ------------------------------------------------------------------ recorder
  async function startRecording() {
    if (state.recording) return;
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      toast("这个浏览器不支持录音，请直接打字", true);
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      toast("没有拿到麦克风权限：" + e.message, true);
      return;
    }
    const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "";
    state.recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    state.chunks = [];
    state.recorder.ondataavailable = (ev) => { if (ev.data.size) state.chunks.push(ev.data); };
    state.recorder.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      uploadAudio(new Blob(state.chunks, { type: state.recorder.mimeType || "audio/webm" }));
    };
    state.recorder.start();
    state.recording = true;
    $("btn-record").classList.add("recording");
    $("mic-label").textContent = "录音中… 再点一下结束";
    $("rec-status").textContent = "正在录音";
  }

  function stopRecording() {
    if (!state.recording || !state.recorder) return;
    state.recorder.stop();
    state.recording = false;
    $("btn-record").classList.remove("recording");
    $("mic-label").textContent = "按住说话";
    $("rec-status").textContent = "上传中…";
  }

  async function uploadAudio(blob) {
    if (!blob.size) {
      $("rec-status").textContent = "没有录到声音";
      return;
    }
    const form = new FormData();
    form.append("file", blob, "recording.webm");
    form.append("idempotency_key", "web-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8));
    try {
      const created = await api("/api/notes/audio", { method: "POST", body: form });
      handleCreated(created, "voice");
    } catch (e) {
      $("rec-status").textContent = "";
      handleError(e);
    }
  }

  async function sendText() {
    const text = $("text-input").value.trim();
    if (!text) { toast("先说点什么"); return; }
    $("btn-send").disabled = true;
    try {
      const created = await api("/api/notes/text", {
        method: "POST",
        body: {
          text,
          idempotency_key: "web-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8),
        },
      });
      $("text-input").value = "";
      handleCreated(created, "text");
    } catch (e) {
      handleError(e);
    } finally {
      $("btn-send").disabled = false;
    }
  }

  function handleError(e) {
    if (e.detail && e.detail.error === "quota_exceeded") {
      toast(`今日免费额度已用完（${e.detail.used}/${e.detail.limit}），` +
            `本地时间零点重置`, true);
    } else {
      toast(e.message || "出错了", true);
    }
    refreshQuota();
  }

  // ------------------------------------------------------------------ job flow
  function handleCreated(created, source) {
    state.jobId = created.job_id;
    state.source = source;
    $("rec-status").textContent = source === "voice" ? "已上传，正在处理" : "";
    $("result-empty").classList.add("hidden");
    $("result").classList.remove("hidden");
    $("job-id").textContent = created.job_id;
    setJobState("queued");
    $("summary-title").textContent = "";
    $("summary-body").textContent = "正在处理…";
    $("todos").innerHTML = "";
    $("reminders").innerHTML = "";
    $("steps").innerHTML = "";
    hideApprovals();
    refreshQuota();
    refreshNotes();
    streamJob(created.job_id);
  }

  const TERMINAL = ["done", "failed", "cancelled"];

  // Same vocabulary as the app, so the product does not speak two languages in
  // one recording. The trace keeps TECHNICAL names (ASSISTANT/TOOL) on purpose.
  const STATE_LABELS = {
    queued: "排队中",
    transcribing: "转写中",
    agent_running: "整理中",
    done: "已完成",
    failed: "失败",
    cancelled: "已停止",
  };
  const stateLabel = (name) => STATE_LABELS[name] || name;

  function setJobState(name) {
    const badge = $("job-state");
    badge.textContent = stateLabel(name);
    const cls = name === "done" ? "done"
      : name === "failed" ? "failed"
      : name === "cancelled" ? "cancelled"
      : "";
    badge.className = "state " + cls;
    // Stop only makes sense while something is still running.
    const stop = $("btn-stop");
    if (stop) stop.classList.toggle("hidden", TERMINAL.indexOf(name) !== -1);
  }

  async function stopJob() {
    if (!state.jobId) return;
    const btn = $("btn-stop");
    btn.disabled = true;
    try {
      const res = await api(`/api/jobs/${encodeURIComponent(state.jobId)}/cancel`, {
        method: "POST",
      });
      if (state.source && state.source.close) {
        state.source.close();
        state.source = null;
      }
      renderJob(res.job);
      toast(res.cancelled ? "已停止，本次额度已退回" : "任务已结束");
      refreshQuota();
      refreshNotes();
    } catch (e) {
      toast(e.message || "停止失败", true);
    } finally {
      btn.disabled = false;
    }
  }

  function streamJob(jobId) {
    if (state.source && state.source.close) state.source.close();
    if (!window.EventSource) { pollJob(jobId); return; }

    const es = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/stream`);
    state.source = es;

    es.addEventListener("state", (ev) => {
      const data = JSON.parse(ev.data);
      setJobState(data.state);
    });

    es.addEventListener("step", (ev) => {
      appendStep(JSON.parse(ev.data));
    });

    es.addEventListener("done", (ev) => {
      const job = JSON.parse(ev.data);
      renderJob(job);
      es.close();
      state.source = null;
      refreshQuota();
      refreshNotes();
      loadSteps(jobId);
    });

    es.onerror = () => {
      es.close();
      state.source = null;
      pollJob(jobId);
    };
  }

  async function pollJob(jobId) {
    for (let i = 0; i < 120; i++) {
      try {
        const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
        setJobState(job.state);
        if (TERMINAL.indexOf(job.state) !== -1) {
          renderJob(job);
          await loadSteps(jobId);
          refreshQuota();
          refreshNotes();
          return;
        }
      } catch (e) { /* keep polling */ }
      await new Promise((r) => setTimeout(r, 800));
    }
  }

  async function loadSteps(jobId) {
    try {
      const steps = await api(`/api/jobs/${encodeURIComponent(jobId)}/steps`);
      $("steps").innerHTML = "";
      steps.forEach(appendStep);
    } catch (e) { /* ignore */ }
  }

  function appendStep(step) {
    const li = el("li", step.type);
    const head = el("div", "s-head");
    head.appendChild(el("span", "s-type", step.type === "tool_result" ? "tool" : step.type));
    head.appendChild(el("span", "s-name", step.name || ""));
    if (step.ms) head.appendChild(el("span", "s-ms", step.ms + " ms"));
    li.appendChild(head);

    const body = step.result !== undefined && step.result !== null ? step.result : step.args;
    if (body !== undefined && body !== null) {
      const pre = el("pre");
      pre.textContent = typeof body === "string" ? body : JSON.stringify(body, null, 2);
      li.appendChild(pre);
    }
    $("steps").appendChild(li);
    // Scroll the trace list, not the page: scrollIntoView also scrolled the
    // document, which slid the whole layout up under the top bar.
    const list = $("steps");
    list.scrollTop = list.scrollHeight;
  }

  function renderJob(job) {
    setJobState(job.state);
    $("job-id").textContent = job.job_id;
    $("result-empty").classList.add("hidden");
    $("result").classList.remove("hidden");

    $("summary-title").textContent = job.title || "";
    $("summary-body").textContent = job.summary || (job.state === "failed"
      ? `处理失败：${job.error || "未知错误"}（本次额度已退回）`
      : job.state === "cancelled"
        ? "已停止，本次额度已退回。没有生成待办或提醒。"
        : "正在处理…");

    const todos = $("todos");
    todos.innerHTML = "";
    if (!job.todos || !job.todos.length) {
      todos.appendChild(el("li", "", "暂无待办"));
    } else {
      for (const t of job.todos) {
        const li = el("li");
        const box = document.createElement("input");
        box.type = "checkbox";
        box.checked = t.status === "done";
        box.addEventListener("change", async () => {
          await api(`/api/todos/${encodeURIComponent(t.id)}/done`, { method: "POST" });
          li.classList.add("done");
          loadSteps(job.job_id);
        });
        li.appendChild(box);
        li.appendChild(el("span", "t-title", t.title));
        if (t.due_at) li.appendChild(el("span", "t-due", fmtTime(t.due_at)));
        if (t.status === "done") li.classList.add("done");
        todos.appendChild(li);
      }
    }

    const rem = $("reminders");
    rem.innerHTML = "";
    if (!job.reminders || !job.reminders.length) {
      rem.appendChild(el("li", "", "暂无提醒"));
    } else {
      for (const r of job.reminders) {
        const li = el("li");
        li.appendChild(el("span", "t-title", r.title));
        li.appendChild(el("span", "t-due", fmtTime(r.fire_at)));
        rem.appendChild(li);
      }
    }
    refreshApprovals();
  }

  // ------------------------------------------------------------------ approvals
  async function refreshApprovals() {
    let actions = [];
    try { actions = await api("/api/actions"); } catch (e) { return; }
    const wrap = $("approvals");
    const list = $("approval-list");
    list.innerHTML = "";
    if (!actions.length) { wrap.classList.add("hidden"); return; }
    wrap.classList.remove("hidden");
    for (const a of actions) {
      const li = el("li");
      li.appendChild(el("strong", "", `${a.tool}（不可撤销）`));
      const pre = el("pre");
      pre.textContent = JSON.stringify(a.args, null, 2);
      li.appendChild(pre);
      const row = el("div", "a-actions");
      const yes = el("button", "approve", "确认执行");
      const no = el("button", "reject", "忽略");
      yes.addEventListener("click", () => decide(a.id, true));
      no.addEventListener("click", () => decide(a.id, false));
      row.appendChild(yes);
      row.appendChild(no);
      li.appendChild(row);
      list.appendChild(li);
    }
  }

  function hideApprovals() { $("approvals").classList.add("hidden"); }

  async function decide(actionId, approve) {
    try {
      const res = await api(`/api/actions/${encodeURIComponent(actionId)}`, {
        method: "POST",
        body: { approve },
      });
      toast(approve ? "已执行：" + (res.result ? res.result.title || "" : "") : "已忽略");
      refreshApprovals();
      loadSteps(state.jobId);
    } catch (e) {
      toast(e.message, true);
    }
  }

  // ------------------------------------------------------------------ ask
  async function ask() {
    const question = $("ask-input").value.trim();
    if (!question) { toast("问点什么"); return; }
    const box = $("ask-answer");
    box.classList.remove("hidden");
    box.textContent = "检索中…";
    try {
      const res = await api("/api/ask", { method: "POST", body: { question } });
      box.innerHTML = "";
      box.appendChild(el("div", "", res.answer));
      const meta = el("div", "cites");
      if (res.retrieved === 0) {
        meta.textContent = "没有命中任何历史记录 —— 因此没有调用模型，避免编造。";
      } else {
        meta.textContent = `命中 ${res.retrieved} 条` +
          (res.grounded ? "，引用可核验" : "，⚠ 回答包含无法核验的引用");
      }
      box.appendChild(meta);
      for (const c of res.citations || []) {
        const a = document.createElement("a");
        a.href = "#";
        a.textContent = `[note:${c.note_id}] ${(c.text || "").slice(0, 40)}`;
        a.addEventListener("click", (ev) => { ev.preventDefault(); loadJobForNote(c.note_id); });
        const line = el("div", "cites");
        line.appendChild(a);
        box.appendChild(line);
      }
    } catch (e) {
      box.textContent = "提问失败：" + e.message;
    }
  }

  // ------------------------------------------------------------------ wire up
  function bind() {
    $("user-input").value = state.user;
    $("user-input").addEventListener("change", (e) => {
      state.user = e.target.value.trim() || "demo";
      localStorage.setItem("nota.user", state.user);
      refreshQuota();
      refreshNotes();
    });

    $("btn-record").addEventListener("click", () => {
      if (state.recording) stopRecording(); else startRecording();
    });
    $("btn-send").addEventListener("click", sendText);
    $("btn-stop").addEventListener("click", stopJob);
    $("text-input").addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") sendText();
    });
    $("btn-sample").addEventListener("click", () => {
      const samples = [
        "明天下午三点提醒我给张总发报价单",
        "下周三上午十点跟设计团队评审新版原型",
        "记得买牛奶和鸡蛋，顺便取快递",
        "今天开了个会，聊了下季度排期",
      ];
      $("text-input").value = samples[Math.floor(Math.random() * samples.length)];
    });
    $("btn-ask").addEventListener("click", ask);
    $("ask-input").addEventListener("keydown", (e) => { if (e.key === "Enter") ask(); });

    refreshQuota().then(refreshNotes).then(refreshApprovals);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) { refreshQuota(); refreshApprovals(); }
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    bind();
    // reflect the running mode in the header, and say which model is answering
    fetch("/healthz").then((r) => r.json()).then((h) => {
      const tag = $("mode-tag");
      const parts = [];
      if (h.stt === "faster_whisper") parts.push("本地转写");
      if (h.demo) {
        tag.textContent = "demo · 无模型密钥";
        tag.title = "使用确定性本地规划器跑通全链路，没有调用任何模型。\n" +
                    "要接真模型：在 .env 里设置 LLM_PROVIDER=openai 并填入 key，然后重启。";
        return;
      }
      tag.textContent = h.provider + " · " + (h.model || "在线");
      tag.classList.add("live");
      tag.title = [
        "provider: " + h.provider,
        "model: " + (h.model || "?"),
        "key: " + (h.api_key_configured ? "已配置" : "缺失"),
      ].concat(parts).join("\n");
    }).catch(() => {});
  });
})();
