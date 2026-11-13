# Roundel Sans

TeX Gyre Heros (a Nimbus Sans / Helvetica-metric clone) with the inner corners rounded:
the concave corners of the outer contours (K, E, W, M, n, h...) and the corners of the
counters (A, B, D, P, R, 4...). Outer corners are unchanged.

Built with a 130-unit fillet radius (1000 upm). Very sharp notches (W, M, N, w) are capped
at 110 units so they don't fill in. To rebuild from the original TeX Gyre Heros OTFs
(https://www.gust.org.pl/projects/e-foundry/tex-gyre/heros):

    pip install fonttools
    python3 build/round_inner.py texgyreheros-regular.otf RoundelSans-regular.otf 130 \
        "Roundel Sans" --counters --cap 110 --share 0.48

Options: `--counters` also rounds hole corners, `--cap N` caps the cutback on sharp notches
(font units), `--share F` is the largest fraction of a neighbouring edge a fillet may use.

See NOTICE.txt for licensing (GUST Font License: modified versions must be renamed).
