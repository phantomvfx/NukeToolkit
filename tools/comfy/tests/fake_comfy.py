"""
A small in-process fake of the ComfyUI HTTP API, for tests that must not need a real server (SAM3Matte runner, and
the SAM3Matte driver tests that reuse it). Stdlib only, no nuke.

    fake = FakeComfy(responder)          # responder(workflow, prompt_no) -> {node_id: [png bytes, ...]}
    fake.start(); fake.url; fake.stop()  # or `with FakeComfy(responder) as fake:`

The responder is called once per POST /prompt, in the server thread. It returns the PNG bytes of every SaveImage node
(in batch order, as ComfyUI saves them) or raises
  FakeError(node_type, message, node_id=None, exception_type="RuntimeError")   -> history entry with execution_error
  FakeReject(message, node_errors=None)                                         -> HTTP 400 on /prompt
It can also be a plain dict or None (every prompt then fails with a FakeError).

Records (for assertions): uploads [(subfolder, name)], upload_data {(subfolder, name): bytes},
prompts [workflow] (prompt_no is the index into this list), interrupts [prompt_no | None].
pending_polls: how many /history/<id> polls answer "not done yet" before the entry shows up.

Routes: POST /upload/image, POST /prompt, GET /history/<id>, GET /view, POST /interrupt, GET /system_stats.
Same wire shapes as ComfyUI: /upload/image -> {"name","subfolder","type"}; history entry
{"status": {"status_str","completed","messages": [[event, data], ...]}, "outputs": {node_id: {"images": [...]}}}.
"""
import email.parser
import json
import struct
import threading
import urllib.parse
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeError(Exception):
    """Raised by a responder to make the prompt end in an execution_error."""

    def __init__(self, node_type, message, node_id=None, exception_type="RuntimeError"):
        Exception.__init__(self, "%s: %s" % (node_type, message))
        self.node_type, self.message = node_type, message
        self.node_id, self.exception_type = node_id, exception_type


class FakeReject(Exception):
    """Raised by a responder to make POST /prompt answer HTTP 400 (validation failure)."""

    def __init__(self, message, node_errors=None):
        Exception.__init__(self, message)
        self.message, self.node_errors = message, node_errors or {}


# ----------------------------------------------------------------- PNG helpers (independent of sam3matte_png)

def _chunk(tag, body):
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)


def rgb_png_bytes(w, h, grey):
    """8-bit RGB PNG (filter 0) from w*h grey bytes, like a MaskToImage -> SaveImage output."""
    assert len(grey) == w * h
    rows = b"".join(b"\x00" + b"".join(bytes((v, v, v)) for v in grey[y * w:(y + 1) * w]) for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(rows)) + _chunk(b"IEND", b""))


def decode_grey(png_bytes):
    """(w, h, grey bytes = channel 0) of a filter-0 8-bit RGB/grey PNG made by rgb_png_bytes."""
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat = 8, b""
    w = h = 0
    ctype = 2
    while pos < len(png_bytes):
        n = struct.unpack(">I", png_bytes[pos:pos + 4])[0]
        tag = png_bytes[pos + 4:pos + 8]
        body = png_bytes[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            w, h, _bd, ctype = struct.unpack(">IIBB", body[:10])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    nch = {0: 1, 2: 3, 6: 4}[ctype]
    raw = zlib.decompress(idat)
    stride = w * nch
    out = bytearray()
    for y in range(h):
        row = raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)]
        assert raw[y * (stride + 1)] == 0, "decode_grey handles filter 0 only"
        out += row[0::nch]
    return w, h, bytes(out)


# ------------------------------------------------------------------ workflow helpers for responders

def save_nodes(workflow):
    """Ids of the SaveImage nodes of a workflow, in a stable order."""
    return sorted((k for k, v in workflow.items() if v.get("class_type") == "SaveImage"),
                  key=lambda k: (len(k), k))


def nodes_of(workflow, class_type):
    return [k for k, v in workflow.items() if v.get("class_type") == class_type]


def track_info(workflow):
    """What a Video Track prompt asks for: directory, reverse, seeds (LoadImage values in batch order), has_text, n_save."""
    loaders = nodes_of(workflow, "VHS_LoadImages")
    track = nodes_of(workflow, "SAM3_VideoTrack")
    if not loaders or not track:
        return None
    inputs = workflow[track[0]]["inputs"]
    seeds = []
    if "initial_mask" in inputs:
        # initial_mask -> ThresholdMask -> ImageToMask -> ImageScale -> ImageBatch chain / LoadImage
        def walk(nid):
            node = workflow[nid]
            if node["class_type"] == "LoadImage":
                seeds.append(node["inputs"]["image"])
            elif node["class_type"] == "ImageBatch":
                walk(node["inputs"]["image1"][0])
                walk(node["inputs"]["image2"][0])
            else:
                for v in node["inputs"].values():
                    if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] in workflow:
                        walk(v[0])
        walk(inputs["initial_mask"][0])
    return {"directory": workflow[loaders[0]]["inputs"]["directory"],
            "reverse": bool(nodes_of(workflow, "ReverseImageBatch")),
            "seeds": seeds, "has_text": "conditioning" in inputs,
            "n_save": len([k for k in save_nodes(workflow) if k.startswith("save")])}


# ------------------------------------------------------------------------------------------- the server

class FakeComfy:
    def __init__(self, responder=None, pending_polls=0):
        self.responder = responder
        self.pending_polls = pending_polls
        self.uploads = []
        self.upload_data = {}
        self.prompts = []
        self.interrupts = []
        self.history = {}
        self._files = {}      # (filename, subfolder, type) -> bytes
        self._counters = {}   # filename prefix -> next number
        self._polls = {}
        self._lock = threading.Lock()
        self._httpd = None
        self._thread = None
        self.url = None

    # lifecycle
    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                if not isinstance(body, bytes):
                    body = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                if u.path.startswith("/history/"):
                    return self._send(200, fake._history(u.path[len("/history/"):]))
                if u.path == "/view":
                    q = urllib.parse.parse_qs(u.query)
                    key = (q.get("filename", [""])[0], q.get("subfolder", [""])[0], q.get("type", ["output"])[0])
                    data = fake._files.get(key)
                    if data is None:
                        return self._send(404, {"error": "no such file"})
                    return self._send(200, data, "image/png")
                if u.path == "/system_stats":
                    return self._send(200, {"system": {"comfyui_version": "fake"}, "devices": []})
                return self._send(404, {"error": "unknown route " + u.path})

            def do_POST(self):
                u = urllib.parse.urlparse(self.path)
                body = self._body()
                if u.path == "/upload/image":
                    return self._send(*fake._upload(self.headers.get("Content-Type", ""), body))
                if u.path == "/prompt":
                    return self._send(*fake._prompt(body))
                if u.path == "/interrupt":
                    with fake._lock:
                        fake.interrupts.append(len(fake.prompts) - 1 if fake.prompts else None)
                    return self._send(200, {})
                return self._send(404, {"error": "unknown route " + u.path})

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.url = "http://127.0.0.1:%d" % self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()

    def uploaded_names(self, subfolder):
        """File names uploaded into `subfolder` so far, in upload order (no locking: safe inside a responder)."""
        return [name for sub, name in self.uploads if sub == subfolder]

    # route implementations
    def _upload(self, ctype, body):
        msg = email.parser.BytesParser().parsebytes(b"Content-Type: " + ctype.encode("latin-1") + b"\r\n\r\n" + body)
        fields, name, data = {}, None, b""
        for part in msg.walk():
            disp = part.get("Content-Disposition")
            if not disp:
                continue
            field = part.get_param("name", header="content-disposition")
            if part.get_filename():
                name, data = part.get_filename(), part.get_payload(decode=True)
            else:
                fields[field] = part.get_payload(decode=True).decode("utf-8")
        if name is None:
            return 400, {"error": "no image part"}
        subfolder = fields.get("subfolder", "")
        with self._lock:
            self.uploads.append((subfolder, name))
            self.upload_data[(subfolder, name)] = data
        return 200, {"name": name, "subfolder": subfolder, "type": fields.get("type", "input")}

    def _prompt(self, body):
        try:
            workflow = json.loads(body.decode("utf-8"))["prompt"]
        except (ValueError, KeyError):
            return 400, {"error": {"message": "bad request"}, "node_errors": {}}
        with self._lock:
            prompt_no = len(self.prompts)
            self.prompts.append(workflow)
            pid = "fake-%04d" % prompt_no
            try:
                responder = self.responder
                if responder is None:
                    raise FakeError("FakeComfy", "no responder set")
                made = responder(workflow, prompt_no) if callable(responder) else responder
                entry = self._success(workflow, made)
            except FakeReject as e:
                self.prompts.pop()
                return 400, {"error": {"type": "prompt_outputs_failed_validation", "message": e.message,
                                       "details": "", "extra_info": {}}, "node_errors": e.node_errors}
            except FakeError as e:
                entry = {"status": {"status_str": "error", "completed": False, "messages": [
                    ["execution_start", {"prompt_id": pid}],
                    ["execution_error", {"prompt_id": pid, "node_id": e.node_id or "1", "node_type": e.node_type,
                                         "exception_message": e.message, "exception_type": e.exception_type}]]},
                    "outputs": {}}
            self.history[pid] = entry
            self._polls[pid] = 0
        return 200, {"prompt_id": pid, "number": prompt_no, "node_errors": {}}

    def _success(self, workflow, made):
        outputs = {}
        for node_id, blobs in (made or {}).items():
            prefix = workflow.get(node_id, {}).get("inputs", {}).get("filename_prefix", "ComfyUI")
            subfolder, _, base = prefix.rpartition("/")
            images = []
            for blob in blobs:
                n = self._counters.get(prefix, 0) + 1
                self._counters[prefix] = n
                fname = "%s_%05d_.png" % (base, n)
                self._files[(fname, subfolder, "output")] = blob
                images.append({"filename": fname, "subfolder": subfolder, "type": "output"})
            outputs[node_id] = {"images": images}
        return {"status": {"status_str": "success", "completed": True,
                           "messages": [["execution_success", {}]]}, "outputs": outputs}

    def _history(self, pid):
        with self._lock:
            if pid not in self.history:
                return {}
            self._polls[pid] += 1
            if self._polls[pid] <= self.pending_polls:
                return {}
            return {pid: self.history[pid]}
