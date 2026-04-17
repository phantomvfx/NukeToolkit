import nuke
import os

try:
    from PySide6 import QtCore, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtWidgets
from NukeComfy.python.api import api_client

class ComfyPollingThread(QtCore.QThread):
    """
    Async thread to poll ComfyUI history endpoint until the image is ready.
    """
    finished = QtCore.Signal(dict)
    error = QtCore.Signal(str)

    def __init__(self, node, input_files):
        super().__init__()
        self.node = node
        self.input_files = input_files

    def run(self):
        try:
            # Determine workflow template from the node's 'template' knob
            template_knob = self.node.knob('template')
            if template_knob:
                template_name = template_knob.value()
            else:
                # Fallback: try 'mode' knob (for MasterBridge compatibility)
                mode_knob = self.node.knob('mode')
                if mode_knob:
                    template_name = mode_knob.value().lower().replace(" ", "_")
                else:
                    self.error.emit("No 'template' or 'mode' knob found on node")
                    return

            workflow = api_client.load_template(template_name)
            if not workflow or not workflow.get('prompt', workflow):
                self.error.emit(f"Failed to load workflow template: {template_name}")
                return

            # Strip the 'prompt' wrapper if present (API format vs raw format)
            if 'prompt' in workflow and isinstance(workflow['prompt'], dict):
                workflow = workflow['prompt']

            patched_workflow = api_client.patch_workflow(workflow, self.node, self.input_files)

            self.node.knob('status').setValue("Sending to ComfyUI...")
            result = api_client.send_prompt(patched_workflow)
            if not result:
                self.error.emit("Failed to send prompt to ComfyUI API")
                return

            prompt_id = result.get('prompt_id')
            if not prompt_id:
                self.error.emit("No prompt_id returned from ComfyUI")
                return

            # Poll until completion
            while True:
                self.node.knob('status').setValue("Polling ComfyUI...")
                history = api_client.check_history()

                if prompt_id in history:
                    history_entry = history[prompt_id]
                    
                    # Check if execution had an error
                    status_obj = history_entry.get('status', {})
                    if status_obj.get('status_str') == 'error':
                        error_msg = "ComfyUI Execution Error"
                        messages = status_obj.get('messages', [])
                        for msg_list in messages:
                            if isinstance(msg_list, list) and len(msg_list) > 1 and msg_list[0] == 'execution_error':
                                error_details = msg_list[1]
                                exception_message = error_details.get('exception_message', '').strip()
                                node_type = error_details.get('node_type', 'UnknownNode')
                                error_msg = f"Error in {node_type}: {exception_message}"
                                break
                        self.error.emit(error_msg)
                        return

                    output_data = history_entry.get('outputs', {})
                    self.finished.emit(output_data)
                    break

                self.msleep(2000)

        except Exception as e:
            self.error.emit(str(e))


class ComfyResultIntegrator:
    """
    Handles the creation of the resulting Read node in Nuke.
    Downloads the output EXR from ComfyUI and creates a Read node.
    """
    @staticmethod
    def create_result_node(node, output_data, prompt_text, seed):
        # Find the output image from the Save EXR node
        output_images = []
        for node_id, node_output in output_data.items():
            images = node_output.get('images', [])
            for img in images:
                output_images.append(img)

        if not output_images:
            # Fallback for custom nodes like 'Save EXR (ACEScg)' that don't report output via standard API
            comfy_dir = os.environ.get("COMFYUI_OUTPUT_DIR", "")
            if not comfy_dir or not os.path.exists(comfy_dir):
                for path in [r"C:\ComfyUI\output", r"L:\ComfyUI\output", r"D:\ComfyUI\output", r"E:\ComfyUI\output"]:
                    if os.path.exists(path):
                        comfy_dir = path
                        break
            if os.path.exists(comfy_dir):
                import glob
                import shutil
                exr_files = glob.glob(os.path.join(comfy_dir, '*.exr'))
                if exr_files:
                    latest_exr = max(exr_files, key=os.path.getmtime)
                    
                    from NukeComfy.python.bridge import bridge
                    import time
                    local_filename = f"comfy_result_{int(time.time())}.exr"
                    save_path = os.path.join(bridge.temp_folder, local_filename).replace('\\', '/')
                    
                    try:
                        shutil.copy2(latest_exr, save_path)
                    except Exception as e:
                        print(f"Error copying: {e}")
                        save_path = latest_exr.replace('\\', '/')
                    
                    read_node = nuke.nodes.Read(file=save_path)
                    pos = node.xpos()
                    y_pos = node.ypos() + 150
                    read_node.setXYpos(pos, y_pos)
                    label = f"ComfyUI Result | Prompt: {prompt_text[:30]}... | Seed: {seed}"
                    read_node.knob('label').setValue(label)
                    node.knob('status').setValue("Done")
                    return read_node

            node.knob('status').setValue("Error: No output images found")
            return None

        # Standard API Download block
        # Download the first output image (EXR) via ComfyUI /view endpoint
        img_info = output_images[0]
        filename = img_info.get('filename', '')
        subfolder = img_info.get('subfolder', '')
        output_type = img_info.get('type', 'output')

        # Determine output extension
        ext = os.path.splitext(filename)[1].lower()

        from NukeComfy.python.bridge import bridge

        # Save to Nuke temp folder
        local_filename = f"comfy_result_latest{ext}"
        save_path = os.path.join(bridge.temp_folder, local_filename)

        downloaded = api_client.download_output(filename, subfolder, output_type, save_path)

        if not downloaded:
            node.knob('status').setValue("Error: Failed to download output from ComfyUI")
            return None

        # Create Read node pointing to the downloaded EXR
        read_node = nuke.nodes.Read(file=save_path)
        pos = node.xpos()
        y_pos = node.ypos() + 150
        read_node.setXYpos(pos, y_pos)
        label = f"ComfyUI Result | Prompt: {prompt_text[:30]}... | Seed: {seed}"
        read_node.knob('label').setValue(label)
        node.knob('status').setValue("Done")
        return read_node


_active_threads = []

def _cleanup_thread(thread):
    if thread in _active_threads:
        _active_threads.remove(thread)

def start_comfy_process(node, input_files):
    """
    PyScript entry point: Initiates the async polling cycle.
    input_files: dict of {input_index: rendered_file_path}
    """
    thread = ComfyPollingThread(node, input_files)
    _active_threads.append(thread)
    
    thread.finished.connect(lambda data: ComfyResultIntegrator.create_result_node(
        node, data,
        node.knob('prompt_pos').value() if node.knob('prompt_pos') else "",
        node.knob('seed').value() if node.knob('seed') else 0
    ))
    thread.finished.connect(lambda _: _cleanup_thread(thread))
    
    thread.error.connect(lambda err: node.knob('status').setValue(f"Error: {err}"))
    thread.error.connect(lambda _: _cleanup_thread(thread))
    
    thread.start()

import time
import shutil
import glob

class ComfySequenceThread(QtCore.QThread):
    finished = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(self, node, input_files, first, last):
        super().__init__()
        self.node = node
        self.input_files = input_files
        self.first = first
        self.last = last

    def run(self):
        try:
            template_knob = self.node.knob('template')
            template_name = template_knob.value() if template_knob else "kleinedit"
            
            seq_timestamp = int(time.time())
            seq_prefix = f"sequence_result_{seq_timestamp}"
            
            for f in range(self.first, self.last + 1):
                self.node.knob('status').setValue(f"Processing {f}/{self.last}...")
                
                # Format current frame path for Comfy input map
                current_input = {k: str(v) % f if "%04d" in str(v) else v for k, v in self.input_files.items()}
                
                workflow = api_client.load_template(template_name)
                if 'prompt' in workflow and isinstance(workflow['prompt'], dict):
                    workflow = workflow['prompt']
                    
                patched_workflow = api_client.patch_workflow(workflow, self.node, current_input, frame_index=f)
                
                result = api_client.send_prompt(patched_workflow)
                if not result:
                    self.error.emit(f"Failed prompt at frame {f}")
                    return
                    
                prompt_id = result.get('prompt_id')
                
                # Poll
                output_data = None
                while True:
                    history = api_client.check_history()
                    if prompt_id in history:
                        entry = history[prompt_id]
                        if entry.get('status', {}).get('status_str') == 'error':
                            self.error.emit(f"Error on frame {f}")
                            return
                        output_data = entry.get('outputs', {})
                        break
                    self.msleep(1500)
                    
                # Integrate the output for this specific frame
                output_images = []
                for node_id, node_output in output_data.items():
                    for img in node_output.get('images', []):
                        output_images.append(img)
                        
                from NukeComfy.python.bridge import bridge
                local_filename = f"{seq_prefix}.{f:04d}.exr"
                save_path = os.path.join(bridge.temp_folder, local_filename).replace('\\', '/')
                
                if output_images:
                    img_info = output_images[0]
                    downloaded = api_client.download_output(img_info['filename'], img_info['subfolder'], img_info.get('type', 'output'), save_path)
                    if not downloaded:
                        self.error.emit(f"Failed to download frame {f}")
                        return
                else:
                    # Fallback output parsing
                    comfy_dir = os.environ.get("COMFYUI_OUTPUT_DIR", "")
                    if not comfy_dir or not os.path.exists(comfy_dir):
                        for cpath in [r"C:\ComfyUI\output", r"L:\ComfyUI\output", r"D:\ComfyUI\output", r"E:\ComfyUI\output"]:
                            if os.path.exists(cpath):
                                comfy_dir = cpath
                                break
                    if os.path.exists(comfy_dir):
                        exr_files = glob.glob(os.path.join(comfy_dir, '*.exr'))
                        if exr_files:
                            latest_exr = max(exr_files, key=os.path.getmtime)
                            try:
                                shutil.copy2(latest_exr, save_path)
                            except:
                                pass
            
            # Sequence complete, emit sequence pad
            self.finished.emit(f"{seq_prefix}.%04d.exr")
            
        except Exception as e:
            self.error.emit(str(e))

def start_comfy_sequence_process(node, input_files, first, last):
    thread = ComfySequenceThread(node, input_files, first, last)
    _active_threads.append(thread)
    
    def on_sequence_finished(seq_mask):
        from NukeComfy.python.bridge import bridge
        read_path = os.path.join(bridge.temp_folder, seq_mask).replace('\\', '/')
        read_node = nuke.nodes.Read(file=read_path, first=first, last=last)
        pos = node.xpos()
        y_pos = node.ypos() + 150
        read_node.setXYpos(pos, y_pos)
        prompt = node.knob('prompt_pos').value() if node.knob('prompt_pos') else ""
        read_node.knob('label').setValue(f"Sequence | {prompt[:20]}...")
        node.knob('status').setValue("Done")
        
    thread.finished.connect(on_sequence_finished)
    thread.finished.connect(lambda _: _cleanup_thread(thread))
    thread.error.connect(lambda err: node.knob('status').setValue(f"Error: {err}"))
    thread.error.connect(lambda _: _cleanup_thread(thread))
    thread.start()