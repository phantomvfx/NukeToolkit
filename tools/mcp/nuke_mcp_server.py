import socket
import json
from mcp.server.mcpserver import MCPServer

# Initialize the MCP Server (MCP 2.x syntax)
mcp = MCPServer("NukeX-Bridge")

HOST = '127.0.0.1'
PORT = 54321

def send_to_nuke(code: str) -> dict:
    """Helper function to send Python commands over socket to running Nuke instance."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect((HOST, PORT))
            payload = json.dumps({"code": code}).encode('utf-8')
            s.sendall(payload)
            response = s.recv(65536).decode('utf-8')
            return json.loads(response)
    except ConnectionRefusedError:
        return {
            "status": "error", 
            "error": f"Connection refused. Ensure NukeX 16 is running and listening on {HOST}:{PORT}."
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

@mcp.tool()
def execute_python(code: str) -> str:
    """
    Executes raw Python code inside the active Nuke session.
    Assign values to a variable named 'result' to return a specific output.
    """
    res = send_to_nuke(code)
    if res.get("status") == "ok":
        return str(res.get("result"))
    return f"Error: {res.get('error')}"

@mcp.tool()
def get_node_graph() -> str:
    """Returns a list of all nodes currently in the Nuke DAG."""
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
        knobs: Optional dictionary of knob names and values to set.
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

@mcp.tool()
def reload_module(module_name: str, dev_path: str = "L:/NukeDevSandbox") -> str:
    """Reloads a Python module from the development sandbox inside Nuke."""
    code = f"""
import sys, importlib
if r'{dev_path}' not in sys.path:
    sys.path.append(r'{dev_path}')

if '{module_name}' in sys.modules:
    importlib.reload(sys.modules['{module_name}'])
    result = f"Successfully reloaded {module_name}"
else:
    __import__('{module_name}')
    result = f"Imported {module_name} for the first time"
"""
    res = send_to_nuke(code)
    return str(res.get("result", res.get("error")))

@mcp.tool()
def find_node_errors() -> str:
    """Finds all nodes in the DAG currently displaying an error banner."""
    code = """
import json
errors = [{'node': n.name(), 'error': n.error()} for n in nuke.allNodes() if n.hasError()]
result = json.dumps(errors, indent=2)
"""
    res = send_to_nuke(code)
    return str(res.get("result", res.get("error")))

if __name__ == "__main__":
    mcp.run()