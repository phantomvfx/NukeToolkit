import json
import urllib.request
import urllib.parse
import urllib.error
import os

class ComfyAPIClient:
    def __init__(self, base_url="http://127.0.0.1:8188"):
        self.base_url = base_url

    def load_template(self, template_name):
        """Loads a JSON workflow from the /json/ folder."""
        template_path = os.path.join(os.path.dirname(__file__), '..', 'json', f'{template_name}.json')
        if not os.path.exists(template_path):
            return {"prompt": {}}
        with open(template_path, 'r') as f:
            return json.load(f)

    def patch_workflow(self, workflow, node, input_files=None, frame_index=None):
        """
        Injects Nuke data into the ComfyUI API JSON.
        Handles specific mapping for KleinEdit and other workflows.
        input_files: dict of {input_index: rendered_file_path} from bridge.render_inputs
        """
        # Common values
        pos_prompt = node.knob('prompt_pos').value() if node.knob('prompt_pos') else ""
        seed = int(node.knob('seed').value()) if node.knob('seed') else 0
        steps = int(node.knob('steps').value()) if node.knob('steps') else 20
        cfg = float(node.knob('cfg').value()) if node.knob('cfg') else 8.0

        # 1. Inject Prompts (Look for CLIPTextEncode or GeminiImage2Node)
        # For KleinEdit: Node 133 is Positive Prompt
        if '133' in workflow and workflow['133'].get('class_type') == 'CLIPTextEncode':
            workflow['133']['inputs']['text'] = pos_prompt
        else:
            for node_id, node_info in workflow.items():
                if node_info.get('class_type') == 'CLIPTextEncode':
                    node_info['inputs']['text'] = pos_prompt
                    break
                elif node_info.get('class_type') == 'GeminiImage2Node':
                    node_info['inputs']['prompt'] = pos_prompt
                    break

        # 2. Inject Seed (Look for RandomNoise or GeminiImage2Node - Node 126 in KleinEdit)
        if '126' in workflow and workflow['126'].get('class_type') == 'RandomNoise':
            workflow['126']['inputs']['noise_seed'] = seed
        else:
            for node_id, node_info in workflow.items():
                if node_info.get('class_type') == 'RandomNoise':
                    node_info['inputs']['noise_seed'] = seed
                    break
                elif node_info.get('class_type') == 'GeminiImage2Node':
                    node_info['inputs']['seed'] = seed
                    break

        # 3. Inject Steps (Look for Flux2Scheduler - Node 132 in KleinEdit)
        if '132' in workflow and workflow['132'].get('class_type') == 'Flux2Scheduler':
            workflow['132']['inputs']['steps'] = steps
        else:
            for node_id, node_info in workflow.items():
                if node_info.get('class_type') == 'Flux2Scheduler':
                    node_info['inputs']['steps'] = steps
                    break

        # 4. Inject CFG (Look for CFGGuider - Node 131 in KleinEdit)
        if '131' in workflow and workflow['131'].get('class_type') == 'CFGGuider':
            workflow['131']['inputs']['cfg'] = cfg
        else:
            for node_id, node_info in workflow.items():
                if node_info.get('class_type') == 'CFGGuider':
                    node_info['inputs']['cfg'] = cfg
                    break

        # 5. Inject EXR input path (Look for Load EXR - Node 166 in KleinEdit)
        if input_files and 0 in input_files:
            exr_path = input_files[0]
            if '166' in workflow:
                workflow['166']['inputs']['image_path'] = exr_path.replace('\\', '/')
                if frame_index is not None:
                    workflow['166']['inputs']['frame_index'] = frame_index
            else:
                for node_id, node_info in workflow.items():
                    if node_info.get('class_type') in ['Load EXR (ACEScg)', 'Load EXR']:
                        node_info['inputs']['image_path'] = exr_path.replace('\\', '/')
                        if frame_index is not None:
                            node_info['inputs']['frame_index'] = frame_index
                        break

        # 6. Sync SeedVR2 upscaler seed (Node 160 in KleinEdit)
        if '160' in workflow and workflow['160'].get('class_type') == 'SeedVR2VideoUpscaler':
            workflow['160']['inputs']['seed'] = seed
        else:
            for node_id, node_info in workflow.items():
                if node_info.get('class_type') == 'SeedVR2VideoUpscaler':
                    node_info['inputs']['seed'] = seed
                    break

        return workflow

    def send_prompt(self, prompt_json):
        """Sends the patched JSON to the ComfyUI /prompt endpoint."""
        payload = {"prompt": prompt_json}
        try:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(f"{self.base_url}/prompt", data=data, headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            print(f"API Error: {e}")
            return None

    def check_history(self):
        """Polls the /history endpoint to check for completion."""
        try:
            req = urllib.request.Request(f"{self.base_url}/history")
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            print(f"History Error: {e}")
            return {}

    def get_output_images(self, prompt_id):
        """Get the output image info from ComfyUI history for a given prompt_id."""
        history = self.check_history()
        if prompt_id not in history:
            return []
        outputs = history[prompt_id].get('outputs', {})
        images = []
        for node_id, node_output in outputs.items():
            for img in node_output.get('images', []):
                images.append(img)
        return images

    def download_output(self, filename, subfolder, output_type, save_path):
        """Download an output image from ComfyUI /view endpoint and save it locally."""
        try:
            query_string = urllib.parse.urlencode({
                'filename': filename,
                'subfolder': subfolder,
                'type': output_type
            })
            url = f"{self.base_url}/view?{query_string}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=60) as response:
                with open(save_path, 'wb') as f:
                    f.write(response.read())
            return save_path
        except Exception as e:
            print(f"Download Error: {e}")
            return None

api_client = ComfyAPIClient()
