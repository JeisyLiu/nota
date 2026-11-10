/* Personal centre: balance, call history, profile. */
import {
  api, el, clear, fmtTime, fmtNumber, pct, stateBadge, stateLabel, toast,
} from "./api.js";
import { barChart, legend } from "./charts.js";

function card(label, value, hint) {
  const box = el("div", "metric");
  box.appendChild(el("div", "metric-label", label));
  box.appendChild(el("div", "metric-value", value));
  if (hint) box.appendChild(el("div", "metric-hint", hint));
  return box;
}

function table(headers, rows) {
  const wrap = el("div", "table-wrap");
  const table_ = el("table", "table");
  const head = el("tr");
  for (const h of headers) head.appendChild(el("th", "", h));
  table_.appendChild(el("thead")).appendChild(head);
  const body = el("tbody");
  for (const cells of rows) {
    const tr = el("tr");
    for (const cell of cells) {
      const td = el("td");
      if (cell instanceof Node) td.appendChild(cell);
      else td.textContent = cell ?? "—";
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
  table_.appendChild(body);
  wrap.appendChild(table_);
  return wrap;
}

function section(title, subtitle, body) {
  const box = el("section", "panel");
  const head = el("div", "panel-head");
  const left = el("div");
  left.appendChild(el("h2", "", title));
  if (subtitle) left.appendChild(el("p", "panel-sub", subtitle));
  head.appendChild(left);
  box.appendChild(head);
  box.appendChild(body);
  return box;
}

export async function renderOverview(view) {
  const [usage, stats, activity] = await Promise.all([
    api("/api/me/usage?days=14"),
    api("/api/me/stats?days=14"),
    api("/api/me/activity?limit=6"),
  ]);

  const today = usage.today;
  const metrics = el("div", "metrics");
  metrics.appendChild(card(
    "今日剩余",
    today.limit === null ? "不限" : `${today.remaining} 次`,
    today.limit === null ? `${usage.tz} · Pro` : `已用 ${today.used} / ${today.limit}`,
  ));
  metrics.appendChild(card("近 14 天记录", fmtNumber(stats.stats.notes.recent),
    `语音 ${stats.stats.notes.voice} · 文字 ${stats.stats.notes.text}`));
  metrics.appendChild(card("任务成功率", pct(stats.stats.jobs.success_rate),
    `共 ${stats.stats.jobs.total} 个任务`));
  metrics.appendChild(card("待办", `${stats.stats.todos.open} 未完成`,
    `已完成 ${stats.stats.todos.done}`));
  view.appendChild(metrics);

  const keys = [{ key: "used", label: "每日调用", color: "#6ee7b7" }];
  const chartPanel = section("用量趋势", `按 ${usage.tz} 的自然日统计`, (() => {
    const box = el("div");
    box.appendChild(legend(keys));
    box.appendChild(barChart(usage.series, keys));
    return box;
  })());
  view.appendChild(chartPanel);

  const rows = activity.activity.map((item) => [
    fmtTime(item.created_at),
    item.source === "voice" ? "语音" : "文字",
    item.title || "—",
    stateBadge(item.state || "queued"),
  ]);
  view.appendChild(section("最近调用", "最新 6 条", rows.length
    ? table(["时间", "来源", "内容", "状态"], rows)
    : el("p", "empty", "还没有记录")));
}

export async function renderUsage(view) {
  const usage = await api("/api/me/usage?days=14");
  const stats = await api("/api/me/stats?days=14");
  const [data] = await Promise.all([api("/api/me/activity?limit=100")]);

  const metrics = el("div", "metrics");
  metrics.appendChild(card("今日已用", `${usage.today.used} 次`,
    usage.today.limit === null ? "不限额度" : `上限 ${usage.today.limit} 次`));
  metrics.appendChild(card("累计退回", `${usage.refunds} 次`,
    "取消或失败时自动退回"));
  metrics.appendChild(card("平均耗时", `${fmtNumber(stats.stats.agent.avg_latency_ms)} ms`,
    `${stats.stats.agent.runs} 次运行`));
  view.appendChild(metrics);

  const rows = data.activity.map((item) => [
    fmtTime(item.created_at),
    item.source === "voice" ? "语音" : "文字",
    item.title || item.transcript || "—",
    stateBadge(item.state || "queued"),
    item.error || "—",
  ]);
  view.appendChild(section("调用记录", `共 ${rows.length} 条`, rows.length
    ? table(["时间", "来源", "内容", "状态", "错误"], rows)
    : el("p", "empty", "还没有调用记录")));
}

export async function renderProfile(view, { user }) {
  const data = await api("/api/me/profile");
  const profile = data.profile;

  const list = el("dl", "profile-grid");
  const rows = [
    ["用户名", profile.username || profile.user_id],
    ["显示名", profile.display_name],
    ["邮箱", profile.email || "—"],
    ["套餐", profile.plan === "pro" ? "Pro（不限额度）" : "Free（每日 10 次）"],
    ["角色", profile.role === "admin" ? "管理员" : "普通用户"],
    ["时区", profile.tz],
    ["注册时间", fmtTime(profile.created_at)],
    ["上次登录", fmtTime(profile.last_login_at)],
  ];
  for (const [label, value] of rows) {
    list.appendChild(el("dt", "", label));
    list.appendChild(el("dd", "", value));
  }
  view.appendChild(section("个人信息", "账号资料由管理员维护", list));

  const form = el("form", "form-inline");
  const current = el("input");
  current.type = "password";
  current.placeholder = "当前密码";
  current.autocomplete = "current-password";
  const next = el("input");
  next.type = "password";
  next.placeholder = "新密码（至少 6 位）";
  next.autocomplete = "new-password";
  const submit = el("button", "primary", "修改密码");
  submit.type = "submit";
  form.append(current, next, submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (next.value.length < 6) { toast("新密码至少 6 位", true); return; }
    submit.disabled = true;
    try {
      await api("/api/me/password", {
        method: "POST",
        body: { current: current.value, new: next.value },
      });
      toast("密码已修改，其它设备已退出登录");
      current.value = "";
      next.value = "";
    } catch (error) {
      toast(error.message || "修改失败", true);
    } finally {
      submit.disabled = false;
    }
  });
  view.appendChild(section("修改密码", `当前账号：${profile.username || profile.user_id}`, form));
}

export { card, table, section };
