# Setup

Machine setup for the `L:\Nuke` monorepo. Paths assume the repo is at `L:\Nuke`.

## 1. Nuke loader (one line)

`~/.nuke/init.py`:

```python
nuke.pluginAddPath('L:/Nuke')
```

`L:\Nuke\init.py` then adds:

| Plugin path | Gives you |
|---|---|
| `python/` | `mllamas_tools` (menu started by `L:\Nuke\menu.py`) |
| `tools/comfy` | NukeComfy menu, also importable as the `NukeComfy` package |
| `tools/mcp` | MCP listener on `127.0.0.1:54321`, auto-started |
| `tools/taptrack/nuke` | Nodes ▸ TAPTrack |

Don't add any of these separately, keep copies of `nuke_mcp_listener.py` in
`~/.nuke`, or leave a `~/.nuke/NukeComfy` junction behind. All of them shadow
or duplicate the repo versions. TAPTrack's own `install/add_to_nuke.py` isn't
needed when the root loader is used.

## 2. NukeMCP (Claude ↔ live Nuke)

```powershell
cd L:\Nuke\tools\mcp
py -3.10 -m venv .venv
.venv\Scripts\pip install mcp
claude mcp add --scope user nuke -- L:\Nuke\tools\mcp\.venv\Scripts\python.exe L:\Nuke\tools\mcp\nuke_mcp_server.py
```

Check: with Nuke open, `Get-NetTCPConnection -LocalPort 54321` shows `Listen`
and `/mcp` in Claude Code lists `nuke` as connected. In Claude Code you can
also run `/nuke-status`.

## 3. NukeComfy (ComfyUI)

- ComfyUI at `http://127.0.0.1:8188` by default. Override with `COMFY_URL`.
  `tools/comfy/Nuke_ComfyUI.bat` offers local (Tailscale) or RunPod and then
  launches Nuke 17.
- Output pickup: `COMFYUI_OUTPUT_DIR` (default `C:\ComfyUI\output`).
- Models: see `tools/comfy/README.md` (Qwen-Image 2.1, FLUX 3 Klein, …).

## 4. TAPTrack (ML point tracking)

Full guide: `tools/taptrack/README.md`. In short:

```powershell
cd L:\Nuke\tools\taptrack
.\install\setup_venv.ps1          # Python 3.11 venv + tapnet at the pinned commit
.\install\download_weights.ps1    # -> checkpoints/
.\install\check_install.ps1
```

The venv (about 4.4 GB), `checkpoints/` (about 1.9 GB), `vendor/` and test
footage are local only. After moving the repo, the venv's `python.exe` still
works, but the `.exe` shims (`pytest.exe`, `pip.exe`) keep the old path, so
use `.venv\Scripts\python.exe -m pytest`. Saved TAPTrack nodes store
`worker_python`. Fix stale ones in the node's Settings tab or with the
`TAPTRACK_PYTHON` env var.

## 5. Knowledge base

`knowledge/` isn't in git. Copy it from a backup to `L:\Nuke\knowledge\`
(see `knowledge/README.md`). Without it, the `nuke-docs` agent and doc
lookups have nothing to search.

## Installed Nuke builds

`L:\Apps\Nuke13.2v4\Nuke13.2.exe` · `L:\Apps\Nuke16.1v4\Nuke16.1.exe` (baseline) · `L:\Apps\Nuke17.0v1\Nuke17.0.exe`
