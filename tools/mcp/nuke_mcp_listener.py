"""
nuke.mcp_listener.py
====================
Nuke-side TCP listener for the NukeMCP bridge.

Runs inside a live Nuke session and listens on 127.0.0.1:54321 for
JSON commands sent by nuke_mcp_server.py. Each received payload is
executed in Nuke's Python context and the value of the `result`
variable (if any) is returned to the caller.

Protocol
--------
Request  (JSON): {"code": "<python code to execute>"}
Response (JSON): {"status": "ok",    "result": <value>}
                  {"status": "error", "error": "<message>"}
"""

import json
import socket
import threading
import traceback

try:
    import nuke
except ImportError:
    nuke = None

HOST = '127.0.0.1'
PORT = 54321

_listener_thread = None
_stop_event = threading.Event()
_lock = threading.Lock()


def _handle_client(conn):
    """Read one command, execute it in Nuke, and send back the result."""
    response = {'status': 'error', 'error': 'No response generated.'}
    try:
        conn.settimeout(10)
        data = conn.recv(65536).decode('utf-8')
        if not data:
            return
        payload = json.loads(data)
        code = payload.get('code', '')

        # Execute in a fresh namespace seeded with nuke and builtins.
        namespace = {'nuke': nuke, '__builtins__': __builtins__}
        namespace['result'] = None
        exec(compile(code, '<nuke_mcp>', 'exec'), namespace)

        response = {'status': 'ok', 'result': namespace.get('result')}
    except Exception as exc:
        response = {
            'status': 'error',
            'error': f"{exc}\n{traceback.format_exc()}",
        }
    finally:
        try:
            conn.sendall(json.dumps(response).encode('utf-8'))
        except Exception:
            pass
        conn.close()


def _listener_loop():
    """Accept connections until the stop event is set."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen(5)
        server.settimeout(0.5)
        while not _stop_event.is_set():
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=_handle_client, args=(conn,), daemon=True
            ).start()


def start_listener():
    """Start the listener thread. Safe to call multiple times."""
    global _listener_thread
    with _lock:
        if _listener_thread and _listener_thread.is_alive():
            return f"Listener already running on {HOST}:{PORT}."
        _stop_event.clear()
        _listener_thread = threading.Thread(
            target=_listener_loop, daemon=True, name='NukeMCPListener'
        )
        _listener_thread.start()
        return f"Listener started on {HOST}:{PORT}."


def stop_listener():
    """Stop the listener thread."""
    global _listener_thread
    with _lock:
        _stop_event.set()
        if _listener_thread:
            _listener_thread.join(timeout=2)
        _listener_thread = None
        return "Listener stopped."


def is_running():
    """Return True if the listener thread is currently alive."""
    return bool(_listener_thread and _listener_thread.is_alive())
