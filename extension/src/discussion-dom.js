const MARKDOWN = "[data-testid='markdown-body'], .markdown-body, .js-comment-body";
const BLOCK = new Set(["ADDRESS", "ARTICLE", "ASIDE", "BLOCKQUOTE", "DIV", "DL", "DT", "DD",
  "FIGURE", "FIGCAPTION", "H1", "H2", "H3", "H4", "H5", "H6", "LI", "OL", "P", "PRE",
  "SECTION", "SUMMARY", "TABLE", "TR", "UL"]);
const IGNORE = "script, style, svg, button, clipboard-copy, [hidden], [aria-hidden='true']";

function readText(element, includeCode) {
  const parts = [];
  function walk(node) {
    if (node.nodeType === 3) {
      parts.push(node.nodeValue);
      return;
    }
    if (node.nodeType !== 1 || node.matches(IGNORE)) return;
    if (node.tagName === "BR") {
      parts.push("\n");
      return;
    }
    if (node.tagName === "PRE" && !includeCode) {
      parts.push("\n\n");
      return;
    }
    const separate = BLOCK.has(node.tagName);
    if (separate) parts.push("\n\n");
    node.childNodes.forEach(walk);
    if (node.tagName === "TD" || node.tagName === "TH") parts.push("\t");
    if (separate) parts.push("\n\n");
  }
  walk(element);
  return parts.join("").replaceAll("\u00a0", " ").replace(/\r\n?/g, "\n")
    .replace(/\n[ \t]+(?=\n)/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

function authorOf(header) {
  return [...(header?.querySelectorAll("a[href^='/']") ?? [])]
    .find((link) => /^\/[^/?#]+$/.test(link.getAttribute("href")) && link.textContent.trim())
    ?.textContent.trim();
}

export function captureDiscussion(root, { includeCode }) {
  const messages = [];
  const references = new Map();
  const title = (root.querySelector("main h1, .js-issue-title")?.textContent ?? "")
    .replace(/\s*#[0-9]+\s*$/, "").trim();

  function append(kind, content, marker, author) {
    if (!content) return;
    const text = readText(content, includeCode);
    if (!text) return;
    const message_id = "M" + (messages.length + 1);
    messages.push({ message_id, kind, text });
    references.set(message_id, { element: marker ?? content, anchor: marker?.id, author });
  }

  const issueBody = root.querySelector("#issue-body-viewer, [data-testid='issue-body-viewer']");
  const bodyLink = root.querySelector("[data-testid='issue-body-header-link']");
  const bodyAnchor = bodyLink?.hash?.slice(1);
  const bodyMarker = (bodyAnchor && root.getElementById(bodyAnchor)) || issueBody;
  const legacyBody = root.querySelector("#discussion_bucket [id^='issue-'] .js-comment-body");
  append(
    "body",
    issueBody?.querySelector(MARKDOWN) ?? issueBody ?? legacyBody,
    bodyMarker || legacyBody,
    root.querySelector("[data-testid='issue-body-header-author']")?.textContent.trim(),
  );

  const modern = root.querySelectorAll("#issue-timeline [data-testid^='comment-viewer-outer-box']");
  if (modern.length) {
    modern.forEach((comment) => {
      const marker = comment.querySelector("[data-testid='comment-header']");
      append("comment", comment.querySelector(MARKDOWN), marker, authorOf(marker));
    });
  } else {
    root.querySelectorAll("#discussion_bucket [id^='issuecomment-']").forEach((comment) => {
      append("comment", comment.querySelector(MARKDOWN), comment, authorOf(comment));
    });
  }

  return { discussion: { title, messages }, references };
}
