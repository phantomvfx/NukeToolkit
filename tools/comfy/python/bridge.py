import nuke
import os
import json
from datetime import datetime
from NukeComfy.python.api import api_client
from NukeComfy.python.polling import start_comfy_process

class ComfyBridge:
    def __init__(self):
        self.comfy_url = "http://127.0.0.1:8188"
        self.temp_folder = self._resolve_temp_folder()

    def _resolve_temp_folder(self):
        """Resolves the output path based on Nuke script directory."""
        script_path = nuke.root().name()
        if not script_path or script_path == 'Root':
            return os.path.join(os.path.expanduser("~"), "nuke_comfy_temp").replace("\\", "/")

        base_dir = os.path.dirname(script_path)
        render_dir = os.path.abspath(os.path.join(base_dir, "..", "prerender", "airender")).replace("\\", "/")

        if not os.path.exists(render_dir):
            try:
                os.makedirs(render_dir)
            except:
                return os.path.join(os.path.expanduser("~"), "nuke_comfy_temp").replace("\\", "/")

        return render_dir

    def render_inputs(self, node, first=None, last=None):
        """
        Renders connected inputs to EXR files.
        Input 0: Source — the EXR to send to ComfyUI
        """
        rendered_files = {}
        inputs_to_render = {
            0: "Source",
        }

        for idx, name in inputs_to_render.items():
            try:
                upstream = node.input(idx)
                if upstream:
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    if first is not None and last is not None:
                        filename = f"comfy_input_{idx}_{timestamp}.%04d.exr"
                    else:
                        filename = f"comfy_input_{idx}_{timestamp}.exr"
                    full_path = os.path.join(self.temp_folder, filename).replace("\\", "/")
                    w = nuke.nodes.Write(file=full_path, file_type="exr")
                    w.setInput(0, upstream)

                    w.knob('create_directories').setValue(True)
                    w.knob('write_ACES_compliant_EXR').setValue(True)

                    knobs_to_set = {
                        'first_part': 'rgba',
                        'colorspace': 'scene_linear',
                        'version': 5,
                        'ocioColorspace': 'scene_linear',
                        'display': 'sRGB - Display',
                        'view': 'ACES 1.0 - SDR Video'
                    }

                    for k, v in knobs_to_set.items():
                        if w.knob(k):
                            w.knob(k).setValue(v)

                    if first is not None and last is not None:
                        nuke.execute(w, first, last)
                    else:
                        cur_frame = nuke.frame()
                        nuke.execute(w, cur_frame, cur_frame)
                        
                    nuke.delete(w)
                    rendered_files[idx] = full_path
            except Exception as e:
                print(f"Error rendering input {name}: {e}")

        return rendered_files

    def generate(self, node):
        """
        Main execution flow: Render -> Pass to Polling system
        """
        node.knob('status').setValue("Rendering...")
        try:
            input_files = self.render_inputs(node)
            if 0 not in input_files:
                node.knob('status').setValue("Error: No source input connected")
                nuke.message("KleinEdit requires a source EXR connected to its input.")
                return False
            start_comfy_process(node, input_files)
            return True

        except Exception as e:
            node.knob('status').setValue(f"Error: {str(e)}")
            nuke.message(f"ComfyBridge Error: {str(e)}")
            return False

    def generate_sequence(self, node):
        """
        Main execution flow for sequence: Render Sequence -> Pass to Polling system
        """
        try:
            from NukeComfy.python.polling import start_comfy_sequence_process
            
            root = nuke.root()
            default_first = int(root['first_frame'].value())
            default_last = int(root['last_frame'].value())
            
            p = nuke.Panel("Generate Sequence")
            p.addExpressionInput("Start Frame", default_first)
            p.addExpressionInput("End Frame", default_last)
            if not p.show():
                return False
                
            first = int(p.value("Start Frame"))
            last = int(p.value("End Frame"))
            
            node.knob('status').setValue(f"Rendering {first}-{last}...")
            
            input_files = self.render_inputs(node, first=first, last=last)
            if 0 not in input_files:
                node.knob('status').setValue("Error: No source input connected")
                nuke.message("Sequence generation requires a source EXR connected.")
                return False
                
            start_comfy_sequence_process(node, input_files, first, last)
            return True
            
        except Exception as e:
            node.knob('status').setValue(f"Error: {str(e)}")
            nuke.message(f"Sequence Error: {str(e)}")
            return False

bridge = ComfyBridge()
