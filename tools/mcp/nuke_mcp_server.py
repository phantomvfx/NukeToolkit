import socket
import json
import os
from pathlib import Path
from mcp.server.mcpserver import MCPServer, Imager

# Initialize MCP 2.x Server
mcp = MCPServer("NukeX-Bridge")

HOST = '127.0.0.1'
PORT = 54321

def send_to_nuke(code: str) -> dict:
    """Helper function to dispatch Python commands to the running Nuke instance."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(15)  # Extended to 15s to allow single-frame preview renders
            s.connect((HOST, PORT))
            payload = json.dumps({"code": code}).encode('utf-8')
            s.sendall(payload)
            response = s.recv(65536).decode('utf-8')
            return json.loads(response)
    except ConnectionRefusedError:
        return {
            "status": "error",
            "error": f"Connection refused. Ensure NukeX is running with the listener active on {HOST}:{PORT}."
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

# ==========================================
# BASE TOOLS
# ==========================================

@mcp.tool()
def execute_python(code: str) -> str:
    """
    Executes raw Python code inside the active Nuke session.
    Assign the output value to a variable named 'result' to return data.
    """
    res = send_to_nuke(code)
    if res.get("status") == "ok":
        return str(res.get("result"))
    return f"Error: {res.get('error')}"

@mcp.tool()
def get_node_graph() -> str:
    """Returns a list of all nodes currently in the Nuke DAG with their classes."""
    code = """
import json
nodes = [{'name': n.name(), 'class': n.Class()} for n in nuke.allNodes()]
result = json.dumps(nodes)
"""
    res = send_to_nuke(code)
    return str(res.get("result", res.get("error")))

@mcp.tool()
def create_node(node_class: str, knobs: dict = None) -> str:
    """
    Creates a node in NukeX (e.g., Grade, Blur, CameraTracker, CopyCat).
    Args:
        node_class: The class of the node to create.
        knobs: Optional dictionary of knob names and values to assign.
    """
    knob_setup = ""
    if knobs:
        for k, v in knobs.items():
            if isinstance(v, str):
                knob_setup += f"n['{k}'].setValue('{v}'); "
            else:
                knob_setup += f"n['{k}'].setValue({v}); "

    code = f"""
n = nuke.createNode('{node_class}')
{knob_setup}
result = f"Created node: {{n.name()}}"
"""
    res = send_to_nuke(code)
    return str(res.get("result", res.get("error")))

# ==========================================
# CATEGORY 1: "GIVE CLAUDE EYES" (IMAGE PREVIEW)
# ==========================================

@mcp.tool()
def capture_node_preview(node_name: str = None, frame: int = None) -> Image:
    """
    Renders a frame of a node (or the active Viewer input if omitted) to PNG 
    and returns the visual image directly into Claude's context so Claude can see it.
    Args:
        node_name: Name of the node to capture. If None, captures the node plugged into the active Viewer.
        frame: Frame number to render. If None, uses current playhead frame.
    """
    preview_file = "L:/NukeMCP/preview.png"
    
    code = f"""
import os

# If no node specified, find whatever is plugged into the active Viewer
target = None
if '{node_name}' != 'None':
    target = nuke.toNode('{node_name}')
else:
    viewer = nuke.activeViewer()
    if viewer:
        target = viewer.node().input(0)

if not target:
    result = "Error: Target node not found."
else:
    f = {frame} if {frame is not None} else int(nuke.frame())
    out_path = '{preview_file}'
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    
    # Render 1 frame as PNG
    w = nuke.nodes.Write(inputs=[target], file=out_path, file_type='png')
    try:
        nuke.execute(w, f, f)
        result = out_path
    finally:
        nuke.delete(w)
"""
    res = send_to_nuke(code)
    
    if res.get("status") == "ok" and res.get("result") == preview_file:
        # Returns the actual image binary to Claude's multimodal vision model
        return Image(path=Path(preview_file))
    else:
        raise RuntimeError(f"Failed to capture preview: {res.get('error') or res.get('result')}")

# ==========================================
# CATEGORY 3: PROJECT & SHOT ENVIRONMENT
# ==========================================

@mcp.tool()
def get_project_settings() -> str:
    """Returns project-level settings: frame range, resolution format, FPS, and OCIO config."""
    code = """
import json
root = nuke.root()
info = {
    'script_path': root.name(),
    'first_frame': int(root['first_frame'].value()),
    'last_frame': int(root['last_frame'].value()),
    'current_frame': int(nuke.frame()),
    'fps': root['fps'].value(),
    'format': f"{root.format().width()}x{root.format().height()}",
    'colorManagement': root['colorManagement'].value() if 'colorManagement' in root.knobs() else 'Nuke'
}
result = json.dumps(info, indent=2)
"""
    res = send_to_nuke(code)
    return str(res.get("result", res.get("error")))

@mcp.tool()
def set_current_frame(frame: int) -> str:
    """
    Moves the Nuke timeline cursor/playhead to a specific frame number.
    """
    code = f"nuke.frame({frame}); result = f'Moved timeline to frame {frame}'"
    res = send_to_nuke(code)
    return str(res.get("result", res.get("error")))

# ==========================================
# CATEGORY 4: ASSET & PIPELINE SANITY CHECKS
# ==========================================

@mcp.tool()
def find_missing_frames() -> str:
    """Checks all Read nodes in the active script and reports any missing frames or broken filepaths."""
    code = """
import json, os
missing = []
for r in nuke.allNodes('Read'):
    file_path = r['file'].value()
    first = int(r['first'].value())
    last = int(r['last'].value())
    
    # Check start and end frame existence on disk
    for f in [first, last]:
        frame_file = nuke.filename(r, nuke.REPLACE)
        if frame_file and not os.path.exists(frame_file):
            missing.append({'node': r.name(), 'file': file_path, 'frame': f})
            break

result = json.dumps(missing, indent=2)
"""
    res = send_to_nuke(code)
    return str(res.get("result", res.get("error")))

@mcp.tool()
def replace_filepaths(old_pattern: str, new_pattern: str) -> str:
    """
    Searches and replaces filepath substrings across all Read, Write, and Geo nodes in the DAG.
    Useful for remounting drives or migrating project directories.
    """
    code = f"""
import json
updated = []
for n in nuke.allNodes():
    for knob_name in ['file', 'vfield_file']:
        if knob_name in n.knobs():
            val = n[knob_name].value()
            if '{old_pattern}' in val:
                new_val = val.replace('{old_pattern}', '{new_pattern}')
                n[knob_name].setValue(new_val)
                updated.append(f"{{n.name()}}: {{val}} -> {{new_val}}")
result = json.dumps(updated, indent=2)
"""
    res = send_to_nuke(code)
    return str(res.get("result", res.get("error")))

# ==========================================
# ENTRY POINT
# ==========================================

if __name__ == "__main__":
    mcp.run()