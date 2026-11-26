import { SETTINGS_MESSAGE, ISSUE_MESSAGE, githubIssue } from "./protocol.js";

const BASE_URL = "http://127.0.0.1:48729";
const SETTINGS_PATH = "/v1/settings/deepseek";
const OPTIONS_URL = chrome.runtime.getURL("settings.html");
const METHODS = { status: "GET", save: "PUT", clear: "DELETE" };
const JOB_ROUTES = {
  "cache-lookup": ["/v1/cache/lookup", "POST"],
  "start-analysis": ["/v1/analyses", "POST"],
  "analysis-status": ["/v1/analyses", "GET"],
  "start-summary": ["/v1/summaries", "POST"],
  "summary-status": ["/v1/summaries", "GET"],
};

async function requestBackend(path, method, { payload, thread } = {}) {
  const options = {
    method,
    headers: { "X-Seed-Client": chrome.runtime.id },
    credentials: "omit",
    redirect: "error",
    cache: "no-store",
    signal: AbortSignal.timeout(5000),
  };
  if (thread) options.headers["X-Seed-Thread"] = thread;
  if (payload !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(payload);
  }

  let response;
  try {
    response = await fetch(BASE_URL + path, options);
  } catch {
    return { ok: false, code: "unavailable" };
  }

  try {
    const data = await response.json();
    if (response.ok) return { ok: true, data };
    const fallback = response.status === 422 ? "invalid_discussion"
      : response.status === 404 ? "backend_old" : "backend_error";
    return { ok: false, code: data?.detail?.code ?? fallback };
  } catch {
    return { ok: false, code: "backend_error" };
  }
}

async function requestSettings(message) {
  if (message.action === "open-options") {
    try {
      await chrome.runtime.openOptionsPage();
      return { ok: true };
    } catch {
      return { ok: false, code: "cannot_open_options" };
    }
  }

  const payload = message.action === "save" ? { api_key: message.apiKey } : undefined;
  const reply = await requestBackend(SETTINGS_PATH, METHODS[message.action], { payload });
  if (!reply.ok) {
    return reply.code === "invalid_discussion" ? { ok: false, code: "invalid_key" } : reply;
  }
  const configured = reply.data?.configured;
  if (typeof configured !== "boolean") return { ok: false, code: "backend_error" };
  return { ok: true, configured };
}

function requestIssue(message, issue) {
  const [route, method] = JOB_ROUTES[message.action];
  if (method === "GET" && !/^[0-9a-f]{32}$/.test(message.jobId)) {
    return Promise.resolve({ ok: false, code: "job_missing" });
  }
  return requestBackend(method === "GET" ? route + "/" + message.jobId : route, method, {
    payload: method === "POST" ? message.payload : undefined,
    thread: issue.id,
  });
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id) return;
  const issue = sender.frameId === 0 ? githubIssue(sender.url) : null;

  let pending;
  if (message?.type === SETTINGS_MESSAGE) {
    const fromOptions = sender.url === OPTIONS_URL;
    // The content script can show status and navigate. The extension page owns the secret form.
    const allowed = (fromOptions && Object.hasOwn(METHODS, message.action))
      || (issue && (message.action === "status" || message.action === "open-options"));
    if (allowed) pending = requestSettings(message);
  } else if (message?.type === ISSUE_MESSAGE && issue && Object.hasOwn(JOB_ROUTES, message.action)) {
    if (message.thread !== issue.id) {
      respond({ ok: false, code: "issue_changed" });
      return;
    }
    pending = requestIssue(message, issue);
  } else if (message?.type !== ISSUE_MESSAGE) {
    return;
  }

  if (!pending) {
    respond({ ok: false, code: "forbidden" });
    return;
  }

  pending.then(respond, () => respond({ ok: false, code: "backend_error" }));
  return true;
});
