# svg2fcsketch: Inkscape drawings as FreeCAD sketches

Save an Inkscape drawing as FreeCAD Sketcher sketches: **File > Save
As... > FreeCAD sketch (\*.FCStd)**. One sketch per layer, in
millimetres, with the lower left corner of the page at the origin, ready
to pad, pocket or revolve in FreeCAD's Part Design.

This is a new version of [jnweiger/inkscape-svg2fcsketch](https://github.com/jnweiger/inkscape-svg2fcsketch)
(by Jürgen Weigert, 2018). That version ran inside FreeCAD's own Python
and was written for Python 2 and Inkscape 0.92, so it no longer runs.
This one writes the `.FCStd` file itself: **FreeCAD is not needed** to
export, and it works in the Inkscape Flatpak.

## What you get

- **Lines** stay lines; **cubic and quadratic Bezier curves** become
  exact B-splines (a Bezier is a B-spline, so nothing is approximated).
- **Circular arcs** become arcs; **circles and ellipses** stay circles
  and ellipses, also when skewed or rotated (a skewed circle becomes the
  right ellipse). Elliptical arcs become B-splines.
- Segments that meet are joined by **coincident constraints**;
  horizontal and vertical lines get **horizontal/vertical constraints**
  (optional). The sketches solve without moving anything.
- B-spline **control points** can be added as construction circles,
  aligned like FreeCAD's "Show control points" (optional).
- Every transform, the document's units and its viewBox are applied.
- Only some elements: list their ids in the dialog.

Text has to be converted to paths first (Path > Object to Path).

In FreeCAD, recompute once after opening (Ctrl+R): the file holds the
sketches' geometry and constraints, and FreeCAD builds the shapes.

## Install

    dev/install.sh            # symlinks into your Inkscape extensions folder
    dev/install.sh --uninstall

Or copy `svg2fcsketch.inx`, `svg2fcsketch.py` and `fcstd.py` into your
Inkscape extensions folder (Edit > Preferences > System: User
extensions). Restart Inkscape. From the command line:

    inkscape drawing.svg --export-extension=com.github.jnweiger.inkscape-svg2fcsketch \
        --export-filename=drawing.FCStd

## Tests

`tests/run.py` converts every drawing in `tests/data` (new test drawings
and the original version's own test drawings), opens each file in
FreeCAD, and checks that

- every sketch solves, solving moves nothing, and no constraint is
  malformed, conflicting or redundant; the shapes are valid;
- the sketch edges, as FreeCAD samples them, lie within 0.005 mm of the
  drawing's shapes and the other way round (worst case so far 0.0049 mm;
  Bezier curves and lines
  sampled exactly, arcs from the SVG specification's formulas), and on
  what Inkscape itself renders (within 0.05 mm);
- the same drawing in other units gives the same sketches.

Needs Inkscape and FreeCAD (Flatpaks or native) and inkex on the host.
`tests/test_units.py` needs neither and runs on GitHub Actions.

Checked with Inkscape 1.4.4 and FreeCAD 1.1.4, on Linux.

## Licence

LGPL-2.1-or-later, like the original; see `LICENSE`.
