# Copyright 2026 David
# SPDX-License-Identifier: LGPL-2.1-or-later
"""
Unit tests without FreeCAD or Inkscape (inkex 1.4 and numpy needed):

    python3 -m pytest tests/test_units.py
"""

import io
import math
import os
import sys
import zipfile
from xml.etree import ElementTree

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import itertools

import inkex

import fcstd
from svg2fcsketch import Mapping, Svg2FcSketch, arc_beziers, arc_centre


def convert(body, *args, size='width="100mm" height="50mm" viewBox="0 0 100 50"'):
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        f"{size}>{body}</svg>"
    )
    out = io.BytesIO()
    path = os.path.join(HERE, "work", "unit.svg")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(svg)
    Svg2FcSketch().run([*args, path], output=out)
    xml = zipfile.ZipFile(io.BytesIO(out.getvalue())).read("Document.xml")
    return ElementTree.fromstring(xml)


def geometry(doc):
    return [g for g in doc.iter("Geometry")]


def constraints(doc):
    return [
        (
            int(c.get("Type")),
            int(c.get("First")),
            int(c.get("FirstPos")),
            int(c.get("Second")),
            int(c.get("SecondPos")),
        )
        for c in doc.iter("Constrain")
    ]


def test_mapping_flips_y_and_uses_mm():
    svg = inkex.load_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" '
        'viewBox="-10 0 200 100"/>'
    ).getroot()
    m = Mapping(svg)
    assert m.point((-10, 100)) == pytest.approx((0, 0))  # lower left
    assert m.point((190, 0)) == pytest.approx((100, 50))  # upper right


def test_rectangle_lines_and_constraints():
    doc = convert('<rect x="10" y="10" width="30" height="20"/>')
    kinds = [g.get("type") for g in geometry(doc)]
    assert kinds == ["Part::GeomLineSegment"] * 4
    cons = constraints(doc)
    assert sum(1 for c in cons if c[0] == fcstd.COINCIDENT) == 4  # closed loop
    assert sum(1 for c in cons if c[0] == fcstd.HORIZONTAL) == 2
    assert sum(1 for c in cons if c[0] == fcstd.VERTICAL) == 2


def test_no_hv_constraints_option():
    doc = convert(
        '<rect x="10" y="10" width="30" height="20"/>', "--hv_constraints=false"
    )
    assert {c[0] for c in constraints(doc)} == {fcstd.COINCIDENT}


def test_circle_and_skewed_circle():
    doc = convert(
        '<circle cx="20" cy="20" r="5"/><g transform="skewX(20)"><circle cx="60" cy="20" r="5"/></g>'
    )
    kinds = [g.get("type") for g in geometry(doc)]
    assert kinds == ["Part::GeomCircle", "Part::GeomEllipse"]
    circle = next(doc.iter("Circle"))
    assert float(circle.get("Radius")) == pytest.approx(5)
    assert float(circle.get("CenterY")) == pytest.approx(30)  # 50 - 20


def test_arc_direction_mirrored():
    # the same half circle, drawn mirrored: the arc stays on the same side
    plain = convert('<path d="M 10,20 A 5,5 0 0 1 20,20"/>')
    mirrored = convert(
        '<g transform="translate(30 0) scale(-1 1)"><path d="M 20,20 A 5,5 0 0 0 10,20"/></g>'
    )
    for doc in (plain, mirrored):
        arc = next(doc.iter("ArcOfCircle"))
        mid = (float(arc.get("StartAngle")) + float(arc.get("EndAngle"))) / 2
        # SVG sweep 1 from left to right goes over the top on screen: y up in FreeCAD
        assert math.sin(mid) > 0.99


def test_bezier_is_exact_bspline():
    doc = convert('<path d="M 0,0 C 10,10 20,10 30,0"/>')
    spline = next(doc.iter("BSplineCurve"))
    assert spline.get("Degree") == "3"
    poles = [(float(p.get("X")), float(p.get("Y"))) for p in spline.iter("Pole")]
    assert poles == [(0, 50), (10, 40), (20, 40), (30, 50)]


def test_control_points_exposed():
    doc = convert(
        '<path d="M 0,0 C 10,10 20,10 30,0"/>', "--expose_internal_geometry=true"
    )
    construction = [
        g for g in geometry(doc) if g.find("Construction").get("value") == "1"
    ]
    assert len(construction) == 4
    aligned = [
        c
        for c in doc.iter("Constrain")
        if c.get("Type") == str(fcstd.INTERNAL_ALIGNMENT)
    ]
    assert [c.get("InternalAlignmentIndex") for c in aligned] == ["0", "1", "2", "3"]


@pytest.mark.parametrize(
    "d",
    [
        "M 104,30 A 10,5 30 0 1 90,32",
        "M 0,0 A 10,5 0 1 1 20,0",
        "M 0,0 A 3,3 0 0 0 20,0",
    ],
)
def test_arc_maths_end_exactly(d):
    path = inkex.Path(d)
    proxy = list(path.proxy_iterator())[1]
    start = (proxy.previous_end_point.x, proxy.previous_end_point.y)
    params = arc_centre(start, proxy.command)
    pieces = arc_beziers(start, (proxy.command.x, proxy.command.y), params)
    assert pieces[0][0] == start
    assert pieces[-1][-1] == (proxy.command.x, proxy.command.y)
    for a, b in itertools.pairwise(pieces):  # joined
        assert a[-1] == pytest.approx(b[0], abs=1e-9)


def test_layers_become_sketches():
    doc = convert(
        '<g inkscape:groupmode="layer" inkscape:label="One"><rect width="5" height="5"/></g>'
        '<g inkscape:groupmode="layer" inkscape:label="Two"><circle r="3"/></g>'
    )
    labels = [
        o.find(".//Property[@name='Label']/String").get("value")
        for o in doc.find("ObjectData")
    ]
    assert labels == ["One", "Two"]
    assert [o.get("name") for o in doc.find("Objects")] == ["Sketch", "Sketch001"]
    assert all(o.get("Touched") == "1" for o in doc.find("Objects"))
