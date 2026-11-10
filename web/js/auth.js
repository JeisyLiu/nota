/* Login, logout, and "who am I". */
import { api, toast } from "./api.js";

export async function login(username, password) {
  const data = await api("/api/auth/login", {
    method: "POST",
    body: { username, password },
  });
  return data.user;
}

export async function logout() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch {
    // Already gone: clearing the cookie locally is enough.
  }
  window.location.href = "/";
}

export async function currentUser() {
  const data = await api("/api/auth/me");
  return data.user;
}

/** Wires the login form on the public page. */
export function initLoginForm() {
  const form = document.getElementById("login-form");
  if (!form) return;
  const error = document.getElementById("login-error");
  const button = form.querySelector("button[type=submit]");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.textContent = "";
    button.disabled = true;
    const username = document.getElementById("username").value.trim();
    const password = document.getElementById("password").value;
    try {
      await login(username, password);
      // The console decides what to show based on the role, so send everyone to
      // the same place.
      window.location.href = "/console";
    } catch (err) {
      error.textContent = err.status === 401 ? "用户名或密码不正确" : (err.message || "登录失败");
      button.disabled = false;
    }
  });
}

/** Fills in demo credentials so a reviewer does not have to type them. */
export function initDemoFill() {
  for (const chip of document.querySelectorAll("[data-demo-user]")) {
    chip.addEventListener("click", () => {
      document.getElementById("username").value = chip.dataset.demoUser;
      document.getElementById("password").value = chip.dataset.demoPass || "123456";
      document.getElementById("username").focus();
      toast("已填入演示账号");
    });
  }
}
