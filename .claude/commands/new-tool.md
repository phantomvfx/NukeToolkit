---
description: Scaffold a new Nuke tool (.nk Group or Python) with the repo's header conventions
argument-hint: <ToolName> [nk|py] [nuke-version] - <what it does>
---

Create a new Nuke tool from: $ARGUMENTS

1. Parse the name, kind (`nk` by default), target Nuke version (default **16**)
   and purpose. If the purpose is missing, ask one question and stop.
2. Location:
   - Sandbox tool → `gizmos/<ToolName>.nk` or `python/<tool_name>.py`.
   - Belongs to an existing integration → `tools/<x>/…`, following that
     tool's `CLAUDE.md` (e.g. NukeComfy tools also need a `json/` workflow
     with `NUKE_*` title markers).
3. Before you write any API calls, use the `nuke-docs` agent to confirm each
   node class, knob name and Python call you're unsure of in the target
   version's docs.
4. Write the file with the standard header comment:
   ```
   # <ToolName> - <one line>
   # Inputs: …   Outputs: …
   # Target: Nuke <version>
   # Import: NOT a .gizmo. Use File > Import Script, nuke.nodePaste("<path>"), or the menu entry
   # Status: untested in Nuke
   ```
   For `.nk`: a single top-level `Group { … }` with user knobs on a named tab.
   Never write a `.gizmo`.
5. If it should appear in a menu, add an entry. Sandbox tools go in
   `python/mllamas_tools`. NukeComfy tools are picked up automatically from
   `tools/comfy/gizmos/`.
6. Offer `/smoke-test` to validate it headless, and run the
   `nuke-tool-reviewer` agent on the result.
