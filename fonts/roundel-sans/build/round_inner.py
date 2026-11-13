#!/usr/bin/env python3
"""Round the inner (concave) corners of every outer contour in a CFF/OTF font.

usage: round_inner.py SRC.otf DST.otf RADIUS "Family Name" [--keep-name]

RADIUS is in font units (TeX Gyre Heros is 1000 upm; stems are ~85 units).
"""
import math
import sys

from fontTools.misc.bezierTools import splitCubicAtT
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import TTFont

MIN_TURN = math.radians(25)   # corners flatter than this are left alone
MAX_TURN = math.radians(172)  # ...and so are cusps that fold back on themselves
MAX_SHARE = 0.45              # a fillet may use at most this much of a neighbour
MAX_CUT = 1.25                # cutback is capped at this many radii


def sub(a, b): return (a[0] - b[0], a[1] - b[1])
def add(a, b): return (a[0] + b[0], a[1] + b[1])
def mul(a, k): return (a[0] * k, a[1] * k)
def dist(a, b): return math.hypot(a[0] - b[0], a[1] - b[1])
def cross(a, b): return a[0] * b[1] - a[1] * b[0]
def dot(a, b): return a[0] * b[0] + a[1] * b[1]


def unit(v):
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n) if n > 1e-9 else None


# A segment is ("L", p0, p1) or ("C", p0, c1, c2, p1).
def seg_start(s): return s[1]
def seg_end(s): return s[-1]
def seg_len(s):
    pts = s[1:]
    return sum(dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def tangent_start(s):
    pts = s[1:]
    for q in pts[1:]:
        t = unit(sub(q, pts[0]))
        if t:
            return t
    return None


def tangent_end(s):
    pts = s[1:][::-1]
    for q in pts[1:]:
        t = unit(sub(pts[0], q))
        if t:
            return t
    return None


def cubic_at(s, t):
    p0, c1, c2, p1 = s[1:]
    mt = 1 - t
    return (
        mt**3 * p0[0] + 3 * mt**2 * t * c1[0] + 3 * mt * t**2 * c2[0] + t**3 * p1[0],
        mt**3 * p0[1] + 3 * mt**2 * t * c1[1] + 3 * mt * t**2 * c2[1] + t**3 * p1[1],
    )


def t_at_distance(s, anchor, d, from_start):
    """Parameter t where the curve is d (straight-line) away from anchor."""
    lo, hi = (0.0, 1.0)
    for _ in range(40):
        mid = (lo + hi) / 2
        far = dist(cubic_at(s, mid), anchor)
        if from_start:
            if far < d: lo = mid
            else: hi = mid
        else:
            if far < d: hi = mid
            else: lo = mid
    return (lo + hi) / 2


def cut(s, d_start, d_end):
    """Trim d_start off the start and d_end off the end of a segment."""
    if d_start <= 0 and d_end <= 0:
        return s
    if s[0] == "L":
        p0, p1 = s[1], s[2]
        u = unit(sub(p1, p0))
        return ("L", add(p0, mul(u, d_start)), sub(p1, mul(u, d_end)))
    p0, p3 = s[1], s[4]
    ts = t_at_distance(s, p0, d_start, True) if d_start > 0 else 0.0
    te = t_at_distance(s, p3, d_end, False) if d_end > 0 else 1.0
    if te <= ts:
        return s
    parts = splitCubicAtT(s[1], s[2], s[3], s[4], *[t for t in (ts, te) if 0 < t < 1])
    if ts > 0 and te < 1:
        mid = parts[1]
    elif ts > 0:
        mid = parts[1]
    else:
        mid = parts[0]
    return ("C",) + tuple(mid)


def contour_to_segments(ops):
    """RecordingPen ops for one contour -> list of segments (cyclic)."""
    segs, cur, start = [], None, None
    for op, args in ops:
        if op == "moveTo":
            start = cur = args[0]
        elif op == "lineTo":
            segs.append(("L", cur, args[0])); cur = args[0]
        elif op == "curveTo":
            segs.append(("C", cur, *args)); cur = args[-1]
        elif op in ("closePath", "endPath"):
            pass
        else:
            return None  # qCurveTo etc: not a CFF glyph, bail out
    if cur is not None and dist(cur, start) > 1e-6:
        segs.append(("L", cur, start))
    return [s for s in segs if seg_len(s) > 1e-6]


def signed_area(segs):
    pts = []
    for s in segs:
        if s[0] == "L":
            pts.append(s[1])
        else:
            for t in (0, 0.25, 0.5, 0.75):
                pts.append(cubic_at(s, t))
    a = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]; x1, y1 = pts[(i + 1) % len(pts)]
        a += x0 * y1 - x1 * y0
    return a / 2


def fillet(a_seg, b_seg):
    """Cubic that bridges the end of a_seg to the start of b_seg."""
    A, B = seg_end(a_seg), seg_start(b_seg)
    u, v = tangent_end(a_seg), tangent_start(b_seg)
    theta = math.atan2(abs(cross(u, v)), dot(u, v))
    chord = dist(A, B)
    if theta < 1e-3 or chord < 1e-6:
        return ("L", A, B)
    h = chord * (4 / 3) * math.tan(theta / 4) / (2 * math.sin(theta / 2))
    return ("C", A, add(A, mul(u, h)), sub(B, mul(v, h)), B)


def round_contour(ops, outer_sign, radius):
    segs = contour_to_segments(ops)
    if not segs or len(segs) < 2:
        return ops, 0
    area = signed_area(segs)
    if area * outer_sign <= 0:      # a counter (hole): leave it alone
        return ops, 0

    n = len(segs)
    cuts = {}   # vertex index i (between seg i and i+1) -> cut distance
    for i in range(n):
        a, b = segs[i], segs[(i + 1) % n]
        u, v = tangent_end(a), tangent_start(b)
        if not u or not v:
            continue
        theta = math.atan2(abs(cross(u, v)), dot(u, v))
        if not (MIN_TURN <= theta <= MAX_TURN):
            continue
        if cross(u, v) * outer_sign >= 0:   # convex corner: keep it crisp
            continue
        d = radius * math.tan(theta / 2)
        # Very acute notches (W, M, N, w) would otherwise fill in and look heavy.
        d = min(d, MAX_CUT * radius)
        d = min(d, MAX_SHARE * seg_len(a), MAX_SHARE * seg_len(b))
        if d > 1.0:
            cuts[i] = d
    if not cuts:
        return ops, 0

    trimmed = []
    for i, s in enumerate(segs):
        trimmed.append(cut(s, cuts.get((i - 1) % n, 0), cuts.get(i, 0)))

    out = [("moveTo", (seg_start(trimmed[0]),))]
    for i, s in enumerate(trimmed):
        out.append(("lineTo", (seg_end(s),)) if s[0] == "L" else ("curveTo", tuple(s[2:])))
        if i in cuts:
            f = fillet(s, trimmed[(i + 1) % n])
            out.append(("lineTo", (f[2],)) if f[0] == "L" else ("curveTo", tuple(f[2:])))
    out.append(("closePath", ()))
    return out, len(cuts)


def split_contours(ops):
    contours, cur = [], []
    for op, args in ops:
        cur.append((op, args))
        if op in ("closePath", "endPath"):
            contours.append(cur); cur = []
    if cur:
        contours.append(cur)
    return contours


def rename(font, family, style):
    full = f"{family} {style}" if style != "Regular" else family
    ps = full.replace(" ", "")
    ps = ps.replace("Regular", "")
    ps = f"{family.replace(' ', '')}-{style.replace(' ', '')}"
    for rec in list(font["name"].names):
        if rec.nameID in (16, 17, 21, 22):
            font["name"].names.remove(rec)
    for nid, text in {1: family, 2: style, 3: f"{ps};rounded", 4: full, 6: ps}.items():
        font["name"].setName(text, nid, 3, 1, 0x409)
        font["name"].setName(text, nid, 1, 0, 0)
    cff = font["CFF "].cff
    cff.fontNames[0] = ps
    top = cff.topDictIndex[0]
    top.FullName, top.FamilyName = full, family


def main():
    src, dst, radius, family = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
    keep_name = "--keep-name" in sys.argv
    font = TTFont(src)
    gs = font.getGlyphSet()
    top = font["CFF "].cff.topDictIndex[0]
    charstrings = top.CharStrings
    changed = corners = 0
    for name in font.getGlyphOrder():
        pen = DecomposingRecordingPen(gs)
        gs[name].draw(pen)
        contours = split_contours(pen.value)
        if not contours:
            continue
        sigs = [contour_to_segments(c) for c in contours]
        areas = [signed_area(s) for s in sigs if s]
        if not areas:
            continue
        outer_sign = 1 if max(areas, key=abs) > 0 else -1
        new_ops, hit = [], 0
        for c in contours:
            o, k = round_contour(c, outer_sign, radius)
            new_ops += o; hit += k
        if not hit:
            continue
        cs = charstrings[name]
        tpen = T2CharStringPen(gs[name].width, gs)
        for op, args in new_ops:
            getattr(tpen, op)(*args)
        new = tpen.getCharString(private=cs.private, globalSubrs=cs.globalSubrs)
        charstrings[name] = new
        changed += 1; corners += hit
    if not keep_name:
        style = font["name"].getDebugName(2) or "Regular"
        rename(font, family, style)
    font.save(dst)
    print(f"{src.split('/')[-1]}: rounded {corners} corners in {changed} glyphs -> {dst.split('/')[-1]}")


if __name__ == "__main__":
    main()
