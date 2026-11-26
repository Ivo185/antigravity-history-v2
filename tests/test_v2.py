"""Unit tests for Antigravity 2.x offline reader and transcript parser."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from antigravity_history.formatters import format_index_markdown, format_markdown
from antigravity_history.offline import (
    extract_user_request_text,
    load_offline_summaries,
    load_transcript_entries,
)
from antigravity_history.parser import FieldLevel, parse_transcript_entries


class TestAntigravityV2(unittest.TestCase):
    def test_extract_user_request_text(self):
        raw = (
            "<USER_REQUEST>\nЗдравей, тест!\n</USER_REQUEST>\n"
            "<ADDITIONAL_METADATA>\nfoo\n</ADDITIONAL_METADATA>"
        )
        self.assertEqual(extract_user_request_text(raw), "Здравей, тест!")
        self.assertEqual(extract_user_request_text("plain prompt"), "plain prompt")

    def test_parse_transcript_entries_levels(self):
        entries = [
            {
                "step_index": 0,
                "type": "SYSTEM_MESSAGE",
                "created_at": "2026-11-26T09:00:00Z",
                "content": "system boilerplate",
            },
            {
                "step_index": 1,
                "type": "USER_INPUT",
                "created_at": "2026-11-26T09:00:01Z",
                "content": "<USER_REQUEST>\nFix bug\n</USER_REQUEST>",
            },
            {
                "step_index": 2,
                "type": "PLANNER_RESPONSE",
                "created_at": "2026-11-26T09:00:02Z",
                "thinking": "Let me check",
                "content": "I will read the file.",
                "tool_calls": [
                    {"name": "read_file", "args": {"Path": "foo.py", "toolSummary": "Read foo"}}
                ],
                "input_tokens": 100,
                "output_tokens": 20,
            },
            {
                "step_index": 3,
                "type": "GENERIC",
                "created_at": "2026-11-26T09:00:03Z",
                "status": "DONE",
                "content": "file contents here",
            },
        ]

        default_msgs = parse_transcript_entries(entries, FieldLevel.DEFAULT)
        self.assertEqual([m["role"] for m in default_msgs], ["user", "assistant", "tool"])
        self.assertNotIn("thinking", default_msgs[1])
        self.assertNotIn("timestamp", default_msgs[0])

        thinking_msgs = parse_transcript_entries(entries, FieldLevel.THINKING)
        self.assertEqual(thinking_msgs[1]["thinking"], "Let me check")
        self.assertEqual(thinking_msgs[0]["timestamp"], "2026-11-26T09:00:01Z")

        full_msgs = parse_transcript_entries(entries, FieldLevel.FULL)
        self.assertEqual([m["role"] for m in full_msgs], ["user", "assistant", "tool", "tool"])
        self.assertEqual(full_msgs[1]["tokens"]["input_tokens"], 100)
        self.assertEqual(full_msgs[2]["tool_args"]["Path"], "foo.py")
        self.assertEqual(full_msgs[3]["output"], "file contents here")

    def test_load_offline_summaries_and_formatting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "conversation_summaries.db"
            con = sqlite3.connect(db_path)
            con.execute(
                """
                CREATE TABLE conversation_summaries (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT,
                    preview TEXT,
                    step_count INTEGER,
                    last_modified_time TEXT,
                    workspace_uris TEXT,
                    status TEXT,
                    parent_conversation_id TEXT,
                    nesting_depth INTEGER,
                    last_user_input_time TEXT
                )
                """
            )
            con.execute(
                "INSERT INTO conversation_summaries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "11111111-aaaa-bbbb-cccc-000000000001",
                    "",
                    "",
                    5,
                    "2026-11-26 10:00:00+00:00",
                    '["file:///d:/Projects/demo"]',
                    "CASCADE_RUN_STATUS_IDLE",
                    "",
                    0,
                    "2026-11-26 09:59:00+00:00",
                ),
            )
            con.commit()
            con.close()

            logs_dir = (
                root
                / "brain"
                / "11111111-aaaa-bbbb-cccc-000000000001"
                / ".system_generated"
                / "logs"
            )
            logs_dir.mkdir(parents=True)
            tfile = logs_dir / "transcript_full.jsonl"
            tfile.write_text(
                json.dumps({
                    "step_index": 0,
                    "type": "USER_INPUT",
                    "created_at": "2026-11-26T09:59:00Z",
                    "content": "<USER_REQUEST>\nFallback title from prompt\n</USER_REQUEST>",
                })
                + "\n",
                encoding="utf-8",
            )

            summaries = load_offline_summaries(root)
            self.assertIn("11111111-aaaa-bbbb-cccc-000000000001", summaries)
            info = summaries["11111111-aaaa-bbbb-cccc-000000000001"]
            self.assertEqual(info["summary"], "Fallback title from prompt")
            self.assertEqual(info["createdTime"], "2026-11-26T09:59:00Z")

            entries = load_transcript_entries(tfile)
            msgs = parse_transcript_entries(entries, FieldLevel.FULL)
            md = format_markdown(info["summary"], "11111111-aaaa-bbbb-cccc-000000000001", info, msgs)
            self.assertIn("# Fallback title from prompt", md)

            idx = format_index_markdown([
                {
                    "cascade_id": "11111111-aaaa-bbbb-cccc-000000000001",
                    "title": info["summary"],
                    "step_count": 5,
                    "message_count": len(msgs),
                    "last_modified_time": info["lastModifiedTime"],
                    "parent_conversation_id": "",
                    "workspaces": ["file:///d:/Projects/demo"],
                    "filename": "Fallback_title_from_prompt.md",
                }
            ])
            self.assertIn("Fallback title from prompt", idx)


if __name__ == "__main__":
    unittest.main()
