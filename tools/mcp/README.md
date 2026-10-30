# NukeMCP

A lightweight **MCP (Model Context Protocol)** bridge that lets an MCP client
(such as Claude Desktop, Cursor, or any MCP host) drive a live **Foundry Nuke**
session over a local TCP socket.

## Architecture

```
+----------------+      +---------------------+      +---------------------+
|  MCP Client    | <--> |  nuke_mcp_server.py | <--> |  nuke_mcp_listener.py |
|  (Claude, etc) | MCP  |  (Python, this repo)| TCP  |  (runs inside Nuke)  |
+----------------+      +---------------------+      +---------------------+
```

1. **`nuke_mcp_server.py`** — an MCP 2.x server that exposes tools. It runs
   outside Nuke (in a Python venv) and communicates with Nuke over TCP.
2. **`nuke_mcp_listener.py`** — a background TCP listener that runs *inside*
   Nuke. It receives Python code, executes it in Nuke's context, and returns
   the value of the `result` variable.
3. **`menu.py`** — a Nuke menu file that imports the listener module and calls
   `nuke_mcp_listener.start_listener()`, auto-starting the listener when Nuke
   launches.

## Requirements

- **Python 3.10+** (for the MCP server)
- **MCP SDK** (`mcp` package)
- **Foundry Nuke 16** (or any Nuke with Python 3)

## Installation

### 1. Set up the Python environment (server side)

NukeMCP lives in `tools/mcp/` of the Nuke monorepo (it was previously `phantomvfx/NukeMCP`).

```powershell
cd L:\Nuke\tools\mcp
py -3.10 -m venv .venv
.venv\Scripts\pip install mcp
```

### 2. Load the Nuke side

Add the monorepo root to Nuke's plugin path in `~/.nuke/init.py`:

```python
nuke.pluginAddPath('L:/Nuke')
```

The root `init.py` adds `tools/mcp` to the plugin path, and its `menu.py`
starts the listener. Don't copy `nuke_mcp_listener.py` into `~/.nuke`: a
stale copy there shadows the repo version. Then **restart Nuke**. The listener starts automatically — `menu.py` calls
`nuke_mcp_listener.start_listener()` on launch. The listener does **not** print
anything to the Nuke console; verify it's up by checking the port (see
[Verify the connection](#verify-the-connection)).

## Usage

### 1. Start Nuke (the listener auto-starts)

The listener starts automatically when Nuke launches — `menu.py` imports
`nuke_mcp_listener` and calls `nuke_mcp_listener.start_listener()`. No manual
step is needed. The listener does **not** print a message to the Nuke console;
to confirm it's running, check that port 54321 is listening:

```powershell
Get-NetTCPConnection -LocalPort 54321   # expect State: Listen
```

### 2. Run the MCP server

```powershell
cd L:\Nuke\tools\mcp
.venv\Scripts\python nuke_mcp_server.py
```

### 3. Connect an MCP client

Point your MCP client at the server (e.g. `python nuke_mcp_server.py` as a
stdio command, or configure it as a Streamable HTTP / SSE endpoint). The
following tools are exposed:

| Tool | Description |
|------|-------------|
| `execute_python` | Run raw Python inside the active Nuke session. Assign `result` to return a value. |
| `get_node_graph` | List all nodes currently in the Nuke DAG. |
| `create_node` | Create a node (e.g. `Grade`, `Blur`, `CameraTracker`) with optional knob values. |
| `capture_node_preview` | Render one frame of a node (or the Viewer input) to PNG and return it as an image. |
| `get_project_settings` | Frame range, format, FPS and colour management of the open script. |
| `set_current_frame` | Move the playhead. |
| `find_missing_frames` | Report Read nodes with missing frames or broken paths. |
| `replace_filepaths` | Search and replace path substrings across Read, Write and Geo nodes. |

## Using with Claude Code CLI

The server runs as a **stdio** MCP server, so it can be registered directly with
the [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code). The
listener auto-starts when Nuke launches (see [Usage](#usage)), so just make sure
Nuke is open before you start Claude Code.

### Option A — Register via the CLI

```powershell
claude mcp add --scope user nuke -- L:\Nuke\tools\mcp\.venv\Scripts\python.exe L:\Nuke\tools\mcp\nuke_mcp_server.py
```

Then start Claude Code and the tools will be available automatically:

```powershell
claude
```

### Option B — Project-level `.mcp.json`

Create a `.mcp.json` file in the project root (or your working directory) so the
server is picked up whenever Claude Code runs in that folder:

```json
{
  "mcpServers": {
    "nuke": {
      "command": "L:\\Nuke\\tools\\mcp\\.venv\\Scripts\\python.exe",
      "args": ["L:\\Nuke\\tools\\mcp\\nuke_mcp_server.py"]
    }
  }
}
```

### Verify the connection

Inside Claude Code, run:

```
/mcp
```

You should see `nuke` listed as a connected stdio server with the tools in the
table above. You can then ask Claude to, for example, *"list all nodes in
Nuke"* or *"create a Blur node"*.

> **Note:** The paths above assume the monorepo lives at `L:\Nuke`. Adjust the
> `command`/`args` paths if you cloned it elsewhere.

## Wire Protocol

The server and listener exchange JSON over TCP on `127.0.0.1:54321`.

**Request** (server → Nuke):

```json
{ "code": "result = nuke.allNodes()" }
```

**Response** (Nuke → server):

```json
{ "status": "ok", "result": "..." }
```

On failure:

```json
{ "status": "error", "error": "<traceback>" }
```

## Troubleshooting

- **`Connection refused`** — the listener is not running. Make sure Nuke is open
  and the listener started. Verify with
  `Get-NetTCPConnection -LocalPort 54321` (expect `State: Listen`), or run
  `nuke_mcp_listener.start_listener()` in Nuke's Script Editor to start it
  without restarting.
- **Port already in use** — another process is on `54321`. Stop the listener
  (or the other process) and retry.
- **`nuke` not found** — make sure `nuke_mcp_listener.py` is running inside
  Nuke, not in a plain Python shell.
- **Listener not starting** — confirm `~/.nuke/init.py` calls
  `nuke.pluginAddPath('L:/Nuke')`, that no old `nuke_mcp_listener.py` copy sits
  in `~/.nuke`, and restart Nuke.

## License

MIT
