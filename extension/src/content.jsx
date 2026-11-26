import { createRoot } from "react-dom/client";
import { githubIssue } from "./protocol.js";
import { FloatingSeed } from "./floating-seed.jsx";
import stylesheet from "../dist/ui.css";

let mounted;

function syncIssue() {
  const issue = githubIssue(location.href);
  if (!mounted && !issue) return;
  if (mounted?.id === issue?.id && mounted?.host.isConnected) return;

  if (mounted) {
    mounted.app.unmount();
    mounted.host.remove();
    mounted = undefined;
  }
  if (!issue || !document.body) return;

  const host = document.createElement("div");
  host.dataset.seedRoot = "";
  const shadow = host.attachShadow({ mode: "closed" });
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(stylesheet);
  shadow.adoptedStyleSheets = [sheet];
  document.body.append(host);

  const app = createRoot(shadow);
  app.render(<FloatingSeed issue={issue} />);
  mounted = { id: issue.id, app, host };
}

syncIssue();
// GitHub can change routes or swap the page body without reloading the document.
window.setInterval(syncIssue, 750);
