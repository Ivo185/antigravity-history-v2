# antigravity-history (v0.3.0 — Antigravity 1.x & 2.x)

Export and recover your Google Antigravity AI conversations to Markdown and JSON.

Updated for **Antigravity 2.x** (tested on `2.35.2`):
- **Offline 2.x Reader**: Automatically reads `~/.gemini/antigravity/conversation_summaries.db` and `brain/<id>/.system_generated/logs/transcript_full.jsonl` when LanguageServer uses stdin host-bridge auth (`--read_host_bridge_token_from_stdin`).
- **Full Fidelity Export**: Preserves `<USER_REQUEST>` prompts, AI `thinking` chains, `tool_calls` + arguments, tool outputs, token counts, subagent relationships, and generates `_index.md`.
- **1.x Compatibility**: Retains LanguageServer discovery/API support (`--source online --port ... --token ...`).

## Quick Start

```bash
pip install -e .
aghistory info
aghistory list
aghistory export --full -o ./antigravity_export
```

## License

Apache-2.0 (based on [neo1027144-creator/antigravity-history](https://github.com/neo1027144-creator/antigravity-history)).
