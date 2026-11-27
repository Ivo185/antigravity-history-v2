"""
Formatted output — Markdown / JSON / Index.
"""

import json
import os
import re
from datetime import datetime
from typing import Any


def _format_collapsible(
    title: str,
    body_lines: list[str],
    callout_type: str = "note",
    md_style: str = "obsidian",
) -> list[str]:
    """Render a collapsible section (Obsidian foldable callout or HTML <details>)."""
    if md_style == "html":
        return [f"<details><summary>{title}</summary>", ""] + body_lines + ["", "</details>"]

    lines = [f"> [!{callout_type}]- {title}"]
    for line in body_lines:
        lines.append(">" if line == "" else f"> {line}")
    return lines


def format_markdown(
    title: str,
    cascade_id: str,
    metadata: dict,
    messages: list[dict],
    md_style: str = "obsidian",
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
        lines.extend(_format_message_md(msg, md_style=md_style))

    return "\n".join(lines)


def _format_message_md(msg: dict, md_style: str = "obsidian") -> list[str]:
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
            lines.extend(
                _format_collapsible(
                    "💭 Thinking",
                    thinking.splitlines(),
                    callout_type="abstract",
                    md_style=md_style,
                )
            )
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
        lines.append(f"> [!warning]- ⚙️ System{ts_suffix}" if md_style == "obsidian" else f"> ⚙️ **System**{ts_suffix}")
        for line in content.splitlines():
            lines.append(">" if line == "" else f"> {line}")
        lines.append("")

    elif role == "tool":
        tool_name = msg.get("tool_name", "unknown")
        lines.append(f"### 🔧 Tool: `{tool_name}`{ts_suffix}")

        if tool_name == "code_edit":
            lines.append(content)
            diff = msg.get("diff")
            if diff:
                lines.append("")
                diff_body = diff[:3000] + (f"\n... (truncated, {len(diff)} chars total)" if len(diff) > 3000 else "")
                lines.extend(
                    _format_collapsible(
                        "📝 Diff",
                        ["```diff", *diff_body.splitlines(), "```"],
                        callout_type="info",
                        md_style=md_style,
                    )
                )

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
                out_body = output[:5000] + (f"\n... (truncated, {len(output)} chars total)" if len(output) > 5000 else "")
                lines.extend(
                    _format_collapsible(
                        "📤 Output",
                        ["```", *out_body.splitlines(), "```"],
                        callout_type="example",
                        md_style=md_style,
                    )
                )

        elif tool_name == "search_web":
            lines.append(f"Query: {content}")
            search_summary = msg.get("search_summary")
            if search_summary:
                lines.append("")
                lines.extend(
                    _format_collapsible(
                        "🔍 Search Results",
                        search_summary.splitlines(),
                        callout_type="info",
                        md_style=md_style,
                    )
                )

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
                lines.extend(
                    _format_collapsible(
                        "📋 Arguments",
                        ["```json", *args_str.splitlines(), "```"],
                        callout_type="info",
                        md_style=md_style,
                    )
                )
            output = msg.get("output")
            if output:
                if len(output) > 5000:
                    output = output[:5000] + f"\n... (truncated, {len(output)} chars total)"
                lines.append("")
                lines.extend(
                    _format_collapsible(
                        "📤 Output",
                        ["```", *output.splitlines(), "```"],
                        callout_type="example",
                        md_style=md_style,
                    )
                )

        lines.append("")

    return lines


def format_index_markdown(entries: list[dict[str, Any]]) -> str:
    """Format an _index.md overview table of all exported conversations, grouped by project."""
    # Group entries by project
    from collections import defaultdict

    by_project: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for i, item in enumerate(entries, 1):
        project = item.get("project", "")
        by_project[project].append((i, item))

    # Sort project groups: named projects first (alpha), then no-project at the end
    sorted_projects = sorted(
        by_project.keys(), key=lambda p: (p == "", p.lower())
    )

    project_count = sum(1 for p in sorted_projects if p)

    lines = [
        "# Antigravity Conversations Index",
        "",
        f"- **Exported**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **Total conversations**: {len(entries)}",
        f"- **Projects**: {project_count}",
        "",
    ]

    for project in sorted_projects:
        items = by_project[project]
        if project:
            lines.append(f"## 📁 {project}")
        else:
            lines.append("## 💬 Conversations (no project)")
        lines.append("")
        lines.append("| # | Last Modified | Steps | Messages | Title | ID |")
        lines.append("|---:|---|---:|---:|---|---|")

        for i, item in items:
            mod = (item.get("last_modified_time") or "?")[:19].replace("T", " ")
            steps = item.get("step_count", 0)
            msgs = item.get("message_count", 0)
            title = (item.get("title") or "Untitled").replace("|", "\\|")
            filename = item.get("filename", "")
            title_cell = f"[{title}]({filename})" if filename else title
            if item.get("parent_conversation_id"):
                title_cell = f"↳ {title_cell}"
            cid = item.get("cascade_id", "")[:8]
            lines.append(f"| {i} | {mod} | {steps} | {msgs} | {title_cell} | `{cid}` |")

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


def extract_project_name(metadata: dict) -> str:
    """Extract project folder name from workspace URIs.

    Returns the last path segment of the first workspace URI,
    or "" if no workspace is set (plain chat).
    """
    from urllib.parse import unquote, urlparse

    workspaces = metadata.get("workspaces", [])
    if not workspaces:
        return ""
    uri = ""
    if isinstance(workspaces[0], dict):
        uri = workspaces[0].get("workspaceFolderAbsoluteUri", "")
    elif isinstance(workspaces[0], str):
        uri = workspaces[0]
    if not uri:
        return ""
    # Decode percent-encoding and get last path segment
    decoded = unquote(uri)
    # Remove file:/// prefix and trailing slashes
    path = decoded.replace("file:///", "").rstrip("/")
    # Last segment is the project name
    name = path.rsplit("/", 1)[-1] if "/" in path else path
    # Clean for use as folder name
    cleaned = re.sub(r'[<>:"|?*]', "_", name).strip(" _.")
    return cleaned or ""


def write_conversation(
    content: str,
    title: str,
    output_dir: str,
    extension: str = ".md",
    fallback_name: str = "conversation",
    subfolder: str = "",
) -> str:
    """Write formatted content to a file, return the file path.

    If subfolder is set, the file is placed in output_dir/subfolder/.
    """
    target_dir = os.path.join(output_dir, subfolder) if subfolder else output_dir
    os.makedirs(target_dir, exist_ok=True)
    base = safe_filename(title, fallback=fallback_name)
    filepath = os.path.join(target_dir, base + extension)
    counter = 2
    while os.path.exists(filepath):
        filepath = os.path.join(target_dir, f"{base}_{counter}{extension}")
        counter += 1
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath
