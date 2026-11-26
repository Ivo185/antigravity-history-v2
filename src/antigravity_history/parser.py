"""
Step parser — parse raw API steps (1.x) and transcript_full.jsonl entries (2.x)
into structured messages.

Three-level field strategy:
  default:  response, userResponse, basic tool calls
  thinking: + thinking, timestamp, exitCode, cwd, stopReason, errors
  full:     + diff, combinedOutput, tool args & outputs, token counts, model
"""

import json
from typing import Any, Optional

from antigravity_history.offline import extract_user_request_text


class FieldLevel:
    """Field export level."""

    DEFAULT = "default"
    THINKING = "thinking"
    FULL = "full"


# ════════════════════════════════════════════════════════════════
# Antigravity 2.x transcript_full.jsonl parser
# ════════════════════════════════════════════════════════════════

_SKIP_SYSTEM_TYPES = {
    "SYSTEM_MESSAGE",
    "EPHEMERAL_MESSAGE",
    "CONVERSATION_HISTORY",
}

_TOOL_RESULT_TYPES = {
    "GENERIC",
    "CODE_ACTION",
    "RUN_COMMAND",
    "VIEW_FILE",
    "LIST_DIRECTORY",
    "GREP_SEARCH",
    "FIND",
    "SEARCH_WEB",
    "READ_URL_CONTENT",
    "INVOKE_SUBAGENT",
    "COMMAND_STATUS",
}


def _summarize_tool_call(name: str, args: dict[str, Any]) -> str:
    """Create a concise human-readable summary of a tool call."""
    if not isinstance(args, dict):
        return str(args)
    for key in (
        "CommandLine",
        "command",
        "Path",
        "AbsolutePath",
        "TargetFile",
        "query",
        "Url",
        "url",
        "toolSummary",
        "Description",
    ):
        val = args.get(key)
        if val:
            return str(val)
    filtered = {k: v for k, v in args.items() if k not in ("toolAction", "toolSummary")}
    if not filtered:
        return f"[{name}]"
    return json.dumps(filtered, ensure_ascii=False)[:200]


def parse_transcript_entries(
    entries: list[dict[str, Any]],
    level: str = FieldLevel.DEFAULT,
) -> list[dict[str, Any]]:
    """Parse Antigravity 2.x transcript*.jsonl entries into structured messages."""
    include_thinking = level in (FieldLevel.THINKING, FieldLevel.FULL)
    include_full = level == FieldLevel.FULL

    messages: list[dict[str, Any]] = []

    for entry in entries:
        step_type = entry.get("type", "")
        timestamp = entry.get("created_at") if include_thinking else None

        if step_type in _SKIP_SYSTEM_TYPES:
            continue

        if step_type == "CHECKPOINT":
            if include_full and entry.get("content"):
                msg: dict[str, Any] = {
                    "role": "system",
                    "content": entry["content"],
                }
                if timestamp:
                    msg["timestamp"] = timestamp
                messages.append(msg)
            continue

        if step_type == "ERROR_MESSAGE":
            if include_thinking and entry.get("content"):
                msg = {"role": "system", "content": entry["content"]}
                if timestamp:
                    msg["timestamp"] = timestamp
                messages.append(msg)
            continue

        if step_type == "USER_INPUT":
            raw = entry.get("content", "")
            content = extract_user_request_text(raw)
            if not content:
                continue
            msg = {"role": "user", "content": content}
            if timestamp:
                msg["timestamp"] = timestamp
            messages.append(msg)
            continue

        if step_type == "PLANNER_RESPONSE":
            content = entry.get("content", "")
            thinking = entry.get("thinking") if include_thinking else None
            tool_calls = entry.get("tool_calls") or []

            if content or thinking:
                msg = {"role": "assistant", "content": content}
                if thinking:
                    msg["thinking"] = thinking
                if include_full:
                    tokens = {}
                    for k in ("input_tokens", "output_tokens", "cache_read_tokens"):
                        if entry.get(k) is not None:
                            tokens[k] = entry[k]
                    if tokens:
                        msg["tokens"] = tokens
                if timestamp:
                    msg["timestamp"] = timestamp
                messages.append(msg)

            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                tname = tc.get("name", "unknown")
                targs = tc.get("args", {})
                tmsg: dict[str, Any] = {
                    "role": "tool",
                    "tool_name": tname,
                    "content": _summarize_tool_call(tname, targs),
                }
                if include_full and isinstance(targs, dict):
                    filtered_args = {
                        k: v
                        for k, v in targs.items()
                        if k not in ("toolAction", "toolSummary")
                    }
                    if filtered_args:
                        tmsg["tool_args"] = filtered_args
                if timestamp:
                    tmsg["timestamp"] = timestamp
                messages.append(tmsg)
            continue

        # Tool result entries (GENERIC in 2.x, or legacy tool types)
        if step_type in _TOOL_RESULT_TYPES or entry.get("source") == "MODEL":
            raw_content = entry.get("content", "")
            if not raw_content:
                continue
            if include_full:
                tmsg = {
                    "role": "tool",
                    "tool_name": f"{step_type.lower()}_result",
                    "content": raw_content.splitlines()[0][:120] if raw_content else "",
                    "output": raw_content,
                }
                if entry.get("status"):
                    tmsg["status"] = entry["status"]
                if timestamp:
                    tmsg["timestamp"] = timestamp
                messages.append(tmsg)
            continue

    return messages


# ════════════════════════════════════════════════════════════════
# Antigravity 1.x LanguageServer API step parser
# ════════════════════════════════════════════════════════════════


def parse_steps(
    steps: list[dict],
    level: str = FieldLevel.DEFAULT,
) -> list[dict]:
    """Parse raw 1.x API steps into a list of structured messages."""
    include_thinking = level in (FieldLevel.THINKING, FieldLevel.FULL)
    include_full = level == FieldLevel.FULL

    messages = []
    for step in steps:
        step_type = step.get("type", "")
        metadata = step.get("metadata", {})
        timestamp = metadata.get("createdAt") if include_thinking else None

        msg = _parse_step(step, step_type, include_thinking, include_full)
        if msg is None:
            continue

        if timestamp:
            msg["timestamp"] = timestamp
        messages.append(msg)

    return messages


def _parse_step(
    step: dict,
    step_type: str,
    include_thinking: bool,
    include_full: bool,
) -> Optional[dict]:
    """Parse a single step, return a message dict or None (skip system types)."""
    if step_type == "CORTEX_STEP_TYPE_USER_INPUT":
        return _parse_user_input(step, include_full)
    if step_type == "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
        return _parse_planner_response(step, include_thinking, include_full)
    if step_type == "CORTEX_STEP_TYPE_CODE_ACTION":
        return _parse_code_action(step, include_full)
    if step_type == "CORTEX_STEP_TYPE_RUN_COMMAND":
        return _parse_run_command(step, include_thinking, include_full)
    if step_type == "CORTEX_STEP_TYPE_VIEW_FILE":
        return _parse_view_file(step, include_thinking)
    if step_type == "CORTEX_STEP_TYPE_FIND":
        find = step.get("find", {})
        return {
            "role": "tool",
            "tool_name": "find",
            "content": find.get("query", "[File Search]"),
        }
    if step_type == "CORTEX_STEP_TYPE_LIST_DIRECTORY":
        ld = step.get("listDirectory", {})
        path = ld.get("directoryPath", ld.get("path", ""))
        return {
            "role": "tool",
            "tool_name": "list_dir",
            "content": path or "[List Directory]",
        }
    if step_type == "CORTEX_STEP_TYPE_SEARCH_WEB":
        return _parse_search_web(step, include_full)
    if step_type == "CORTEX_STEP_TYPE_READ_URL_CONTENT":
        ru = step.get("readUrlContent", {})
        return {
            "role": "tool",
            "tool_name": "read_url",
            "content": ru.get("url", "[Read URL]"),
        }
    if step_type == "CORTEX_STEP_TYPE_COMMAND_STATUS":
        return {
            "role": "tool",
            "tool_name": "command_status",
            "content": "[Check Command Status]",
        }
    return None


_DIFF_PREFIX = {
    "UNIFIED_DIFF_LINE_TYPE_INSERT": "+",
    "UNIFIED_DIFF_LINE_TYPE_DELETE": "-",
    "UNIFIED_DIFF_LINE_TYPE_CONTEXT": " ",
}


def _normalize_diff(diff) -> str:
    """Normalize diff to string. API may return str or structured dict."""
    if isinstance(diff, str):
        return diff
    if isinstance(diff, dict):
        lines_data = diff.get("unifiedDiff", {}).get("lines", [])
        if not lines_data:
            return str(diff)
        parts = []
        for line in lines_data:
            text = line.get("text", "")
            prefix = _DIFF_PREFIX.get(line.get("type", ""), " ")
            parts.append(f"{prefix}{text}")
        return "\n".join(parts)
    return str(diff)


def _parse_user_input(step: dict, include_full: bool) -> Optional[dict]:
    ui = step.get("userInput", {})
    content = ui.get("userResponse", "")
    if not content:
        return None

    msg = {"role": "user", "content": content}
    if include_full:
        state = ui.get("activeUserState", {})
        active_doc = state.get("activeDocument", {})
        if active_doc.get("absoluteUri"):
            msg["active_file"] = active_doc["absoluteUri"]
            msg["editor_language"] = active_doc.get("editorLanguage", "")
    return msg


def _parse_planner_response(
    step: dict, include_thinking: bool, include_full: bool
) -> Optional[dict]:
    pr = step.get("plannerResponse", {})
    content = pr.get("modifiedResponse") or pr.get("response", "")
    if not content:
        return None

    msg = {"role": "assistant", "content": content}
    if include_thinking:
        thinking = pr.get("thinking")
        if thinking:
            msg["thinking"] = thinking
        stop_reason = pr.get("stopReason")
        if stop_reason:
            msg["stop_reason"] = stop_reason

    if include_full:
        metadata = step.get("metadata", {})
        model = metadata.get("generatorModel")
        if model:
            msg["model"] = model
        thinking_duration = pr.get("thinkingDuration")
        if thinking_duration:
            msg["thinking_duration"] = thinking_duration
        message_id = pr.get("messageId")
        if message_id:
            msg["message_id"] = message_id

    return msg


def _parse_code_action(step: dict, include_full: bool) -> Optional[dict]:
    ca = step.get("codeAction", {})
    description = ca.get("description", "")

    file_path = ""
    action_result = ca.get("actionResult", {})
    edit = action_result.get("edit", {})
    if edit.get("absoluteUri"):
        file_path = edit["absoluteUri"]
    elif ca.get("actionSpec", {}).get("createFile", {}).get("path"):
        file_path = ca["actionSpec"]["createFile"]["path"]

    summary = f"[Code Edit] {file_path}" if file_path else "[Code Edit]"
    if description:
        summary += f"\n{description}"

    msg = {"role": "tool", "tool_name": "code_edit", "content": summary}
    if file_path:
        msg["file_path"] = file_path

    if include_full:
        diff = edit.get("diff")
        if diff:
            msg["diff"] = _normalize_diff(diff)
        artifact = ca.get("artifactMetadata", {})
        if artifact.get("summary"):
            msg["artifact_summary"] = artifact["summary"]
        if artifact.get("artifactType"):
            msg["artifact_type"] = artifact["artifactType"]
        if ca.get("isArtifactFile"):
            msg["is_artifact"] = True

    return msg


def _parse_run_command(
    step: dict, include_thinking: bool, include_full: bool
) -> Optional[dict]:
    rc = step.get("runCommand", {})
    command = rc.get("commandLine", rc.get("command", ""))
    if not command:
        return None

    msg = {"role": "tool", "tool_name": "run_command", "content": command}
    if include_thinking:
        cwd = rc.get("cwd")
        if cwd:
            msg["cwd"] = cwd
        exit_code = rc.get("exitCode")
        if exit_code is not None:
            msg["exit_code"] = exit_code

    if include_full:
        output = rc.get("combinedOutput", {}).get("full")
        if output:
            msg["output"] = output
    return msg


def _parse_view_file(step: dict, include_thinking: bool) -> Optional[dict]:
    vf = step.get("viewFile", {})
    path = vf.get("absolutePathUri", vf.get("filePath", vf.get("path", "")))
    if not path:
        return None

    msg = {"role": "tool", "tool_name": "view_file", "content": path}
    if include_thinking:
        num_lines = vf.get("numLines")
        num_bytes = vf.get("numBytes")
        if num_lines:
            msg["num_lines"] = num_lines
        if num_bytes:
            msg["num_bytes"] = num_bytes
    return msg


def _parse_search_web(step: dict, include_full: bool) -> Optional[dict]:
    sw = step.get("searchWeb", {})
    query = sw.get("query", "")

    msg = {
        "role": "tool",
        "tool_name": "search_web",
        "content": query or "[Web Search]",
    }
    if include_full:
        summary = sw.get("summary")
        if summary:
            msg["search_summary"] = summary
        provider = sw.get("thirdPartyConfig", {}).get("provider")
        if provider:
            msg["search_provider"] = provider
    return msg
