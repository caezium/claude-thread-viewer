import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import APP_DIR, Store, Unavailable, export_markdown, make_server, parse_transcript


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sessions = self.root / "sessions"
        self.org = self.sessions / "account" / "org"
        self.org.mkdir(parents=True)
        self.projects = self.root / "projects"
        self.project = self.projects / "-Users-example-project"
        self.project.mkdir(parents=True)
        self.store = Store(self.sessions, self.projects)

    def tearDown(self):
        self.tmp.cleanup()

    def session(self, name="active", **overrides):
        data = {"sessionId": f"local_{name}", "cliSessionId": name, "isArchived": False, "title": name, "cwd": "/Users/example/project", "lastActivityAt": 1780000000000, "createdAt": 1770000000000, "completedTurns": 1}
        data.update(overrides)
        path = self.org / f"local_{name}.json"
        path.write_text(json.dumps(data))
        return path

    def transcript(self, name="active", records=None):
        path = self.project / f"{name}.jsonl"
        records = records if records is not None else [self.message("u", None, "user", "Fix the parser."), self.message("a", "u", "assistant", "The parser now handles empty input.")]
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return path

    def message(self, uid, parent, role, text):
        return {"uuid": uid, "parentUuid": parent, "type": role, "timestamp": "2026-10-30T10:00:00Z", "message": {"content": [{"type": "text", "text": text}]}}

    def test_only_current_active_desktop_records_are_listed(self):
        self.session()
        self.session("archived", isArchived=True)
        self.session("indexed")
        self.session("deleted")
        self.session("unknown", isArchived=None)
        (self.org / "archived-sessions.idx").write_text(json.dumps({"v": 1, "archived": ["local_indexed"]}))
        (self.org / "deleted_deleted").write_text("1780000000000")
        self.transcript("orphan")
        result = self.store.listing()
        self.assertEqual([t["title"] for t in result["threads"]], ["active"])
        self.assertEqual(result["counts"]["archived"], 2)
        self.assertEqual(result["counts"]["deleted"], 1)

    def test_archive_after_open_prevents_export(self):
        self.session()
        self.transcript()
        key = self.store.listing()["threads"][0]["id"]
        self.assertEqual(len(self.store.thread(key)["messages"]), 2)
        self.session(isArchived=True)
        with self.assertRaises(Unavailable):
            self.store.thread(key)

    def test_archive_while_reading_a_transcript_prevents_export(self):
        self.session()
        self.transcript()
        key = self.store.listing()["threads"][0]["id"]

        def archive_after_parse(path):
            result = parse_transcript(path)
            self.session(isArchived=True)
            return result

        with patch("app.parse_transcript", side_effect=archive_after_parse):
            with self.assertRaises(Unavailable):
                self.store.thread(key)

    def test_delete_after_open_prevents_export_even_if_transcript_remains(self):
        metadata = self.session()
        transcript = self.transcript()
        key = self.store.listing()["threads"][0]["id"]
        self.store.thread(key)
        metadata.unlink()
        with self.assertRaises(Unavailable):
            self.store.thread(key)
        self.assertTrue(transcript.exists())

    def test_tombstone_in_same_account_other_org_blocks_export(self):
        self.session()
        self.transcript()
        key = self.store.listing()["threads"][0]["id"]
        other_org = self.org.parent / "another-org"
        other_org.mkdir()
        (other_org / "deleted_active").write_text("1780000000000")
        with self.assertRaises(Unavailable):
            self.store.thread(key)

    def test_unreadable_archive_index_fails_closed(self):
        self.session()
        (self.org / "archived-sessions.idx").write_text("incomplete {")
        self.assertEqual(self.store.listing()["threads"], [])

    def test_malformed_archive_entries_fail_closed(self):
        self.session()
        (self.org / "archived-sessions.idx").write_text(json.dumps({"v": 1, "archived": [None]}))
        self.assertEqual(self.store.listing()["threads"], [])

    def test_export_rechecks_archive_index_even_with_unchanged_mtime(self):
        self.session()
        self.transcript()
        index = self.org / "archived-sessions.idx"
        index.write_text(json.dumps({"v": 1, "archived": ["local_spared"]}))
        before = index.stat()
        key = self.store.listing()["threads"][0]["id"]
        index.write_text(json.dumps({"v": 1, "archived": ["local_active"]}))
        os.utime(index, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaises(Unavailable):
            self.store.thread(key)

    def test_bad_display_fields_do_not_break_the_thread_list(self):
        self.session(title={"unsupported": True}, cwd=[], model=123, completedTurns="many")
        row = self.store.listing()["threads"][0]
        self.assertEqual(row["title"], "Untitled thread")
        self.assertEqual(row["cwd"], "")
        self.assertEqual(row["model"], "")
        self.assertEqual(row["turns"], 0)

    def test_latest_branch_only_and_no_hidden_reasoning_or_app_settings(self):
        self.session(remoteMcpServersConfig=[{"secret": "do-not-export"}])
        records = [self.message("u", None, "user", "Question"), self.message("abandoned", "u", "assistant", "Old branch"), self.message("chosen", "u", "assistant", "Chosen branch")]
        records[-1]["message"]["content"].insert(0, {"type": "thinking", "thinking": "private-reasoning"})
        self.transcript(records=records)
        key = self.store.listing()["threads"][0]["id"]
        thread = self.store.thread(key)
        self.assertEqual([m["id"] for m in thread["messages"]], ["u", "chosen"])
        exported = json.dumps(thread)
        self.assertNotIn("private-reasoning", exported)
        self.assertNotIn("do-not-export", exported)

    def test_injected_skill_is_context_and_does_not_become_a_user_handoff_message(self):
        self.session()
        records = [self.message("u", None, "user", "Fix the parser"), self.message("skill", "u", "user", "INJECTED-SKILL-TEXT"), self.message("a", "skill", "assistant", "Done")]
        records[1]["isMeta"] = True
        self.transcript(records=records)
        thread = self.store.thread(self.store.listing()["threads"][0]["id"])
        self.assertEqual(thread["messages"][1]["role"], "context")
        self.assertNotIn("INJECTED-SKILL-TEXT", export_markdown(thread, handoff=True))
        self.assertIn("Context supplied by Claude", export_markdown(thread))

    def test_handoff_keeps_the_latest_request_when_previous_messages_are_long(self):
        self.session()
        records = [self.message(str(i), str(i - 1) if i else None, "assistant", "Earlier discussion. " * 400) for i in range(11)]
        records.append(self.message("latest", "10", "user", "LATEST-REQUEST-MUST-SURVIVE"))
        self.transcript(records=records)
        thread = self.store.thread(self.store.listing()["threads"][0]["id"])
        self.assertIn("LATEST-REQUEST-MUST-SURVIVE", export_markdown(thread, handoff=True))

    def test_compaction_retains_linked_history_without_abandoned_branch(self):
        records = [self.message("u", None, "user", "Question"), self.message("abandoned", "u", "assistant", "Old branch"), self.message("chosen", "u", "assistant", "Chosen branch"), {"type": "system", "subtype": "compact_boundary", "uuid": "boundary", "parentUuid": None, "logicalParentUuid": "chosen"}, self.message("summary", "boundary", "user", "Earlier context"), self.message("next", "summary", "assistant", "Continuing")]
        messages, warnings = parse_transcript(self.transcript(records=records))
        self.assertEqual([m["id"] for m in messages], ["u", "chosen", "summary", "next"])
        self.assertEqual(warnings, [])

    def test_partial_last_line_does_not_break_a_live_thread(self):
        path = self.transcript()
        with path.open("a") as file:
            file.write('{"type":')
        messages, warnings = parse_transcript(path)
        self.assertEqual(len(messages), 2)
        self.assertEqual(len(warnings), 1)

    def test_partial_unicode_write_does_not_hide_complete_messages(self):
        path = self.transcript()
        with path.open("ab") as file:
            file.write(b'{"message":"\xe2\x82')
        messages, warnings = parse_transcript(path)
        self.assertEqual(len(messages), 2)
        self.assertEqual(len(warnings), 1)

    def test_unexpected_block_and_parent_shapes_are_handled(self):
        records = [self.message("u", None, "user", "Keep this message")]
        records[0]["message"]["content"] += [{"type": "text", "text": None}, {"type": "image", "source": None}, {"type": "document", "source": []}]
        broken = self.message("broken", ["u"], "assistant", "Invalid parent")
        messages, warnings = parse_transcript(self.transcript(records=[*records, broken]))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["blocks"][0]["text"], "Keep this message")
        self.assertTrue(warnings)

    def test_handoff_quotes_paths_and_keeps_custom_sources(self):
        self.session()
        self.transcript()
        thread = self.store.thread(self.store.listing()["threads"][0]["id"])
        with patch("app.APP_DIR", self.root / "a $literal and 'quotes'"):
            handoff = export_markdown(thread, handoff=True)
        command = next(line for line in handoff.splitlines() if line.startswith("python3 "))
        argv = shlex.split(command)
        self.assertEqual(argv[1], str(self.root / "a $literal and 'quotes'" / "app.py"))
        self.assertEqual(argv[argv.index("--session-root") + 1], str(self.sessions.resolve()))
        self.assertEqual(argv[argv.index("--projects-root") + 1], str(self.projects.resolve()))

    def test_cli_export_does_not_overwrite_source_data(self):
        self.session()
        transcript = self.transcript()
        before = transcript.read_bytes()
        key = self.store.listing()["threads"][0]["id"]
        result = subprocess.run([sys.executable, str(APP_DIR / "app.py"), "--session-root", str(self.sessions), "--projects-root", str(self.projects), "export", key, "--output", str(transcript)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the source", result.stderr)
        self.assertEqual(transcript.read_bytes(), before)

    def test_custom_backup_handoff_command_can_export(self):
        self.session()
        self.transcript()
        thread = self.store.thread(self.store.listing()["threads"][0]["id"])
        handoff = export_markdown(thread, handoff=True)
        argv = shlex.split(next(line for line in handoff.splitlines() if line.startswith("python3 ")))
        argv[0] = sys.executable
        result = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Fix the parser", result.stdout)

    def test_tool_output_and_images_survive_json_export(self):
        self.session()
        records = [self.message("u", None, "user", "Look at this"), self.message("a", "u", "assistant", "Checking")]
        records[0]["message"]["content"].append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "fixture"}})
        records[1]["message"]["content"].append({"type": "tool_use", "name": "Read", "id": "tool-1", "input": {"file_path": "parser.py"}})
        records.append({"type": "user", "uuid": "result", "parentUuid": "a", "message": {"content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "source code"}]}})
        self.transcript(records=records)
        thread = self.store.thread(self.store.listing()["threads"][0]["id"])
        self.assertEqual(thread["messages"][0]["blocks"][1]["data"], "fixture")
        self.assertEqual(thread["messages"][-1]["role"], "tool")
        exported = export_markdown(thread)
        self.assertIn("source code", exported)
        self.assertIn("Attached image", exported)
        self.assertIn("Active status checked", exported)

    def test_prior_transcripts_are_merged_without_duplicate_messages(self):
        self.session(priorCliSessionIds=["prior"])
        self.transcript("prior", [self.message("u", None, "user", "Question")])
        self.transcript(records=[self.message("u", None, "user", "Question"), self.message("a", "u", "assistant", "Answer")])
        thread = self.store.thread(self.store.listing()["threads"][0]["id"])
        self.assertEqual(len(thread["messages"]), 2)
        self.assertEqual(len(thread["transcript_paths"]), 2)

    def test_http_requires_local_host_and_launch_key_and_rechecks_export(self):
        self.session()
        self.transcript()
        server = make_server(self.store)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/api/threads")
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base + "/api/threads", headers={"X-Viewer-Key": "invalid-\u00e9"}))
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
            headers = {"X-Viewer-Key": server.viewer_key}
            with urlopen(Request(base + "/api/threads", headers=headers)) as response:
                key = json.load(response)["threads"][0]["id"]
            with urlopen(Request(base + f"/api/thread/{key}/export?format=markdown", headers=headers)) as response:
                self.assertIn("Fix the parser", response.read().decode())
            self.session(isArchived=True)
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base + f"/api/thread/{key}/export?format=json", headers=headers))
            self.assertEqual(error.exception.code, 410)
            error.exception.close()
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base + "/", headers={"Host": "untrusted.example"}))
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
