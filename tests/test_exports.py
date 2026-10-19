import pytest

from ofmhelpers.exports import (
    CapcutExport,
    DeviceExport,
    ExportOptions,
    OriginalExport,
    exports,
    get_export,
)
from ofmhelpers.iphones import available


def test_registry_lists_original_capcut_then_every_device_profile():
    names = list(exports())

    assert names[:2] == ["original", "capcut"]
    assert names[2:] == available()
    assert isinstance(get_export("original"), OriginalExport)
    assert isinstance(get_export("capcut"), CapcutExport)
    assert isinstance(get_export("iphone17"), DeviceExport)


def test_device_exports_are_labelled_with_the_phone_model():
    assert get_export("iphone17").label == "iPhone 17 Pro Max"


def test_unknown_export_is_an_error():
    with pytest.raises(ValueError, match="unknown export"):
        get_export("nokia3310")


MIRROR = ExportOptions(mirror=True)


def test_mirror_switch_reaches_the_device_recording(monkeypatch, tmp_path):
    import ofmhelpers.exports as module

    seen = []
    monkeypatch.setattr(module, "record", lambda *a: seen.append(a[3].mirror))
    src = tmp_path / "v.mp4"

    plain = get_export("iphone17").render(src, ExportOptions())
    flipped = get_export("iphone17").render(src, MIRROR)

    assert seen == [False, True]
    assert (plain.name, flipped.name) == ("v_iphone17.mov", "v_iphone17_mirror.mov")


def test_mirror_switch_reaches_the_capcut_preset(monkeypatch, tmp_path):
    import ofmhelpers.exports as module

    seen = []
    monkeypatch.setattr(module, "apply_preset", lambda *a: seen.append(a[2].mirror))

    out = get_export("capcut").render(tmp_path / "v.mp4", MIRROR)

    assert seen == [True]
    assert out.name == "v_capcut_mirror.mp4"


def test_original_is_untouched_without_switches(tmp_path):
    src = tmp_path / "v.mp4"
    assert get_export("original").render(src, ExportOptions()) == src


def test_original_with_mirror_is_flipped(monkeypatch, tmp_path):
    import ofmhelpers.exports as module

    seen = []
    monkeypatch.setattr(module, "run", lambda args, _t: seen.append(args))

    out = get_export("original").render(tmp_path / "v.mp4", MIRROR)

    assert out.name == "v_original_mirror.mp4"
    assert seen[0][seen[0].index("-vf") + 1] == "hflip"
