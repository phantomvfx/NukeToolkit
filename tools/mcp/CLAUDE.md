# NukeMCP (tools/mcp)

MCP server that lets Claude drive a live Nuke session.

```
Claude Code --stdio--> nuke_mcp_server.py --TCP 127.0.0.1:54321--> nuke_mcp_listener.py (inside Nuke)
```

| File | Runs in | What |
|---|---|---|
| `nuke_mcp_server.py` | `.venv` (Python 3.10, `mcp` SDK 2.x, `MCPServer`) | Defines the MCP tools. Each one sends a Python snippet to Nuke |
| `nuke_mcp_listener.py` | Nuke | Daemon thread. `exec`s each snippet and returns the `result` variable as JSON |
| `menu.py` | Nuke | Starts the listener at Nuke startup (via root `L:\Nuke\init.py`) |

Tools: `execute_python`, `get_node_graph`, `create_node`, `capture_node_preview`
(renders a PNG and returns it as an image), `get_project_settings`,
`set_current_frame`, `find_missing_frames`, `replace_filepaths`.

## Rules

- Protocol: one JSON request per connection, `{"code": "..."}` →
  `{"status": "ok", "result": ...}` or `{"status": "error", "error": "..."}`.
  The listener reads a single `recv(65536)`, so keep payloads under 64 KB.
- The listener runs snippets on a **background thread**. Anything that
  touches the GUI or DAG and can crash off the main thread should go through
  `nuke.executeInMainThreadWithResult`.
- Server-side tools build Python by f-string interpolation. Watch the
  quoting: a `'` in a node name or path breaks the snippet. Prefer passing
  values through `json.dumps`.
- `preview.png` is written next to the server file and is gitignored.
- The `.venv` isn't committed. Recreate it with
  `py -3.10 -m venv .venv; .venv\Scripts\pip install mcp`.
- Restarting the server needs a Claude Code restart, or `/mcp` → reconnect.
  Changes to the listener need Nuke restarted or
  `importlib.reload(nuke_mcp_listener)` in Nuke.

## Registration

Registered at user scope as `nuke`:

```
claude mcp add --scope user nuke -- L:\Nuke\tools\mcp\.venv\Scripts\python.exe L:\Nuke\tools\mcp\nuke_mcp_server.py
```

Check it with `Get-NetTCPConnection -LocalPort 54321` (expect `Listen`) and `/mcp`.
