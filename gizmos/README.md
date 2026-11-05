# gizmos/

Node-based tools built from groups of Nuke nodes — saved as `.nk`, **not**
`.gizmo`. See CLAUDE.md's "Nuke tool file format" convention for why: a
`.gizmo` is loaded by external reference and can go missing or mismatch on
render farm nodes; a `.nk` snippet embeds the whole Group directly into
whatever script imports it.

- One subfolder per tool (or a flat layout, as the work dictates).
- `BulletHitDebris.nk` — particle debris chips + dust spray burst for a bullet
  hit on a table, 8-frame sim, placeable in 3D (Nuke 16, tested headless on a
  synthetic shot). Built by `python/build_bullet_hit_debris.py`.
- `CornerPinNudge.nk` — tracked CornerPin2D wrapped with static To/From
  nudge buttons, edge bleed and overscan limit (Nuke 16, untested in Nuke).
- `InjectionScar.nk` — procedural needle / injection mark on skin: puncture,
  redness ring and bruise halo graded through procedural mattes, so the plate's
  own grain survives (Nuke 16, tested headless on a synthetic plate and a real
  skin still; not yet checked in the GUI). Built by
  `python/build_injection_scar.py`, smoke test `python/smoke_injection_scar.py`.
- Each tool should carry a short header comment: what it does, its inputs/
  outputs, the Nuke version it targets, and how to import it (`nuke.nodePaste`
  / File > Import Script — it won't appear in the node toolbar the way a
  `.gizmo` does).
