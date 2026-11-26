import { useEffect, useRef, useState } from "react";
import seedArtwork from "../assets/seed-button.png";
import { STATUS_LABEL, sendSettings, settingsStatus } from "./settings-client.js";
import { captureDiscussion } from "./discussion-dom.js";
import { discussionFingerprint, IssueError, lookupIssueCache, runIssueJob } from "./issue-client.js";

const STAGES = {
  queued: "等待本机服务…",
  collect: "正在读取网页…",
  split: "SESAME 正在分句…",
  classify: "SESAME 正在识别极性…",
  shap: "SESAME 正在计算 SHAP…",
  opinion: "SESAME 正在提取 Opinion…",
  aspect: "SESAME 正在提取 Aspect…",
  summarize: "DeepSeek 正在生成摘要…",
};

const ERRORS = {
  unavailable: "连接不上本机 SEED 后端。启动后再试。",
  backend_old: "请重启 SEED 后端，让新接口生效。",
  backend_error: "本机后端返回错误，请检查服务后重试。",
  empty_page: "当前页面还没有可读取的正文或评论。请等待页面加载后再试。",
  invalid_discussion: "讨论内容超出限制或格式有误。请检查当前页面后重试。",
  too_many_sentences: "本次提取上限为 160 句。可以选择用当前页面的全文生成摘要。",
  sesame_runtime_missing: "找不到 SESAME 的本机环境。请检查 README 中的 SESAME 路径。",
  sesame_model_missing: "找不到 SESAME 权重或 RoBERTa 缓存。请检查 README 中的模型路径。",
  corenlp_unavailable: "SESAME 无法连接本机 CoreNLP。请启动 9000 端口的 CoreNLP 后重试。",
  sesame_timeout: "SESAME 运行超时。可以重试，或选择直接全文生成摘要。",
  sesame_failed: "本机 SESAME 未能完成提取。请检查模型和 CoreNLP 后重试。",
  key_missing: "请先从「设置 Key」保存 DeepSeek API Key。",
  analysis_required: "请先完成提取评价，再基于评价生成摘要。",
  analysis_changed: "讨论内容已更新，请先重新提取评价。",
  analysis_empty: "本次提取没有可用的评价。可以选择直接全文生成摘要。",
  job_missing: "本次任务已过期。请重新运行。",
  busy: "本机任务较多，请等一个任务完成后再试。",
  deepseek_key_rejected: "DeepSeek 拒绝了当前 Key。请在设置中更新。",
  deepseek_balance: "DeepSeek 账户余额不足，请充值后重试。",
  deepseek_busy: "DeepSeek 请求较多，请稍后重试。",
  deepseek_response: "DeepSeek 返回的摘要格式或证据编号有误。可以重试。",
  deepseek_failed: "DeepSeek 请求失败。请检查网络后重试。",
  issue_changed: "当前 Issue 已切换，请在新页面重试。",
  forbidden: "请在 GitHub Issue 页面重试。",
};

export function FloatingSeed({ issue }) {
  const [mode, setMode] = useState("closed");
  const trigger = useRef(null);
  const open = mode === "open";

  function hide(nextMode) {
    setMode(nextMode);
    trigger.current?.focus();
  }

  return (
    <div className="seed-position">
      {mode !== "closed" && (
        <IssuePanel
          issue={issue}
          open={open}
          minimize={() => hide("minimized")}
          close={() => hide("closed")}
        />
      )}
      <button
        type="button"
        ref={trigger}
        className="seed-mark seed-trigger"
        aria-label={open ? "收起 SEED" : "打开 SEED"}
        title={open ? "收起 SEED" : "打开 SEED"}
        aria-expanded={open}
        aria-controls={mode === "closed" ? undefined : "seed-panel"}
        onClick={() => open ? hide("minimized") : setMode("open")}
      >
        <img src={seedArtwork} alt="" />
      </button>
    </div>
  );
}

function IssuePanel({ issue, open, minimize, close }) {
  const [status, setStatus] = useState("checking");
  const [opening, setOpening] = useState(false);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState("");
  const [analysis, setAnalysis] = useState(null);
  const [summary, setSummary] = useState(null);
  const firstButton = useRef(null);
  const activeJob = useRef(null);
  const actionVersion = useRef(0);

  useEffect(() => () => activeJob.current?.abort(), []);

  useEffect(() => {
    let alive = true;
    const version = actionVersion.current;

    async function restore() {
      const analysisSnapshot = captureDiscussion(document, { includeCode: false });
      const fullSnapshot = captureDiscussion(document, { includeCode: true });
      const [analysisFingerprint, fullFingerprint] = await Promise.all([
        discussionFingerprint(analysisSnapshot.discussion),
        discussionFingerprint(fullSnapshot.discussion),
      ]);
      if (!analysisFingerprint && !fullFingerprint) return;
      const found = await lookupIssueCache(issue.id, analysisFingerprint, fullFingerprint);
      if (!alive || actionVersion.current !== version) return;
      if (found.analysis) {
        setAnalysis({
          ...found.analysis,
          fingerprint: analysisFingerprint,
          references: analysisSnapshot.references,
        });
      }
      if (found.summary) {
        const basedOnEvaluations = found.summary.data.mode === "evaluations";
        setSummary({
          ...found.summary,
          references: basedOnEvaluations ? analysisSnapshot.references : fullSnapshot.references,
          citations: basedOnEvaluations
            ? evaluationCitations(found.analysis.data.evaluations)
            : new Map(fullSnapshot.discussion.messages.map(({ message_id }) => [message_id, message_id])),
        });
      }
    }

    // The lookup is optional and sends only fingerprints to the local backend.
    void restore().catch(() => {});
    return () => { alive = false; };
  }, [issue.id]);
  useEffect(() => {
    if (!open) return;
    let revision = 0;

    function refresh() {
      const request = ++revision;
      sendSettings("status").then((result) => {
        if (request === revision) setStatus(settingsStatus(result));
      });
    }

    firstButton.current?.focus();
    refresh();
    window.addEventListener("focus", refresh);
    return () => {
      revision += 1;
      window.removeEventListener("focus", refresh);
    };
  }, [open]);

  async function openSettings() {
    setOpening(true);
    setError("");
    const result = await sendSettings("open-options");
    setOpening(false);
    if (!result.ok) setError("请从 Chrome 的 SEED 扩展详情打开设置。");
  }

  async function run(kind, mode) {
    if (activeJob.current) return;
    actionVersion.current += 1;
    const controller = new AbortController();
    activeJob.current = controller;
    const signal = controller.signal;
    setError("");
    setProgress({ kind, stage: mode === "evaluations" ? "queued" : "collect" });
    if (kind === "analysis") {
      setAnalysis(null);
      if (summary?.data.mode === "evaluations") setSummary(null);
    } else {
      setSummary(null);
    }

    try {
      let payload;
      let references;
      let citations;
      let fingerprint;
      if (mode === "evaluations") {
        if (!analysis?.data.evaluations.length) throw new IssueError("analysis_required");
        const snapshot = captureDiscussion(document, { includeCode: false });
        if (await discussionFingerprint(snapshot.discussion) !== analysis.fingerprint) {
          setAnalysis(null);
          throw new IssueError("analysis_changed");
        }
        payload = { mode, analysis_id: analysis.id };
        references = snapshot.references;
        citations = evaluationCitations(analysis.data.evaluations);
      } else {
        const snapshot = captureDiscussion(document, { includeCode: mode === "full" });
        references = snapshot.references;
        if (!snapshot.discussion.messages.length) throw new IssueError("empty_page");
        payload = kind === "summary" ? { ...snapshot.discussion, mode } : snapshot.discussion;
        if (kind === "analysis") fingerprint = await discussionFingerprint(snapshot.discussion);
        citations = new Map(snapshot.discussion.messages.map((item) => [item.message_id, item.message_id]));
      }
      if (signal.aborted) return;
      const result = await runIssueJob(kind, payload, {
        thread: issue.id,
        signal,
        onStage(stage) {
          if (!signal.aborted) setProgress({ kind, stage });
        },
      });
      if (!result || signal.aborted) return;
      const outcome = { ...result, references, citations, fingerprint };
      if (kind === "analysis") setAnalysis(outcome);
      else setSummary(outcome);
    } catch (failure) {
      if (!signal.aborted) setError(ERRORS[failure.code] ?? ERRORS.backend_error);
    } finally {
      if (activeJob.current === controller) activeJob.current = null;
      if (!signal.aborted) setProgress(null);
    }
  }

  const ready = Boolean(analysis?.data.evaluations.length);
  const [phase, step] = (progress?.stage ?? "").split(":");

  return (
    <section
      id="seed-panel"
      className="seed-panel"
      role="dialog"
      aria-label={"Issue #" + issue.number + " 的分析操作"}
      hidden={!open}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          minimize();
        }
      }}
    >
      <div className="seed-menu">
        <div className="seed-first-row">
          <button type="button" ref={firstButton} className="seed-settings-row"
            disabled={Boolean(progress)} onClick={() => void run("analysis")}>
            <span>提取评价</span>
            {analysis && <span className="seed-row-state">{analysis.data.evaluations.length} 条</span>}
          </button>
          <button type="button" className="seed-icon-control" onClick={close} aria-label="关闭" title="关闭">×</button>
        </div>
        <div className="seed-menu-section">
          <button type="button" className="seed-settings-row" aria-expanded={summaryOpen} aria-controls="seed-summary-choices"
            onClick={() => setSummaryOpen(!summaryOpen)}>
            <span>生成摘要</span>
            <span className="seed-row-state" aria-hidden="true">{summaryOpen ? "⌄" : "›"}</span>
          </button>
          <div id="seed-summary-choices" className="seed-summary-choices" hidden={!summaryOpen}>
            <button type="button" className="seed-mode" disabled={!ready || Boolean(progress)}
              onClick={() => void run("summary", "evaluations")}>
              <span>基于评价生成</span>
              <small>{ready ? "将提取的评价和原句发送给 DeepSeek" : "先提取评价，获得可用的评价结果"}</small>
            </button>
            <button type="button" className="seed-mode" disabled={Boolean(progress)}
              onClick={() => void run("summary", "full")}>
              <span>直接全文生成</span>
              <small>将当前页面已加载的标题、正文和评论发送给 DeepSeek</small>
            </button>
          </div>
        </div>
        <button type="button" className="seed-settings-row" disabled={opening} onClick={openSettings}>
          <span>设置 Key</span>
          <span className="seed-row-state" aria-live="polite">{STATUS_LABEL[status]} <span aria-hidden="true">›</span></span>
        </button>
      </div>
      {progress && <p className="seed-progress" role="status">{STAGES[phase] ?? STAGES.queued}{step && "（" + step + " 句）"}</p>}
      {error && <p className="seed-panel-error seed-error" role="alert">{error}</p>}
      {summary && <SummaryResult outcome={summary} />}
      {analysis && <EvaluationResults outcome={analysis} />}
    </section>
  );
}

function evaluationCitations(evaluations) {
  return new Map(evaluations.map(({ source_id, message_id }) => [source_id, message_id]));
}

function SourceButton({ references, message, children }) {
  const source = references.get(message);
  if (!source) return <span className="seed-evidence-badge">{children}</span>;
  return (
    <button type="button" className="seed-evidence-badge" title={source.author ?? "查看原消息"}
      onClick={() => {
        const target = source.element.isConnected ? source.element : document.getElementById(source.anchor);
        if (target) target.scrollIntoView({ block: "center" });
        else if (source.anchor) location.hash = source.anchor;
      }}>
      {children}
    </button>
  );
}

function EvaluationResults({ outcome: { data, references, cached } }) {
  const positive = data.evaluations.filter(({ sentiment }) => sentiment === "positive").length;
  const negative = data.evaluations.length - positive;
  return (
    <section className="seed-results" aria-label="评价结果">
      <h2>评价结果</h2>
      <p className="seed-scope">已分析 {data.message_count} 条消息、{data.sentence_count} 句{cached && " · 本机缓存"}</p>
      {data.evaluations.length === 0 ? <p className="seed-scope">本次未提取到评价。</p> : (
        <>
          <div className="seed-polarity-legend">
            <span data-tone="positive">正面 <strong>{positive}</strong></span>
            <span data-tone="negative">负面 <strong>{negative}</strong></span>
          </div>
          <div className="seed-polarity-bar" role="img" aria-label={"正面 " + positive + " 条，负面 " + negative + " 条"}>
            <span style={{ flexGrow: positive }} />
            <span style={{ flexGrow: negative }} />
          </div>
          {data.evaluations.map((item) => (
            <article key={item.evaluation_id} className="seed-evaluation">
              <div className="seed-evaluation-top">
                <span className="seed-sentiment" data-tone={item.sentiment}>{item.sentiment === "positive" ? "正面" : "负面"}</span>
                <SourceButton references={references} message={item.message_id}>{item.source_id}</SourceButton>
              </div>
              <dl>
                <dt>Aspect</dt><dd>{item.aspect?.text ?? "评价对象待确认"}</dd>
                <dt>Opinion</dt><dd>{item.opinion.text}</dd>
              </dl>
              <blockquote>{item.sentence}</blockquote>
            </article>
          ))}
        </>
      )}
    </section>
  );
}

function SummaryResult({ outcome }) {
  const { data, references, citations, cached } = outcome;
  return (
    <section className="seed-results seed-summary" aria-label="讨论摘要">
      <h2>讨论摘要</h2>
      <p className="seed-scope">{data.mode === "full" ? "基于当前页面全文" : "基于已提取的评价"}{cached && " · 本机缓存"}</p>
      {data.sentences.map(({ text, source_ids }, index) => (
        <p key={index} className="seed-summary-line">
          {text}{" "}
          {source_ids.map((source) => (
            <SourceButton key={source} references={references} message={citations.get(source)}>{source}</SourceButton>
          ))}
        </p>
      ))}
    </section>
  );
}
