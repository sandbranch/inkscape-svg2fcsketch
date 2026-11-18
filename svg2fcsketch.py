#!/usr/bin/env python3
# Copyright (C) 2018 Juergen Weigert and contributors (svg2fcsketch 0.8)
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
Save an Inkscape drawing as FreeCAD Sketcher sketches (.FCStd), with no
FreeCAD needed: one sketch per layer, in millimetres, the page's lower
left corner at the origin.

Lines stay lines; cubic and quadratic Bezier curves become exact
B-splines; circular arcs become arcs; circles and ellipses stay circles
and ellipses (also when transformed). Consecutive segments are joined by
coincident constraints; horizontal and vertical lines can be constrained
so; B-spline control points can be shown, as in FreeCAD.
"""

import math
import os
import sys

import inkex
import numpy as np
from inkex.localization import inkex_gettext as _
from inkex.paths import Arc, Curve, Line, Move, Quadratic, ZoneClose

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import itertools

import fcstd

MM_PER_PX = 25.4 / 96.0
EPS = 1e-7  # mm


class Mapping:
    """Document user units -> FreeCAD millimetres, y up."""

    def __init__(self, svg):
        x, y, width, height = svg.get_viewbox()
        if not width or not height:
            width, height = svg.viewport_width, svg.viewport_height
        self.k = svg.scale * MM_PER_PX
        self.x0, self.y0 = x, y
        self.height_mm = height * self.k
        self.linear = np.array([[self.k, 0.0], [0.0, -self.k]])

    def point(self, p):
        return ((p[0] - self.x0) * self.k, self.height_mm - (p[1] - self.y0) * self.k)


def same(a, b, eps=EPS):
    return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps


def arc_centre(p0, arc):
    """Centre, radii and angles of an SVG arc (endpoint to centre
    parameterisation, SVG 1.1 appendix F.6.5), in the arc's own space."""
    x1, y1 = p0
    x2, y2 = arc.x, arc.y
    rx, ry = abs(arc.rx), abs(arc.ry)
    phi = math.radians(arc.x_axis_rotation)
    if rx == 0 or ry == 0:
        return None
    cos, sin = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cos * dx + sin * dy, -sin * dx + cos * dy
    scale = (x1p / rx) ** 2 + (y1p / ry) ** 2
    if scale > 1:
        rx, ry = rx * math.sqrt(scale), ry * math.sqrt(scale)
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if arc.large_arc == arc.sweep:
        coef = -coef
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx = cos * cxp - sin * cyp + (x1 + x2) / 2
    cy = sin * cxp + cos * cyp + (y1 + y2) / 2

    def angle(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    theta1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = angle(
        (x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry
    )
    if not arc.sweep and dtheta > 0:
        dtheta -= 2 * math.pi
    elif arc.sweep and dtheta < 0:
        dtheta += 2 * math.pi
    return (cx, cy), (rx, ry), phi, theta1, dtheta


def arc_beziers(start, end, params):
    """Cubic Beziers for an SVG arc, one per eighth of a turn at most
    (within about 4e-6 of the radius), from its
    centre parameterisation; the first and last points are exactly the
    arc's ends. (inkex's Arc.to_curves can miss the end point when the
    radii have to be scaled up.)"""
    (cx, cy), (rx, ry), phi, theta1, dtheta = params
    cos, sin = math.cos(phi), math.sin(phi)

    def point(t):
        x, y = rx * math.cos(t), ry * math.sin(t)
        return (cx + cos * x - sin * y, cy + sin * x + cos * y)

    def tangent(t):
        x, y = -rx * math.sin(t), ry * math.cos(t)
        return (cos * x - sin * y, sin * x + cos * y)

    count = max(1, math.ceil(abs(dtheta) / (math.pi / 4) - 1e-9))
    step = dtheta / count
    k = 4 / 3 * math.tan(step / 4)
    pieces = []
    for i in range(count):
        t0, t1 = theta1 + i * step, theta1 + (i + 1) * step
        p0 = start if i == 0 else point(t0)
        p3 = end if i == count - 1 else point(t1)
        d0, d1 = tangent(t0), tangent(t1)
        p1 = (p0[0] + k * d0[0], p0[1] + k * d0[1])
        p2 = (p3[0] - k * d1[0], p3[1] - k * d1[1])
        pieces.append([p0, p1, p2, p3])
    return pieces


class Svg2FcSketch(inkex.OutputExtension):
    """Save the drawing as FreeCAD sketches"""

    def add_arguments(self, pars):
        pars.add_argument("--tab")
        pars.add_argument("--ids", default="", help="comma separated ids; all if empty")
        pars.add_argument("--per_layer", type=inkex.Boolean, default=True)
        pars.add_argument("--hv_constraints", type=inkex.Boolean, default=True)
        pars.add_argument(
            "--expose_internal_geometry", type=inkex.Boolean, default=False
        )
        # the option name of version 0.8
        pars.add_argument(
            "--expose-internal-geometry", dest="expose_old", type=inkex.Boolean
        )

    # -- which elements

    def groups(self):
        """(label, [elements]) for each sketch to make."""
        ids = [i.strip() for i in self.options.ids.split(",") if i.strip()]
        if ids:
            chosen = [self.svg.getElementById(i) for i in ids]
            missing = [i for i, n in zip(ids, chosen) if n is None]
            if missing:
                raise inkex.AbortExtension(
                    _("No such ids: {}").format(", ".join(missing))
                )
            return [(self.name(), chosen)]
        return self.groups_of(self.svg, self.options.per_layer, self.name())

    @staticmethod
    def groups_of(svg, per_layer=True, name="drawing"):
        """One sketch per top-level layer (shapes outside layers in one
        more), or everything in one."""
        layers = [n for n in svg if isinstance(n, inkex.Layer)]
        loose = [
            n
            for n in svg
            if isinstance(n, inkex.ShapeElement) and not isinstance(n, inkex.Layer)
        ]
        if not per_layer or not layers:
            return [(name, list(svg))]
        groups = [(layer.label or layer.get("id"), [layer]) for layer in layers]
        if loose:
            groups.insert(0, (name, loose))
        return groups

    def name(self):
        doc = self.svg.get("sodipodi:docname") or "drawing"
        return os.path.splitext(os.path.basename(doc))[0]

    @staticmethod
    def shapes(roots):
        for root in roots:
            for node in root.descendants():  # includes root itself
                if not isinstance(node, inkex.ShapeElement):
                    continue
                if isinstance(
                    node, (inkex.Group, inkex.TextElement, inkex.Tspan, inkex.Image)
                ):
                    continue
                if any(
                    a.style.get("display") == "none"
                    for a in [node, *node.ancestors()]
                    if isinstance(a, inkex.ShapeElement)
                ):
                    continue
                yield node

    # -- geometry

    def add_ellipse(self, sketch, node, mapping):
        """A circle or ellipse element, through all its transforms."""
        m = node.composed_transform()
        if isinstance(node, inkex.Circle):
            centre, rx, ry = node.center, node.radius, node.radius
        else:
            centre, rx, ry = node.center, node.radius.x, node.radius.y
        linear = mapping.linear @ np.array([[m.a, m.c], [m.b, m.d]]) @ np.diag([rx, ry])
        u, s, _vt = np.linalg.svd(linear)
        c = mapping.point(m.apply_to_point(centre))
        if abs(s[0] - s[1]) <= 1e-9 * s[0]:
            sketch.circle(c, s[0])
        else:
            sketch.ellipse(c, s[0], s[1], math.atan2(u[1, 0], u[0, 0]))

    def add_path(self, sketch, node, mapping):
        m = node.composed_transform()
        det = m.a * m.d - m.b * m.c
        uniform = math.isclose(
            math.hypot(m.a, m.b), math.hypot(m.c, m.d), rel_tol=1e-9
        ) and abs(m.a * m.c + m.b * m.d) <= 1e-9 * abs(det)

        def fc(p):
            return mapping.point(m.apply_to_point(p))

        pieces = []  # one list per subpath: (geo, start pos, end pos, start, end)
        current = None

        def add(geo, start_pos, end_pos, start, end):
            current.append((geo, start_pos, end_pos, start, end))

        def bezier(points):
            poles = [fc(p) for p in points]
            if all(same(p, poles[0]) for p in poles):
                return
            geo = sketch.bezier(poles)
            add(geo, fcstd.START, fcstd.END, poles[0], poles[-1])
            if self.options.expose_internal_geometry:
                size = math.dist(poles[0], poles[-1]) or 1.0
                sketch.expose_control_points(
                    geo, poles, max(0.05, min(2.0, 0.05 * size))
                )

        def line(p0, p1):
            a, b = fc(p0), fc(p1)
            if same(a, b):
                return
            geo = sketch.line(a, b)
            add(geo, fcstd.START, fcstd.END, a, b)
            if self.options.hv_constraints:
                length = math.dist(a, b)
                if abs(a[1] - b[1]) <= 1e-9 * length:
                    sketch.constrain(fcstd.HORIZONTAL, geo)
                elif abs(a[0] - b[0]) <= 1e-9 * length:
                    sketch.constrain(fcstd.VERTICAL, geo)

        def arc(p0, seg):
            found = arc_centre((p0.x, p0.y), seg)
            if found is None:
                line(p0, (seg.x, seg.y))
                return
            centre, (rx, ry), _phi, theta1, dtheta = found
            if not (uniform and math.isclose(rx, ry, rel_tol=1e-9)):
                for piece in arc_beziers((p0.x, p0.y), (seg.x, seg.y), found):
                    bezier(piece)
                return
            start, end = fc(p0), fc((seg.x, seg.y))
            mid_angle = theta1 + dtheta / 2
            mid = fc(
                (
                    centre[0] + rx * math.cos(mid_angle),
                    centre[1] + rx * math.sin(mid_angle),
                )
            )
            c = fc(centre)
            radius = math.dist(c, start)
            a_start = math.atan2(start[1] - c[1], start[0] - c[0])
            a_end = math.atan2(end[1] - c[1], end[0] - c[0])
            a_mid = math.atan2(mid[1] - c[1], mid[0] - c[0])

            def ccw(a, b):  # angle from a to b counter-clockwise, in [0, 2pi)
                return (b - a) % (2 * math.pi)

            if ccw(a_start, a_mid) <= ccw(a_start, a_end):  # runs counter-clockwise
                geo = sketch.arc(c, radius, a_start, a_start + ccw(a_start, a_end))
                add(geo, fcstd.START, fcstd.END, start, end)
            else:  # clockwise in FreeCAD: store it the other way round
                geo = sketch.arc(c, radius, a_end, a_end + ccw(a_end, a_start))
                add(geo, fcstd.END, fcstd.START, start, end)

        path = node.path.to_absolute().to_non_shorthand()
        first = None
        for proxy in path.proxy_iterator():
            seg = proxy.command
            prev = proxy.previous_end_point
            if isinstance(seg, Move):
                current = []
                pieces.append(current)
                first = proxy.end_point
            elif isinstance(seg, Line):
                line(prev, proxy.end_point)
            elif isinstance(seg, (Curve, Quadratic)):
                bezier([prev, *proxy.control_points])
            elif isinstance(seg, Arc):
                arc(prev, seg)
            elif isinstance(seg, ZoneClose):
                if first is not None and not same(fc(prev), fc(first)):
                    line(prev, first)
                if current:
                    current.append("closed")
        self.join(sketch, pieces)

    @staticmethod
    def join(sketch, pieces):
        """Coincident constraints where segments meet."""
        for sub in pieces:
            closed = bool(sub) and sub[-1] == "closed"
            items = [s for s in sub if s != "closed"]
            pairs = list(itertools.pairwise(items))
            if closed and len(items) > 1:
                pairs.append((items[-1], items[0]))
            for a, b in pairs:
                if same(a[4], b[3], 1e-6):
                    sketch.constrain(fcstd.COINCIDENT, a[0], a[2], b[0], b[1])

    # -- output

    def save(self, stream):
        if self.options.expose_old is not None:
            self.options.expose_internal_geometry = self.options.expose_old
        mapping = Mapping(self.svg)
        sketches = []
        for label, roots in self.groups():
            sketch = fcstd.Sketch(label)
            for node in self.shapes(roots):
                if isinstance(node, (inkex.Circle, inkex.Ellipse)):
                    self.add_ellipse(sketch, node, mapping)
                else:
                    self.add_path(sketch, node, mapping)
            if sketch.geometry:
                sketches.append(sketch)
        if not sketches:
            raise inkex.AbortExtension(_("Nothing to convert: no shapes found."))
        stream.write(fcstd.document(sketches, self.name()))


if __name__ == "__main__":
    Svg2FcSketch().run()
