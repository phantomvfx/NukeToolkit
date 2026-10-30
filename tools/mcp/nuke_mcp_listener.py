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

Threading
---------
Sockets are handled on background threads, but in the GUI the code itself
runs on Nuke's main thread (nuke.executeInMainThread). Running nuke.execute()
or DAG edits from a background thread can deadlock the GUI. If the main
thread doesn't pick the code up within MAIN_THREAD_TIMEOUT (busy render,
modal dialog), the caller gets an error. The code may still run later.
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
MAIN_THREAD_TIMEOUT = 60  # seconds; nuke_mcp_server's socket timeout must be longer

_listener_thread = None
_stop_event = threading.Event()
_lock = threading.Lock()


def _run_code(code):
    """Execute code in a fresh namespace and return its `result` variable."""
    namespace = {'nuke': nuke, '__builtins__': __builtins__, 'result': None}
    exec(compile(code, '<nuke_mcp>', 'exec'), namespace)
    return namespace.get('result')


def _run_on_main_thread(code):
    """Run code on Nuke's main thread in the GUI, or inline in terminal mode."""
    if nuke is None or not nuke.GUI or threading.current_thread() is threading.main_thread():
        return _run_code(code)

    done = threading.Event()
    out = {}

    def task():
        try:
            out['value'] = _run_code(code)
        except Exception as exc:
            out['error'] = f"{exc}\n{traceback.format_exc()}"
        finally:
            done.set()

    nuke.executeInMainThread(task)
    if not done.wait(MAIN_THREAD_TIMEOUT):
        raise TimeoutError(
            f"Nuke's main thread did not run the code within {MAIN_THREAD_TIMEOUT}s "
            "(busy render or a modal dialog open?). It may still run later."
        )
    if 'error' in out:
        raise RuntimeError(out['error'])
    return out.get('value')


def _recv_json(conn):
    """Read until the buffered bytes parse as one JSON object (payloads can exceed one recv)."""
    data = b''
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        data += chunk
        try:
            return json.loads(data.decode('utf-8'))
        except ValueError:
            continue
    return json.loads(data.decode('utf-8')) if data else None


def _handle_client(conn):
    """Read one command, execute it in Nuke, and send back the result."""
    response = {'status': 'error', 'error': 'No response generated.'}
    try:
        conn.settimeout(10)
        payload = _recv_json(conn)
        if payload is None:
            return
        response = {'status': 'ok', 'result': _run_on_main_thread(payload.get('code', ''))}
    except Exception as exc:
        response = {
            'status': 'error',
            'error': f"{exc}\n{traceback.format_exc()}",
        }
    finally:
        try:
            # default=str: a non-JSON result (e.g. a Node) must still produce a reply,
            # otherwise the caller waits for its full socket timeout.
            conn.sendall(json.dumps(response, default=str).encode('utf-8'))
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
