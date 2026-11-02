"""
SAM3Matte, Nuke layer: the glue between the SAM3Matte Group's buttons and knobs and the Nuke-free runner.

What it does
  generate(node)        Generate button. On the main thread: checks the inputs, renders the plate (and the key frame),
                        samples Select / Stencil on the key frame, builds the job. Then a daemon thread runs
                        sam3matte_runner.run_job and, when it ends, adds (or replaces the resumed) version on the main thread.
  cancel(node)          Cancel button: asks the running job to stop (the runner interrupts ComfyUI).
  knob_changed(node, k) version / objects / hard_stencil / source_range / key_current / mode / inputChange.
  show_version(node, i) points the Group's Reads at version i and sets the hidden knobs, the Cryptomatte names.
  apply_objects(node)   obj_on0..7 from the Objects knob and the current version's objects.
  render_plate(...)     tracking-size frames + the full-size key frame as 8-bit PNGs, through the script's display transform.
  sample_input(...)     alpha grid of the Select (input 1) or Stencil (input 2) on one frame.

Inputs : the SAM3Matte Group (gizmos/SAM3Matte.nk; knobs and inner node names are contracts C5/C6 of the plan).
Output : mask PNGs `<temp>/<job>/obj<k>.####.png` (written by the runner), versions in the hidden knob `versions_data`.

Plate colours (decision 7): with OCIO colour management the plate goes through a temporary OCIODisplay (the project's
default viewer process, i.e. the display and view of root.monitorLut, else the config's default) into a RAW 8-bit PNG
Write, so SAM3 sees what the artist sees (tone-mapped, not clipped). With colour management off (Nuke default LUTs) the
Write is a normal sRGB 8-bit PNG. The temporary nodes are created inside the Group and deleted afterwards.

Threads: the worker never touches a knob. Status text goes through `_post_status` (nuke.executeInMainThread) and the
finished version through `_on_main` (nuke.executeInMainThreadWithResult); both swallow the errors a deleted node or a
closed script raises. Tests replace both.

Target Nuke 16.1 (Python 3.11). Tested: headless Nuke 16.1 against stub run_job (tests/nuke/test_sam3matte_nuke.py).
The real executeInMainThread paths and the Generate button in a GUI session have not been run.
"""
import json
import os
import re
import shutil
import threading
import traceback
import urllib.request
from datetime import datetime

import nuke

try:
    from . import api
    from . import bridge as bridge_module
    from . import sam3matte_chunks as chunks
    from . import sam3matte_crypto as crypto
    from . import sam3matte_hints as hints
    from . import sam3matte_versions as versions
    from .sam3matte_runner import ComfyClient, NothingFound, run_job
except ImportError:  # run as bare modules (scripts)
    import api
    import bridge as bridge_module
    import sam3matte_chunks as chunks
    import sam3matte_crypto as crypto
    import sam3matte_hints as hints
    import sam3matte_versions as versions
    from sam3matte_runner import ComfyClient, NothingFound, run_job

SLOTS = 8
SUBSAMPLES = 4      # per axis, inside each Select / Stencil grid cell
MIN_TRACK_WIDTH = 64

_RUNNING = {}      # node full name -> {"thread": Thread, "cancel": Event}
_quiet = [0]       # >0 while this module sets the version knob itself: knob_changed must not show the version a second time


# ---------------------------------------------------------------------------------------------- main-thread plumbing

def _on_main(func):
    """Run `func` on Nuke's main thread and return its result. Replaced in tests."""
    return nuke.executeInMainThreadWithResult(func)


def _post_status(node, text):
    """Set node['status'] from any thread. A deleted node or closed script is ignored. Replaced in tests."""
    def apply():
        try:
            node["status"].setValue(text)
        except (ValueError, RuntimeError, NameError):
            pass
    try:
        nuke.executeInMainThread(apply)
    except Exception:
        pass


def _message(text):
    """Modal message for an error the artist must see. Replaced in tests."""
    try:
        nuke.message(text)
    except Exception:
        print(text)


def _set_status(node, text):
    try:
        node["status"].setValue(text)
    except (ValueError, RuntimeError, NameError):
        pass


# ------------------------------------------------------------------------------------------------------ small helpers

def _check_server():
    try:
        urllib.request.urlopen(f"{api.api_client.base_url}/system_stats", timeout=5).close()
    except Exception as e:
        raise RuntimeError(f"ComfyUI is not reachable at {api.api_client.base_url} ({e}). Start it, or check COMFY_URL.")


def _temp_folder():
    # NUKECOMFY_TEMP wins, so an unsaved script does not write its renders to the home folder
    folder = os.environ.get("NUKECOMFY_TEMP") or bridge_module.bridge._resolve_temp_folder()
    os.makedirs(folder, exist_ok=True)
    return folder.replace("\\", "/")


def _versions(node):
    try:
        data = json.loads(node["versions_data"].value() or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _current_index(node):
    n = len(_versions(node))
    if not n:
        return None
    try:
        return max(0, min(int(node["version"].getValue()), n - 1))
    except (ValueError, TypeError):
        return n - 1


def _current_version(node):
    idx = _current_index(node)
    return None if idx is None else _versions(node)[idx]


def _source_format(node):
    fmt = node.node("Source").format()
    return int(fmt.width()), int(fmt.height())


def _source_range(node):
    """First and last frame of the Source input. A node without a range of its own (a Constant, a still) reports
    a single frame; the project range is used then."""
    src = node.input(0)
    first, last = int(src.firstFrame()), int(src.lastFrame())
    if last <= first:
        root = nuke.root()
        first, last = int(root["first_frame"].value()), int(root["last_frame"].value())
    return first, last


# ------------------------------------------------------------------------------------------------------- plate render

def _ocio_display_view():
    """(display, view) of the script's default viewer process, or None when colour management is not OCIO."""
    root = nuke.root()
    if not root.knob("colorManagement") or root["colorManagement"].value() != "OCIO":
        return None
    probe = nuke.nodes.OCIODisplay()
    try:
        displays, views = probe["display"].values(), probe["view"].values()
        default = (probe["display"].value(), probe["view"].value())
    finally:
        nuke.delete(probe)
    # root.monitorLut reads "<view> (<display>)"; names may contain parentheses themselves
    if root.knob("monitorLut"):
        view, sep, display = root["monitorLut"].value().rpartition(" (")
        display = display[:-1] if display.endswith(")") else display
        if sep and display in displays and view in views:
            return display, view
    return default


def render_plate(node, first, last, key, track_w, track_h, folder):
    """Render the plate for SAM3. Main thread.

    Frames first..last go to `<folder>/_plate/f_<frame-first:05d>.png` at track_w x track_h, and the key frame (when
    `key` is not None) to `<folder>/_plate/key.png` at full size. Returns (frames_dir, key_png or None).
    """
    frames_dir = f"{folder}/_plate"
    os.makedirs(frames_dir, exist_ok=True)
    src = node.node("Source")
    fmt = src.format()
    full = (int(fmt.width()), int(fmt.height()))
    ocio = _ocio_display_view()
    key_png = f"{frames_dir}/key.png" if key is not None else None
    total = last - first + 1 + (1 if key is not None else 0)
    done = 0

    def write_node(tail):
        w = nuke.nodes.Write(file=f"{frames_dir}/tmp.png", file_type="png", create_directories=True)
        w["datatype"].setValue("8 bit")
        w["channels"].setValue("rgb")
        w["raw"].setValue(ocio is not None)   # OCIO: the display transform is already applied; otherwise Nuke's sRGB encode
        w.setInput(0, tail)
        return w

    with node:
        temps = []
        try:
            tail = src
            if ocio is not None:
                disp = nuke.nodes.OCIODisplay()
                disp["display"].setValue(ocio[0])
                disp["view"].setValue(ocio[1])
                disp.setInput(0, src)
                temps.append(disp)
                tail = disp
            if key is not None:
                kw = write_node(tail)
                temps.append(kw)
                kw["file"].setValue(key_png)
                nuke.execute(kw, int(key), int(key))
                done += 1
                _set_status(node, f"Rendering plate {done}/{total}")
            if (track_w, track_h) != full:
                rf = nuke.nodes.Reformat(type="to box", box_fixed=True, resize="distort", filter="Cubic")
                rf["box_width"].setValue(track_w)
                rf["box_height"].setValue(track_h)
                rf.setInput(0, tail)
                temps.append(rf)
                tail = rf
            tw = write_node(tail)
            temps.append(tw)
            for frame in range(first, last + 1):
                tw["file"].setValue(f"{frames_dir}/f_{frame - first:05d}.png")
                nuke.execute(tw, frame, frame)
                done += 1
                if done % 10 == 0 or done == total:
                    _set_status(node, f"Rendering plate {done}/{total}")
        finally:
            for n in reversed(temps):
                try:
                    nuke.delete(n)
                except Exception:
                    pass
    return frames_dir, key_png


# ------------------------------------------------------------------------------------------------ Select / Stencil grids

def sample_input(node, input_index, frame, cols=hints.GRID_COLS):
    """Alpha of the node connected to Group input `input_index` on `frame`, as a grid (row 0 = top), or None when
    that input is not connected."""
    upstream = node.input(input_index)
    if upstream is None:
        return None
    width, height = _source_format(node)
    cols, rows = hints.grid_size(width, height, cols)

    # A thin stroke is narrower than a grid cell (about 40 px at 2K), so one sample per cell can step over it:
    # each cell takes the max of SUBSAMPLES x SUBSAMPLES single-pixel samples spread evenly inside it.
    cell_w, cell_h = width / float(cols), height / float(rows)
    offsets = [((i + 0.5) / SUBSAMPLES - 0.5) for i in range(SUBSAMPLES)]

    def sampler(x, y):
        return max(upstream.sample("rgba.alpha", x + ox * cell_w, y + oy * cell_h, frame=frame)
                   for oy in offsets for ox in offsets)
    try:
        return hints.sample_grid(sampler, width, height, cols, rows)
    except (RuntimeError, ValueError) as e:
        raise RuntimeError(f"Could not read the alpha of {'Select' if input_index == 1 else 'Stencil'} "
                           f"on frame {frame}: {e}")


# ----------------------------------------------------------------------------------------------------------- versions

def _store_versions(node, vs, select=None):
    """Write the versions and the pulldown. Does not show the version (the caller calls show_version once)."""
    _quiet[0] += 1
    try:
        node["versions_data"].setValue(json.dumps(vs))
        node["version"].setValues([versions.version_label(i, v) for i, v in enumerate(vs)] or [" "])
        if select is not None:
            node["version"].setValue(int(select))
    finally:
        _quiet[0] -= 1


def apply_objects(node, index=None):
    """obj_on0..7 from the Objects knob, against the objects of version `index` (default: the one picked in the
    pulldown). show_version passes its own index, which can differ from the pulldown while it is being set."""
    if index is None:
        v = _current_version(node)
    else:
        vs = _versions(node)
        v = vs[max(0, min(int(index), len(vs) - 1))] if vs else None
    if v is None:
        return
    chosen = set(versions.parse_objects(node["objects"].value(), v.get("objects", [])))
    for k in range(SLOTS):
        node[f"obj_on{k}"].setValue(k in chosen)


def show_version(node, index):
    """Point the Group at version `index`: Reads, presence flags, ranges, Cryptomatte names. Main thread."""
    vs = _versions(node)
    if not vs:
        node["has_result"].setValue(False)
        for k in range(SLOTS):
            node[f"obj_present{k}"].setValue(False)
        return
    index = max(0, min(int(index), len(vs) - 1))
    v = vs[index]
    folder = str(v.get("folder", "")).replace("\\", "/")
    found = os.path.isdir(folder)
    present = {int(o["index"]): o for o in v.get("objects", [])} if found else {}
    first, last = int(v["first"]), int(v["last"])

    for k in range(SLOTS):
        node[f"obj_present{k}"].setValue(k in present)
        if k in present:
            rd = node.node(f"Obj{k}Read")
            rd["file"].setValue(f"{folder}/obj{k}.####.png")
            for knob, value in (("first", first), ("last", last), ("origfirst", first), ("origlast", last)):
                rd[knob].setValue(value)
            # without a reload the Read can keep showing black (on_error) from before the files were there
            try:
                rd["reload"].execute()
            except Exception:   # headless scripts answer "already executing something else"; the file is set anyway
                pass
    node["mask_first"].setValue(first)
    node["mask_last"].setValue(last)
    node["stencil_frame"].setValue(int(v["key"]) if v.get("key") is not None else first)
    names = [(v.get("names") or {}).get(str(k)) if k in present else None for k in range(SLOTS)]
    crypto.apply_to_nodes(node.node("CryptoExpr"), node.node("CryptoMeta"), names)
    apply_objects(node, index)
    node["has_result"].setValue(found)
    if not found:
        _set_status(node, f"Masks not found: {folder}")


# ----------------------------------------------------------------------------------------------------- knob callbacks

def sync_ui(node):
    """Enable the First/Last/Key frame knobs that apply to the current settings."""
    range_mode = node["mode"].value() == "Whole range"
    node["first"].setEnabled(not node["source_range"].value())
    node["last"].setEnabled(not node["source_range"].value())
    node["key_current"].setEnabled(not range_mode)
    node["key_frame"].setEnabled(not range_mode and not node["key_current"].value())


def knob_changed(node, knob):
    try:
        name = knob.name()
        if name == "version":
            if _quiet[0]:
                return
            show_version(node, int(knob.getValue()))
        elif name == "objects":
            apply_objects(node)
        elif name == "hard_stencil":
            if knob.value() and node.input(2) is None:
                _set_status(node, "Hard stencil is on but nothing is connected to Stencil, so nothing is cut")
            elif not knob.value() and node["mode"].value() == "Whole range" and node.input(2) is not None:
                _set_status(node, "Whole range always cuts the Stencil, whatever Hard stencil says")
        elif name == "source_range":
            if not knob.value() and node.input(0) is not None:   # switched off: start from the Source's range
                first, last = _source_range(node)
                node["first"].setValue(first)
                node["last"].setValue(last)
            sync_ui(node)
        elif name == "key_current":
            if not knob.value():
                node["key_frame"].setValue(int(nuke.frame()))
            sync_ui(node)
        elif name in ("mode", "inputChange"):
            sync_ui(node)
    except ValueError:  # "not attached to a node" while the Group is being pasted or deleted
        pass


# ------------------------------------------------------------------------------------------------------------- Generate

class _CountingClient(ComfyClient):
    """ComfyClient that counts the files it uploaded (ComfyUI cannot delete input files)."""
    uploaded = 0

    def upload(self, path, subfolder):
        value = super().upload(path, subfolder)
        self.uploaded += 1
        return value


def _prepare(node):
    """Validate the Group, sample Select / Stencil, render the plate and build the job. Main thread.
    Returns (job, resume, resume_index, prompt). Raises RuntimeError with the text to show."""
    _check_server()
    key_name = node.fullName()
    if key_name in _RUNNING:
        raise RuntimeError("Already running. Press Cancel to stop it.")
    if node.input(0) is None:
        raise RuntimeError("Connect a Source image")

    range_mode = node["mode"].value() == "Whole range"
    mode = "range" if range_mode else "key"
    prompt = node["prompt"].value().strip()
    select_on = node.input(1) is not None
    if range_mode and select_on:
        raise RuntimeError("Select only works on one frame: switch Mode to From key frame, or disconnect Select")
    if range_mode and not prompt:
        raise RuntimeError("Whole range needs a Prompt")
    if not range_mode and not prompt and not select_on:
        raise RuntimeError("Enter a Prompt, or connect a Select")

    if node["source_range"].value():
        first, last = _source_range(node)
    else:
        first, last = int(node["first"].value()), int(node["last"].value())
    if first < 0:
        raise RuntimeError(f"First frame {first} is negative: the mask files need frame numbers of 0 or more")
    if first > last:
        raise RuntimeError(f"First ({first}) is after Last ({last})")
    chunk_size = int(node["chunk_size"].value())
    if chunk_size < 2:
        raise RuntimeError("Chunk size must be at least 2")

    key = None
    if not range_mode:
        key = int(nuke.frame()) if node["key_current"].value() else int(node["key_frame"].value())
        if not first <= key <= last:
            raise RuntimeError(f"Key frame {key} is outside {first}-{last}")

    width, height = _source_format(node)
    track_w, track_h = chunks.tracking_size(width, height, max(MIN_TRACK_WIDTH, int(node["tracking_width"].value())))

    positive, negative, stencil = [], [], None
    if not range_mode:
        select_grid = sample_input(node, 1, key)
        if select_grid is not None:
            positive = hints.hint_points(select_grid, width, height)
            if not positive:
                raise RuntimeError(f"Select is empty on frame {key}")
        stencil_grid = sample_input(node, 2, key)
        if stencil_grid is not None:
            negative = hints.hint_points(stencil_grid, width, height)
            cols = len(stencil_grid[0])
            stencil = {"cols": cols, "rows": len(stencil_grid), "values": stencil_grid}

    job_id = f"{node.name()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    job = {"job": job_id, "folder": None, "frames_dir": None, "key_png": None, "mode": mode,
           "first": first, "last": last, "key": key, "text": prompt, "positive": positive, "negative": negative,
           "stencil": stencil, "max_objects": max(1, min(SLOTS, int(node["max_objects"].value()))),
           "chunk_size": chunk_size, "track_w": track_w, "track_h": track_h}

    resume, resume_index = None, None
    idx = _current_index(node)
    if idx is not None:
        current = _versions(node)[idx]
        if (versions.version_matches(current, job) and current.get("completed_chunks")
                and os.path.isdir(str(current.get("folder", "")))):
            resume, resume_index = current, idx

    job["folder"] = resume["folder"] if resume else f"{_temp_folder()}/{job_id}"
    try:
        job["frames_dir"], job["key_png"] = render_plate(node, first, last, key, track_w, track_h, job["folder"])
    except Exception:
        _cleanup_folder(job["folder"], resumed=resume is not None)
        raise
    return job, resume, resume_index, prompt


def _cleanup_folder(folder, resumed):
    """Remove the plate scratch, and the version folder when nothing was kept in it."""
    shutil.rmtree(f"{folder}/_plate", ignore_errors=True)
    if not resumed:
        try:
            os.rmdir(folder)     # only when empty
        except OSError:
            pass


def generate(node):
    """Entry point of the Generate button. Returns True once the worker thread has started."""
    try:
        job, resume, resume_index, prompt = _prepare(node)
    except RuntimeError as e:
        _set_status(node, str(e))
        _message(f"SAM3Matte: {e}")
        return False
    except Exception as e:
        _set_status(node, f"Error: {e}")
        _message(f"SAM3Matte: {e}")
        return False

    cancel_event = threading.Event()
    key_name = node.fullName()
    client = _CountingClient(api.api_client.base_url)
    thread = threading.Thread(target=_worker, name=f"SAM3Matte-{node.name()}", daemon=True,
                              args=(node, key_name, job, resume, resume_index, prompt, cancel_event, client))
    _RUNNING[key_name] = {"thread": thread, "cancel": cancel_event}
    _set_status(node, "Starting...")
    try:
        thread.start()
    except Exception as e:
        _RUNNING.pop(key_name, None)
        _cleanup_folder(job["folder"], resumed=resume is not None)
        _set_status(node, f"Error: {e}")
        _message(f"SAM3Matte: {e}")
        return False
    return True


def cancel(node):
    """Cancel button."""
    entry = _RUNNING.get(node.fullName())
    if entry is None:
        _set_status(node, "Nothing is running")
        return
    entry["cancel"].set()
    _set_status(node, "Cancelling...")


def _remaining_note(client):
    n = client.uploaded
    if not n:
        return ""
    return f" {n} uploaded file{'' if n == 1 else 's'} remain in ComfyUI input/nukecomfy (ComfyUI cannot delete them)."


def _node_gone(node):
    """True when the node was deleted or its script closed."""
    try:
        node.name()
        return False
    except (ValueError, RuntimeError, NameError):
        return True


def _on_main_reporting(node, func, what, hint=""):
    """Run `func` on the main thread. A deleted node is ignored; any other failure is printed (traceback) and shown
    as an Error status instead of vanishing."""
    def guarded():
        try:
            func()
        except Exception as e:
            if _node_gone(node):
                return
            traceback.print_exc()
            _set_status(node, f"Error: {what} failed ({type(e).__name__}: {e}).{hint}")
    try:
        _on_main(guarded)
    except Exception:
        traceback.print_exc()


def _finish(node, text):
    """Final status from the worker thread, through the main thread."""
    _on_main_reporting(node, lambda: _set_status(node, text), "setting the final status")


def _worker(node, key_name, job, resume, resume_index, prompt, cancel_event, client):
    """Runs on the background thread. Touches no knob except through _post_status / _on_main."""
    try:
        def report(message):
            _post_status(node, message)
        try:
            result = run_job(job, report, cancel_event, client=client, resume=resume)
        except NothingFound:
            _cleanup_folder(job["folder"], resumed=resume is not None)
            what = f'"{prompt}"' if prompt else "the Select strokes"
            _finish(node, f"Nothing found for {what}.{_remaining_note(client)}")
            return
        except Exception as e:  # the runner folds its own errors into the result; this is a bug or worse
            _cleanup_folder(job["folder"], resumed=resume is not None)
            _finish(node, f"Error: {type(e).__name__}: {e}.{_remaining_note(client)}")
            return

        _on_main_reporting(node, lambda: _add_result(node, job, result, resume, resume_index, client),
                           "adding the version", f" The masks are in {result.get('folder')}.")
        if not result["objects"]:
            _cleanup_folder(job["folder"], resumed=resume is not None)
    finally:
        _RUNNING.pop(key_name, None)


def _add_result(node, job, result, resume, resume_index, client):
    """Main thread: store the finished (or partial) version, show it, set the final status."""
    note = _remaining_note(client)
    keep = bool(result["objects"])      # a run that saved nothing leaves nothing to show
    if result.get("error"):
        text = f"Error: {result['error']}".rstrip(".") + "."
    elif result.get("cancelled"):
        text = result["message"]
    else:
        text = f"Done. {result['message']}"
    if not keep:
        _set_status(node, text + note)
        return
    vs = _versions(node)
    version = versions.new_version(result, job, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if (resume is not None and resume_index is not None and resume_index < len(vs)
            and vs[resume_index].get("folder") == resume.get("folder")):
        vs[resume_index] = version
        select = resume_index
    else:
        vs.append(version)
        select = len(vs) - 1
    _store_versions(node, vs, select)
    show_version(node, select)
    if result.get("partial"):
        text += " Partial version kept: Generate again with the same settings to continue it."
    _set_status(node, text + note)
