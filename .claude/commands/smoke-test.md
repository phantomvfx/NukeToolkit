---
description: Run a Nuke Python script or .nk tool headless (Nuke -t) and report the result
argument-hint: <path to .py or .nk> [nuke-version]
---

Smoke-test in headless Nuke: $ARGUMENTS

1. Resolve the Nuke executable from the version: 16 (default) →
   `L:\Apps\Nuke16.1v4\Nuke16.1.exe`, 17 → `L:\Apps\Nuke17.0v1\Nuke17.0.exe`,
   13 → `L:\Apps\Nuke13.2v4\Nuke13.2.exe`. If the version is omitted, read it
   from the file header.
2. If the target is a `.py`: run `& "<nuke.exe>" -t "<path>"`.
3. If the target is a `.nk` tool: write a temporary script in the session
   scratchpad. It should:
   - `nuke.nodePaste("<path with forward slashes>")`
   - feed the Group's inputs with test sources (`CheckerBoard2`, `ColorBars`,
     `Constant`, …)
   - check for errors (`node.hasError()`, and `node.error()` where it exists)
   - `nuke.execute` one frame into a Write in the scratchpad
   - print a clear PASS/FAIL line per check

   Then run it with `-t`. If a matching `python/smoke_<tool>.py` already
   exists, use it instead.
4. TAPTrack has its own suite. Use `tools/taptrack/.venv/Scripts/python.exe -m pytest`
   (see `tools/taptrack/CLAUDE.md`).
5. Report the exact command, the relevant stdout and stderr, and PASS/FAIL.
   If Nuke couldn't run (licence, missing exe), say so. Never report a tool
   as tested when it wasn't. If it passed, offer to update the tool header's
   `Status:` line.
