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
  Both sides read until the JSON is complete, so payloads have no size limit.
  Non-JSON results are sent back as `str()`.
- Sockets are handled on background threads, but in the GUI each snippet is
  **run on Nuke's main thread** (`nuke.executeInMainThread` plus a wait).
  Snippets can call `nuke.execute`, create nodes and so on directly. Don't wrap
  them in `executeInMainThreadWithResult`: they're already on the main thread,
  and nesting would deadlock. Running them on a background thread used to hang
  the GUI (2026-10-30). In `nuke -t` they run inline.
- Timeouts: the listener gives the main thread `MAIN_THREAD_TIMEOUT` = 60 s to
  pick a snippet up (a render in progress or a modal dialog blocks it), and
  the server's socket waits 75 s. Keep the server timeout the longer of the two.
- Server-side tools never interpolate values into snippet source. Pass them
  with `with_args(code, name=value, ...)`, which prepends `args = {...!r}`,
  and read `args['name']` in the snippet. Quotes, backslashes and newlines in
  names, paths and knob values are then safe. Don't write f-string snippets.
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
