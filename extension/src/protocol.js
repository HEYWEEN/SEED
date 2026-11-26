export const SETTINGS_MESSAGE = "seed/deepseek-settings";
export const ISSUE_MESSAGE = "seed/issue-job";

export function githubIssue(url) {
  if (typeof url !== "string") return null;
  const match = /^https:\/\/github\.com\/([^/?#]+)\/([^/?#]+)\/issues\/([1-9]\d*)\/?(?:[?#].*)?$/.exec(url);
  if (!match) return null;

  const [, owner, name, number] = match;
  const repository = owner + "/" + name;
  return { id: repository + "#" + number, repository, number };
}
