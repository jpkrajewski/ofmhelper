# Module purpose

Drive ComfyUI workflows from Python. The RunPod box is a **Pod** running
ComfyUI, not a serverless endpoint, so this talks ComfyUI's own HTTP API
(`/prompt`, `/history`, `/view`) and `RUNPOD_API_KEY` is unused on that path.
Point it at a local ComfyUI by changing one env var.

Each workflow gets one hand-written wrapper exposing the few parameters that
matter. Deliberately not code-generated: the graphs differ too much (subgraph
namespacing, link-driven widgets, an rgthree LoRA stack whose widgets are
objects) for a generator to be simpler than the wrappers it would produce.

# Module files

- `client.py` — `ComfyUIClient`. `.from_env()`, `health`, `object_info`,
  `upload_image`, `submit`, `wait` (polls with backoff), `outputs`,
  `download`, and `run` (submit+wait+download, what wrappers call). Parses
  `/prompt`'s 400 `node_errors` body into the raised message — that is the
  only place the server says *which node* was rejected.
- `graph.py` — pure helpers over an API-format graph: `load_graph`,
  `find_by_class`/`find_by_title`, `set_input`/`set_by_title`, `apply_seed`,
  `bypass_node`/`bypass_missing_loras`, `model_inputs`.
- `deps.py` — reports what a graph needs and the server lacks
  (`missing_nodes`, `missing_models`, `resolve_repos`, `report`). Never
  installs.
- `wrappers/` — one module per workflow, each with `WORKFLOW`, `METADATA`
  (for a later HTML form builder), one function, and a runnable
  `if __name__ == "__main__":` example.
- `workflows/*.api.json` — API-format exports only.

# Workflow format

`POST /prompt` accepts **API format** (`{node_id: {class_type, inputs}}`),
not the litegraph UI export (`nodes`/`links`). Export via
*Workflow > Export (API)*. `load_graph` rejects the wrong format, and also
rejects an export containing nodes with no `class_type` — ComfyUI writes
those when the exporting machine is missing that custom node, and the server
would reject the graph with a much less obvious error. Export from a server
where every node in the graph is installed.

# State of each workflow

- `krea2_simple` — verified end to end against the pod.
- `krea2_raw` — verified. Its three LoRAs are not on the pod and are bypassed
  by default (`use_loras=False`), so output is the base model's look.
- `krea2_carousel` — the only image-input workflow. Its character/POV LoRAs
  are absent and get bypassed; the identity-edit LoRA is substituted v1_1 ->
  v1_2. Needs a source image.
- `krea2_danrisi`, `krea2_krast` — **incomplete exports**, 2 and 12
  unserialized nodes respectively (in krast, including the model/CLIP/VAE
  loaders). Re-export both from the pod and they should work unchanged.

Model names in the exports frequently do not match what is on the pod (a bf16
vs fp8 build of the same thing). Wrappers default to the on-pod names and take
`unet`/`clip`/`vae` overrides.

# Who calls this

Nothing yet — scripts and `__main__` blocks only. `METADATA` on each wrapper
is the hook for a future HTML form builder.

# Config

`OFM_RUNPOD_COMFY_BASE_URL` (default `http://127.0.0.1:8188`),
`OFM_RUNPOD_TIMEOUT_S`, `OFM_RUNPOD_POLL_INTERVAL_S`,
`OFM_RUNPOD_OUTPUT_DIR`, `RUNPOD_API_KEY`. See `config/settings.py`
(`RunpodSettings`).

Pod HTTP proxy: `https://<pod-id>-8188.proxy.runpod.net`. Or tunnel:
`ssh -N -L 8188:localhost:8188 root@<host> -p <port>`.

Restart ComfyUI on the pod:

```bash
pkill -f main.py
cd /workspace/ComfyUI && ./venv/bin/python3 main.py \
    --listen 0.0.0.0 --port 8188 --enable-cors-header
```
