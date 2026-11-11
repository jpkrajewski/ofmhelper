"""The back of an iPhone as an SVG, drawn from the catalog: the body at the
model's real size class (millimetres), in the finish's color, with the
camera layout that tells models apart at a glance (`CameraLayout`). Every
card and the phone dialog's preview use it, so no photo is stored or
fetched anywhere. A new layout = one drawing function in `_LAYOUTS`."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache

from pydantic import BaseModel

from ofmhelpers.schemas.phone_catalog.models import (
    CameraLayout,
    Finish,
    PhoneModel,
    SizeClass,
)

# Width, height and corner radius (mm) of each size class: SE, the minis,
# the 6.1" phones, Plus / Max / Air.
_BODY: dict[SizeClass, tuple[float, float, float]] = {
    SizeClass.COMPACT: (67.3, 138.4, 9.0),
    SizeClass.MINI: (64.2, 131.5, 9.5),
    SizeClass.STANDARD: (71.5, 147.0, 11.0),
    SizeClass.MAX: (77.6, 160.8, 12.0),
}
# Room around the largest body, so every phone sits on the same canvas and
# a mini looks smaller than a Max.
_CANVAS = (90.0, 170.0)
_UNKNOWN = "#4b4b57"
# Luma above which a finish counts as light (raised parts drawn darker).
_LIGHT_LUMA = 150


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)


def _mix(a: str, b: str, t: float) -> str:
    """`a` moved `t` (0..1) of the way to `b`."""
    ca, cb = _rgb(a), _rgb(b)
    mixed = (round(x + (y - x) * t) for x, y in zip(ca, cb, strict=True))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def _light(hex_color: str) -> bool:
    r, g, b = _rgb(hex_color)
    return 0.299 * r + 0.587 * g + 0.114 * b > _LIGHT_LUMA


class _Pen:
    """Collects the SVG elements of one drawing, in paint order."""

    def __init__(self, finish: str) -> None:
        self.finish = finish
        self.parts: list[str] = []
        # Bumps and plateaus: a shade off the back, darker on light finishes.
        self.raised = _mix(finish, "#000000" if _light(finish) else "#ffffff", 0.1)
        self.edge = _mix(finish, "#000000", 0.35)

    def add(self, element: str) -> None:
        self.parts.append(element)

    def plate(self, x: float, y: float, w: float, h: float, r: float) -> None:
        self.add(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'rx="{r:.2f}" fill="{self.raised}" stroke="{self.edge}" '
            'stroke-width="0.5"/>'
        )

    def lens(self, cx: float, cy: float, r: float) -> None:
        self.add(
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="#202126" '
            f'stroke="{_mix(self.finish, "#9a9aa2", 0.6)}" stroke-width="{r * 0.18:.2f}"/>'
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r * 0.58:.2f}" fill="#07070a"/>'
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r * 0.3:.2f}" fill="#1c2a44"/>'
            f'<circle cx="{cx - r * 0.25:.2f}" cy="{cy - r * 0.28:.2f}" '
            f'r="{r * 0.13:.2f}" fill="#ffffff" fill-opacity="0.55"/>'
        )

    def flash(self, cx: float, cy: float, r: float = 2.2) -> None:
        self.add(
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="#f4ecd6" '
            'stroke="#b9ae92" stroke-width="0.35"/>'
        )

    def dot(self, cx: float, cy: float, r: float) -> None:
        """LiDAR or a microphone: a small dark disc."""
        self.add(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="#16161b"/>')


class _Corner(BaseModel, frozen=True):
    x: float
    y: float


@dataclass(frozen=True)
class _Camera:
    """One phone's camera, drawn with `pen` inside the body whose top-left
    corner is (x, y) and width `w`; coordinates are millimetres. Each layout
    is a method."""

    pen: _Pen
    x: float
    y: float
    w: float
    lidar: bool

    def single_bare(self) -> None:
        pen, x, y = self.pen, self.x, self.y
        pen.lens(x + 9.0, y + 9.5, 4.2)
        pen.flash(x + 17.5, y + 9.5, 1.6)

    def single_bump(self) -> None:
        pen, x, y, w = self.pen, self.x, self.y, self.w
        s = w * 0.36
        pen.plate(x + 4.5, y + 4.5, s, s, s * 0.24)
        pen.lens(x + 4.5 + s * 0.38, y + 4.5 + s * 0.38, s * 0.24)
        pen.flash(x + 4.5 + s * 0.76, y + 4.5 + s * 0.3, 1.6)

    def pill_x(self) -> None:
        pen, x, y, w = self.pen, self.x, self.y, self.w
        pw, ph = w * 0.17, w * 0.42
        px, py = x + 6.0, y + 5.0
        pen.plate(px, py, pw, ph, pw / 2)
        cx = px + pw / 2
        pen.lens(cx, py + pw / 2, pw * 0.38)
        pen.flash(cx, py + ph / 2, 1.4)
        pen.lens(cx, py + ph - pw / 2, pw * 0.38)

    def _square(self, s: float) -> _Corner:
        """The square bump of side `s`; its top-left corner."""
        bx, by = self.x + 4.5, self.y + 4.5
        self.pen.plate(bx, by, s, s, s * 0.24)
        return _Corner(x=bx, y=by)

    def dual_square(self) -> None:
        pen, w = self.pen, self.w
        s = w * 0.42
        corner = self._square(s)
        bx, by = corner.x, corner.y
        pen.lens(bx + s * 0.3, by + s * 0.28, s * 0.19)
        pen.lens(bx + s * 0.3, by + s * 0.72, s * 0.19)
        pen.flash(bx + s * 0.74, by + s * 0.26, 1.7)
        pen.dot(bx + s * 0.74, by + s * 0.5, 0.5)

    def dual_diagonal(self) -> None:
        pen, w = self.pen, self.w
        s = w * 0.44
        corner = self._square(s)
        bx, by = corner.x, corner.y
        pen.lens(bx + s * 0.29, by + s * 0.29, s * 0.2)
        pen.lens(bx + s * 0.71, by + s * 0.71, s * 0.2)
        pen.flash(bx + s * 0.74, by + s * 0.27, 1.7)
        pen.dot(bx + s * 0.27, by + s * 0.74, 0.5)

    def dual_pill(self) -> None:
        pen, x, y, w = self.pen, self.x, self.y, self.w
        pw, ph = w * 0.22, w * 0.5
        px, py = x + 5.0, y + 5.0
        pen.plate(px, py, pw, ph, pw / 2)
        cx = px + pw / 2
        pen.lens(cx, py + pw / 2, pw * 0.38)
        pen.lens(cx, py + ph - pw / 2, pw * 0.38)
        pen.flash(px + pw + 3.6, py + ph * 0.3, 1.6)
        pen.dot(px + pw + 3.6, py + ph * 0.55, 0.5)

    def _triangle(self, bx: float, by: float, s: float) -> None:
        pen = self.pen
        r = s * 0.2
        pen.lens(bx + s * 0.28, by + s * 0.26, r)
        pen.lens(bx + s * 0.28, by + s * 0.74, r)
        pen.lens(bx + s * 0.72, by + s * 0.5, r)

    def triple(self) -> None:
        pen, w, lidar = self.pen, self.w, self.lidar
        s = w * 0.5
        corner = self._square(s)
        bx, by = corner.x, corner.y
        self._triangle(bx, by, s)
        pen.flash(bx + s * 0.74, by + s * 0.17, 1.6)
        if lidar:
            pen.dot(bx + s * 0.74, by + s * 0.84, 1.6)

    def plateau_triple(self) -> None:
        pen, x, y, w, lidar = self.pen, self.x, self.y, self.w, self.lidar
        h = w * 0.5
        pen.plate(x + 2.5, y + 3.0, w - 5.0, h, 8.0)
        self._triangle(x + 4.0, y + 3.0, h)
        fx = x + w - 13.0
        pen.flash(fx, y + 3.0 + h * 0.32, 1.7)
        if lidar:
            pen.dot(fx, y + 3.0 + h * 0.68, 1.7)

    def plateau_single(self) -> None:
        pen, x, y, w = self.pen, self.x, self.y, self.w
        h = w * 0.26
        pen.plate(x + 2.5, y + 3.0, w - 5.0, h, h / 2)
        cy = y + 3.0 + h / 2
        pen.lens(x + 3.0 + h / 2, cy, h * 0.33)
        pen.dot(x + 3.0 + h * 1.25, cy, h * 0.13)
        pen.flash(x + w - 3.0 - h / 2, cy, 1.5)


_LAYOUTS: dict[CameraLayout, Callable[[_Camera], None]] = {
    CameraLayout.SINGLE_BARE: _Camera.single_bare,
    CameraLayout.SINGLE_BUMP: _Camera.single_bump,
    CameraLayout.PILL_X: _Camera.pill_x,
    CameraLayout.DUAL_SQUARE: _Camera.dual_square,
    CameraLayout.DUAL_DIAGONAL: _Camera.dual_diagonal,
    CameraLayout.DUAL_PILL: _Camera.dual_pill,
    CameraLayout.TRIPLE: _Camera.triple,
    CameraLayout.PLATEAU_TRIPLE: _Camera.plateau_triple,
    CameraLayout.PLATEAU_SINGLE: _Camera.plateau_single,
}


@lru_cache(maxsize=512)
def render(model: PhoneModel | None, finish: Finish | None) -> str:
    """The SVG document of `model`'s back in `finish`; a plain phone in a
    neutral color for one the catalog does not list (or no color)."""
    color = finish.hex if finish else _UNKNOWN
    size = model.size if model else SizeClass.STANDARD
    w, h, r = _BODY[size]
    cw, ch = _CANVAS
    x, y = (cw - w) / 2, (ch - h) / 2
    pen = _Pen(color)
    shine = _mix(color, "#ffffff", 0.22)
    pen.add(
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{shine}"/><stop offset="0.55" '
        f'stop-color="{color}"/><stop offset="1" stop-color="{_mix(color, "#000000", 0.12)}"/>'
        "</linearGradient></defs>"
    )
    pen.add(
        f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{r:.2f}" '
        f'fill="url(#g)" stroke="{pen.edge}" stroke-width="1.1"/>'
    )
    if model is not None:
        _LAYOUTS[model.camera](_Camera(pen, x, y, w, model.lidar))
    label = f"{model.label} back" if model else "Phone back"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {cw:g} {ch:g}" '
        f'role="img" aria-label="{label}">{"".join(pen.parts)}</svg>'
    )
