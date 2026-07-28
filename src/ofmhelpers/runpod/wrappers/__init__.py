"""One module per ComfyUI workflow, each exposing a plain Python function.

Every wrapper follows the same shape:

  * a module-level ``WORKFLOW`` path to its API-format JSON,
  * a ``METADATA`` dict describing its inputs (for a later HTML form builder),
  * one function taking the parameters that matter, everything else keyword
    with a default, returning ``list[Path]`` of downloaded images,
  * an ``if __name__ == "__main__":`` block with a runnable example.

Model-name defaults are what is actually present on the pod, which is not
always what the exported JSON references -- several graphs were authored
against a differently-built copy of the same model.
"""
