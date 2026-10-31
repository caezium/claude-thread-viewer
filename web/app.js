"use strict";

const $ = (id) => document.getElementById(id);
const hash = new URLSearchParams(location.hash.slice(1));
const launchKey = hash.get("key");
if (launchKey) {
  sessionStorage.setItem("viewer-key", launchKey);
  history.replaceState(null, "", location.pathname);
}
const key = sessionStorage.getItem("viewer-key") || "";
let threads = [],
  selected = null,
  current = null,
  requestNumber = 0,
  showCount = 60;
let refreshing = false;

function element(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function dateText(value, full = false) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return full
    ? date.toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : date.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        ...(date.getFullYear() !== new Date().getFullYear()
          ? { year: "numeric" }
          : {}),
      });
}

function projectName(path) {
  return path
    ? path.split("/").filter(Boolean).at(-1) || path
    : "No project folder";
}

async function api(path) {
  const response = await fetch(path, {
    headers: { "X-Viewer-Key": key },
    cache: "no-store",
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({
        error: "The local viewer is not responding. Reopen its launcher.",
      }));
    const failure = new Error(
      error.error || "Could not read the local thread.",
    );
    failure.status = response.status;
    throw failure;
  }
  return response;
}

function notice(node, text) {
  node.textContent = text;
  node.hidden = !text;
}

function actionStatus(text, error = false) {
  $("action-status").textContent = text;
  $("action-status").classList.toggle("error", error);
}

function setActions(disabled) {
  for (const id of ["handoff", "markdown", "json"]) $(id).disabled = disabled;
}

function invalidate(text) {
  current = null;
  requestNumber += 1;
  setActions(true);
  $("messages").replaceChildren();
  $("message-count").textContent = "";
  $("thread-title").textContent = "Thread unavailable";
  $("thread-project").textContent = "";
  $("thread-meta").textContent = "";
  $("thread-status").textContent = "Unavailable";
  $("copy-fallback").hidden = true;
  $("handoff-text").value = "";
  notice($("thread-notice"), text);
  actionStatus("Choose another active thread to continue.");
}

async function refresh(manual = false) {
  if (refreshing) return;
  refreshing = true;
  $("refresh").disabled = true;
  try {
    const data = await (await api("/api/threads")).json();
    threads = data.threads;
    const previousProject = $("project").value;
    $("project").replaceChildren(new Option("All projects", ""));
    const folders = [
      ...new Set(threads.map((t) => t.cwd).filter(Boolean)),
    ].sort();
    for (const folder of folders) {
      const option = new Option(projectName(folder), folder);
      option.title = folder;
      $("project").append(option);
    }
    $("project").value = folders.includes(previousProject)
      ? previousProject
      : "";
    $("updated").textContent =
      `Checked ${new Date(data.checked_at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
    notice($("list-notice"), data.warnings.join("\n"));
    renderList();
    if (selected && !threads.some((t) => t.id === selected)) {
      invalidate(
        "This thread is no longer active. It has been removed from the viewer and cannot be exported.",
      );
    } else if (manual && selected) {
      await openThread(selected);
    }
  } catch (error) {
    notice($("list-notice"), error.message);
    $("updated").textContent = "Unable to check local files";
    if (!threads.length)
      $("thread-list").replaceChildren(
        element(
          "p",
          "list-empty",
          "Reopen the launcher, then refresh this page.",
        ),
      );
  } finally {
    refreshing = false;
    $("refresh").disabled = false;
  }
}

function renderList() {
  const query = $("search").value.trim().toLocaleLowerCase();
  const folder = $("project").value;
  let visible = threads.filter(
    (t) =>
      (!folder || t.cwd === folder) &&
      (!query || `${t.title}\n${t.cwd}`.toLocaleLowerCase().includes(query)),
  );
  const sort = $("sort").value;
  if (sort === "oldest") visible.reverse();
  if (sort === "title") visible.sort((a, b) => a.title.localeCompare(b.title));
  $("thread-count").textContent = `${visible.length}`;
  const fragment = document.createDocumentFragment();
  for (const thread of visible) {
    const item = element("button", "thread-item");
    item.type = "button";
    item.setAttribute("aria-current", String(thread.id === selected));
    item.dataset.id = thread.id;
    item.append(element("span", "thread-item-title", thread.title));
    const meta = element("span", "thread-item-meta");
    const project = element(
      "span",
      "thread-item-project",
      projectName(thread.cwd),
    );
    project.title = thread.cwd;
    const time = element("time", "", dateText(thread.updated));
    time.dateTime = thread.updated;
    time.title = dateText(thread.updated, true);
    meta.append(project, time);
    item.append(meta);
    item.addEventListener("click", () => openThread(thread.id));
    fragment.append(item);
  }
  if (!visible.length)
    fragment.append(
      element(
        "p",
        "list-empty",
        threads.length
          ? "No matches. Try a different title or project folder."
          : "No active desktop threads were found. Archived and deleted threads are excluded.",
      ),
    );
  $("thread-list").replaceChildren(fragment);
}

async function openThread(id) {
  selected = id;
  current = null;
  showCount = 60;
  const sequence = ++requestNumber;
  $("welcome").hidden = true;
  $("reader").hidden = false;
  $("copy-fallback").hidden = true;
  document.body.classList.add("reading");
  const row = threads.find((t) => t.id === id);
  $("thread-title").textContent = row?.title || "Loading thread…";
  $("thread-project").textContent = row?.cwd || "Local thread";
  $("thread-meta").textContent = "Reading the saved conversation…";
  $("thread-status").textContent = "Checking";
  $("messages").replaceChildren(
    element("p", "loading-line", "Loading conversation…"),
  );
  $("message-count").textContent = "";
  notice($("thread-notice"), "");
  setActions(true);
  actionStatus("Checking that this thread is still active…");
  renderList();
  $("conversation").scrollTop = 0;
  try {
    const thread = await (await api(`/api/thread/${id}`)).json();
    if (sequence !== requestNumber) return;
    current = thread;
    $("thread-status").textContent = "Active";
    $("thread-title").textContent = thread.title;
    $("thread-project").textContent =
      thread.cwd || "No project folder recorded";
    $("thread-meta").textContent = [
      `Updated ${dateText(thread.updated, true)}`,
      thread.model,
      `${thread.turns} completed turns`,
    ]
      .filter(Boolean)
      .join(" · ");
    notice($("thread-notice"), thread.warnings.join("\n"));
    renderMessages();
    setActions(!thread.messages.length);
    actionStatus(
      thread.messages.length
        ? "Copy a handoff into Codex or T3 to continue this thread."
        : "There is no saved conversation to export for this entry.",
    );
  } catch (error) {
    if (sequence !== requestNumber) return;
    invalidate(error.message);
  }
}

function inline(parent, text) {
  const pattern =
    /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g;
  let start = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(start, match.index)));
    const token = match[0];
    if (token.startsWith("`"))
      parent.append(element("code", "", token.slice(1, -1)));
    else if (token.startsWith("**"))
      parent.append(element("strong", "", token.slice(2, -2)));
    else {
      const parts = token.match(/^\[(.+)\]\((.+)\)$/);
      const link = element("a", "", parts[1]);
      link.href = parts[2];
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      parent.append(link);
    }
    start = match.index + token.length;
  }
  parent.append(document.createTextNode(text.slice(start)));
}

function markdown(text) {
  const container = element("div");
  const lines = text.split("\n");
  for (let i = 0; i < lines.length;) {
    if (!lines[i].trim()) {
      i += 1;
      continue;
    }
    const code = lines[i].match(/^\s*(`{3,}|~{3,})/);
    if (code) {
      const body = [];
      i += 1;
      while (i < lines.length && !lines[i].trimStart().startsWith(code[1]))
        body.push(lines[i++]);
      i += 1;
      const pre = element("pre");
      pre.append(element("code", "", body.join("\n")));
      container.append(pre);
      continue;
    }
    const heading = lines[i].match(/^#{1,6}\s+(.+)/);
    if (heading) {
      const h = element("h3");
      inline(h, heading[1]);
      container.append(h);
      i += 1;
      continue;
    }
    if (/^\s*([-*+] |\d+\. )/.test(lines[i])) {
      const list = element(/^\s*\d+\./.test(lines[i]) ? "ol" : "ul");
      while (i < lines.length && /^\s*([-*+] |\d+\. )/.test(lines[i])) {
        const item = element("li");
        inline(item, lines[i++].replace(/^\s*([-*+] |\d+\. )/, ""));
        list.append(item);
      }
      container.append(list);
      continue;
    }
    const paragraph = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      (paragraph.length === 0 ||
        !/^\s*(`{3,}|~{3,}|#{1,6}\s|[-*+] |\d+\. )/.test(lines[i]))
    )
      paragraph.push(lines[i++]);
    const p = element("p");
    inline(p, paragraph.join("\n"));
    container.append(p);
  }
  return container;
}

function renderBlock(block, showTools) {
  if (block.type === "text") return markdown(block.text);
  if (block.type === "attachment")
    return element("p", "attachment-note", block.text);
  if (block.type === "image") {
    const details = element("details", "image-block");
    details.append(element("summary", "", "Attached image"));
    details.addEventListener("toggle", () => {
      if (details.open && !details.querySelector("img")) {
        const image = document.createElement("img");
        image.alt = "Image attached to the original conversation";
        image.src = `data:${block.media_type};base64,${block.data}`;
        image.loading = "lazy";
        details.append(image);
      }
    });
    return details;
  }
  if (!showTools) return null;
  const details = element("details", "tool-block");
  const label =
    block.type === "tool_use"
      ? `Tool · ${block.name}`
      : `Tool result${block.error ? " · error" : ""}`;
  details.append(element("summary", block.error ? "error-text" : "", label));
  const content = element("div");
  if (block.type === "tool_use")
    content.append(element("pre", "", JSON.stringify(block.input, null, 2)));
  else
    for (const child of block.content || []) {
      if (child.type === "text") content.append(element("pre", "", child.text));
      else {
        const node = renderBlock(child, true);
        if (node) content.append(node);
      }
    }
  details.append(content);
  return details;
}

function renderMessages() {
  if (!current) return;
  const showTools = $("show-tools").checked;
  const visible = current.messages.filter(
    (m) =>
      showTools ||
      (m.role !== "context" &&
        m.blocks.some((b) => !["tool_use", "tool_result"].includes(b.type))),
  );
  const start = Math.max(0, visible.length - showCount);
  const fragment = document.createDocumentFragment();
  if (start > 0) {
    const more = element(
      "button",
      "load-earlier",
      `Load ${Math.min(start, 100)} earlier messages`,
    );
    more.type = "button";
    more.addEventListener("click", () => {
      const pane = $("conversation"),
        before = pane.scrollHeight,
        top = pane.scrollTop;
      showCount += 100;
      renderMessages();
      pane.scrollTop = top + pane.scrollHeight - before;
    });
    fragment.append(more);
  }
  for (const message of visible.slice(start)) {
    const article = element("article", `message ${message.role}`);
    const header = element("div", "message-label");
    const role = {
      user: "You",
      assistant: "Claude",
      tool: "Tool output",
      context: "Context supplied by Claude",
    }[message.role];
    const mark = element("span", "role-mark", role[0]);
    mark.setAttribute("aria-hidden", "true");
    header.append(
      mark,
      element("span", "", role + (message.summary ? " · Context summary" : "")),
    );
    const time = element("time", "", dateText(message.timestamp, true));
    time.dateTime = message.timestamp;
    header.append(time);
    const body = element("div", "message-body");
    for (const block of message.blocks) {
      const node = renderBlock(block, showTools);
      if (node) body.append(node);
    }
    article.append(header, body);
    fragment.append(article);
  }
  if (!visible.length)
    fragment.append(
      element(
        "p",
        "loading-line",
        "No visible messages in this local transcript.",
      ),
    );
  $("messages").replaceChildren(fragment);
  $("message-count").textContent =
    `${start ? `Latest ${visible.length - start} of ` : ""}${visible.length} messages${showTools ? " · including tools" : " · tools hidden"}`;
}

async function exportThread(format) {
  if (!current) return;
  const id = current.id;
  const title = current.title;
  const sequence = requestNumber;
  const stillSelected = () =>
    selected === id && current?.id === id && sequence === requestNumber;
  setActions(true);
  actionStatus("Rechecking active status and preparing your export…");
  try {
    const response = await api(`/api/thread/${id}/export?format=${format}`);
    if (!stillSelected()) return;
    if (format === "handoff") {
      const text = await response.text();
      if (!stillSelected()) return;
      try {
        await navigator.clipboard.writeText(text);
        if (!stillSelected()) return;
        actionStatus(
          "Handoff copied. Paste it into Codex or a T3 thread on this Mac.",
        );
      } catch {
        if (!stillSelected()) return;
        $("handoff-text").value = text;
        $("copy-fallback").hidden = false;
        $("copy-fallback").open = true;
        $("handoff-text").focus();
        $("handoff-text").select();
        actionStatus(
          "Select and copy the handoff below. Your browser did not allow clipboard access.",
        );
      }
    } else {
      const blob = await response.blob();
      if (!stillSelected()) return;
      const url = URL.createObjectURL(blob);
      const link = element("a");
      link.href = url;
      link.download = `${title.replace(/[^\p{L}\p{N}._-]+/gu, "-").slice(0, 80) || "claude-thread"}.${format === "json" ? "json" : "md"}`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      actionStatus(
        `${format === "json" ? "JSON" : "Markdown"} downloaded. Attach it to a conversation in Codex or T3.`,
      );
    }
  } catch (error) {
    if (!stillSelected()) return;
    if (error.status === 410) {
      invalidate(error.message);
      await refresh();
    } else actionStatus(error.message, true);
  } finally {
    if (stillSelected()) setActions(!current.messages.length);
  }
}

for (const id of ["search", "project", "sort"])
  $(id).addEventListener(id === "search" ? "input" : "change", renderList);
$("refresh").addEventListener("click", () => refresh(true));
$("show-tools").addEventListener("change", renderMessages);
$("handoff").addEventListener("click", () => exportThread("handoff"));
$("markdown").addEventListener("click", () => exportThread("markdown"));
$("json").addEventListener("click", () => exportThread("json"));
$("latest").addEventListener("click", () => {
  $("conversation").scrollTop = $("conversation").scrollHeight;
});
$("back").addEventListener("click", () =>
  document.body.classList.remove("reading"),
);
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "k") {
    event.preventDefault();
    document.body.classList.remove("reading");
    $("search").focus();
  }
});
window.addEventListener("focus", () => refresh());
setInterval(() => {
  if (!document.hidden) refresh();
}, 30000);
refresh();
