"""
menu.py
=======
Nuke menu file that registers the NukeMCP listener controls.

Place this file (along with nuke.mcp_listener.py) in a directory
listed in NUKE_PATH, or copy both to your ~/.nuke/ folder, then
restart Nuke. A "NukeMCP" menu will appear in the menu bar.
"""

import importlib.util
import os

import nuke

_LISTENER_MODULE = None


def _load_listener():
    """Locate and import nuke.mcp_listener.py from the Nuke plugin path."""
    global _LISTENER_MODULE
    if _LISTENER_MODULE is not None:
        return _LISTENER_MODULE

    candidates = []
    for path in nuke.pluginPath():
        candidates.append(os.path.join(path, 'nuke.mcp_listener.py'))
    # Also check the directory containing this menu.py file.
    candidates.append(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'nuke.mcp_listener.py',
        )
    )

    for candidate in candidates:
        if os.path.isfile(candidate):
            spec = importlib.util.spec_from_file_location(
                'nuke_mcp_listener_impl', candidate
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _LISTENER_MODULE = module
            return module

    raise ImportError(
        'nuke.mcp_listener.py not found in the Nuke plugin path.'
    )


def _start_listener():
    try:
        nuke.message(_load_listener().start_listener())
    except Exception as exc:
        nuke.message(f"Failed to start listener:\n{exc}")


def _stop_listener():
    try:
        nuke.message(_load_listener().stop_listener())
    except Exception as exc:
        nuke.message(f"Failed to stop listener:\n{exc}")


def _listener_status():
    try:
        status = "running" if _load_listener().is_running() else "stopped"
        nuke.message(f"NukeMCP listener is {status}.")
    except Exception as exc:
        nuke.message(f"Could not determine listener status:\n{exc}")


def _add_menu():
    toolbar = nuke.menu('Nuke')
    menu = toolbar.addMenu('NukeMCP')
    menu.addCommand('Start Listener', _start_listener)
    menu.addCommand('Stop Listener', _stop_listener)
    menu.addCommand('Listener Status', _listener_status)
    menu.addSeparator()
    menu.addCommand(
        'About',
        lambda: nuke.message(
            'NukeMCP bridge - connects Nuke to an MCP client over TCP.'
        ),
    )


_add_menu()
