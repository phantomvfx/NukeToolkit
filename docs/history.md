# History

## 2026-10-30: three repos merged into one

| Old repo | Old local path | New path | Commits |
|---|---|---|---|
| `phantomvfx/NukeDevSandbox` | `L:\NukeDevSandbox` | repo root (`gizmos/`, `python/`, `icons/`); `taptrack/` → `tools/taptrack/`; `reference/` → `knowledge/` | 3 |
| `phantomvfx/NukeComfy` | `L:\NukeComfy` (+ junction `~/.nuke/NukeComfy`) | `tools/comfy/` | 10 |
| `phantomvfx/NukeMCP` | `L:\NukeMCP` | `tools/mcp/` | 6 |

History was kept. NukeComfy and NukeMCP were rewritten into their subfolders
(`git filter-branch --index-filter`) and merged with
`--allow-unrelated-histories`. Use `git log --follow -- <path>` to trace a file
back through the move.

Also changed in the merge:

- `~/.nuke/init.py` now has a single `nuke.pluginAddPath('L:/Nuke')`. The
  `~/.nuke/NukeComfy` junction and the `nuke_mcp_listener.py` copy in
  `~/.nuke` were removed, and the mllamas and listener blocks in
  `~/.nuke/menu.py` moved into the repo loader.
- `tools/mcp/menu.py` no longer duplicates the Deadline (Thinkbox) menu.
- The `nuke` MCP server is registered at the new path.
- Hardcoded `L:/NukeDevSandbox` and `L:/NukeMCP` paths were updated.

Pre-merge backups of the three `.git` folders, the `.gitignore` files, the
`~/.nuke` startup files and the Claude settings are in `L:\Nuke_premerge_backup\`.

The old GitHub repos are still up. Archive them once the monorepo is pushed
and verified.
