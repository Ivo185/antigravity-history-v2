"""
Offline reader for Antigravity 2.x local storage (~/.gemini/antigravity).

Reads:
- conversation_summaries.db (SQLite index of conversations)
- brain/<id>/.system_generated/logs/transcript_full.jsonl (or transcript.jsonl)
- conversations/<id>.db (per-conversation SQLite DB for unindexed detection/recovery)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


def get_default_data_dir() -> Path:
    """Return the Antigravity data directory (~/.gemini/antigravity or ANTIGRAVITY_DATA_DIR)."""
    env_dir = os.environ.get("ANTIGRAVITY_DATA_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    return Path(os.path.expanduser("~/.gemini/antigravity"))


def extract_user_request_text(content: str) -> str:
    """Extract clean user prompt from <USER_REQUEST>...</USER_REQUEST> if present."""
    if not content:
        return ""
    m = _USER_REQUEST_RE.search(content)
    if m:
        return m.group(1).strip()
    return content.strip()


def _normalize_timestamp(ts: str) -> str:
    """Normalize SQLite/ISO timestamps to ISO-8601 format (e.g. '2026-11-13T08:54:59+00:00')."""
    if not ts:
        return ""
    # '2026-11-13 08:54:59.4451217+00:00' -> '2026-11-13T08:54:59.4451217+00:00'
    if len(ts) > 10 and ts[10] == " ":
        ts = ts[:10] + "T" + ts[11:]
    return ts


def _connect_sqlite_ro(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite database in read-only mode."""
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5.0)


def get_transcript_path(data_dir: Path, cascade_id: str) -> Optional[Path]:
    """Return path to transcript_full.jsonl or transcript.jsonl for a conversation."""
    logs_dir = data_dir / "brain" / cascade_id / ".system_generated" / "logs"
    full_path = logs_dir / "transcript_full.jsonl"
    if full_path.is_file():
        return full_path
    compact_path = logs_dir / "transcript.jsonl"
    if compact_path.is_file():
        return compact_path
    return None


def load_transcript_entries(transcript_path: Path) -> list[dict[str, Any]]:
    """Read all JSON objects from a transcript*.jsonl file."""
    entries: list[dict[str, Any]] = []
    if not transcript_path or not transcript_path.is_file():
        return entries
    with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _inspect_transcript_meta(transcript_path: Path) -> dict[str, Any]:
    """Quickly extract created_at, last_at, entry_count, and first user prompt from transcript."""
    created_at = ""
    last_at = ""
    first_user_prompt = ""
    count = 0

    with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            count += 1
            ts = obj.get("created_at", "")
            if ts:
                if not created_at:
                    created_at = ts
                last_at = ts
            if not first_user_prompt and obj.get("type") == "USER_INPUT":
                text = extract_user_request_text(obj.get("content", ""))
                if text:
                    first_user_prompt = text

    return {
        "created_at": created_at,
        "last_at": last_at,
        "count": count,
        "first_user_prompt": first_user_prompt,
    }


def _make_fallback_title(first_user_prompt: str, cascade_id: str, is_subagent: bool = False) -> str:
    """Build a clean single-line title when conversation_summaries.title is empty."""
    if first_user_prompt:
        one_line = " ".join(first_user_prompt.split())
        if len(one_line) > 80:
            return one_line[:77] + "..."
        return one_line
    prefix = "[subagent]" if is_subagent else "[untitled]"
    return f"{prefix} {cascade_id[:8]}"


def _parse_workspaces(workspace_uris_raw: Optional[str]) -> list[dict[str, str]]:
    """Convert conversation_summaries.workspace_uris JSON string to formatters-compatible list."""
    if not workspace_uris_raw:
        return []
    try:
        uris = json.loads(workspace_uris_raw)
        if isinstance(uris, list):
            return [{"workspaceFolderAbsoluteUri": u} for u in uris if isinstance(u, str) and u]
    except Exception:
        pass
    return []


def _get_conv_db_step_count(db_path: Path) -> int:
    """Return number of rows in `steps` table of conversations/<id>.db, or 0."""
    try:
        con = _connect_sqlite_ro(db_path)
        try:
            row = con.execute("SELECT COUNT(*) FROM steps").fetchone()
            return int(row[0]) if row else 0
        finally:
            con.close()
    except Exception:
        return 0


def load_offline_summaries(
    data_dir: Optional[Path] = None,
    include_subagents: bool = True,
) -> dict[str, dict[str, Any]]:
    """Load all conversation summaries from Antigravity 2.x local storage.

    Combines `conversation_summaries.db`, `brain/<id>/.system_generated/logs/transcript*.jsonl`,
    and `conversations/<id>.db`.
    """
    data_dir = data_dir or get_default_data_dir()
    summaries: dict[str, dict[str, Any]] = {}

    db_path = data_dir / "conversation_summaries.db"
    if db_path.is_file():
        con = _connect_sqlite_ro(db_path)
        try:
            rows = con.execute(
                """
                SELECT conversation_id, title, preview, step_count,
                       last_modified_time, workspace_uris, status,
                       parent_conversation_id, nesting_depth, last_user_input_time
                FROM conversation_summaries
                """
            ).fetchall()
        finally:
            con.close()

        for (
            cid,
            title,
            preview,
            step_count,
            last_mod,
            ws_raw,
            status,
            parent_cid,
            nesting_depth,
            last_user_input,
        ) in rows:
            is_subagent = bool(parent_cid) or (nesting_depth or 0) > 0
            if not include_subagents and is_subagent:
                continue

            summaries[cid] = {
                "summary": (title or "").strip(),
                "preview": (preview or "").strip(),
                "stepCount": step_count or 0,
                "lastModifiedTime": _normalize_timestamp(last_mod or ""),
                "lastUserInputTime": _normalize_timestamp(last_user_input or ""),
                "createdTime": "",
                "status": status or "",
                "workspaces": _parse_workspaces(ws_raw),
                "parentConversationId": parent_cid or "",
                "nestingDepth": nesting_depth or 0,
                "source": "db",
            }

    # Discover any brain/<id> transcripts not in conversation_summaries.db
    brain_dir = data_dir / "brain"
    if brain_dir.is_dir():
        for entry in brain_dir.iterdir():
            if not entry.is_dir():
                continue
            cid = entry.name
            tpath = get_transcript_path(data_dir, cid)
            if not tpath:
                continue
            if cid not in summaries:
                summaries[cid] = {
                    "summary": "",
                    "preview": "",
                    "stepCount": 0,
                    "lastModifiedTime": "",
                    "lastUserInputTime": "",
                    "createdTime": "",
                    "status": "OFFLINE_TRANSCRIPT",
                    "workspaces": [],
                    "parentConversationId": "",
                    "nestingDepth": 0,
                    "source": "transcript",
                }

    # Enrich all entries with transcript metadata (createdTime, fallback title, transcript_path)
    for cid, info in list(summaries.items()):
        tpath = get_transcript_path(data_dir, cid)
        is_subagent = bool(info.get("parentConversationId")) or (info.get("nestingDepth") or 0) > 0
        if tpath:
            info["transcriptPath"] = str(tpath)
            meta = _inspect_transcript_meta(tpath)
            if meta["created_at"] and not info.get("createdTime"):
                info["createdTime"] = meta["created_at"]
            if meta["last_at"] and not info.get("lastModifiedTime"):
                info["lastModifiedTime"] = meta["last_at"]
            if not info.get("stepCount"):
                info["stepCount"] = meta["count"]
            if not info.get("summary"):
                info["summary"] = _make_fallback_title(
                    meta["first_user_prompt"], cid, is_subagent=is_subagent
                )
        else:
            if not info.get("summary"):
                info["summary"] = _make_fallback_title("", cid, is_subagent=is_subagent)

    # Also scan conversations/*.db for any unindexed .db files
    conv_dir = data_dir / "conversations"
    if conv_dir.is_dir():
        for db_file in conv_dir.glob("*.db"):
            cid = db_file.stem
            if cid not in summaries:
                mtime = datetime.fromtimestamp(
                    db_file.stat().st_mtime, tz=timezone.utc
                ).isoformat()
                step_count = _get_conv_db_step_count(db_file)
                summaries[cid] = {
                    "summary": f"[db-only] {cid[:8]}",
                    "preview": "",
                    "stepCount": step_count,
                    "lastModifiedTime": mtime,
                    "lastUserInputTime": "",
                    "createdTime": mtime,
                    "status": "DB_ONLY",
                    "workspaces": [],
                    "parentConversationId": "",
                    "nestingDepth": 0,
                    "source": "conv_db",
                }

    return summaries


def get_offline_info(data_dir: Optional[Path] = None) -> dict[str, Any]:
    """Gather summary statistics about local Antigravity 2.x storage."""
    data_dir = data_dir or get_default_data_dir()
    db_path = data_dir / "conversation_summaries.db"
    conv_dir = data_dir / "conversations"
    brain_dir = data_dir / "brain"

    conv_dbs = list(conv_dir.glob("*.db")) if conv_dir.is_dir() else []
    transcripts = (
        list(brain_dir.glob("*/.system_generated/logs/transcript*.jsonl"))
        if brain_dir.is_dir()
        else []
    )
    summaries = load_offline_summaries(data_dir) if data_dir.is_dir() else {}

    subagents = sum(
        1
        for v in summaries.values()
        if v.get("parentConversationId") or (v.get("nestingDepth") or 0) > 0
    )
    total_steps = sum(v.get("stepCount", 0) for v in summaries.values())
    times = sorted(
        [v.get("createdTime") or v.get("lastModifiedTime", "") for v in summaries.values() if v.get("createdTime") or v.get("lastModifiedTime")]
    )
    mod_times = sorted(
        [v.get("lastModifiedTime", "") for v in summaries.values() if v.get("lastModifiedTime")]
    )

    return {
        "data_dir": str(data_dir),
        "exists": data_dir.is_dir(),
        "db_exists": db_path.is_file(),
        "total_conversations": len(summaries),
        "subagent_count": subagents,
        "conv_db_count": len(conv_dbs),
        "transcript_count": len({p.parents[2].name for p in transcripts}),
        "total_steps": total_steps,
        "oldest": times[0][:10] if times else "?",
        "newest": mod_times[-1][:10] if mod_times else "?",
    }
