---
description: Check the Claude ↔ live Nuke bridge (NukeMCP) end to end and diagnose breaks
---

Check the live-Nuke bridge, stopping at the first failure:

1. **Nuke running?** `Get-Process Nuke* | Select Name,Id,Path`.
2. **Listener up?** `Get-NetTCPConnection -LocalPort 54321 -State Listen`.
   If not, the likely causes are that `~/.nuke/init.py` lacks
   `nuke.pluginAddPath('L:/Nuke')`, a stale `~/.nuke/nuke_mcp_listener.py`
   exists, or Nuke wasn't restarted.
3. **MCP server registered and connected?** Look for `nuke` in the available
   `mcp__nuke__*` tools. If it's missing, run `claude mcp get nuke` and check
   that its command is
   `L:\Nuke\tools\mcp\.venv\Scripts\python.exe L:\Nuke\tools\mcp\nuke_mcp_server.py`
   and that the venv exists (`.venv\Scripts\python.exe -c "import mcp"`).
4. **Round trip:** call `mcp__nuke__get_project_settings` and
   `mcp__nuke__get_node_graph`, and report the script name, frame range and
   node count.

Summarize as a short table with each step marked OK or FAIL. For the first
failure, give the one most likely fix.
