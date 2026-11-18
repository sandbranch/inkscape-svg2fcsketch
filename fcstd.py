# Copyright (C) 2026 David
#
# This library is free software; you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation; either version 2.1 of the License, or (at
# your option) any later version.
#
# This library is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
"""
Write a FreeCAD document (.FCStd) holding Sketcher sketches, without
FreeCAD: a zip with a Document.xml. The XML follows what FreeCAD 1.1
itself writes for these objects; the sketches are saved as needing a
recompute, and FreeCAD builds their shapes from the geometry.

Constraint and point codes, as FreeCAD 1.1 reports them: Coincident 1,
Horizontal 2, Vertical 3, InternalAlignment 15 (B-spline control point:
alignment type 9); point positions: none 0, start 1, end 2, centre 3.
"""

import io
import zipfile
from xml.sax.saxutils import quoteattr

COINCIDENT, HORIZONTAL, VERTICAL, INTERNAL_ALIGNMENT = 1, 2, 3, 15
BSPLINE_CONTROL_POINT = 9
NONE, START, END, CENTRE = 0, 1, 2, 3
UNUSED = -2000  # FreeCAD's "no geometry" index


def f(value):
    return f"{float(value):.16f}"


class Sketch:
    """Geometry and constraints of one sketch, in millimetres."""

    def __init__(self, label):
        self.label = label
        self.geometry = []  # (xml, construction, internal type)
        self.constraints = []  # xml

    def _add(self, xml, construction=False, internal=0):
        self.geometry.append((xml, construction, internal))
        return len(self.geometry) - 1

    def line(self, p0, p1, construction=False):
        return self._add(
            f'<LineSegment StartX="{f(p0[0])}" StartY="{f(p0[1])}" StartZ="{f(0)}" '
            f'EndX="{f(p1[0])}" EndY="{f(p1[1])}" EndZ="{f(0)}"/>',
            construction,
        )

    def circle(self, centre, radius, construction=False, internal=0):
        return self._add(
            f'<Circle CenterX="{f(centre[0])}" CenterY="{f(centre[1])}" CenterZ="{f(0)}" '
            f'NormalX="{f(0)}" NormalY="{f(0)}" NormalZ="{f(1)}" AngleXU="{f(0)}" '
            f'Radius="{f(radius)}"/>',
            construction,
            internal,
        )

    def arc(self, centre, radius, start, end):
        """Counter-clockwise from angle `start` to `end` (radians)."""
        return self._add(
            f'<ArcOfCircle CenterX="{f(centre[0])}" CenterY="{f(centre[1])}" CenterZ="{f(0)}" '
            f'NormalX="{f(0)}" NormalY="{f(0)}" NormalZ="{f(1)}" AngleXU="{f(0)}" '
            f'Radius="{f(radius)}" StartAngle="{f(start)}" EndAngle="{f(end)}"/>'
        )

    def ellipse(self, centre, major, minor, angle):
        return self._add(
            f'<Ellipse CenterX="{f(centre[0])}" CenterY="{f(centre[1])}" CenterZ="{f(0)}" '
            f'NormalX="{f(0)}" NormalY="{f(0)}" NormalZ="{f(1)}" MajorRadius="{f(major)}" '
            f'MinorRadius="{f(minor)}" AngleXU="{f(angle)}" />'
        )

    def bezier(self, poles):
        """A Bezier curve (2 to 4 poles) as an exact clamped B-spline."""
        degree = len(poles) - 1
        body = "".join(
            f'<Pole X="{f(x)}" Y="{f(y)}" Z="{f(0)}" Weight="{f(1)}"/>'
            for x, y in poles
        )
        body += f'<Knot Value="{f(0)}" Mult="{degree + 1}"/><Knot Value="{f(1)}" Mult="{degree + 1}"/>'
        return self._add(
            f'<BSplineCurve PolesCount="{len(poles)}" KnotsCount="2" Degree="{degree}" '
            f'IsPeriodic="0">{body}</BSplineCurve>'
        )

    def constrain(
        self, kind, first, first_pos=NONE, second=UNUSED, second_pos=NONE, extra=""
    ):
        self.constraints.append(
            f'<Constrain Name="" Type="{kind}"{extra} Value="{f(0)}" LabelDistance="{f(10)}" '
            f'LabelPosition="{f(0)}" IsDriving="1" IsInVirtualSpace="0" IsVisible="1" '
            f'IsActive="1" First="{first}" FirstPos="{first_pos}" Second="{second}" '
            f'SecondPos="{second_pos}" Third="{UNUSED}" ThirdPos="0" '
            f'ElementIds="{first} {second} {UNUSED}" ElementPositions="{first_pos} {second_pos} 0" />'
        )

    def expose_control_points(self, spline, poles, radius):
        """Construction circles on the poles, aligned to them, as
        FreeCAD's "show control points" does."""
        for index, pole in enumerate(poles):
            circle = self.circle(
                pole, radius, construction=True, internal=BSPLINE_CONTROL_POINT
            )
            self.constrain(
                INTERNAL_ALIGNMENT,
                circle,
                CENTRE,
                spline,
                NONE,
                extra=f' InternalAlignmentType="{BSPLINE_CONTROL_POINT}" InternalAlignmentIndex="{index}"',
            )

    def xml(self):
        geometry = []
        for number, (body, construction, internal) in enumerate(self.geometry, 1):
            kind = {
                "<LineSegment": "Part::GeomLineSegment",
                "<Circle": "Part::GeomCircle",
                "<ArcOfCircle": "Part::GeomArcOfCircle",
                "<Ellipse": "Part::GeomEllipse",
                "<BSplineCurve": "Part::GeomBSplineCurve",
            }[body.split(" ", 1)[0]]
            # bit 1 is "construction", as FreeCAD 1.1 writes it
            flags = ("10" if construction else "0").rjust(32, "0")
            geometry.append(
                f'<Geometry type="{kind}" id="{number}" migrated="1">'
                f'<GeoExtensions count="1"><GeoExtension type="Sketcher::SketchGeometryExtension" '
                f'id="{number}" internalGeometryType="{internal}" geometryModeFlags="{flags}" '
                f'geometryLayer="0"/></GeoExtensions>{body}'
                f'<Construction value="{1 if construction else 0}"/></Geometry>'
            )
        return (
            '<Property name="Constraints" type="Sketcher::PropertyConstraintList">'
            f'<ConstraintList count="{len(self.constraints)}">{"".join(self.constraints)}'
            "</ConstraintList></Property>"
            '<Property name="Geometry" type="Part::PropertyGeometryList">'
            f'<GeometryList count="{len(self.geometry)}">{"".join(geometry)}</GeometryList></Property>'
        )


def object_names(count):
    """Sketch, Sketch001, Sketch002, ... as FreeCAD names them."""
    return ["Sketch" if i == 0 else f"Sketch{i:03d}" for i in range(count)]


def document(sketches, label="Inkscape drawing"):
    """The .FCStd file as bytes."""
    names = object_names(len(sketches))
    objects = "".join(
        f'<Object type="Sketcher::SketchObject" name="{name}" id="{i + 1}" Touched="1" />'
        for i, name in enumerate(names)
    )
    data = "".join(
        f'<Object name="{name}"><Properties Count="3">'
        f'<Property name="Label" type="App::PropertyString"><String value={quoteattr(sketch.label)}/></Property>'
        f"{sketch.xml()}</Properties></Object>"
        for name, sketch in zip(names, sketches)
    )
    xml = (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        '<Document SchemaVersion="4" ProgramVersion="0.21" FileVersion="1">'
        '<Properties Count="1"><Property name="Label" type="App::PropertyString">'
        f"<String value={quoteattr(label)}/></Property></Properties>"
        f'<Objects Count="{len(names)}">{objects}</Objects>'
        f'<ObjectData Count="{len(names)}">{data}</ObjectData></Document>\n'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Document.xml", xml)
    return out.getvalue()
