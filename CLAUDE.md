# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

**NukeDev sandbox** — a workspace for building Nuke tools, gizmos, and NukeX
extensions. This is where active development happens. The Nuke documentation
itself lives elsewhere (see the reference library below); this repo holds the
tools we build against it.

## Commands

There is no build/lint/test toolchain — tools are plain `.nk` files (node
groups, saved as scripts — see "Nuke tool file format" below) and Python
scripts that run inside Nuke. The only "run" workflow is smoke-testing a tool
in Nuke's terminal mode:

- `nuke -t <script.py>` — run a Python script headless (no GUI); the standard
  way to validate a tool or exercise its logic. Print to stdout for results.
- `nuke -i` — interactive Python shell inside Nuke.

Nuke is not currently on this machine's PATH, so these only work on a machine
with Nuke installed (or after adding it to PATH). When a tool's behavior can't
be verified without Nuke, say so rather than claiming it was tested.

## Nuke version targeting

Tools are built against a specific Nuke version. **State the target version
when starting work on a tool** (e.g. "build a v13 gizmo"); if none is stated,
assume **16** (the baseline). The tool's header records the version it targets
— that's the durable anchor, so a session may touch several versions.

Doc searches are scoped to the version folder matching the target version
(`reference/nuke13/`, `reference/nuke16/`, `reference/nuke17/` — only imported
versions exist). If a symbol isn't found in the target version's docs, check
the nearest version and flag the difference rather than silently using it.

## Reference library

The Nuke docs are converted to Markdown for LLM/RAG use and imported into
`reference/`, one folder per Nuke version. **Read the version's `DOCS_INDEX.md`
before searching the docs** — it maps every section and its entry points.

| Nuke version | Reference (in this repo) | Source project (regeneration) |
|---|---|---|
| **13** | `reference/nuke13/` — index: `reference/nuke13/DOCS_INDEX.md` | `L:\Nuke13Dev` (converter + `Documentation/` + raw `nukemds/`) |
| **16** | `reference/nuke16/` — index: `reference/nuke16/DOCS_INDEX.md` | `L:\Nuke16Dev` (converter + `Documentation/` + raw `nukemds/`) |
| **17** | `reference/nuke17/` — index: `reference/nuke17/DOCS_INDEX.md` | `L:\Nuke17Dev` (converter + `Documentation/` + raw `nukemds/`) |

Newer versions get imported into `reference/nukeXX/` when their docs are
converted — see `reference/nuke16/README.md` for the pattern.

`reference/tutorials/` holds a separate, growing collection of manually
dropped-in tutorial/article write-ups (e.g. CopyCat training guides) — not
version-scoped, not part of the official-docs conversion pipeline above.
See `reference/tutorials/README.md` for its own ingestion convention and
`reference/tutorials/INDEX.md` for the catalog.

**How to use the reference:**
- Start at the version's `DOCS_INDEX.md` to pick a section and entry point.
- For tool building, the two most-used sections are `python_dev_guide/`
  (Python API) and `online_help/` (node/user docs) — both have entry points
  listed in the index.
- Navigate sections / `grep` for symbols — never glob the whole doc tree
  (~4,500 files per version).
- Every doc file starts with `# Source: <original-filename>` for traceability.
- The imported copy is a snapshot — if the source project is regenerated,
  re-import it (see `reference/nuke16/README.md`).

## Layout

```
L:\NukeDevSandbox\
├── CLAUDE.md              ← this file
├── README.md              ← project one-liner
├── reference/             ← versioned reference library (imports)
│   ├── nuke13/            ← Nuke 13 docs (nukemds_clean/ + DOCS_INDEX.md)
│   ├── nuke16/            ← Nuke 16 docs (nukemds_clean/ + DOCS_INDEX.md)
│   ├── nuke17/            ← Nuke 17 docs (nukemds_clean/ + DOCS_INDEX.md)
│   └── tutorials/         ← manually dropped-in tutorials (README.md + INDEX.md)
├── gizmos/                ← Nuke tool .nk files (node groups, NOT .gizmo)
└── python/                ← Python tools / scripts
```

- **`gizmos/`** — node-based tools, saved as `.nk` files. One subfolder per
  tool or a flat layout, as the work dictates. (The folder is named `gizmos/`
  for the kind of tool it holds, not the file format — see "Nuke tool file
  format" below.)
- **`python/`** — Python tools and scripts (menus, callbacks, custom panels,
  startup scripts, etc.).

## Conventions

- Keep tools self-contained and documented at the top of each file.
- When a tool depends on a specific Nuke version's API, note the version in the
  file header.
- Reference docs are pointers only — never copy the doc tree into this repo
  (it would go stale).

### Nuke tool file format: `.nk`, never `.gizmo`

**Node-based tools in this repo are always saved as `.nk` files, never
`.gizmo`.** A `.gizmo` is loaded by external reference (from the toolbar/plugin
path); a render farm node with a missing file, stale copy, or unsynced plugin
path silently breaks or renders wrong — and gizmo files have a history of
version-to-version instability. A `.nk` snippet (a single top-level `Group {
... }`, same text format as a gizmo minus the toolbar registration) embeds the
whole tool directly into whatever script imports it, so there's no external
dependency at render time.

- Build/iterate on the tool as a `Group` node in a live Nuke session as usual.
- Export with `nuke.nodeCopy("path/to/Tool.nk")` (select only that node first)
  — same mechanism as exporting a gizmo, just with a `.nk` extension instead of
  `.gizmo`.
- Import with `nuke.nodePaste(path)`, File > Import Script, or by pasting the
  file's text into the node graph. It will **not** appear in the node toolbar
  the way a `.gizmo` does — call this out in the tool's header comment so
  users know how to bring it in.
- If an existing `.gizmo` is found in this repo, treat it as stale: convert it
  to `.nk` (same content, same header conventions, extension changed) rather
  than leaving both formats around.
