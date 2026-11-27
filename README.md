# antigravity-history (v0.3.0 — Antigravity 1.x & 2.x)

Export and recover your Google Antigravity AI conversations to Markdown (Obsidian-ready) and JSON.

Updated for **Antigravity 2.x** (tested on `2.35.2`):
- **Project Folder Organization**: Automatically places conversation files into subfolders named after their project (e.g. `Android backup app/`, `20-20-20 app/`, `DiskCleanup/`), while generic non-project chats stay directly in the root export directory. `_index.md` also groups conversations by project.
- **Offline 2.x Reader**: Automatically reads `~/.gemini/antigravity/conversation_summaries.db` and `brain/<id>/.system_generated/logs/transcript_full.jsonl` when LanguageServer uses stdin host-bridge auth (`--read_host_bridge_token_from_stdin`).
- **Obsidian Foldable Callouts**: Thinking chains, tool arguments, and outputs use native Obsidian collapsible callouts (`> [!abstract]- 💭 Thinking`, `> [!info]- 📋 Arguments`, `> [!example]- 📤 Output`) so they collapse/expand cleanly with the triangle in Obsidian (`--md-style html` is also available for `<details>`).
- **Full Fidelity Export**: Preserves `<USER_REQUEST>` prompts, AI `thinking` chains, `tool_calls` + arguments, tool outputs, token counts, subagent relationships, and generates `_index.md`.
- **1.x Compatibility**: Retains LanguageServer discovery/API support (`--source online --port ... --token ...`).

---

## Installation

```powershell
# From GitHub:
pip install git+https://github.com/Ivo185/antigravity-history-v2.git
```

```powershell
# Or local editable install (recommended if you pull updates with `git pull`):
git clone https://github.com/Ivo185/antigravity-history-v2.git
cd antigravity-history-v2
pip install -e .
```

To update later:
- If installed with `pip install -e .`: just run `git pull` inside the folder.
- If installed directly via `pip install git+...`: run `pip install --upgrade --force-reinstall git+https://github.com/Ivo185/antigravity-history-v2.git`.

---

## Usage Commands

### Check Status (`info`)
```powershell
aghistory info
```

### List Conversations (`list`)
```powershell
aghistory list                  # last 50 conversations
aghistory list -n 20            # last 20 conversations
aghistory list --today          # only today's conversations
aghistory list --no-subagents   # hide subagent conversations
aghistory list --json           # JSON output
```

### Export Conversations (`export`)
```powershell
# Full backup (thinking, tool calls, arguments, outputs) as Markdown + JSON + _index.md:
aghistory export --full -o "D:\Backup\AG_export_$(Get-Date -Format yyyy-MM-dd)"

# Markdown only with AI thinking, excluding subagents:
aghistory export --thinking --no-subagents -f md -o "D:\Backup\AG_md"

# Today's conversations only with full details:
aghistory export --today --full -o "D:\Backup\AG_today"

# Specific conversation by ID (full UUID or 8-char prefix):
aghistory export --id b04fe22f --full -o "D:\Backup\AG_one"

# Use HTML <details> instead of Obsidian callouts:
aghistory export --full --md-style html -o "D:\Backup\AG_html"

# Export from a copied/archived .gemini/antigravity folder:
aghistory export --full --data-dir "D:\Backup\Antigravity_raw\C__Users_Ivail_.gemini_antigravity" -o "D:\Backup\AG_from_copy"
```

### Check Integrity / Recover (`recover`)
```powershell
aghistory recover --dry-run
```

---

## License

Apache-2.0 (based on [neo1027144-creator/antigravity-history](https://github.com/neo1027144-creator/antigravity-history)).
