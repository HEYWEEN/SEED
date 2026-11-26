import { ISSUE_MESSAGE } from "./protocol.js";

export class IssueError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

async function request(action, fields) {
  let reply;
  try {
    reply = await chrome.runtime.sendMessage({ type: ISSUE_MESSAGE, action, ...fields });
  } catch {
    throw new IssueError("unavailable");
  }
  if (!reply?.ok) throw new IssueError(reply?.code ?? "unavailable");
  return reply.data;
}

export async function discussionFingerprint(discussion) {
  if (!discussion.messages.length) return null;
  const messages = discussion.messages.map(({ message_id, kind, text }) => [message_id, kind, text]);
  const contents = JSON.stringify([discussion.title, messages]);
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(contents));
  return [...new Uint8Array(hash)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function lookupIssueCache(thread, analysisFingerprint, fullFingerprint) {
  return request("cache-lookup", {
    thread,
    payload: { analysis_fingerprint: analysisFingerprint, full_fingerprint: fullFingerprint },
  });
}

export async function runIssueJob(kind, payload, { thread, signal, onStage }) {
  const identifier = kind + "_id";
  const created = await request("start-" + kind, { payload, thread });
  const jobId = created[identifier];
  if (!/^[0-9a-f]{32}$/.test(jobId)) throw new IssueError("backend_error");
  // The background worker only handles short requests. The page polls the local job.
  while (!signal.aborted) {
    const job = await request(kind + "-status", { jobId, thread });
    if (job.status === "completed") return { id: jobId, data: job.result, cached: Boolean(created.cached) };
    if (job.status === "failed") throw new IssueError(job.code ?? "backend_error");
    onStage(job.stage ?? "queued");
    await new Promise((resolve) => window.setTimeout(resolve, 950));
  }
  return null;
}
