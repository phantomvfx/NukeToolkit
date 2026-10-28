# gizmos/

Node-based tools built from groups of Nuke nodes — saved as `.nk`, **not**
`.gizmo`. See CLAUDE.md's "Nuke tool file format" convention for why: a
`.gizmo` is loaded by external reference and can go missing or mismatch on
render farm nodes; a `.nk` snippet embeds the whole Group directly into
whatever script imports it.

- One subfolder per tool (or a flat layout, as the work dictates).
- `CornerPinNudge.nk` — tracked CornerPin2D wrapped with static To/From
  nudge buttons, edge bleed and overscan limit (Nuke 16, untested in Nuke).
- Each tool should carry a short header comment: what it does, its inputs/
  outputs, the Nuke version it targets, and how to import it (`nuke.nodePaste`
  / File > Import Script — it won't appear in the node toolbar the way a
  `.gizmo` does).
