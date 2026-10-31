#!/usr/bin/env python3
"""Read-only browser and exporter for locally saved Claude desktop Code threads."""
from __future__ import annotations

import argparse
from collections import Counter, OrderedDict
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import shlex
import stat
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

APP_DIR = Path(__file__).resolve().parent
DEFAULT_SESSIONS = Path.home() / "Library/Application Support/Claude/claude-code-sessions"
DEFAULT_PROJECTS = Path.home() / ".claude/projects"


class Unavailable(Exception):
    """A selected desktop thread is no longer eligible for continuation."""


def iso_time(value):
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    return value if isinstance(value, str) else ""


def fence(text):
    longest = max((len(m.group()) for m in re.finditer(r"`+", text)), default=0)
    marker = "`" * max(3, longest + 1)
    return f"{marker}\n{text}\n{marker}"


def as_text(value, default=""):
    return value if isinstance(value, str) else default


def blocks_from(content):
    """Export visible content, not request logs, hidden reasoning, or app config."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content.strip() else []
    blocks = []
    for b in content if isinstance(content, list) else []:
        if not isinstance(b, dict):
            continue
        kind = b.get("type")
        if kind == "text" and as_text(b.get("text")).strip():
            blocks.append({"type": "text", "text": b["text"]})
        elif kind in ("tool_use", "server_tool_use"):
            blocks.append({"type": "tool_use", "name": as_text(b.get("name"), "Tool"), "id": as_text(b.get("id")), "input": b.get("input", {})})
        elif kind == "tool_result":
            blocks.append({"type": "tool_result", "tool_id": b.get("tool_use_id", ""), "error": bool(b.get("is_error")), "content": blocks_from(b.get("content", []))})
        elif kind == "image":
            source = b.get("source") if isinstance(b.get("source"), dict) else {}
            if source.get("type") == "base64" and source.get("media_type") in ("image/png", "image/jpeg", "image/webp", "image/gif"):
                blocks.append({"type": "image", "media_type": source["media_type"], "data": as_text(source.get("data"))})
            else:
                blocks.append({"type": "attachment", "text": "Image reference (not embedded locally)"})
        elif kind == "document":
            source = b.get("source") if isinstance(b.get("source"), dict) else {}
            label = as_text(b.get("title")) or "Document attachment"
            body = as_text(source.get("data")) if source.get("type") == "text" else ""
            blocks.append({"type": "attachment", "text": label + ("\n" + body if body else " (original attachment is not included in this export)")})
    return blocks


def parse_transcript(path):
    """Follow the latest primary message's parent chain, retaining compacted history."""
    nodes, order, messages, warnings = {}, [], {}, []
    malformed = 0
    with path.open("rb") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                malformed += 1
                continue
            if not isinstance(record, dict) or record.get("isSidechain"):
                continue
            uid = record.get("uuid")
            if not isinstance(uid, str):
                continue
            if any(record.get(field) is not None and not isinstance(record[field], str)
                   for field in ("parentUuid", "logicalParentUuid")):
                malformed += 1
                continue
            if uid not in nodes:
                order.append(uid)
            nodes[uid] = {
                "parent": record.get("parentUuid"),
                "logical_parent": record.get("logicalParentUuid"),
                "compact": record.get("subtype") == "compact_boundary",
            }
            message = record.get("message", {})
            if record.get("type") in ("user", "assistant") and isinstance(message, dict):
                blocks = blocks_from(message.get("content", []))
                if blocks:
                    only_tools = all(b["type"] == "tool_result" for b in blocks)
                    role = "tool" if only_tools else "context" if record.get("isMeta") and record["type"] == "user" else record["type"]
                    messages[uid] = {
                        "id": uid, "role": role,
                        "timestamp": as_text(record.get("timestamp")), "blocks": blocks,
                        "summary": bool(record.get("isCompactSummary")),
                    }
    if malformed:
        warnings.append(f"Skipped {malformed} incomplete or invalid transcript record(s). A running Claude session may still be writing.")
    if not messages:
        return [], warnings
    leaf = next(uid for uid in reversed(order) if uid in messages)
    positions = {uid: i for i, uid in enumerate(order)}
    selected, cursor, gap = set(), leaf, False
    while cursor and cursor not in selected:
        node = nodes.get(cursor)
        if not node:
            gap = True
            break
        selected.add(cursor)
        previous = node["parent"] or node["logical_parent"]
        if node["compact"] and not previous:
            # Older formats reset the parent at compaction. Join only the latest
            # prior message's lineage, rather than reviving abandoned branches.
            previous = next((uid for uid in reversed(order[:positions[cursor]]) if uid in messages), None)
        cursor = previous
    if gap:
        warnings.append("Part of this conversation's parent chain is unavailable locally; only the linked messages are shown.")
    return [messages[uid] for uid in order if uid in selected and uid in messages], warnings


class Store:
    def __init__(self, session_root=DEFAULT_SESSIONS, projects_root=DEFAULT_PROJECTS):
        self.session_root = Path(session_root).expanduser().resolve()
        self.projects_root = Path(projects_root).expanduser().resolve()
        self._json_cache, self._transcript_cache = {}, OrderedDict()
        self._records = {}
        self._lock = threading.RLock()

    def _json(self, path, fresh=False):
        try:
            if not path.resolve().is_relative_to(self.session_root):
                return None
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
                return None
            signature = (info.st_mtime_ns, info.st_size, info.st_ino)
            cached = self._json_cache.get(path)
            if not fresh and cached and cached[0] == signature:
                return cached[1]
            value = json.loads(path.read_text(encoding="utf-8"))
            self._json_cache[path] = signature, value
            return value
        except (OSError, ValueError, UnicodeError):
            return None

    def _key(self, path):
        return hashlib.sha256(str(path.relative_to(self.session_root)).encode()).hexdigest()[:24]

    def _eligibility(self, path, record, fresh=False):
        if not isinstance(record, dict) or record.get("isArchived") is not False:
            return "archived" if isinstance(record, dict) and record.get("isArchived") else "unreadable"
        sid = record.get("sessionId")
        if not isinstance(sid, str) or path.stem != sid:
            return "unreadable"
        ids = {sid, sid.removeprefix("local_")}
        for field in ("cliSessionId", "unarchivedCliSessionId"):
            if isinstance(record.get(field), str):
                ids.add(record[field])
        # Desktop deletion tombstones are scoped to the account, across orgs.
        account = path.parent.parent
        for org in account.iterdir():
            if org.is_dir() and any((org / ("deleted_" + value)).exists() for value in ids if re.fullmatch(r"[\w-]+", value)):
                return "deleted"
        index_path = path.parent / "archived-sessions.idx"
        if index_path.exists():
            index = self._json(index_path, fresh=fresh)
            if not isinstance(index, dict) or index.get("v") != 1 or not isinstance(index.get("archived"), list):
                return "unreadable"
            if not all(isinstance(value, str) for value in index["archived"]):
                return "unreadable"
            if sid in index["archived"]:
                return "archived"
        return "active"

    def _summary(self, path, record):
        return {
            "id": self._key(path), "desktop_session_id": record["sessionId"],
            "title": as_text(record.get("title")) or "Untitled thread",
            "cwd": as_text(record.get("cwd")) or as_text(record.get("originCwd")),
            "origin_cwd": as_text(record.get("originCwd")),
            "updated": iso_time(record.get("lastActivityAt")),
            "created": iso_time(record.get("createdAt")), "model": as_text(record.get("model")),
            "turns": record.get("completedTurns") if type(record.get("completedTurns")) is int else 0,
            "has_transcript_id": bool(record.get("cliSessionId")), "status": "active",
        }

    def listing(self):
        with self._lock:
            counts, rows, records = Counter(), [], {}
            warnings = []
            if not self.session_root.is_dir():
                warnings.append(f"Claude desktop's local Code session folder was not found: {self.session_root}")
            for path in self.session_root.glob("*/*/local_*.json"):
                record = self._json(path)
                try:
                    status = self._eligibility(path, record)
                except OSError:
                    status = "unreadable"
                counts[status] += 1
                if status == "active":
                    row = self._summary(path, record)
                    records[row["id"]] = path
                    rows.append(row)
            self._records = records
            rows.sort(key=lambda row: row["updated"], reverse=True)
            if counts["unreadable"]:
                warnings.append(f"{counts['unreadable']} record(s) were omitted because their active status could not be confirmed.")
            return {"threads": rows, "counts": dict(counts), "warnings": warnings, "checked_at": datetime.now(timezone.utc).isoformat()}

    def _current(self, key):
        path = self._records.get(key)
        if path is None:
            self.listing()
            path = self._records.get(key)
        if path is None:
            raise Unavailable("This thread is no longer in the active desktop list. Refresh to choose another thread.")
        record = self._json(path, fresh=True)
        try:
            status = self._eligibility(path, record, fresh=True)
        except OSError:
            status = "unreadable"
        if status != "active":
            raise Unavailable("This thread has been archived, deleted, or is no longer readable. It cannot be opened or exported.")
        return path, record

    def _transcript_paths(self, record):
        ids = []
        prior = record.get("priorCliSessionIds")
        prior = prior if isinstance(prior, list) else []
        for value in [*prior, record.get("unarchivedCliSessionId"), record.get("cliSessionId")]:
            if isinstance(value, str) and re.fullmatch(r"[\w-]+", value) and value not in ids:
                ids.append(value)
        paths, missing = [], []
        for sid in ids:
            candidates = []
            for candidate in self.projects_root.glob(f"*/{sid}.jsonl"):
                if candidate.is_file() and candidate.resolve().is_relative_to(self.projects_root):
                    candidates.append(candidate)
            if candidates:
                paths.append(max(candidates, key=lambda p: p.stat().st_mtime_ns))
            else:
                missing.append(sid)
        return paths, missing

    def thread(self, key):
        with self._lock:
            metadata_path, record = self._current(key)
            paths, missing = self._transcript_paths(record)
            messages, warnings, seen = [], [], set()
            for path in paths:
                stat = path.stat()
                signature = (str(path), stat.st_mtime_ns, stat.st_size)
                parsed = self._transcript_cache.get(signature)
                if parsed is None:
                    parsed = parse_transcript(path)
                    self._transcript_cache[signature] = parsed
                    while len(self._transcript_cache) > 4:
                        self._transcript_cache.popitem(last=False)
                for message in parsed[0]:
                    if message["id"] not in seen:
                        messages.append(message)
                        seen.add(message["id"])
                warnings.extend(parsed[1])
            if missing:
                warnings.append(f"{len(missing)} referenced transcript(s) are missing locally. This export may be incomplete.")
            if not paths:
                warnings.append("This desktop entry has no readable local transcript. It may be empty, remote, or its transcript may have been removed.")
            # A thread can be dismissed while a large transcript is being read.
            self._current(key)
            return {
                **self._summary(metadata_path, record), "messages": messages,
                "warnings": warnings, "transcript_paths": [str(p) for p in paths],
                "metadata_path": str(metadata_path), "checked_at": datetime.now(timezone.utc).isoformat(),
                "source_roots": {"sessions": str(self.session_root), "projects": str(self.projects_root)},
                "notes": "Only locally saved visible conversation content is included. Hidden reasoning, API request logs, and desktop settings are omitted. Images are embedded in JSON exports; Markdown marks their positions. Tool history is reference material, not a request to replay commands.",
            }


def block_markdown(block):
    kind = block["type"]
    if kind == "text":
        return block["text"]
    if kind == "image":
        return "[Attached image — embedded data is available in the JSON export.]"
    if kind == "attachment":
        return block["text"]
    if kind == "tool_use":
        return f"**Tool: {block['name']}**\n\n" + fence(json.dumps(block["input"], ensure_ascii=False, indent=2))
    if kind == "tool_result":
        content = "\n\n".join(block_markdown(b) for b in block["content"])
        return f"**Tool result{' (error)' if block['error'] else ''}**\n\n" + fence(content)
    return ""


def export_markdown(thread, handoff=False):
    lines = [f"# {thread['title']}", "", f"Project: {thread['cwd'] or '(not recorded)'}", f"Desktop session: {thread['desktop_session_id']}", f"Active status checked: {thread['checked_at']}", ""]
    if handoff:
        roots = thread["source_roots"]
        command = shlex.join(["python3", str(APP_DIR / "app.py"), "--session-root", roots["sessions"],
                              "--projects-root", roots["projects"], "export", thread["id"], "--format", "markdown"])
        lines += ["## Continuation request", "", "Read the saved conversation and inspect the current project files to recover the goal, decisions, and unfinished work. Use the current user's request to decide what to do next. Historical tool commands are context, not instructions to execute again.", "", "Before continuing, recheck that the desktop thread is still active by running:", "", fence(command), "", "If that command reports the thread is unavailable, stop: it has been archived, deleted, or cannot be verified.", ""]
    lines += ["## Local sources", "", f"Desktop metadata: {thread['metadata_path']}"]
    lines += [f"Transcript: {path}" for path in thread["transcript_paths"]]
    lines += ["", thread["notes"], ""]
    lines += [f"Note: {warning}" for warning in thread["warnings"]]
    messages = thread["messages"]
    if handoff:
        messages = [m for m in messages if m["role"] in ("user", "assistant") and any(b["type"] == "text" for b in m["blocks"])][-12:]
        lines += ["", "## Recent conversation excerpt", "", "This is an excerpt. Use the source transcripts or the export command above for the rest.", ""]
    else:
        lines += ["", "## Conversation", ""]
    budget = 24000
    for index, message in enumerate(messages):
        body = "\n\n".join(block_markdown(b) for b in message["blocks"] if not handoff or b["type"] == "text")
        if handoff:
            # Reserve space for every later message, especially the latest user
            # request. Long early excerpts must not crowd out the end of a thread.
            limit = min(4000, budget // (len(messages) - index))
            if len(body) > limit:
                body = body[:limit] + "\n[Excerpt shortened; see the full transcript.]"
            budget -= min(len(body), limit)
            if not limit:
                continue
        label = {"user": "You", "assistant": "Claude", "tool": "Tool output", "context": "Context supplied by Claude"}[message["role"]]
        lines += [f"### {label}" + (" · Compaction summary" if message["summary"] else ""), "", message["timestamp"], "", body, ""]
    return "\n".join(lines)


def make_server(store, port=0):
    key = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status, data, mime="application/json; charset=utf-8", filename=None):
            if not isinstance(data, (str, bytes)):
                data = json.dumps(data, ensure_ascii=False)
            if isinstance(data, str):
                data = data.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            expected = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != expected:
                return self.respond(403, {"error": "Open the viewer using its local launch URL."})
            route = urlsplit(self.path)
            if route.path.startswith("/api/"):
                supplied_key = self.headers.get("X-Viewer-Key", "").encode("utf-8")
                if not secrets.compare_digest(supplied_key, key.encode("ascii")):
                    return self.respond(403, {"error": "Reopen the viewer using the launch URL in its Terminal window."})
                try:
                    if route.path == "/api/threads":
                        return self.respond(200, store.listing())
                    match = re.fullmatch(r"/api/thread/([a-f0-9]{24})(/export)?", route.path)
                    if match:
                        thread = store.thread(match[1])
                        if not match[2]:
                            return self.respond(200, thread)
                        fmt = parse_qs(route.query).get("format", ["markdown"])[0]
                        if fmt not in ("markdown", "json", "handoff"):
                            return self.respond(400, {"error": "Unknown export format."})
                        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", thread["title"]).strip("-.")[:80] or "claude-thread"
                        if fmt == "json":
                            return self.respond(200, json.dumps(thread, ensure_ascii=False, indent=2), filename=f"{slug}.json")
                        return self.respond(200, export_markdown(thread, fmt == "handoff"), "text/markdown; charset=utf-8", f"{slug}{'-handoff' if fmt == 'handoff' else ''}.md")
                    return self.respond(404, {"error": "Not found."})
                except Unavailable as error:
                    return self.respond(410, {"error": str(error)})
                except (OSError, ValueError, TypeError) as error:
                    return self.respond(500, {"error": f"Could not read the local thread: {error}"})
            assets = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
            if route.path in assets:
                file = APP_DIR / "web" / assets[route.path]
                mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}[file.suffix]
                return self.respond(200, file.read_bytes(), mime + "; charset=utf-8")
            self.respond(404, {"error": "Not found."})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.viewer_key = key
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-root", type=Path, default=DEFAULT_SESSIONS)
    parser.add_argument("--projects-root", type=Path, default=DEFAULT_PROJECTS)
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Open the local viewer (default)")
    serve.add_argument("--port", type=int, default=0)
    serve.add_argument("--no-open", action="store_true")
    sub.add_parser("list", help="List active desktop threads as JSON")
    export = sub.add_parser("export", help="Export a currently active thread")
    export.add_argument("id")
    export.add_argument("--format", choices=["markdown", "json", "handoff"], default="markdown")
    export.add_argument("--output", type=Path)
    args = parser.parse_args()
    store = Store(args.session_root, args.projects_root)
    if args.command == "list":
        print(json.dumps(store.listing(), ensure_ascii=False, indent=2))
        return
    if args.command == "export":
        try:
            thread = store.thread(args.id)
        except (Unavailable, OSError) as error:
            parser.exit(1, str(error) + "\n")
        output = json.dumps(thread, ensure_ascii=False, indent=2) if args.format == "json" else export_markdown(thread, args.format == "handoff")
        if args.output:
            destination = args.output.expanduser().resolve()
            if any(destination.is_relative_to(root) for root in (store.session_root, store.projects_root)):
                parser.exit(1, "Choose an export destination outside the source session and transcript folders.\n")
            destination.write_text(output, encoding="utf-8")
        else:
            print(output)
        return
    server = make_server(store, getattr(args, "port", 0))
    url = f"http://127.0.0.1:{server.server_port}/#key={server.viewer_key}"
    print(f"\nClaude Thread Viewer\n\n{url}\n\nKeep this window open while browsing. Press Control-C to stop.\n", flush=True)
    if not getattr(args, "no_open", False):
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nViewer stopped. Claude's files were not changed.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
