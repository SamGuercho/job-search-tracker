import { toast } from "./ui.js";

// Single entry point to the backend: every call is a named action.
export async function call(action, payload = {}) {
  const res = await fetch(`/api/actions/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.status === 401) { location.href = "/login"; throw new Error("Login required"); }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(body.detail)
      ? body.detail.map((d) => `${(d.loc || []).join(".")}: ${d.msg}`).join("; ")
      : body.detail;
    throw new Error(detail || `${action} failed (${res.status})`);
  }
  if (body.notice) toast(body.notice);  // e.g. OpenAI quota reached, switched to Claude
  return body.result;
}

export const fileUrl = (path) => `/files/${path.split("/").map(encodeURIComponent).join("/")}`;

// Multipart upload to a non-action endpoint (files can't travel in JSON actions).
export async function upload(url, file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(url, { method: "POST", body: form });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Upload failed (${res.status})`);
  return body.result;
}
