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

    def _find_node(self, workflow, title, class_type=None):
        """Finds a node in the workflow by its title marker or class type."""
        # 1. Try finding by title marker first (e.g., NUKE_PROMPT)
        for node_id, node_info in workflow.items():
            if node_info.get('_meta', {}).get('title') == title:
                return node_id

        # 2. Fallback to class type if provided
        if class_type:
            for node_id, node_info in workflow.items():
                if node_info.get('class_type') == class_type:
                    return node_id
        return None

    def patch_workflow(self, workflow, node, input_files=None):
        """
        Injects Nuke data into the ComfyUI API JSON.
        Matches nodes via title markers (e.g., NUKE_PROMPT) or class types.
        """
        # Extract values from Nuke knobs
        pos_prompt = node.knob('prompt_pos').value() if node.knob('prompt_pos') else ""
        seed = int(node.knob('seed').value()) if node.knob('seed') else 0
        steps = int(node.knob('steps').value()) if node.knob('steps') else 20
        cfg = float(node.knob('cfg').value()) if node.knob('cfg') else 8.0

        # Mapping: (Marker Title, Fallback Class, Input Key, Value)
        mappings = [
            ('NUKE_PROMPT', 'CLIPTextEncode', 'text', pos_prompt),
            ('NUKE_PROMPT', 'GeminiImage2Node', 'prompt', pos_prompt),
            ('NUKE_SEED', 'RandomNoise', 'noise_seed', seed),
            ('NUKE_SEED', 'GeminiImage2Node', 'seed', seed),
            ('NUKE_STEPS', 'Flux2Scheduler', 'steps', steps),
            ('NUKE_CFG', 'CFGGuider', 'cfg', cfg),
            ('NUKE_SEED_UPSCALER', 'SeedVR2VideoUpscaler', 'seed', seed),
        ]

        for title, cls, key, value in mappings:
            node_id = self._find_node(workflow, title, cls)
            if node_id:
                workflow[node_id]['inputs'][key] = value

        # Special handling for EXR input path
        if input_files and 0 in input_files:
            exr_path = input_files[0].replace('\\', '/')
            input_node_id = self._find_node(workflow, 'NUKE_INPUT', 'Load EXR (ACEScg)')
            if not input_node_id:
                input_node_id = self._find_node(workflow, 'NUKE_INPUT', 'Load EXR')

            if input_node_id:
                workflow[input_node_id]['inputs']['image_path'] = exr_path

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
