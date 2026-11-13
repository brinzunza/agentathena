#!/usr/bin/env python3
"""Make letters less curvy by squaring off their bowls (run on the TeX Gyre Heros source, BEFORE round_inner.py).

usage: square_s.py FONT.otf FACTOR [--all]      (edits FONT.otf in place; Roundel Sans uses 1.5 --all)

Every bowl curve that starts and ends on an extreme (horizontal, or vertical for the italic
slant) has its two handles lengthened by FACTOR, so the arc flattens along its sides.
Without --all only S and s (and the accented glyphs built on them) are edited; with --all
every curved contour in the font is.  Run it before rounding, so the fillets round_inner.py adds are not squared too; it is not idempotent.
"""
import math
import sys

from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import TTFont


def mul_(v, f):
    return (v[0] * f, v[1] * f)


def contours_of(ops):
    out, cur = [], []
    for op, args in ops:
        cur.append((op, args))
        if op in ("closePath", "endPath"):
            out.append(cur); cur = []
    return out


def start_of(contour):
    return contour[0][1][0]


def same_shape(a, b):
    """True if contour a is contour b shifted by some offset."""
    if len(a) != len(b):
        return False
    ax, ay = start_of(a); bx, by = start_of(b)
    for (oa, pa), (ob, pb) in zip(a, b):
        if oa != ob or len(pa) != len(pb):
            return False
        for p, q in zip(pa, pb):
            if abs((p[0] - ax) - (q[0] - bx)) > 0.5 or abs((p[1] - ay) - (q[1] - by)) > 0.5:
                return False
    return True


def main():
    path, k = sys.argv[1], float(sys.argv[2])
    everything = "--all" in sys.argv
    font = TTFont(path)
    gs = font.getGlyphSet()
    cs = font["CFF "].cff.topDictIndex[0].CharStrings
    shear = math.tan(math.radians(-font["post"].italicAngle))   # x shift per unit of y

    def straight(v):                       # horizontal, vertical, or vertical along the italic slant
        n = math.hypot(*v)
        return n > 0 and (abs(v[1]) < 0.06 * n or abs(v[0]) < 0.06 * n
                          or abs(v[0] - v[1] * shear) < 0.06 * n)

    def square(contour):
        out, cur = [], None
        for op, args in contour:
            if op == "curveTo":
                p0, c1, c2, p3 = cur, *args
                t0 = (c1[0] - p0[0], c1[1] - p0[1]); t1 = (p3[0] - c2[0], p3[1] - c2[1])
                cos = abs(t0[0] * t1[0] + t0[1] * t1[1]) / (math.hypot(*t0) * math.hypot(*t1) or 1)
                if straight(t0) and straight(t1) and cos < 0.4:   # a quarter-turn-ish arc
                    chord = (p3[0] - p0[0], p3[1] - p0[1])
                    def grow(t):           # lengthen, but never past the chord's reach along t
                        n = math.hypot(*t)
                        reach = 0.95 * abs(chord[0] * t[0] + chord[1] * t[1]) / n
                        return mul_(t, min(n * k, max(reach, n)) / n)
                    g0, g1 = grow(t0), grow(t1)
                    c1 = (p0[0] + g0[0], p0[1] + g0[1])
                    c2 = (p3[0] - g1[0], p3[1] - g1[1])
                args = (c1, c2, p3)
            if args:
                cur = args[-1]
            out.append((op, args))
        return out

    refs = {}
    for n in ("S", "s"):
        rec = DecomposingRecordingPen(gs); gs[n].draw(rec)
        refs[n] = contours_of(rec.value)[0]

    changed = []
    for name in font.getGlyphOrder():
        rec = DecomposingRecordingPen(gs); gs[name].draw(rec)
        contours = contours_of(rec.value)
        if everything:
            hits = [i for i, c in enumerate(contours) if any(op == "curveTo" for op, _ in c)]
        else:
            hits = [i for i, c in enumerate(contours) if any(same_shape(c, r) for r in refs.values())]
        if not hits:
            continue
        pen = T2CharStringPen(gs[name].width, gs)
        for i, c in enumerate(contours):
            for op, args in (square(c) if i in hits else c):
                getattr(pen, op)(*args)
        old = cs[name]
        cs[name] = pen.getCharString(private=old.private, globalSubrs=old.globalSubrs)
        changed.append(name)
    font.save(path)
    print(f"{path.split('/')[-1]}: squared {len(changed)} glyphs")


if __name__ == "__main__":
    main()
