import { SETTINGS_MESSAGE } from "./protocol.js";

export const STATUS_LABEL = {
  checking: "正在读取",
  configured: "已保存",
  missing: "未设置",
  offline: "后端未连接",
  error: "读取失败",
};

export async function sendSettings(action, apiKey) {
  try {
    const message = { type: SETTINGS_MESSAGE, action };
    if (action === "save") message.apiKey = apiKey;
    return (await chrome.runtime.sendMessage(message)) ?? { ok: false, code: "unavailable" };
  } catch {
    return { ok: false, code: "unavailable" };
  }
}

export function settingsStatus(result) {
  if (result.ok) return result.configured ? "configured" : "missing";
  return result.code === "unavailable" ? "offline" : "error";
}
