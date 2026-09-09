# NukeMCP

A lightweight **MCP (Model Context Protocol)** bridge that lets an MCP client
(such as Claude Desktop, Cursor, or any MCP host) drive a live **Foundry Nuke**
session over a local TCP socket.

## Architecture

```
+----------------+      +---------------------+      +---------------------+
|  MCP Client    | <--> |  nuke_mcp_server.py | <--> |  nuke.mcp_listener.py |
|  (Claude, etc) | MCP  |  (Python, this repo)| TCP  |  (runs inside Nuke)  |
+----------------+      +---------------------+      +---------------------+
```

1. **`nuke_mcp_server.py`** — an MCP 2.x server that exposes tools. It runs
   outside Nuke (in a Python venv) and communicates with Nuke over TCP.
2. **`nuke.mcp_listener.py`** — a background TCP listener that runs *inside*
   Nuke. It receives Python code, executes it in Nuke's context, and returns
   the value of the `result` variable.
3. **`menu.py`** — a Nuke menu file that adds a **NukeMCP** menu to start/stop
   the listener from inside Nuke.

## Requirements

- **Python 3.10+** (for the MCP server)
- **MCP SDK** (`mcp` package)
- **Foundry Nuke 16** (or any Nuke with Python 3)

## Installation

### 1. Set up the Python environment (server side)

```powershell
cd L:\NukeMCP
python -m venv .venv
.venv\Scripts\activate
pip install mcp
```

### 2. Install the Nuke-side files

Copy **`nuke.mcp_listener.py`** and **`menu.py`** into a directory that Nuke
loads at startup. Any of these work:

- Your user Nuke directory, e.g. `C:\Users\<you>\.nuke\`
- Any directory listed in the `NUKE_PATH` environment variable

Then **restart Nuke**. A **NukeMCP** menu will appear in the menu bar.

## Usage

### 1. Start the listener inside Nuke

In Nuke, go to **NukeMCP → Start Listener**. You should see a message like:

```
Listener started on 127.0.0.1:54321.
```

### 2. Run the MCP server

```powershell
cd L:\NukeMCP
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
| `create_node` | Create a node (e.g. `Grade`, `Blur`, `CameraTracker`). |
| `reload_module` | Reload a Python module from a dev sandbox path inside Nuke. |
| `find_node_errors` | Find all nodes in the DAG showing an error banner. |

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

- **`Connection refused`** — the listener is not running. Start it via
  **NukeMCP → Start Listener** in Nuke.
- **Port already in use** — another process is on `54321`. Stop the listener
  (or the other process) and retry.
- **`nuke` not found** — make sure `nuke.mcp_listener.py` is running inside
  Nuke, not in a plain Python shell.
- **Menu not appearing** — confirm `menu.py` and `nuke.mcp_listener.py` are in
  a directory Nuke scans (NUKE_PATH or `~/.nuke`) and restart Nuke.

## License

MIT
