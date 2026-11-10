/* Shared browser helpers: fetch wrapper, formatting, small DOM utilities. */

/** Raised for any non-2xx response so callers can react to status codes. */
export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

/**
 * Same-origin fetch that sends the session cookie.
 *
 * A 401 means the session is gone, which is not an error worth showing: the
 * console bounces back to the login screen instead of painting a broken page.
 */
export async function api(path, options = {}) {
  const opts = { credentials: "same-origin", ...options };
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  }
  const res = await fetch(path, opts);
  if (res.status === 401 && !path.startsWith("/api/auth/login")) {
    window.location.href = "/";
    throw new ApiError("未登录", 401, null);
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!res.ok) {
    const detail = (data && data.detail) || data || {};
    const message = detail.message || detail.error || res.statusText || "请求失败";
    throw new ApiError(message, res.status, detail);
  }
  return data;
}

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

export function toast(message, isError = false) {
  const node = el("div", "toast" + (isError ? " error" : ""), message);
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 3200);
}

const STATE_LABELS = {
  queued: "排队中",
  transcribing: "转写中",
  agent_running: "整理中",
  done: "已完成",
  failed: "失败",
  cancelled: "已停止",
};

export const stateLabel = (state) => STATE_LABELS[state] || state || "—";

export function stateBadge(state) {
  return el("span", "state " + (state || ""), stateLabel(state));
}

export function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fmtNumber(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString("zh-CN");
}

export function pct(value) {
  if (value === null || value === undefined) return "—";
  return `${Math.round(Number(value) * 100)}%`;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}
