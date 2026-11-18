#!/usr/bin/env python3
# Copyright 2026 David
# SPDX-License-Identifier: LGPL-2.1-or-later
"""
End-to-end check with FreeCAD: convert each tests/data/*.svg with the
extension (in the Inkscape Flatpak if installed), open the result in
FreeCAD (Flatpak org.freecad.FreeCAD, or a native freecadcmd), and check:

- every sketch solves (solver result 0), and solving moves nothing: the
  constraints agree with the drawing; none is malformed, conflicting or
  redundant; the shapes are valid;
- every point FreeCAD samples along the sketch edges lies within 0.005 mm
  of the drawing's own shapes (sampled here with inkex, independently of
  the extension), and every sampled point of the drawing within 0.005 mm
  of the sketch: the same geometry, nothing missing, nothing extra.

    tests/run.py            needs inkex 1.4 and numpy on the host too
"""

import json
import math
import os
import shutil
import subprocess
import sys

import inkex
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WORK = os.path.join(HERE, "work")
TOLERANCE = 0.005  # mm
sys.path.insert(0, REPO)

from svg2fcsketch import Mapping, Svg2FcSketch


def have_flatpak(app):
    return bool(shutil.which("flatpak")) and (
        subprocess.run(
            ["flatpak", "info", app], capture_output=True, check=False
        ).returncode
        == 0
    )


def convert(svg, out, *args):
    if have_flatpak("org.inkscape.Inkscape"):
        cmd = [
            "flatpak",
            "run",
            f"--filesystem={REPO}",
            "--env=PYTHONPATH=/app/share/inkscape/extensions",
            "--command=python3",
            "org.inkscape.Inkscape",
        ]
    else:
        cmd = [sys.executable]
    subprocess.run(
        [*cmd, os.path.join(REPO, "svg2fcsketch.py"), *args, f"--output={out}", svg],
        check=True,
    )


def freecad(fcstd, report):
    if have_flatpak("org.freecad.FreeCAD"):
        cmd = [
            "flatpak",
            "run",
            f"--filesystem={REPO}",
            f"--env=IN={fcstd}",
            f"--env=OUT={report}",
            "--command=freecadcmd",
            "org.freecad.FreeCAD",
        ]
        env = None
    else:
        cmd = ["freecadcmd"]
        env = {**os.environ, "IN": fcstd, "OUT": report}
    result = subprocess.run(
        [*cmd, os.path.join(HERE, "freecad_check.py")],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if "FREECAD CHECK DONE" not in result.stdout:
        raise SystemExit(f"FreeCAD failed:\n{result.stdout}\n{result.stderr}")
    with open(report) as f:
        return json.load(f)


def spec_arc(p0, arc, steps=400):
    """Points along an SVG arc, straight from the SVG 1.1 implementation
    notes (F.6.5 and F.6.6: out-of-range radii are scaled up). Written
    separately from the extension's own arc code, on purpose."""
    (x1, y1), (x2, y2) = p0, (arc.x, arc.y)
    rx, ry, phi = abs(arc.rx), abs(arc.ry), math.radians(arc.x_axis_rotation)
    c, s = math.cos(phi), math.sin(phi)
    xp = c * (x1 - x2) / 2 + s * (y1 - y2) / 2
    yp = -s * (x1 - x2) / 2 + c * (y1 - y2) / 2
    lam = xp**2 / rx**2 + yp**2 / ry**2
    if lam > 1:
        rx, ry = math.sqrt(lam) * rx, math.sqrt(lam) * ry
    sign = -1 if arc.large_arc == arc.sweep else 1
    root = sign * math.sqrt(
        max(
            0,
            (rx * rx * ry * ry - rx * rx * yp * yp - ry * ry * xp * xp)
            / (rx * rx * yp * yp + ry * ry * xp * xp),
        )
    )
    cxp, cyp = root * rx * yp / ry, -root * ry * xp / rx
    cx, cy = c * cxp - s * cyp + (x1 + x2) / 2, s * cxp + c * cyp + (y1 + y2) / 2
    t1 = math.atan2((yp - cyp) / ry, (xp - cxp) / rx)
    t2 = math.atan2((-yp - cyp) / ry, (-xp - cxp) / rx)
    dt = t2 - t1
    if arc.sweep and dt < 0:
        dt += 2 * math.pi
    if not arc.sweep and dt > 0:
        dt -= 2 * math.pi
    t = t1 + dt * np.linspace(0, 1, steps)
    return np.stack(
        [
            cx + c * rx * np.cos(t) - s * ry * np.sin(t),
            cy + s * rx * np.cos(t) + c * ry * np.sin(t),
        ],
        axis=1,
    )


def reference(svg):
    """Each layer's shapes as polylines, in FreeCAD millimetres: Beziers
    and lines sampled exactly, arcs from the SVG formulas."""
    document = inkex.load_svg(svg).getroot()
    mapping = Mapping(document)
    out = {}
    for label, roots in Svg2FcSketch.groups_of(document):
        lines = []
        for node in Svg2FcSketch.shapes(roots):
            m = node.composed_transform()

            def emit(points, m=m, lines=lines):
                lines.append(
                    np.array(
                        [
                            mapping.point(m.apply_to_point((float(p[0]), float(p[1]))))
                            for p in points
                        ]
                    )
                )

            path = node.path.to_absolute().to_non_shorthand()
            if isinstance(node, (inkex.Circle, inkex.Ellipse)):
                cx, cy = node.center
                rx = ry = node.radius if isinstance(node, inkex.Circle) else None
                if rx is None:
                    rx, ry = node.radius.x, node.radius.y
                t = np.linspace(0, 2 * math.pi, 2000)
                emit(np.stack([cx + rx * np.cos(t), cy + ry * np.sin(t)], axis=1))
                continue
            start = None
            for proxy in path.proxy_iterator():
                seg, prev = proxy.command, proxy.previous_end_point
                t = np.linspace(0, 1, 400)[:, None]
                if isinstance(seg, inkex.paths.Move):
                    start = proxy.end_point
                elif isinstance(seg, inkex.paths.Line):
                    emit([prev, proxy.end_point])
                elif isinstance(seg, inkex.paths.ZoneClose):
                    emit([prev, start])
                elif isinstance(seg, inkex.paths.Arc):
                    emit(spec_arc((prev.x, prev.y), seg))
                else:  # Bezier: Curve or Quadratic
                    pts = [
                        np.array(prev, float),
                        *(np.array(q, float) for q in proxy.control_points),
                    ]
                    n = len(pts) - 1
                    xy = sum(
                        math.comb(n, i) * (1 - t) ** (n - i) * t**i * pts[i]
                        for i in range(n + 1)
                    )
                    emit(xy)
        out[label] = lines
    return out


def rendered(svg, layer_label):
    """Inkscape's own render of one layer: True where it drew, about
    0.02 mm per pixel, with the pixel size in mm."""
    from lxml import etree
    from PIL import Image

    tree = etree.parse(svg)
    ns = {
        "svg": "http://www.w3.org/2000/svg",
        "inkscape": "http://www.inkscape.org/namespaces/inkscape",
    }
    for layer in tree.xpath('//svg:g[@inkscape:groupmode="layer"]', namespaces=ns):
        if layer.get(f"{{{ns['inkscape']}}}label") != layer_label:
            layer.getparent().remove(layer)
    one = os.path.join(WORK, "layer.svg")
    tree.write(one)
    png = os.path.join(WORK, "layer.png")
    dpi = 1270
    cmd = (
        ["flatpak", "run", f"--filesystem={REPO}", "org.inkscape.Inkscape"]
        if have_flatpak("org.inkscape.Inkscape")
        else ["inkscape"]
    )
    subprocess.run(
        [
            *cmd,
            one,
            "--export-area-page",
            f"--export-dpi={dpi}",
            "--export-background=#ffffff",
            "--export-background-opacity=1",
            f"--export-filename={png}",
        ],
        check=True,
        capture_output=True,
    )
    Image.MAX_IMAGE_PIXELS = None
    return np.asarray(Image.open(png).convert("L")) < 200, 25.4 / dpi


def segments(polylines):
    starts = np.concatenate([p[:-1] for p in polylines if len(p) > 1])
    ends = np.concatenate([p[1:] for p in polylines if len(p) > 1])
    return starts, ends


def distance(points, polylines):
    """Largest distance from any of `points` to the nearest segment of
    `polylines`."""
    a, b = segments(polylines)
    ab = b - a
    length2 = np.maximum((ab**2).sum(-1), 1e-30)
    worst = 0.0
    for chunk in np.array_split(points, max(1, len(points) // 200)):
        ap = chunk[:, None, :] - a[None, :, :]
        t = np.clip((ap * ab[None]).sum(-1) / length2[None], 0, 1)
        nearest = a[None] + t[..., None] * ab[None]
        d = np.sqrt(((chunk[:, None, :] - nearest) ** 2).sum(-1)).min(axis=1)
        worst = max(worst, float(d.max()))
    return worst


def main():
    os.makedirs(WORK, exist_ok=True)
    failures = []
    data = os.path.join(HERE, "data")
    for name in sorted(n for n in os.listdir(data) if n.endswith(".svg")):
        svg = os.path.join(data, name)
        for variant, args in (
            ("", ()),
            ("-controls", ("--expose_internal_geometry=true",)),
        ):
            stem = os.path.splitext(name)[0] + variant
            fcstd = os.path.join(WORK, stem + ".FCStd")
            convert(svg, fcstd, *args)
            sketches = freecad(fcstd, os.path.join(WORK, stem + ".json"))
            expected = reference(svg)
            print(stem)
            for sketch in sketches:
                label = sketch["label"]
                problems = (
                    [k for k in ("malformed", "conflicting", "redundant") if sketch[k]]
                    + ([] if sketch["solve"] == 0 else ["solve"])
                    + ([] if sketch["valid"] else ["invalid"])
                )
                if sketch["moved"] > 1e-6:
                    problems.append(f"moved {sketch['moved']:.2g} mm")
                got = [np.array(p) for p in sketch["points"] if len(p) > 1]
                ref = expected.get(label)
                if ref is None or not got:
                    problems.append("no matching layer")
                    there = back = math.inf
                else:
                    there = distance(np.concatenate(got), ref)
                    back = distance(np.concatenate(ref), got)
                    # and against what Inkscape itself draws
                    drawn, mm = rendered(svg, label)
                    pts = np.concatenate(got)
                    height = drawn.shape[0]
                    # Inkscape renders only the page: check what lies on it
                    on_page = (
                        (pts[:, 0] > 0.1)
                        & (pts[:, 0] < drawn.shape[1] * mm - 0.1)
                        & (pts[:, 1] > 0.1)
                        & (pts[:, 1] < height * mm - 0.1)
                    )
                    pts = pts[on_page]
                    cols = np.clip(
                        np.round(pts[:, 0] / mm).astype(int), 0, drawn.shape[1] - 1
                    )
                    rows = np.clip(
                        np.round(height - pts[:, 1] / mm).astype(int), 0, height - 1
                    )
                    reach = math.ceil(0.05 / mm)
                    near = np.zeros(len(pts), bool)
                    for dy in range(-reach, reach + 1):
                        for dx in range(-reach, reach + 1):
                            r = np.clip(rows + dy, 0, height - 1)
                            c = np.clip(cols + dx, 0, drawn.shape[1] - 1)
                            near |= drawn[r, c]
                    off_render = int((~near).sum())
                    if off_render:
                        problems.append(f"{off_render} points off Inkscape's render")
                    if there > TOLERANCE or back > TOLERANCE:
                        problems.append(f"geometry off by {max(there, back):.4f} mm")
                print(
                    f"  {label}: {'ok' if not problems else 'FAIL'}  {sketch['geometry']} geometries "
                    f"({sketch['construction']} construction), {sketch['constraints']} constraints, "
                    f"solve {sketch['solve']}, sketch to drawing {there:.5f} mm, back {back:.5f} mm"
                    + (f"  [{', '.join(problems)}]" if problems else "")
                )
                if problems:
                    failures.append(f"{stem}/{label}: {problems}")
            if len(sketches) != len(expected):
                failures.append(
                    f"{stem}: {len(sketches)} sketches, {len(expected)} layers"
                )

    # the same drawing in other units must give the same sketches
    def numbers(fcstd):
        import re
        import zipfile

        xml = zipfile.ZipFile(fcstd).read("Document.xml").decode()
        return [
            float(v)
            for v in re.findall(
                r'(?:X|Y|Radius|Angle\w*|Start\w|End\w)="(-?[0-9.]+)"', xml
            )
        ]

    base = numbers(os.path.join(WORK, "shapes.FCStd"))
    other = numbers(os.path.join(WORK, "shapes-scaled.FCStd"))
    same = (
        len(base) == len(other) and max(abs(a - b) for a, b in zip(base, other)) < 1e-9
    )
    print(
        f"shapes-scaled gives the same sketches as shapes: {'ok' if same else 'FAIL'}"
    )
    if not same:
        failures.append("shapes-scaled differs from shapes")
    print("ALL PASS" if not failures else "FAILED:\n  " + "\n  ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
