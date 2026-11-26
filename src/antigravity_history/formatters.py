"""
Formatted output — Markdown / JSON / Index.
"""

import json
import os
import re
from datetime import datetime
from typing import Any


def format_markdown(
    title: str,
    cascade_id: str,
    metadata: dict,
    messages: list[dict],
) -> str:
    """Format a conversation as a Markdown string."""
    lines = [
        f"# {title}",
        "",
        f"- **Cascade ID**: `{cascade_id}`",
        f"- **Steps**: {metadata.get('stepCount', '?')}",
        f"- **Status**: {metadata.get('status', '?')}",
        f"- **Created**: {metadata.get('createdTime', '?')}",
        f"- **Last Modified**: {metadata.get('lastModifiedTime', '?')}",
        f"- **Last User Input**: {metadata.get('lastUserInputTime', '?')}",
    ]

    parent_id = metadata.get("parentConversationId")
    if parent_id:
        depth = metadata.get("nestingDepth", 1)
        lines.append(f"- **Parent Conversation**: `{parent_id}` (depth {depth})")

    workspaces = metadata.get("workspaces", [])
    if not workspaces:
        workspaces = metadata.get("trajectoryMetadata", {}).get("workspaces", [])
    if workspaces:
        ws_uris = [
            w.get("workspaceFolderAbsoluteUri", "")
            for w in workspaces
            if w.get("workspaceFolderAbsoluteUri")
        ]
        if ws_uris:
            lines.append(f"- **Workspace**: {', '.join(ws_uris)}")

    lines.extend([
        f"- **Exported**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
    ])

    for msg in messages:
        lines.extend(_format_message_md(msg))

    return "\n".join(lines)


def _format_message_md(msg: dict) -> list[str]:
    """Format a single message as Markdown lines."""
    role = msg.get("role", "")
    content = msg.get("content", "")
    timestamp = msg.get("timestamp", "")
    ts_suffix = f"  `{timestamp[:19]}`" if timestamp else ""

    lines: list[str] = []

    if role == "user":
        lines.append(f"## 🧑 User{ts_suffix}")
        lines.append(content)
        lines.append("")

    elif role == "assistant":
        lines.append(f"## 🤖 Assistant{ts_suffix}")
        thinking = msg.get("thinking")
        if thinking:
            lines.append("<details><summary>💭 Thinking</summary>")
            lines.append("")
            lines.append(thinking)
            lines.append("")
            lines.append("</details>")
            lines.append("")
        if content:
            lines.append(content)
        extras = []
        if msg.get("model"):
            extras.append(f"Model: `{msg['model']}`")
        if msg.get("stop_reason"):
            extras.append(f"Stop: `{msg['stop_reason']}`")
        if msg.get("thinking_duration"):
            extras.append(f"Think: `{msg['thinking_duration']}`")
        tokens = msg.get("tokens")
        if isinstance(tokens, dict):
            tok_parts = [f"{k}={v}" for k, v in tokens.items()]
            if tok_parts:
                extras.append(f"Tokens: `{', '.join(tok_parts)}`")
        if extras:
            lines.append("")
            lines.append(f"*{' | '.join(extras)}*")
        lines.append("")

    elif role == "system":
        lines.append(f"> ⚙️ **System**{ts_suffix}")
        for line in content.splitlines():
            lines.append(f"> {line}")
        lines.append("")

    elif role == "tool":
        tool_name = msg.get("tool_name", "unknown")
        lines.append(f"### 🔧 Tool: `{tool_name}`{ts_suffix}")

        if tool_name == "code_edit":
            lines.append(content)
            diff = msg.get("diff")
            if diff:
                lines.append("")
                lines.append("```diff")
                if len(diff) > 3000:
                    lines.append(diff[:3000])
                    lines.append(f"... (truncated, {len(diff)} chars total)")
                else:
                    lines.append(diff)
                lines.append("```")

        elif tool_name == "run_command" and "output" in msg:
            cwd = msg.get("cwd", "")
            exit_code = msg.get("exit_code")
            cwd_info = f" (in `{cwd}`)" if cwd else ""
            exit_info = f" -> exit {exit_code}" if exit_code is not None else ""
            lines.append("```bash")
            lines.append(content)
            lines.append("```")
            if cwd_info or exit_info:
                lines.append(f"*{cwd_info}{exit_info}*")
            output = msg.get("output")
            if output:
                lines.append("")
                lines.append("<details><summary>📤 Output</summary>")
                lines.append("")
                lines.append("```")
                if len(output) > 5000:
                    lines.append(output[:5000])
                    lines.append(f"... (truncated, {len(output)} chars total)")
                else:
                    lines.append(output)
                lines.append("```")
                lines.append("")
                lines.append("</details>")

        elif tool_name == "search_web":
            lines.append(f"Query: {content}")
            search_summary = msg.get("search_summary")
            if search_summary:
                lines.append("")
                lines.append("<details><summary>🔍 Search Results</summary>")
                lines.append("")
                lines.append(search_summary)
                lines.append("")
                lines.append("</details>")

        elif tool_name == "view_file" and (msg.get("num_lines") or msg.get("num_bytes")):
            num_lines = msg.get("num_lines")
            num_bytes = msg.get("num_bytes")
            parts = []
            if num_lines:
                parts.append(f"{num_lines} lines")
            if num_bytes:
                parts.append(f"{num_bytes} bytes")
            lines.append(f"`{content}` ({', '.join(parts)})")

        else:
            if content:
                lines.append(f"`{content[:500]}`")
            tool_args = msg.get("tool_args")
            if tool_args:
                args_str = json.dumps(tool_args, ensure_ascii=False, indent=2)
                if len(args_str) > 3000:
                    args_str = args_str[:3000] + "\n... (truncated)"
                lines.append("")
                lines.append("<details><summary>📋 Arguments</summary>")
                lines.append("")
                lines.append("```json")
                lines.append(args_str)
                lines.append("```")
                lines.append("")
                lines.append("</details>")
            output = msg.get("output")
            if output:
                if len(output) > 5000:
                    output = output[:5000] + f"\n... (truncated, {len(output)} chars total)"
                lines.append("")
                lines.append("<details><summary>📤 Output</summary>")
                lines.append("")
                lines.append("```")
                lines.append(output)
                lines.append("```")
                lines.append("")
                lines.append("</details>")

        lines.append("")

    return lines


def format_index_markdown(entries: list[dict[str, Any]]) -> str:
    """Format an _index.md overview table of all exported conversations."""
    lines = [
        "# Antigravity Conversations Index",
        "",
        f"- **Exported**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **Total conversations**: {len(entries)}",
        "",
        "| # | Last Modified | Steps | Messages | Title | Workspace | ID |",
        "|---:|---|---:|---:|---|---|---|",
    ]
    for i, item in enumerate(entries, 1):
        mod = (item.get("last_modified_time") or "?")[:19].replace("T", " ")
        steps = item.get("step_count", 0)
        msgs = item.get("message_count", 0)
        title = (item.get("title") or "Untitled").replace("|", "\\|")
        filename = item.get("filename", "")
        title_cell = f"[{title}]({filename})" if filename else title
        if item.get("parent_conversation_id"):
            title_cell = f"↳ {title_cell}"
        ws = ", ".join(item.get("workspaces", []))
        ws = ws.replace("file:///", "").replace("%3A", ":").replace("%20", " ")
        if len(ws) > 40:
            ws = "..." + ws[-37:]
        cid = item.get("cascade_id", "")[:8]
        lines.append(f"| {i} | {mod} | {steps} | {msgs} | {title_cell} | {ws} | `{cid}` |")
    lines.append("")
    return "\n".join(lines)


def format_json(conversations: list[dict]) -> str:
    """Format all conversations as a JSON string."""
    return json.dumps(conversations, indent=2, ensure_ascii=False)


def build_conversation_record(
    cascade_id: str,
    title: str,
    metadata: dict,
    messages: list[dict],
) -> dict:
    """Build a JSON record for a single conversation."""
    record: dict[str, Any] = {
        "cascade_id": cascade_id,
        "title": title,
        "step_count": metadata.get("stepCount", 0),
        "created_time": metadata.get("createdTime", ""),
        "last_modified_time": metadata.get("lastModifiedTime", ""),
        "messages": messages,
    }
    if metadata.get("parentConversationId"):
        record["parent_conversation_id"] = metadata["parentConversationId"]
        record["nesting_depth"] = metadata.get("nestingDepth", 1)
    workspaces = metadata.get("workspaces", [])
    if workspaces:
        ws_uris = [
            w.get("workspaceFolderAbsoluteUri", "")
            for w in workspaces
            if w.get("workspaceFolderAbsoluteUri")
        ]
        if ws_uris:
            record["workspaces"] = ws_uris
    return record


def safe_filename(title: str, max_len: int = 60, fallback: str = "conversation") -> str:
    """Convert a title to a safe filename."""
    cleaned = re.sub(r"[^\w\s\-]", "_", title)[:max_len].strip(" _.")
    return cleaned or fallback


def write_conversation(
    content: str,
    title: str,
    output_dir: str,
    extension: str = ".md",
    fallback_name: str = "conversation",
) -> str:
    """Write formatted content to a file, return the file path."""
    base = safe_filename(title, fallback=fallback_name)
    filepath = os.path.join(output_dir, base + extension)
    counter = 2
    while os.path.exists(filepath):
        filepath = os.path.join(output_dir, f"{base}_{counter}{extension}")
        counter += 1
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath
