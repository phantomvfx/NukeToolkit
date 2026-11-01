"""
SAM3Matte runner: sends a plate to ComfyUI (SAM3) in chunks and brings back per-object mask PNGs. No Nuke import.

What it does
  run_job(job, report, cancel, client=None, resume=None, poll_s=2.0) -> result
runs one Generate from start to finish on the worker thread. `job` and `result` are the C3 dicts of the SAM3Matte plan
(see sam3matte_versions.py for the stored-version shape). Network access is only through ComfyClient (urllib: /prompt,
/history, /view, /interrupt, plus api.ComfyAPIClient.upload_image for /upload/image), so it works against any COMFY_URL.

Key mode  : Detect on the full-size key frame (prompt 0) -> objects (points object first; text objects covered more than
            half by the Stencil are dropped; at most Max objects kept) -> seeded Video Track chunks outward from the key
            frame (forward key..last, then reversed key-1..first). Chunks are seeded from the Detect masks (first chunk of
            each direction) or from the previous chunk's last frame as written to the version folder.
Range mode: chunk 0 is a text-only Video Track; later chunks are seeded by the previous chunk's last frame plus the text
            (sam3matte_graph.WHOLE_RANGE_SEED_WITH_TEXT) or, when that is False, tracked by text only and matched back to
            the known slots by mask overlap on the shared overlap frame.

Output: V/obj<slot>.<frame:04d>.png, slot = object number 0..7, frame = the plate's frame number (taken from the position
of the image in the history output, never from file names; a reversed chunk comes back newest-first).
Empty slots (no pixel in any frame) are deleted at the end. `<V>/_plate` (the rendered frames) is always removed.

Errors: JobError / JobCancelled never escape run_job; they are folded into result["error"] / result["cancelled"]. A run that
found nothing raises NothingFound (the caller creates no version).

Target Nuke 16.1 / Python 3.11 (stdlib only). Tested with pytest against tests/fake_comfy.py; not run inside Nuke and not
yet run against a live server (the live graph checks are tests/live/check_sam3matte_graph.py).
"""
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request

try:
    from . import api
    from . import sam3matte_graph as graph
    from . import sam3matte_chunks as chunks
    from . import sam3matte_hints as hints
    from . import sam3matte_png as png
    from . import sam3matte_versions as versions
except ImportError:  # run as bare modules (tests, scripts)
    import api
    import sam3matte_graph as graph
    import sam3matte_chunks as chunks
    import sam3matte_hints as hints
    import sam3matte_png as png
    import sam3matte_versions as versions

MAX_SLOTS = graph.MAX_SLOTS
DETECT_NODE = "detect_save"
MASK_WIDTH = 256          # masks are shrunk to at most this width before IoU / stencil comparisons


class JobError(Exception):
    """A step failed. node_id / exception_type are set when ComfyUI reported an execution_error."""

    def __init__(self, message, node_id=None, exception_type=None):
        Exception.__init__(self, message)
        self.node_id = node_id
        self.exception_type = exception_type


class NothingFound(JobError):
    """SAM3 found no object for the prompt: there is nothing to make a version of."""


class JobCancelled(Exception):
    pass


# ---------------------------------------------------------------------------------------------------- HTTP client

def _validation_text(code, raw):
    """Readable text of a rejected /prompt (HTTP 400): the error message plus every node_errors line."""
    try:
        body = json.loads(raw)
    except ValueError:
        return "ComfyUI rejected the graph (HTTP %s): %s" % (code, raw[:500])
    err = body.get("error")
    parts = []
    if isinstance(err, dict):
        parts.append(str(err.get("message") or err.get("type") or "invalid prompt"))
        if err.get("details"):
            parts.append(str(err["details"]))
    elif err:
        parts.append(str(err))
    for node_id, ne in (body.get("node_errors") or {}).items():
        msgs = []
        for e in ne.get("errors", []):
            m = str(e.get("message", ""))
            if e.get("details"):
                m += " (%s)" % e["details"]
            msgs.append(m)
        parts.append("node %s %s: %s" % (node_id, ne.get("class_type", ""), "; ".join(msgs)))
    return "ComfyUI rejected the graph (HTTP %s): %s" % (code, " | ".join(parts) or raw[:500])


class ComfyClient:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self._api = api.ComfyAPIClient(self.base_url)

    # -- helpers
    def _get_json(self, path, timeout=30):
        try:
            with urllib.request.urlopen(self.base_url + path, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise JobError("ComfyUI answered HTTP %s for %s" % (e.code, path.split("?")[0]))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise JobError("Could not reach ComfyUI at %s: %s" % (self.base_url, getattr(e, "reason", e)))

    # -- API
    def upload(self, path, subfolder):
        """Upload an image to input/<subfolder>; returns the LoadImage value."""
        value = self._api.upload_image(path, subfolder)
        if not value:
            raise JobError("Upload of %s to %s failed (is ComfyUI running at %s?)"
                           % (os.path.basename(path), subfolder, self.base_url))
        return value

    def queue(self, workflow):
        """POST /prompt; returns the prompt id."""
        req = urllib.request.Request(self.base_url + "/prompt", data=json.dumps({"prompt": workflow}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))["prompt_id"]
        except urllib.error.HTTPError as e:
            raise JobError(_validation_text(e.code, e.read().decode("utf-8", "replace")))
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            raise JobError("Could not queue the graph on ComfyUI at %s: %s" % (self.base_url, getattr(e, "reason", e)))

    def interrupt(self):
        req = urllib.request.Request(self.base_url + "/interrupt", data=b"{}",
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except (urllib.error.URLError, OSError):
            pass

    def wait(self, prompt_id, cancel, poll_s=2.0):
        """Poll /history/<id> until the prompt is done. Returns the entry's outputs."""
        failures = 0
        while True:
            if cancel.is_set():
                self.interrupt()
                raise JobCancelled()
            entry = None
            try:
                entry = self._get_json("/history/" + urllib.parse.quote(prompt_id)).get(prompt_id)
                failures = 0
            except JobError:
                failures += 1          # a busy server may miss a poll or two; a dead one fails for long
                if failures >= 10:
                    raise
            if entry is not None:
                return self._outputs(entry)
            cancel.wait(poll_s)

    @staticmethod
    def _outputs(entry):
        status = entry.get("status") or {}
        if status.get("status_str") == "error":
            for kind, data in status.get("messages", []):
                if kind == "execution_error":
                    raise JobError("Error in %s: %s" % (data.get("node_type", "?"), data.get("exception_message", "").strip()),
                                   data.get("node_id"), data.get("exception_type"))
            raise JobError("ComfyUI reported an error for this prompt")
        return entry.get("outputs") or {}

    def download(self, image, dest):
        """GET /view for one history image into `dest` (written atomically)."""
        query = urllib.parse.urlencode({"filename": image["filename"], "subfolder": image.get("subfolder", ""),
                                        "type": image.get("type", "output")})
        tmp = dest + ".part"
        try:
            with urllib.request.urlopen(self.base_url + "/view?" + query, timeout=120) as resp, open(tmp, "wb") as f:
                shutil.copyfileobj(resp, f)
            os.replace(tmp, dest)
        except (urllib.error.URLError, OSError) as e:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise JobError("Download of %s failed: %s" % (image.get("filename"), getattr(e, "reason", e)))


# ------------------------------------------------------------------------------------------------------- the run

_MASK_FILE = re.compile(r"^obj(\d)\.(\d+)\.png$")


def _rmtree(path):
    shutil.rmtree(path, ignore_errors=True)


class _Run:
    def __init__(self, job, report, cancel, client, resume, poll_s):
        self.job, self.cancel, self.client, self.poll_s = job, cancel, client, poll_s
        self._report = report
        self.resumed = bool(resume and resume.get("completed_chunks"))
        self.V = resume["folder"] if self.resumed else job["folder"]
        self.scratch = os.path.join(job["frames_dir"], "_work")
        self.names = dict(resume.get("names", {})) if self.resumed else {}
        self.next_free = int(resume.get("next_free", 0)) if self.resumed else 0
        self.completed = list(resume["completed_chunks"]) if self.resumed else []
        self.dropped = int(resume.get("dropped", 0)) if self.resumed else 0
        self.notes = []
        self.detect_paths = {}          # slot -> full-size Detect mask (fresh key-mode run only)
        self.plan = chunks.plan_chunks(job["first"], job["last"], job["key"] if job["mode"] == "key" else None,
                                       job["chunk_size"])
        self.auto_names = versions.object_names(job["text"], ["text"] * MAX_SLOTS)

    # -- small helpers
    def say(self, msg):
        try:
            self._report(msg)
        except Exception:
            pass

    def check(self):
        if self.cancel.is_set():
            raise JobCancelled()

    def mask_path(self, slot, frame):
        return "%s/obj%d.%04d.png" % (self.V, slot, frame)

    def upload_as(self, src, name, subfolder):
        """Upload `src` under the file name `name` (the API takes the name from the file)."""
        self.check()
        os.makedirs(self.scratch, exist_ok=True)
        tmp = os.path.join(self.scratch, name)
        shutil.copyfile(src, tmp)
        return self.client.upload(tmp, subfolder)

    def queue_and_wait(self, workflow):
        self.check()
        pid = self.client.queue(workflow)
        return self.client.wait(pid, self.cancel, self.poll_s)

    def file_seeds(self, frame):
        """[(slot, path)] of the registered slots that have pixels on `frame`, in slot order."""
        out = []
        for slot in sorted(int(s) for s in self.names):
            path = self.mask_path(slot, frame)
            if os.path.exists(path) and not png.is_blank(path):
                out.append((slot, path))
        return out

    def register(self, slot, name=None):
        self.names[str(slot)] = name or self.auto_names[slot]
        self.next_free = max(self.next_free, slot + 1)

    # -- key mode: Detect
    def detect_stage(self):
        job = self.job
        self.say("Detecting...")
        key = self.upload_as(job["key_png"], "%s_key.png" % job["job"], "nukecomfy")
        workflow, _outs = graph.build_detect({
            "key_image": key, "text": job["text"], "positive": job["positive"], "negative": job["negative"],
            "prefix": "NukeComfy/%s/detect/obj" % job["job"]})
        try:
            outputs = self.queue_and_wait(workflow)
        except JobError as e:
            if e.node_id == DETECT_NODE and (e.exception_type == "IndexError" or "out of bounds" in str(e)):
                raise NothingFound("SAM3 found no object for this prompt.")
            raise
        images = (outputs.get(DETECT_NODE) or {}).get("images") or []
        if not images:
            raise NothingFound("SAM3 found no object for this prompt.")
        os.makedirs(self.scratch, exist_ok=True)
        found = []                                      # (kind, path), batch order = points object first
        for i, image in enumerate(images):
            self.check()
            path = "%s/detect_%d.png" % (self.scratch, i)
            self.client.download(image, path)
            if png.is_blank(path):
                continue
            found.append(("select" if job["positive"] and i == 0 else "text", path))
        if job.get("stencil"):
            st = job["stencil"]
            text_ix = [n for n, (kind, _p) in enumerate(found) if kind == "text"]
            grids = []
            for n in text_ix:
                w, h, data = png.read_mask(found[n][1], MASK_WIDTH)
                grids.append(png.to_grid(w, h, data, st["cols"], st["rows"]))
            keep = set(text_ix[i] for i in hints.drop_inside_stencil(grids, st["values"]))
            found = [f for n, f in enumerate(found) if found[n][0] != "text" or n in keep]
        if len(found) > job["max_objects"]:
            self.dropped += len(found) - job["max_objects"]
            found = found[:job["max_objects"]]
        if not found:
            raise NothingFound("SAM3 found no object for this prompt (or the Stencil covers all of them).")
        names = versions.object_names(job["text"], [kind for kind, _p in found])
        for slot, ((_kind, path), name) in enumerate(zip(found, names)):
            self.names[str(slot)] = name
            self.detect_paths[slot] = path
        self.next_free = len(found)

    # -- chunks
    def chunk_inputs(self, chunk):
        """(seeds [(slot, path)], text, n_save, pass_name) for a chunk."""
        job = self.job
        if job["mode"] == "key":
            if chunk["seed"] == "detect":
                seeds = sorted(self.detect_paths.items()) if self.detect_paths else self.file_seeds(job["key"])
            else:
                seeds = self.file_seeds(self.plan[chunk["seed_chunk"]]["frames"][-1])
            return seeds, "", len(seeds), chunk["direction"]
        base = max(1, min(MAX_SLOTS, int(job["max_objects"])))
        if chunk["seed"] == "text":
            return [], job["text"], base, "text"
        if graph.WHOLE_RANGE_SEED_WITH_TEXT:
            seeds = self.file_seeds(self.plan[chunk["seed_chunk"]]["frames"][-1])
            return seeds, job["text"], min(MAX_SLOTS, max(base, len(seeds))), "text"
        return [], job["text"], base, "text"

    def run_chunk(self, chunk):
        job = self.job
        idx, total = chunk["index"], len(self.plan)
        label = "Chunk %d/%d" % (idx + 1, total)
        frames = chunk["frames"]
        seeds, text, n_save, pass_name = self.chunk_inputs(chunk)
        if not seeds and not text:
            self.say("%s: nothing left to track, skipped" % label)
            self.notes.append("%s skipped: no object left to seed it" % label)
            self.completed.append(idx)
            return
        sub = ("nukecomfy/%s_c%d" if graph.UPLOAD_NESTED else "nukecomfy_%s_c%d") % (job["job"], idx)
        ordered = sorted(frames)
        for n, frame in enumerate(ordered, 1):
            self.check()
            self.client.upload("%s/f_%05d.png" % (job["frames_dir"], frame - job["first"]), sub)
            self.say("Uploading chunk %d/%d (%d/%d)" % (idx + 1, total, n, len(ordered)))
        seed_values = [self.upload_as(path, "%s_seed%d_%d.png" % (job["job"], idx, j), "nukecomfy")
                       for j, (_slot, path) in enumerate(seeds)]
        workflow, outs = graph.build_track({
            "directory": sub, "reverse": chunk["direction"] == "bwd", "track_w": job["track_w"],
            "track_h": job["track_h"], "seeds": seed_values, "text": text, "max_objects": job["max_objects"],
            "n_save": n_save, "prefix": "NukeComfy/%s/c%d/obj" % (job["job"], idx), "pass_name": pass_name})
        self.say("%s: tracking..." % label)
        outputs = self.queue_and_wait(workflow)

        # download every saved slot of this chunk to scratch; image i belongs to frames[i] (tracking order)
        work = os.path.join(self.scratch, "c%d" % idx)
        _rmtree(work)
        os.makedirs(work)
        try:
            by_obj = {v["obj"]: node_id for node_id, v in outs.items()}
            files = {}
            for j in range(n_save):
                images = (outputs.get(by_obj[j]) or {}).get("images") or []
                if len(images) != len(frames):
                    raise JobError("%s: ComfyUI returned %d images for %d frames (result %d)"
                                   % (label, len(images), len(frames), j))
                files[j] = []
                for i, image in enumerate(images):
                    self.check()
                    dest = "%s/s%d_%d.png" % (work, j, i)
                    self.client.download(image, dest)
                    files[j].append(dest)
                self.say("%s: downloaded %d/%d" % (label, j + 1, n_save))
            self.place(chunk, seeds, files)
        finally:
            _rmtree(work)
        self.completed.append(idx)

    def nonblank(self, chunk, paths):
        """True when a saved image of the result (the skipped overlap frame excluded) has pixels."""
        first = 1 if chunk["skip_first"] else 0
        return any(not png.is_blank(p) for p in paths[first:])

    def commit(self, chunk, slot, paths):
        for i, p in enumerate(paths):
            if chunk["skip_first"] and i == 0:
                continue                                   # that frame was written by the previous chunk
            os.replace(p, self.mask_path(slot, chunk["frames"][i]))

    def new_slot(self):
        """Next free slot number, or None (and a count in `dropped`) when all slots are taken."""
        if self.next_free >= MAX_SLOTS:
            self.dropped += 1
            return None
        slot = self.next_free
        self.register(slot)
        return slot

    def place(self, chunk, seeds, files):
        job = self.job
        if job["mode"] == "key":
            for j, (slot, _path) in enumerate(seeds):
                self.commit(chunk, slot, files[j])
            return
        if chunk["seed"] == "text":
            for j in sorted(files):
                if self.nonblank(chunk, files[j]):
                    self.register(j)
                    self.commit(chunk, j, files[j])
            return
        if graph.WHOLE_RANGE_SEED_WITH_TEXT:
            for j in sorted(files):
                if j < len(seeds):
                    self.commit(chunk, seeds[j][0], files[j])
                elif self.nonblank(chunk, files[j]):
                    slot = self.new_slot()
                    if slot is not None:
                        self.commit(chunk, slot, files[j])
            return
        # text only: match what came back to the known slots on the overlap frame
        overlap = chunk["frames"][0]
        try:
            known = {slot: png.read_mask(path, MASK_WIDTH)[2] for slot, path in self.file_seeds(overlap)}
            cand = [j for j in sorted(files) if self.nonblank(chunk, files[j])]
            found = [png.read_mask(files[j][0], MASK_WIDTH)[2] for j in cand]
            slots, nf = chunks.match_objects(known, found, self.next_free, MAX_SLOTS)
        except ValueError as e:
            raise JobError("Chunk %d: cannot match objects between chunks: %s" % (chunk["index"] + 1, e))
        self.next_free = nf
        for j, slot in zip(cand, slots):
            if slot is None:
                self.dropped += 1
                continue
            if str(slot) not in self.names:
                self.register(slot)
            self.commit(chunk, slot, files[j])

    # -- the whole job
    def execute(self):
        if self.job["mode"] == "key" and not self.resumed:
            self.detect_stage()
        for chunk in self.plan:
            if chunk["index"] in self.completed:
                continue
            self.run_chunk(chunk)

    def finish(self, error, cancelled):
        job = self.job
        spans = {}
        if os.path.isdir(self.V):
            for fname in sorted(os.listdir(self.V)):
                m = _MASK_FILE.match(fname)
                if not m:
                    continue
                slot, frame = int(m.group(1)), int(m.group(2))
                if not png.is_blank(os.path.join(self.V, fname)):
                    lo, hi = spans.get(slot, (frame, frame))
                    spans[slot] = (min(lo, frame), max(hi, frame))
            for fname in os.listdir(self.V):
                m = _MASK_FILE.match(fname)
                if m and int(m.group(1)) not in spans:
                    os.remove(os.path.join(self.V, fname))      # a slot that never showed anything
        names = {str(s): self.names.get(str(s), "obj%d" % s) for s in sorted(spans)}
        objects = [{"index": s, "name": names[str(s)], "first": spans[s][0], "last": spans[s][1]} for s in sorted(spans)]
        n_chunks = len(self.plan)
        done = sorted(self.completed)
        partial = bool(done) and len(done) < n_chunks
        if cancelled:
            message = "Cancelled after %d of %d chunks." % (len(done), n_chunks)
        elif error:
            message = "Stopped after %d of %d chunks: %s" % (len(done), n_chunks, error)
        else:
            message = "%d object%s, %d chunk%s." % (len(objects), "" if len(objects) == 1 else "s",
                                                    n_chunks, "" if n_chunks == 1 else "s")
        if self.dropped:
            message += " %d object%s over the limit dropped." % (self.dropped, "" if self.dropped == 1 else "s")
        if self.notes:
            message += " " + "; ".join(self.notes) + "."
        return {"folder": self.V, "first": job["first"], "last": job["last"],
                "key": job["key"] if job["mode"] == "key" else None, "mode": job["mode"], "text": job["text"],
                "width": job["track_w"], "height": job["track_h"], "objects": objects, "names": names,
                "next_free": self.next_free, "completed_chunks": done, "n_chunks": n_chunks, "partial": partial,
                "cancelled": cancelled, "error": error, "dropped": self.dropped,
                "hints": {"select": bool(job["positive"]), "stencil": job.get("stencil") is not None,
                          "text": bool(job["text"])},
                "message": message}

    def cleanup(self):
        _rmtree(self.scratch)
        _rmtree(os.path.join(self.V, "_plate"))
        _rmtree(os.path.join(self.job["folder"], "_plate"))
        if os.path.basename(self.job["frames_dir"].rstrip("/\\")) == "_plate":
            _rmtree(self.job["frames_dir"])


def run_job(job, report, cancel, client=None, resume=None, poll_s=2.0):
    """Run one SAM3Matte Generate. Returns the C3 result dict (see module doc); raises only NothingFound."""
    if client is None:
        client = ComfyClient(api.ComfyAPIClient().base_url)
    run = _Run(job, report, cancel, client, resume, poll_s)
    error, cancelled = None, False
    try:
        try:
            run.execute()
        except NothingFound:
            raise
        except JobCancelled:
            cancelled = True
        except JobError as e:
            error = str(e)
        except Exception as e:  # a bug or a bad file must not kill the worker thread silently
            error = "%s: %s" % (type(e).__name__, e)
        result = run.finish(error, cancelled)
        if (job["mode"] == "range" and not error and not cancelled and not result["objects"]
                and not run.resumed):
            raise NothingFound("SAM3 found no object for this prompt in the range.")
        return result
    finally:
        run.cleanup()
