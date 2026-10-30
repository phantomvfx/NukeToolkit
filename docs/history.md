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

A pre-merge backup (`L:\Nuke_premerge_backup\`) was deleted on 2026-10-30
after the new loader was verified in a live Nuke 16.1 GUI session.

New remote: `https://github.com/phantomvfx/NukeToolkit` (public, branch `main`).
The three old GitHub repos were archived the same day, then deleted. Before
that, their HEADs (`d36d002`, `69deff2`, `f7fd85a`) were confirmed to be in
this history, so their full history lives on here. GitHub keeps deleted repos
restorable for 90 days (Settings → Repositories → Deleted repositories).

An old `~/.nuke/NukeComfy.bak` clone had uncommitted work. It was an early
draft of the title-marker refactor that later landed in the repo, so it was
discarded with the backup.
