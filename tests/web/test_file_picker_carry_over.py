"""FilePicker.carryOver, run for real under node against a stub DOM: files
move picker-to-picker by kind, whatever each tool calls its field."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parents[2] / "src/ofmhelpers/web/static/js/file-picker.js"
).read_text(encoding="utf-8")

HARNESS = """
const el = () => ({
    dataset: {}, style: {}, classList: { add() {}, remove() {} },
    appendChild() {}, append() {}, set innerHTML(_) {},
});
globalThis.document = { addEventListener() {}, createElement: el };
globalThis.window = globalThis;
globalThis.URL = { createObjectURL: () => "blob:" };
%s
const picker = (kind, items) => ({
    dataset: { kind }, _items: items, querySelector: () => el(),
    dispatchEvent() {},
});
const root = (pickers) => ({
    querySelectorAll: () => pickers,
    querySelector: (sel) =>
        pickers.find((p) => sel.includes(`"${p.dataset.kind}"`)) || null,
});
const img = picker("image", [{ kind: "existing", path: "a.png", name: "a.png" }]);
const vid = picker("video", [{ kind: "existing", path: "v.mp4", name: "v.mp4" }]);
const dstImg = picker("image", []);
window.FilePicker.carryOver(root([img, vid]), root([dstImg]));
console.log(JSON.stringify(dstImg._items.map((i) => i.path)));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_carry_over_moves_files_by_kind():
    out = subprocess.run(
        ["node", "-e", HARNESS % SCRIPT],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout
    assert json.loads(out) == ["a.png"]
