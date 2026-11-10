/* Hash router with role guards.
 *
 * Hash routing instead of history API because the pages are served as plain
 * files: a deep link like /console#/admin/users works without teaching the server
 * about every route.
 */
import { el, clear } from "./api.js";

export function createRouter({ routes, view, title, nav, user, home }) {
  const allowed = routes.filter((route) => !route.admin || (user.role || "user") === "admin");
  // Admins land on the global panel, everyone else on their own overview.
  const landing = home || allowed[0].path;

  function renderNav(active) {
    clear(nav);
    for (const route of allowed) {
      if (route.hidden) continue;
      const link = el("a", "nav-item" + (route.path === active ? " active" : ""), route.label);
      link.href = "#" + route.path;
      nav.appendChild(link);
    }
  }

  function currentPath() {
    const hash = window.location.hash.replace(/^#/, "");
    return hash || landing;
  }

  // Longest prefix wins, so #/admin/users/zhangwei matches #/admin/users.
  function resolve(path) {
    const matches = allowed
      .filter((route) => path === route.path || path.startsWith(route.path + "/"))
      .sort((a, b) => b.path.length - a.path.length);
    return matches[0] || null;
  }

  async function paint() {
    const path = currentPath();
    const route = resolve(path);
    if (!route) {
      clear(view).appendChild(el("p", "empty", "页面不存在"));
      return;
    }
    title.textContent = route.label;
    renderNav(route.path);
    clear(view);
    try {
      await route.render(view, { user, path });
    } catch (error) {
      view.appendChild(el("p", "empty", "加载失败：" + (error.message || error)));
    }
  }

  window.addEventListener("hashchange", paint);
  if (!window.location.hash) window.location.hash = "#" + landing;
  paint();
  return { paint };
}
