# CLAUDE.md

Guidance for Claude Code in this repository.

## What this is

One repo for all Nuke work: Nuke tools and gizmos, AI and ML integrations,
the bridge that lets Claude drive a live Nuke session, and a local copy of
the Nuke docs. Formerly three repos (NukeDevSandbox, NukeComfy, NukeMCP),
merged on 2026-10-30 with their history kept. See `docs/history.md`.

```
L:\Nuke\
├── CLAUDE.md  README.md
├── init.py  menu.py      Nuke loader: ~/.nuke/init.py does nuke.pluginAddPath("L:/Nuke")
├── gizmos/               sandbox node tools (.nk Group snippets)
├── python/               sandbox Python tools + mllamas_tools menu
├── icons/
├── tools/
│   ├── comfy/            NukeComfy: Nuke <-> ComfyUI bridge          -> tools/comfy/CLAUDE.md
│   ├── mcp/              NukeMCP: MCP server + in-Nuke TCP listener  -> tools/mcp/CLAUDE.md
│   └── taptrack/         TAPTrack: DeepMind point tracking -> Tracker4 -> tools/taptrack/CLAUDE.md
├── knowledge/            Nuke docs (13/16/17) + tutorials. Gitignored, local only
├── docs/                 setup, architecture, history (for humans)
└── .claude/              agents/ and commands/ for this project
```

**Before working inside `tools/<x>/`, read that tool's `CLAUDE.md`.** Each
one has hard rules that override general habits; TAPTrack's are the strictest.

## Nuke version targeting

State the target Nuke version when starting on a tool. If none is given,
assume **16** (baseline). The tool's file header records its version, and
that header is the durable anchor. Installed builds: `L:\Apps\Nuke13.2v4`,
`L:\Apps\Nuke16.1v4`, `L:\Apps\Nuke17.0v1`.

## Knowledge base: `knowledge/`

Converted official docs, one folder per version, plus `tutorials/`. **Read
`knowledge/nukeXX/DOCS_INDEX.md` before searching**, scope searches to the
target version, and `grep` within a section. Never glob the tree (about 4,500
files per version). If a symbol is missing in the target version, check the
nearest version and flag the difference instead of silently using it. For
doc lookups, prefer the `nuke-docs` agent: it keeps the search out of the
main context.

The folder is gitignored and is the **only copy**: the `L:\Nuke1xDev` converter
projects are gone. Don't delete or bulk-rewrite it. See `knowledge/README.md`.

## Running and testing

There's no build. Tools are `.nk` files and Python that run inside Nuke.

- **Headless:** `& "L:\Apps\Nuke16.1v4\Nuke16.1.exe" -t script.py` runs a script
  with no GUI and prints to stdout. This is the standard smoke test
  (`/smoke-test`). Nuke isn't on PATH, so use the full path.
- **Live session:** if Nuke is open with the listener running, the `nuke` MCP
  server (`tools/mcp`) can run Python in it, inspect the DAG, and render a
  preview frame (`/nuke-status` checks the connection).
- **TAPTrack** has a real pytest suite. See `tools/taptrack/CLAUDE.md`.
- If you couldn't run something in Nuke, say so. Don't claim it was tested.

## Conventions

### Node tools are `.nk`, never `.gizmo`

A `.gizmo` is loaded by external reference. On a farm node with a missing,
stale or unsynced plugin path it silently breaks or renders wrong. A `.nk`
snippet (a single top-level `Group { ... }`) embeds the whole tool in the
script that imports it.

- Build as a Group in a live session, then export with `nuke.nodeCopy("path/Tool.nk")`
  (only that node selected).
- Import with `nuke.nodePaste(path)`, File > Import Script, or from a menu
  entry that calls `nodePaste`. It won't appear in the node toolbar, so say
  so in the header.
- If you find a `.gizmo` in the repo, it's stale. Convert it to `.nk`; don't
  keep both.

### File headers

Every tool starts with a short header: what it does, inputs and outputs, the
target Nuke version, how to import it, and whether it has been tested in Nuke.

### Python

- Code that runs inside Nuke uses only stdlib + `nuke` + `nukescripts`. No
  torch, jax or heavy deps. ML runs in a separate venv process (see TAPTrack).
- Nuke 16/17 run Python 3.11, Nuke 13 runs 3.7. Match syntax to the target.
- No absolute `L:/...` paths in code. Resolve them from `__file__`. Absolute
  paths in saved demo `.nk` files are expected, but update them if the repo moves.
- No backslashes in Nuke string knobs: Nuke eats them on reopen. Use `/`.
- Button callbacks run inside their Group's context. Create nodes in
  `with node.parent():`.

### Compatibility

- **`NukeComfy` is a frozen Python package name.** Saved comps embed
  `import NukeComfy.python.…` in Generate buttons. Root `init.py` aliases
  `tools/comfy` to `NukeComfy`. Never rename those imports.
- Knob names on shipped tools are an API. Renaming one breaks saved scripts.

## Git

- One repo: `L:\Nuke`, branch `main`. Large local-only data is gitignored:
  `knowledge/`, every `.venv/`, `tools/taptrack/{checkpoints,vendor,tests/footage,tests/_out}`,
  and ComfyUI scratch output.
- Don't commit weights, footage, renders or `preview.png`.
- `.claude/settings.local.json` is personal and ignored. Shared agents and
  commands in `.claude/` are committed.
