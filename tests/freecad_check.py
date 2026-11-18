# Run in FreeCAD (freecadcmd): open $IN, recompute, solve, and write
# $OUT (JSON): per sketch the counts, the solver result, whether solving
# moved anything, and points sampled along every edge.
import json
import os

import FreeCAD as App

doc = App.openDocument(os.environ["IN"])
doc.recompute()
report = []
for sk in doc.Objects:
    if sk.TypeId != "Sketcher::SketchObject":
        continue
    before = [
        [(p.x, p.y) for p in g.toShape().discretize(Number=20)] for g in sk.Geometry
    ]
    solved = sk.solve()
    doc.recompute()
    after = [
        [(p.x, p.y) for p in g.toShape().discretize(Number=20)] for g in sk.Geometry
    ]
    moved = max(
        (
            abs(a[0] - b[0]) + abs(a[1] - b[1])
            for ga, gb in zip(before, after)
            for a, b in zip(ga, gb)
        ),
        default=0.0,
    )
    # one polyline per edge, points at most 0.05 mm apart
    points = [
        [(p.x, p.y) for p in edge.discretize(Distance=0.05)] for edge in sk.Shape.Edges
    ]
    report.append(
        {
            "label": sk.Label,
            "geometry": len(sk.Geometry),
            "construction": sum(
                1 for i in range(len(sk.Geometry)) if sk.getConstruction(i)
            ),
            "constraints": len(sk.Constraints),
            "kinds": sorted({c.Type for c in sk.Constraints}),
            "solve": solved,
            "moved": moved,
            "malformed": list(getattr(sk, "MalformedConstraints", [])),
            "conflicting": list(getattr(sk, "Conflicting", [])),
            "redundant": list(getattr(sk, "Redundant", [])),
            "valid": sk.Shape.isValid(),
            "edges": len(sk.Shape.Edges),
            "points": points,
        }
    )
with open(os.environ["OUT"], "w") as f:
    json.dump(report, f)
print("FREECAD CHECK DONE")
