# Thread Library

A local, read-only viewer for Claude desktop's saved **Code** threads. No Claude login, subscription, network connection, or Python packages are required. Requires Python 3.10 or later; it uses only the standard library. The web interface has no external scripts, fonts, analytics, or model calls.

Read the history already on your Mac, keep archived and deleted work out of the active list, and take selected conversations to Codex or T3 using a handoff or export.

This is an independent project, not an Anthropic product or a modification of Claude Desktop. See [implementation provenance](PROVENANCE.md).

## Install

Clone the repository or download its source archive, then open the viewer:

```sh
git clone https://github.com/caezium/claude-thread-viewer.git
cd claude-thread-viewer
python3 app.py serve
```

The default storage locations and double-click launcher target macOS. The reader and tests can use a copied local dataset on other systems through `--session-root` and `--projects-root`; there is no automatic Windows or Linux desktop discovery.

## Open it

Double-click **Launch Viewer.command**. A Terminal window starts the viewer and opens your default browser. Keep that Terminal window open while browsing; Control-C stops the viewer. If the browser does not open, use the local URL printed in Terminal.

Search titles or project folders, filter by project, and select a thread. **Tools & context** reveals saved tool calls, results, and injected skill/context messages. Long conversations initially show the latest 60 visible messages; load earlier messages above the conversation. Command-K focuses search.

## Continue somewhere else

- **Copy handoff** copies a continuation prompt with source paths, recent messages, and a command that rechecks the thread's active status. Paste it into Codex or T3 **on this Mac**. It does not launch another agent or create a T3 thread.
- **Export Markdown** downloads the saved conversation text and tool history. Attach the file to the destination conversation. Images are marked but not embedded.
- **Export JSON** downloads structured conversation content, including locally embedded images, for preservation or another importer. This is a portable export, not a native Codex or T3 database import.

Exports are saved by your browser, normally in Downloads. The viewer does not change your project files or Claude's data. Hidden reasoning, API request logs, and desktop settings are not exported. Some document attachments may only have a label; missing transcripts and incomplete records are reported in the viewer and export.

Exports are not automatically redacted: messages, tool output, paths, and images can contain private information. Review a file before sharing it. See [security and privacy](SECURITY.md).

## Thread status

The list starts from current desktop metadata, not the raw transcript directory. A record must explicitly be unarchived, absent from the archive index, and free of matching deletion tombstones. Removed or unreadable records and orphaned transcripts are excluded. This is intentionally conservative when desktop metadata is incomplete.

The viewer refreshes status every 30 seconds while visible and when you return to the window. **Refresh** also reloads an open conversation. Opening or exporting a thread rechecks its current status, including after parsing its transcript; archived or deleted threads cannot be exported. A file already downloaded or text already copied is a snapshot and cannot be recalled if you later archive the source.

It reads these locations:

```
~/Library/Application Support/Claude/claude-code-sessions/
~/.claude/projects/
```

Only locally saved Claude desktop Code sessions are supported. Claude web chats, Cowork, and server-only history are outside this viewer. If several account directories exist on this Mac, active local Code records from each are included. It does not contact Anthropic.

## Command line

```sh
python3 app.py serve
python3 app.py serve --no-open
python3 app.py list
python3 app.py export THREAD_ID --format markdown --output conversation.md
python3 app.py export THREAD_ID --format json --output conversation.json
python3 -m unittest -v
node --test test_web.cjs
```

`THREAD_ID` is the viewer ID returned by `list`. For another local backup, put `--session-root PATH --projects-root PATH` before the command. Each server listens only on an automatically assigned loopback port. Its API requires the per-launch key supplied to the opened browser.

Node.js 18 or newer is needed only for the frontend regression tests. Running the viewer requires Python and a modern browser, with no package installation.

## Limitations

- The local desktop metadata format is undocumented and can change. The viewer excludes records whose active state it cannot verify.
- Conversation conversion is best-effort. It follows the latest recorded message branch and retains linked compacted history when available; missing ancestors are reported.
- Markdown rendering is intentionally limited. Tables, nested lists, and agent-specific attachments may appear as plain text or placeholders.
- A portable JSON export is not a native Codex or T3 session import. A copied handoff references the current machine's files.
- This project does not recover deleted conversations, unlock Claude features, run an agent, or synchronize with an account.

## License and contributions

Thread Library is available under the [MIT License](LICENSE). The license covers this implementation, not third-party applications or the contents of your transcripts.

For contributions, start with [CONTRIBUTING.md](CONTRIBUTING.md). Please use fictional transcripts when reporting format problems.
