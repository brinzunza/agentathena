# Roundel Sans

TeX Gyre Heros (a Nimbus Sans / Helvetica-metric clone) with the inner (concave) corners
of every outer contour rounded. Counters and outer corners are unchanged.

Built with a 90-unit fillet radius (1000 upm). To rebuild from the original
TeX Gyre Heros OTFs (https://www.gust.org.pl/projects/e-foundry/tex-gyre/heros):

    pip install fonttools
    python3 build/round_inner.py texgyreheros-regular.otf RoundelSans-regular.otf 90 "Roundel Sans"

See NOTICE.txt for licensing (GUST Font License: modified versions must be renamed).
