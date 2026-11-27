"""
CLI entry point — aghistory command.

Supports both:
- Antigravity 2.x offline local storage (~/.gemini/antigravity)
- Antigravity 1.x LanguageServer API (when --port/--token or --csrf_token is available)

Subcommands:
  export   Export conversations to Markdown / JSON
  list     List all conversations
  recover  Detect/recover unindexed conversations
  info     Show local storage & LanguageServer status
"""

import json as json_mod
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Optional

try:
    import typer
    from rich.console import Console
    from rich.progress import Progress, track
    from rich.table import Table
except ImportError:
    print("ERROR: Missing dependencies. Run: pip install antigravity-history")
    sys.exit(1)

from antigravity_history import __version__
from antigravity_history.api import (
    get_all_trajectories_merged,
    get_trajectory_steps,
)
from antigravity_history.discovery import (
    discover_language_servers,
    find_all_endpoints,
)
from antigravity_history.formatters import (
    build_conversation_record,
    extract_project_name,
    format_index_markdown,
    format_json,
    format_markdown,
    write_conversation,
)
from antigravity_history.offline import (
    get_default_data_dir,
    get_offline_info,
    get_transcript_path,
    load_offline_summaries,
    load_transcript_entries,
)
from antigravity_history.parser import (
    FieldLevel,
    parse_steps,
    parse_transcript_entries,
)

app = typer.Typer(
    name="aghistory",
    help="Export and recover your Antigravity (1.x & 2.x) conversations.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


def _resolve_mode(
    source: str,
    port: Optional[int],
    token: Optional[str],
    data_dir: Optional[str],
    log: Optional[Console] = None,
) -> tuple[str, list[dict], Path]:
    """Resolve whether to use 'online' (1.x LS API) or 'offline' (2.x local files)."""
    log = log or console
    data_path = Path(data_dir).expanduser() if data_dir else get_default_data_dir()
    src = source.lower().strip()

    if src not in ("auto", "offline", "online"):
        err_console.print(f"[red]Invalid --source '{source}'. Use auto, offline, or online.[/red]")
        raise typer.Exit(1)

    if src == "offline":
        if not data_path.is_dir():
            err_console.print(f"[red]Data directory not found: {data_path}[/red]")
            raise typer.Exit(1)
        return "offline", [], data_path

    if port and token:
        log.print(f"[dim]Using manual LanguageServer config: port={port}[/dim]")
        return "online", [{"port": port, "csrf": token, "pid": 0}], data_path

    # Try online discovery only if a server has a CLI csrf_token (1.x) or --source online
    servers = discover_language_servers()
    servers_with_csrf = [s for s in servers if s.get("csrf")]

    if src == "online":
        if not servers:
            err_console.print(
                "[bold red]No Antigravity LanguageServer process found.[/bold red]"
            )
            raise typer.Exit(1)
        endpoints = find_all_endpoints(servers)
        if not endpoints:
            err_console.print(
                "[bold red]Cannot connect to any LanguageServer port.[/bold red]\n"
                "[yellow]Antigravity 2.x passes its CSRF token via stdin. Use --source offline (or auto).[/yellow]"
            )
            raise typer.Exit(1)
        return "online", endpoints, data_path

    # auto mode
    if servers_with_csrf:
        endpoints = find_all_endpoints(servers_with_csrf)
        if endpoints:
            log.print(f"[dim]Connected to {len(endpoints)} LanguageServer endpoint(s)[/dim]")
            return "online", endpoints, data_path

    if data_path.is_dir():
        log.print(f"[dim]Using offline 2.x storage: {data_path}[/dim]")
        return "offline", [], data_path

    err_console.print(
        "[bold red]No LanguageServer API and no local Antigravity data directory found.[/bold red]"
    )
    raise typer.Exit(1)


def _filter_summaries(
    summaries: dict[str, dict],
    today: bool,
    ids: Optional[list[str]],
) -> dict[str, dict]:
    """Filter summaries by --id (exact or prefix) and --today."""
    if ids:
        matched: dict[str, dict] = {}
        for wanted in ids:
            if wanted in summaries:
                matched[wanted] = summaries[wanted]
            else:
                for cid, info in summaries.items():
                    if cid.startswith(wanted):
                        matched[cid] = info
        summaries = matched

    if today:
        today_str = date.today().isoformat()
        summaries = {
            k: v
            for k, v in summaries.items()
            if v.get("lastModifiedTime", "").startswith(today_str)
        }
    return summaries


# ════════════════════════════════
# export subcommand
# ════════════════════════════════


@app.command()
def export(
    output: str = typer.Option(
        "./antigravity_export", "-o", "--output", help="Output directory"
    ),
    format: str = typer.Option(
        "all", "-f", "--format", help="Output format: md / json / all"
    ),
    today: bool = typer.Option(False, "--today", help="Export only today's conversations"),
    ids: Optional[list[str]] = typer.Option(None, "--id", help="Export specific cascade ID(s)"),
    thinking: bool = typer.Option(False, "--thinking", help="Include AI thinking process"),
    full: bool = typer.Option(
        False, "--full", help="Include all extended fields (thinking+args+outputs)"
    ),
    no_subagents: bool = typer.Option(
        False, "--no-subagents", help="Exclude subagent conversations"
    ),
    md_style: str = typer.Option(
        "obsidian", "--md-style", help="Collapsible style in Markdown: obsidian / html"
    ),
    source: str = typer.Option(
        "auto", "--source", help="Data source: auto / offline / online"
    ),
    data_dir: Optional[str] = typer.Option(
        None, "--data-dir", help="Antigravity data directory (default: ~/.gemini/antigravity)"
    ),
    port: Optional[int] = typer.Option(None, "--port", help="Manually specify port"),
    token: Optional[str] = typer.Option(None, "--token", help="Manually specify CSRF token"),
):
    """Export conversations to Markdown / JSON format."""
    if full:
        level = FieldLevel.FULL
    elif thinking:
        level = FieldLevel.THINKING
    else:
        level = FieldLevel.DEFAULT

    console.print(f"\n[bold]Antigravity History Export[/bold] v{__version__}")
    console.print(f"[dim]Field level: {level}[/dim]\n")

    mode, endpoints, data_path = _resolve_mode(source, port, token, data_dir)
    failed_eps: list[tuple] = []
    cascade_ep: dict[str, dict] = {}
    default_ep = endpoints[0] if endpoints else {}

    if mode == "offline":
        summaries = load_offline_summaries(
            data_path, include_subagents=not no_subagents
        )
        console.print(f"[dim]  Local conversations found: {len(summaries)}[/dim]")
    else:
        console.print("[dim]Fetching conversation list (scanning all workspaces)...[/dim]")
        summaries, cascade_ep, failed_eps = get_all_trajectories_merged(endpoints)
        console.print(f"[dim]  Indexed conversations: {len(summaries)}[/dim]")
        if failed_eps:
            console.print(f"[dim]  [yellow]LS endpoints failed: {len(failed_eps)}[/yellow][/dim]")

        conv_dir = data_path / "conversations"
        if conv_dir.is_dir():
            for pb_file in conv_dir.glob("*.pb"):
                cid = pb_file.stem
                if cid not in summaries:
                    summaries[cid] = {"summary": f"[unindexed] {cid[:8]}...", "stepCount": 1000}
                    cascade_ep[cid] = {"port": default_ep["port"], "csrf": default_ep["csrf"]}

        if ids:
            for cid in ids:
                if cid not in summaries:
                    summaries[cid] = {"summary": f"[on-demand] {cid[:8]}...", "stepCount": 1000}
                    cascade_ep[cid] = {"port": default_ep["port"], "csrf": default_ep["csrf"]}

    summaries = _filter_summaries(summaries, today=today, ids=ids)
    if today:
        console.print(f"[dim]  Today's conversations: {len(summaries)}[/dim]")

    if not summaries:
        console.print("[yellow]No conversations match the criteria.[/yellow]")
        raise typer.Exit(0)

    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    sorted_items = sorted(
        summaries.items(),
        key=lambda x: x[1].get("lastModifiedTime", ""),
        reverse=True,
    )

    def _fetch_one(cascade_id: str, info: dict):
        title = info.get("summary") or f"Untitled {cascade_id[:8]}"
        if mode == "offline":
            tpath = get_transcript_path(data_path, cascade_id)
            if not tpath:
                return cascade_id, title, info, []
            entries = load_transcript_entries(tpath)
            messages = parse_transcript_entries(entries, level)
            return cascade_id, title, info, messages
        else:
            step_count = info.get("stepCount", 1000)
            ep = cascade_ep.get(
                cascade_id,
                {"port": default_ep["port"], "csrf": default_ep["csrf"]},
            )
            steps = get_trajectory_steps(ep["port"], ep["csrf"], cascade_id, step_count)
            messages = parse_steps(steps, level)
            return cascade_id, title, info, messages

    all_records = []
    index_entries = []
    exported_count = 0
    failed_list = []
    exported_list = []

    with Progress() as progress:
        task = progress.add_task("Exporting...", total=len(sorted_items))
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_fetch_one, cid, info): cid
                for cid, info in sorted_items
            }
            for future in as_completed(futures):
                cid = futures[future]
                try:
                    cascade_id, title, info, messages = future.result()
                except Exception as e:
                    err_console.print(f"[red]Skipped {cid[:8]}...: {e}[/red]")
                    failed_list.append((cid, str(e)))
                    progress.advance(task)
                    continue

                project = extract_project_name(info)

                written_filename = ""
                if format in ("md", "all"):
                    md_content = format_markdown(
                        title, cascade_id, info, messages, md_style=md_style
                    )
                    written_path = write_conversation(
                        md_content,
                        title,
                        str(output_dir),
                        ".md",
                        fallback_name=cascade_id[:8],
                        subfolder=project,
                    )
                    # Relative path from output_dir for index links
                    written_filename = os.path.relpath(written_path, str(output_dir))

                record = build_conversation_record(cascade_id, title, info, messages)
                if format in ("json", "all"):
                    record["project"] = project or "(no project)"
                    all_records.append(record)

                index_entries.append({
                    "cascade_id": cascade_id,
                    "title": title,
                    "step_count": info.get("stepCount", 0),
                    "message_count": len(messages),
                    "last_modified_time": info.get("lastModifiedTime", ""),
                    "parent_conversation_id": info.get("parentConversationId", ""),
                    "workspaces": record.get("workspaces", []),
                    "project": project,
                    "filename": written_filename,
                })

                exported_count += 1
                exported_list.append((cascade_id, title, len(messages)))
                progress.advance(task)

    if format in ("md", "all") and index_entries:
        index_entries.sort(key=lambda x: x.get("last_modified_time", ""), reverse=True)
        (output_dir / "_index.md").write_text(
            format_index_markdown(index_entries), encoding="utf-8"
        )

    if format in ("json", "all") and all_records:
        all_records.sort(key=lambda x: x.get("last_modified_time", ""), reverse=True)
        (output_dir / "conversations_export.json").write_text(
            format_json(all_records), encoding="utf-8"
        )

    _write_export_report(output_dir, exported_list, failed_list, failed_eps)

    total_msgs = sum(len(r["messages"]) for r in all_records) if all_records else sum(
        e["message_count"] for e in index_entries
    )

    console.print("\n[bold green]Export complete![/bold green]")
    console.print(f"  Conversations: {exported_count}")
    if failed_list:
        console.print(f"  [red]Failed: {len(failed_list)}[/red]")
    console.print(f"  Messages: {total_msgs}")
    console.print(f"  Report: {output_dir.absolute() / 'export_report.txt'}")
    console.print(f"  Output directory: {output_dir.absolute()}")


def _write_export_report(
    output_dir: Path,
    exported: list[tuple],
    failed: list[tuple],
    failed_endpoints: list[tuple] = None,
):
    """Write export_report.txt summarizing the export results."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(exported) + len(failed)
    sep = "=" * 60
    lines = [
        sep,
        "  EXPORT REPORT",
        sep,
        "",
        f"  Time:      {now}",
        f"  Total:     {total}",
        f"  Exported:  {len(exported)}",
        f"  Failed:    {len(failed)}",
        "",
    ]

    if failed_endpoints:
        lines.extend([
            "-" * 60,
            "  LS ENDPOINT FAILURES",
            "-" * 60,
        ])
        for i, (port, err) in enumerate(failed_endpoints, 1):
            lines.append(f"  {i}. Port {port} - {err}")
        lines.append("")

    if failed:
        lines.extend([
            "-" * 60,
            "  FAILED CONVERSATIONS",
            "-" * 60,
        ])
        for i, (cid, err) in enumerate(failed, 1):
            lines.append(f"  {i}. {cid}")
            lines.append(f"     Error: {err}")
        lines.append("")

    if exported:
        lines.extend([
            "-" * 60,
            f"  EXPORTED CONVERSATIONS ({len(exported)})",
            "-" * 60,
        ])
        for i, (cid, title, msg_count) in enumerate(exported, 1):
            lines.append(f"  {i:3d}. {title[:50]}")
            lines.append(f"       Messages: {msg_count}  |  ID: {cid[:8]}...")
        lines.append("")

    lines.append(sep)
    (output_dir / "export_report.txt").write_text("\n".join(lines), encoding="utf-8")


# ════════════════════════════════
# list subcommand
# ════════════════════════════════


@app.command(name="list")
def list_conversations(
    limit: int = typer.Option(50, "-n", "--limit", help="Max number of conversations to show"),
    today: bool = typer.Option(False, "--today", help="Show only today's conversations"),
    no_subagents: bool = typer.Option(False, "--no-subagents", help="Hide subagent conversations"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (pipe-friendly)"),
    source: str = typer.Option("auto", "--source", help="Data source: auto / offline / online"),
    data_dir: Optional[str] = typer.Option(None, "--data-dir", help="Antigravity data directory"),
    port: Optional[int] = typer.Option(None, "--port", help="Manually specify port"),
    token: Optional[str] = typer.Option(None, "--token", help="Manually specify CSRF token"),
):
    """List all conversations."""
    out = err_console if json_output else console
    out.print("\n[bold]Antigravity Conversations[/bold]\n")

    mode, endpoints, data_path = _resolve_mode(source, port, token, data_dir, log=out)

    if mode == "offline":
        summaries = load_offline_summaries(data_path, include_subagents=not no_subagents)
    else:
        summaries, _, _ = get_all_trajectories_merged(endpoints)

    summaries = _filter_summaries(summaries, today=today, ids=None)

    sorted_items = sorted(
        summaries.items(),
        key=lambda x: x[1].get("lastModifiedTime", ""),
        reverse=True,
    )[:limit]

    if json_output:
        records = []
        for cid, info in sorted_items:
            records.append({
                "cascade_id": cid,
                "title": info.get("summary", ""),
                "step_count": info.get("stepCount", 0),
                "last_modified": info.get("lastModifiedTime", ""),
                "created": info.get("createdTime", ""),
                "parent_conversation_id": info.get("parentConversationId", ""),
            })
        print(json_mod.dumps(records, indent=2, ensure_ascii=False))
    else:
        table = Table(title=f"{len(summaries)} conversation(s) total")
        table.add_column("#", style="dim", width=4)
        table.add_column("Last Modified", width=20)
        table.add_column("Steps", justify="right", width=6)
        table.add_column("Title", max_width=52)
        table.add_column("ID", style="dim", width=10)

        for i, (cid, info) in enumerate(sorted_items):
            t = info.get("lastModifiedTime", "?")[:19].replace("T", " ")
            title = info.get("summary", "?")
            if info.get("parentConversationId"):
                title = f"↳ {title}"
            table.add_row(
                str(i + 1),
                t,
                str(info.get("stepCount", "?")),
                title[:52],
                cid[:8] + "...",
            )

        console.print(table)


# ════════════════════════════════
# recover subcommand
# ════════════════════════════════


@app.command()
def recover(
    conv_dir: str = typer.Option(
        None, "--conv-dir",
        help="Conversations directory path (default: ~/.gemini/antigravity/conversations)",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Detect only, do not recover"),
    source: str = typer.Option("auto", "--source", help="Data source: auto / offline / online"),
    data_dir: Optional[str] = typer.Option(None, "--data-dir", help="Antigravity data directory"),
    port: Optional[int] = typer.Option(None, "--port", help="Manually specify port"),
    token: Optional[str] = typer.Option(None, "--token", help="Manually specify CSRF token"),
):
    """Detect/recover unindexed conversations (.db in 2.x, .pb in 1.x)."""
    data_path = Path(data_dir).expanduser() if data_dir else get_default_data_dir()
    conv_path = Path(conv_dir).expanduser() if conv_dir else (data_path / "conversations")

    if not conv_path.is_dir():
        err_console.print(f"[red]Directory not found: {conv_path}[/red]")
        raise typer.Exit(1)

    console.print("\n[bold]Antigravity Conversation Recovery[/bold]\n")

    mode, endpoints, _ = _resolve_mode(source, port, token, str(data_path))

    if mode == "offline":
        summaries = load_offline_summaries(data_path)
        db_files = sorted(conv_path.glob("*.db"))
        pb_files = sorted(conv_path.glob("*.pb"))

        console.print(f"[dim]Indexed/discovered conversations: {len(summaries)}[/dim]")
        console.print(f"[dim].db files (2.x): {len(db_files)} | .pb files (1.x): {len(pb_files)}[/dim]\n")

        db_only = []
        missing_transcript = []
        ok_count = 0

        for db_file in db_files:
            cid = db_file.stem
            info = summaries.get(cid, {})
            has_transcript = get_transcript_path(data_path, cid) is not None
            if info.get("source") == "conv_db":
                db_only.append((cid, db_file.stat().st_size // 1024))
            elif not has_transcript:
                missing_transcript.append((cid, db_file.stat().st_size // 1024))
            else:
                ok_count += 1

        console.print(f"  [green]Healthy (with transcript): {ok_count}[/green]")
        if db_only:
            console.print(f"  [yellow]Unindexed .db (missing from summaries DB & no transcript): {len(db_only)}[/yellow]")
            for cid, kb in db_only:
                console.print(f"    - {cid} ({kb}KB)")
        if missing_transcript:
            console.print(f"  [yellow]Indexed .db without transcript*.jsonl: {len(missing_transcript)}[/yellow]")
            for cid, kb in missing_transcript:
                console.print(f"    - {cid} ({kb}KB)")
        if not db_only and not missing_transcript:
            console.print("  [green]All conversations have transcripts ready for offline export.[/green]")
        return

    # Online 1.x mode
    default_ep = endpoints[0]
    p, c = default_ep["port"], default_ep["csrf"]
    indexed, _, _ = get_all_trajectories_merged(endpoints)
    indexed_ids = set(indexed.keys())
    console.print(f"[dim]Indexed conversations: {len(indexed_ids)}[/dim]")

    pb_files = sorted(conv_path.glob("*.pb"))
    console.print(f"[dim].pb files: {len(pb_files)}[/dim]\n")

    activated = []
    failed = []
    already_indexed = []

    for pb_file in track(pb_files, description="Scanning..."):
        cascade_id = pb_file.stem
        size_kb = pb_file.stat().st_size // 1024
        if cascade_id in indexed_ids:
            already_indexed.append(cascade_id)
            continue
        if dry_run:
            console.print(f"  [yellow]Unindexed[/yellow] {cascade_id[:8]}... ({size_kb}KB)")
            continue
        result = get_trajectory_steps(p, c, cascade_id, step_count=5)
        if result:
            activated.append(cascade_id)
            console.print(f"  [green]Activated[/green] {cascade_id[:8]}... ({size_kb}KB)")
        else:
            failed.append(cascade_id)
            console.print(f"  [red]Failed[/red] {cascade_id[:8]}... ({size_kb}KB)")

    console.print(f"\n[bold]{'─' * 40}[/bold]")
    console.print(f"  Total .pb files: {len(pb_files)}")
    console.print(f"  Indexed: {len(already_indexed)}")
    if not dry_run:
        console.print(f"  [green]Newly activated: {len(activated)}[/green]")
        if failed:
            console.print(f"  [red]Failed: {len(failed)}[/red]")


# ════════════════════════════════
# info subcommand
# ════════════════════════════════


@app.command()
def info(
    source: str = typer.Option("auto", "--source", help="Data source: auto / offline / online"),
    data_dir: Optional[str] = typer.Option(None, "--data-dir", help="Antigravity data directory"),
    port: Optional[int] = typer.Option(None, "--port", help="Manually specify port"),
    token: Optional[str] = typer.Option(None, "--token", help="Manually specify CSRF token"),
):
    """Show Antigravity local storage & LanguageServer status."""
    console.print(f"\n[bold]Antigravity History[/bold] v{__version__}\n")

    data_path = Path(data_dir).expanduser() if data_dir else get_default_data_dir()
    off = get_offline_info(data_path)

    console.print("[bold]Local Storage (2.x)[/bold]")
    console.print(f"  Data directory:    {off['data_dir']} ({'exists' if off['exists'] else 'missing'})")
    console.print(f"  Summaries DB:      {'found' if off['db_exists'] else 'missing'}")
    console.print(f"  Conversations:     {off['total_conversations']} (subagents: {off['subagent_count']})")
    console.print(f"  .db files:         {off['conv_db_count']}")
    console.print(f"  Transcript logs:   {off['transcript_count']}")
    console.print(f"  Total steps:       {off['total_steps']}")
    console.print(f"  Time range:        {off['oldest']} ~ {off['newest']}")

    servers = discover_language_servers()
    console.print("\n[bold]LanguageServer Processes[/bold]")
    console.print(f"  Running instances: {len(servers)}")
    for srv in servers:
        auth_mode = "CLI --csrf_token (1.x)" if srv.get("csrf") else "stdin host-bridge token (2.x)"
        console.print(f"  PID {srv['pid']}: {auth_mode}")

    if source == "online" or (port and token):
        mode, endpoints, _ = _resolve_mode("online", port, token, str(data_path))
        summaries, _, _ = get_all_trajectories_merged(endpoints)
        console.print(f"\n  Online endpoints:  {len(endpoints)}")
        console.print(f"  Online summaries:  {len(summaries)}")


# ════════════════════════════════
# version callback
# ════════════════════════════════


def version_callback(value: bool):
    if value:
        console.print(f"antigravity-history v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-v",
        callback=version_callback,
        is_eager=True,
        help="Show version",
    ),
):
    """Export and recover your Antigravity (1.x & 2.x) conversations."""
    pass
