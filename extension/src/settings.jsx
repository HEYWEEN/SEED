import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { STATUS_LABEL, sendSettings, settingsStatus } from "./settings-client.js";

const ERRORS = {
  unavailable: "请启动本机 SEED 后端，再重试。",
  invalid_key: "请输入有效的单行 API Key。",
  backend_error: "设置保存服务返回错误，请检查本机后端。",
  forbidden: "请在 SEED 的扩展设置页重试。",
};

function SettingsPage() {
  const [status, setStatus] = useState("checking");
  const [operation, setOperation] = useState(null);
  const [notice, setNotice] = useState(null);
  const input = useRef(null);
  const working = status === "checking" || operation !== null;

  useEffect(() => {
    let active = true;
    sendSettings("status").then((result) => {
      if (active) setStatus(settingsStatus(result));
    });
    return () => { active = false; };
  }, []);

  async function retryConnection() {
    setStatus("checking");
    setNotice(null);
    setStatus(settingsStatus(await sendSettings("status")));
  }

  async function updateKey(action, apiKey) {
    setOperation(action);
    setNotice(null);
    const result = await sendSettings(action, apiKey);
    setOperation(null);

    if (!result.ok) {
      if (result.code === "unavailable") setStatus("offline");
      setNotice({ error: true, text: ERRORS[result.code] ?? ERRORS.backend_error });
      return;
    }

    if (input.current) input.current.value = "";
    setStatus(settingsStatus(result));
    setNotice({ error: false, text: action === "save" ? "已保存 API Key。" : "已清除 API Key。" });
  }

  function saveKey(event) {
    event.preventDefault();
    const value = input.current.value.trim();
    if (!value) {
      setNotice({ error: true, text: "请输入 API Key。" });
      input.current.focus();
      return;
    }
    void updateKey("save", value);
  }

  return (
    <main className="seed-settings-page">
      <header className="seed-options-brand flex items-center gap-3">
        <span className="seed-mark seed-brand-mark"><img src="seed-button.png" alt="" /></span>
        <span>SEED</span>
      </header>
      <section className="seed-key-card" aria-labelledby="key-heading">
        <div className="seed-key-heading flex flex-wrap items-start justify-between gap-2">
          <h1 id="key-heading">DeepSeek API Key</h1>
          <span className="seed-key-state" data-state={status} role="status">{STATUS_LABEL[status]}</span>
        </div>
        <p className="seed-key-help">Key 保存在本机 SEED 后端，用于后续生成讨论摘要。</p>
        <form onSubmit={saveKey} aria-busy={operation !== null}>
          <label htmlFor="deepseek-key">输入 API Key</label>
          <input
            id="deepseek-key"
            name="api-key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            maxLength={512}
            ref={input}
            disabled={working}
            required
          />
          <div className="seed-key-actions flex flex-wrap items-center gap-2">
            <button type="submit" className="seed-primary" disabled={working}>
              {operation === "save" ? "正在保存…" : "保存 Key"}
            </button>
            {status === "configured" && (
              <button type="button" className="seed-secondary" disabled={working} onClick={() => void updateKey("clear")}>
                {operation === "clear" ? "正在清除…" : "清除已保存的 Key"}
              </button>
            )}
            {(status === "offline" || status === "error") && (
              <button type="button" className="seed-secondary" disabled={working} onClick={retryConnection}>重试连接</button>
            )}
          </div>
        </form>
        {notice && <p className={notice.error ? "seed-error" : "seed-notice"} role={notice.error ? "alert" : "status"}>{notice.text}</p>}
        {!notice && status === "offline" && <p className="seed-error" role="status">{ERRORS.unavailable}</p>}
        {!notice && status === "error" && <p className="seed-error" role="status">{ERRORS.backend_error}</p>}
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<SettingsPage />);
