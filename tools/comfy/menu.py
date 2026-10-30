import nuke
import os

def create_nuke_comfy_menu():
    # Create the top-level menu
    m = nuke.menu('Nuke')
    comfy_menu = m.addMenu('NukeComfy')

    # Add any .gizmo files from the gizmos folder dynamically
    gizmo_dir = None
    for p in nuke.pluginPath():
        if p.endswith('NukeComfy/gizmos') or p.endswith('NukeComfy\\\\gizmos'):
            gizmo_dir = p
            break

    if not gizmo_dir:
        gizmo_dir = os.path.join(os.path.dirname(__file__), 'gizmos')

    if os.path.exists(gizmo_dir):
        for file in os.listdir(gizmo_dir):
            if file.endswith('.gizmo'):
                name = os.path.splitext(file)[0]
                comfy_menu.addCommand(name, f'nuke.createNode("{name}")')
            elif file.endswith('.nk'):
                # .nk tools are single Group snippets: pasted into the script, no plugin path needed at render time
                name = os.path.splitext(file)[0]
                path = os.path.join(gizmo_dir, file).replace('\\', '/')
                comfy_menu.addCommand(name, f'nuke.nodePaste("{path}")')

# Execute menu creation
create_nuke_comfy_menu()
