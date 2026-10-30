# knowledge/ — Nuke knowledge base

Local, LLM-ready reference material. **Everything in this folder except this
README is gitignored**, so a fresh clone has only this file. It is about 66 MB
and 12,700 Markdown files.

| Folder | Contents | Index (read first) |
|---|---|---|
| `nuke13/` | Nuke 13 official docs, converted to Markdown | `nuke13/DOCS_INDEX.md` |
| `nuke16/` | Nuke 16 official docs (baseline version) | `nuke16/DOCS_INDEX.md` |
| `nuke17/` | Nuke 17 official docs | `nuke17/DOCS_INDEX.md` |
| `tutorials/` | Hand-curated tutorial write-ups, not tied to a version | `tutorials/INDEX.md` |

Each `nukeXX/` folder holds `nukemds_clean/` (about 4,500 files, 7 sections)
and a `DOCS_INDEX.md`. Every doc file starts with `# Source: <original-filename>`.

## How to search

1. Open the target version's `DOCS_INDEX.md` and pick a section.
2. The most-used sections for tool building are `python_dev_guide/` (Python
   API) and `online_help/` (node and user docs).
3. `grep` for symbols inside a section. Never glob the whole tree.
4. If a symbol is missing in the target version, check the nearest version
   and flag the difference.

## Provenance and backup

The docs were converted from `L:\Nuke13Dev`, `L:\Nuke16Dev` and `L:\Nuke17Dev`
(converter + `Documentation/` + raw `nukemds/`). **Those folders no longer
exist (checked 2026-10-30), so this folder is the only copy.** Back it up
outside git: it isn't in the repo.

To add a version, convert its docs into the same shape (`nukemds_clean/` +
`DOCS_INDEX.md` + `README.md`) under `knowledge/nukeXX/` and add a row to
the table above and to the root `CLAUDE.md`.

To add a tutorial, follow `tutorials/README.md`.
