/* Admin console: global overview, user/job/note management, approvals. */
import {
  api, el, clear, fmtTime, fmtNumber, pct, stateBadge, stateLabel, toast,
} from "./api.js";
import { barChart, donut, legend } from "./charts.js";
import { card, section, table } from "./user.js";

const PLAN_LABEL = { free: "Free", pro: "Pro" };

export async function renderAdminOverview(view) {
  const data = await api("/api/admin/overview?days=14");
  const totals = data.totals;

  const metrics = el("div", "metrics");
  metrics.appendChild(card("账号数", fmtNumber(totals.users), "含 1 个管理员"));
  metrics.appendChild(card("记录总数", fmtNumber(totals.notes),
    `近 14 天 ${fmtNumber(totals.notes_recent)}`));
  metrics.appendChild(card("任务成功率", pct(totals.success_rate),
    `${fmtNumber(totals.jobs_done)} / ${fmtNumber(totals.jobs)}`));
  metrics.appendChild(card("Token 消耗",
    `${fmtNumber(totals.tokens_in)} / ${fmtNumber(totals.tokens_out)}`, "输入 / 输出"));
  metrics.appendChild(card("平均延迟", `${fmtNumber(totals.avg_latency_ms)} ms`, "近 14 天"));
  metrics.appendChild(card("待审批", fmtNumber(totals.approvals_pending), "不可逆操作"));
  view.appendChild(metrics);

  const keys = [
    { key: "notes", label: "新增记录", color: "#38bdf8" },
    { key: "used", label: "额度消耗", color: "#6ee7b7" },
  ];
  const chartBody = el("div");
  chartBody.appendChild(legend(keys));
  chartBody.appendChild(barChart(data.series, keys));
  view.appendChild(section("近 14 天趋势", `统计时区 ${data.tz}`, chartBody));

  const split = el("div", "two-col");

  const stateColors = {
    done: "#6ee7b7", failed: "#f87171", cancelled: "#fbbf24",
    queued: "#8b93a7", transcribing: "#38bdf8", agent_running: "#818cf8",
  };
  const parts = (["done", "failed", "cancelled", "queued", "transcribing", "agent_running"])
    .map((key) => ({
      label: stateLabel(key), value: data.jobs_by_state[key] || 0, color: stateColors[key],
    }))
    .filter((part) => part.value > 0);

  const donutBox = el("div", "donut-box");
  donutBox.appendChild(parts.length ? donut(parts) : el("p", "empty", "还没有任务"));
  const legendBox = el("div", "legend legend-vertical");
  for (const part of parts) {
    const item = el("span", "legend-item");
    const dot = el("i");
    dot.style.background = part.color;
    item.appendChild(dot);
    item.appendChild(document.createTextNode(`${part.label} ${part.value}`));
    legendBox.appendChild(item);
  }
  donutBox.appendChild(legendBox);
  split.appendChild(section("任务状态分布", "当前全部任务", donutBox));

  const toolsBox = el("ol", "tool-list");
  if (!data.top_tools.length) toolsBox.appendChild(el("li", "empty", "还没有工具调用"));
  for (const tool of data.top_tools) {
    const li = el("li");
    li.appendChild(el("span", "tool-name", tool.tool));
    li.appendChild(el("span", "tool-count", `${tool.count} 次`));
    toolsBox.appendChild(li);
  }
  split.appendChild(section("工具调用 Top", "近 14 天", toolsBox));
  view.appendChild(split);

  const users = await api("/api/admin/users?limit=6");
  const rows = users.users.map((u) => [
    u.username,
    u.display_name,
    PLAN_LABEL[u.plan] || u.plan,
    fmtNumber(u.notes),
    `${u.used_today} 次`,
    pct(u.success_rate),
    fmtTime(u.last_active),
  ]);
  view.appendChild(section("账号概览", `共 ${users.total} 个账号`, table(
    ["用户名", "显示名", "套餐", "记录", "今日用量", "成功率", "最后活跃"], rows)));
}

export async function renderAdminUsers(view) {
  const state = { q: "" };
  const box = el("section", "panel");
  const toolbar = el("div", "toolbar");
  const search = el("input");
  search.type = "search";
  search.placeholder = "搜索用户名 / 显示名";
  search.className = "search";
  toolbar.appendChild(search);
  const total = el("span", "muted");
  toolbar.appendChild(total);
  box.appendChild(toolbar);

  const holder = el("div");
  box.appendChild(holder);
  view.appendChild(box);

  async function load() {
    const data = await api(`/api/admin/users?limit=100&q=${encodeURIComponent(state.q)}`);
    total.textContent = `共 ${data.total} 个账号`;
    clear(holder);
    const rows = data.users.map((user) => {
      const actions = el("div", "row-actions");
      if (user.role === "admin") {
        actions.appendChild(el("span", "muted", "管理员"));
      } else {
        const select = el("select", "plan-select");
        for (const plan of ["free", "pro"]) {
          const option = el("option", "", PLAN_LABEL[plan]);
          option.value = plan;
          if (plan === user.plan) option.selected = true;
          select.appendChild(option);
        }
        select.addEventListener("change", async () => {
          select.disabled = true;
          try {
            await api(`/api/admin/users/${encodeURIComponent(user.user_id)}`, {
              method: "PATCH",
              body: { plan: select.value },
            });
            toast(`${user.username} 已切换为 ${PLAN_LABEL[select.value]}`);
          } catch (error) {
            toast(error.message || "修改失败", true);
            await load();
          } finally {
            select.disabled = false;
          }
        });
        actions.appendChild(select);
      }
      const detail = el("a", "link", "详情");
      detail.href = `#/admin/users/${encodeURIComponent(user.user_id)}`;
      actions.appendChild(detail);

      return [
        user.username,
        user.display_name,
        el("span", "state " + (user.role === "admin" ? "done" : "queued"),
          user.role === "admin" ? "管理员" : "用户"),
        PLAN_LABEL[user.plan] || user.plan,
        fmtNumber(user.notes),
        `${user.used_today} 次`,
        pct(user.success_rate),
        fmtTime(user.last_active),
        actions,
      ];
    });
    holder.appendChild(table(
      ["用户名", "显示名", "角色", "套餐", "记录", "今日用量", "成功率", "最后活跃", "操作"],
      rows));
  }

  let timer = null;
  search.addEventListener("input", () => {
    // Debounce: every keystroke would hit the API.
    clearTimeout(timer);
    timer = setTimeout(() => { state.q = search.value.trim(); load(); }, 250);
  });
  await load();
}

export async function renderAdminUserDetail(view, ctx) {
  const userId = decodeURIComponent((ctx.path || "").split("/").pop() || "");
  const data = await api(`/api/admin/users/${encodeURIComponent(userId)}`);
  const profile = data.profile;

  const metrics = el("div", "metrics");
  metrics.appendChild(card("套餐", PLAN_LABEL[profile.plan] || profile.plan,
    profile.role === "admin" ? "管理员账号" : "可在用户列表修改"));
  metrics.appendChild(card("今日已用", `${data.usage.today.used} 次`,
    data.usage.today.limit === null ? "不限额度" : `上限 ${data.usage.today.limit}`));
  metrics.appendChild(card("记录数", fmtNumber(data.stats.notes.total),
    `语音 ${data.stats.notes.voice} · 文字 ${data.stats.notes.text}`));
  metrics.appendChild(card("成功率", pct(data.stats.jobs.success_rate),
    `${data.stats.jobs.done} / ${data.stats.jobs.total}`));
  view.appendChild(metrics);

  const keys = [{ key: "used", label: "每日调用", color: "#6ee7b7" }];
  const chart = el("div");
  chart.appendChild(legend(keys));
  chart.appendChild(barChart(data.usage.series, keys));
  view.appendChild(section("用量趋势", `按 ${profile.tz} 的自然日统计`, chart));

  const info = el("dl", "profile-grid");
  for (const [label, value] of [
    ["用户名", profile.username],
    ["显示名", profile.display_name],
    ["邮箱", profile.email || "—"],
    ["账号 ID", profile.user_id],
    ["注册时间", fmtTime(profile.created_at)],
    ["上次登录", fmtTime(profile.last_login_at)],
  ]) {
    info.appendChild(el("dt", "", label));
    info.appendChild(el("dd", "", value));
  }
  view.appendChild(section("账号资料", "", info));

  const rows = data.activity.map((item) => [
    fmtTime(item.created_at),
    item.source === "voice" ? "语音" : "文字",
    item.title || "—",
    stateBadge(item.state || "queued"),
  ]);
  view.appendChild(section("最近记录", "最新 20 条", rows.length
    ? table(["时间", "来源", "内容", "状态"], rows)
    : el("p", "empty", "还没有记录")));
}

export async function renderAdminJobs(view) {
  const state = { filter: "" };
  const box = el("section", "panel");
  const toolbar = el("div", "toolbar");
  const select = el("select", "search");
  const options = [["", "全部状态"], ["queued", "排队中"], ["transcribing", "转写中"],
    ["agent_running", "整理中"], ["done", "已完成"], ["failed", "失败"], ["cancelled", "已停止"]];
  for (const [value, label] of options) {
    const option = el("option", "", label);
    option.value = value;
    select.appendChild(option);
  }
  toolbar.appendChild(select);
  const count = el("span", "muted");
  toolbar.appendChild(count);
  box.appendChild(toolbar);
  const holder = el("div");
  box.appendChild(holder);
  view.appendChild(box);

  async function load() {
    const query = state.filter ? `?limit=100&state=${encodeURIComponent(state.filter)}` : "?limit=100";
    const data = await api("/api/admin/jobs" + query);
    count.textContent = `${data.jobs.length} 个任务`;
    clear(holder);
    const rows = data.jobs.map((job) => {
      const actions = el("div", "row-actions");
      if (job.state === "failed") {
        const retry = el("button", "ghost small", "重试");
        retry.addEventListener("click", async () => {
          retry.disabled = true;
          try {
            await api(`/api/admin/jobs/${encodeURIComponent(job.job_id)}/retry`, { method: "POST" });
            toast("已重新排队");
            await load();
          } catch (error) {
            toast(error.message || "重试失败", true);
            retry.disabled = false;
          }
        });
        actions.appendChild(retry);
      }
      if (["queued", "transcribing", "agent_running"].includes(job.state)) {
        const cancel = el("button", "stop small", "取消");
        cancel.addEventListener("click", async () => {
          cancel.disabled = true;
          try {
            const res = await api(`/api/admin/jobs/${encodeURIComponent(job.job_id)}/cancel`,
              { method: "POST" });
            toast(res.cancelled ? "已取消，额度已退回" : "任务已结束");
            await load();
          } catch (error) {
            toast(error.message || "取消失败", true);
            cancel.disabled = false;
          }
        });
        actions.appendChild(cancel);
      }
      if (!actions.childElementCount) actions.appendChild(el("span", "muted", "—"));

      return [
        fmtTime(job.created_at),
        job.username,
        job.source === "voice" ? "语音" : "文字",
        job.title || "—",
        stateBadge(job.state),
        actions,
      ];
    });
    holder.appendChild(rows.length
      ? table(["时间", "用户", "来源", "内容", "状态", "操作"], rows)
      : el("p", "empty", "没有符合条件的任务"));
  }

  select.addEventListener("change", () => { state.filter = select.value; load(); });
  await load();
}

export async function renderAdminNotes(view) {
  const state = { q: "" };
  const box = el("section", "panel");
  const toolbar = el("div", "toolbar");
  const search = el("input");
  search.type = "search";
  search.placeholder = "搜索记录内容";
  search.className = "search";
  toolbar.appendChild(search);
  const count = el("span", "muted");
  toolbar.appendChild(count);
  box.appendChild(toolbar);
  const holder = el("div");
  box.appendChild(holder);
  view.appendChild(box);

  async function load() {
    const data = await api(`/api/admin/notes?limit=100&q=${encodeURIComponent(state.q)}`);
    count.textContent = `${data.notes.length} 条记录`;
    clear(holder);
    const rows = data.notes.map((note) => [
      fmtTime(note.created_at),
      note.username,
      note.source === "voice" ? "语音" : "文字",
      note.title || "—",
      note.transcript || "—",
    ]);
    holder.appendChild(rows.length
      ? table(["时间", "用户", "来源", "标题", "内容"], rows)
      : el("p", "empty", "没有符合条件的记录"));
  }

  let timer = null;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { state.q = search.value.trim(); load(); }, 250);
  });
  await load();
}

export async function renderAdminApprovals(view) {
  const data = await api("/api/admin/approvals");
  const list = el("div", "approval-list");
  if (!data.approvals.length) {
    list.appendChild(el("p", "empty", "没有待确认的操作"));
  }
  for (const item of data.approvals) {
    const box = el("div", "approval");
    const head = el("div", "approval-head");
    head.appendChild(el("strong", "", item.tool));
    head.appendChild(el("span", "state cancelled", "不可撤销"));
    box.appendChild(head);
    box.appendChild(el("div", "muted", `${item.username} · ${fmtTime(item.created_at)} 提交`));
    const pre = el("pre", "code");
    pre.textContent = JSON.stringify(item.args, null, 2);
    box.appendChild(pre);
    box.appendChild(el("div", "muted",
      `超时时间 ${fmtTime(item.expires_at)}，超时视为拒绝`));
    list.appendChild(box);
  }
  view.appendChild(section("待确认操作", "由用户在自己的界面上确认或忽略", list));
}
