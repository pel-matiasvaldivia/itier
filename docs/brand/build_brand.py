"""Genera los logos de iTier como SVG (texto convertido a trazos con Inter ExtraBold, licencia OFL).
Uso: python3 build_brand.py   (requiere fonttools)"""
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

FONT = "/usr/share/fonts/opentype/inter/InterDisplay-ExtraBold.otf"
INDIGO, SKY, INK, MINT = "#4F46E5", "#0EA5E9", "#0F172A", "#22D3A6"


def texto(s, size, x, y, tracking=0):
    f = TTFont(FONT); gs = f.getGlyphSet(); cmap = f.getBestCmap(); upm = f["head"].unitsPerEm
    k = size / upm; cur = 0; d = []
    for ch in s:
        g = cmap[ord(ch)]; pen = SVGPathPen(gs)
        gs[g].draw(TransformPen(pen, (k, 0, 0, -k, x + cur, y)))
        d.append(pen.getCommands()); cur += gs[g].width * k + tracking
    return " ".join(d), cur


GRAD = f'<linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{INDIGO}"/><stop offset="1" stop-color="{SKY}"/></linearGradient>'


def marca(fill="#fff", tiles=True, grad_fill=False):
    """«i» cuyo cuerpo son tres niveles (tiers) apilados; el punto es el nivel superior."""
    c = "url(#g)" if grad_fill else fill
    return (f'<circle cx="32" cy="14.5" r="6.5" fill="{c}"/>'
            f'<rect x="24.5" y="25" width="15" height="8.6" rx="3.6" fill="{c}" opacity=".5"/>'
            f'<rect x="24.5" y="35.2" width="15" height="8.6" rx="3.6" fill="{c}" opacity=".75"/>'
            f'<rect x="24.5" y="45.4" width="15" height="8.6" rx="3.6" fill="{c}"/>')


def tile(r=15):
    return f'<rect width="64" height="64" rx="{r}" fill="url(#g)"/>'


def svg(w, h, body, defs=GRAD, title="iTier"):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{title}">'
            f'<title>{title}</title><defs>{defs}</defs>{body}</svg>\n')


def horizontal(ink, fondo=None, mono=None):
    d, ancho = texto("iTier", 46, 78, 47, -1)
    cuerpo = ""
    if fondo:
        cuerpo += f'<rect width="{int(78+ancho+24)}" height="64" fill="{fondo}"/>'
    if mono:
        cuerpo += (f'<mask id="m"><rect width="64" height="64" fill="#fff"/>' + marca(fill="#000") + '</mask>'
                   f'<rect width="64" height="64" rx="15" fill="{mono}" mask="url(#m)"/>')
    else:
        cuerpo += tile() + marca()
    cuerpo += f'<path d="{d}" fill="{ink}"/>'
    return svg(int(78 + ancho + (24 if fondo else 0)), 64, cuerpo)


def w(n, s):
    open(n, "w").write(s); print("ok", n)


w("itier-icon.svg", svg(64, 64, tile() + marca()))
w("itier-mark.svg", svg(64, 64, marca(grad_fill=True)))
w("itier-horizontal.svg", horizontal(INK))
w("itier-horizontal-dark.svg", horizontal("#FFFFFF", fondo="#0B1020"))
w("itier-mono-black.svg", horizontal("#000000", mono="#000000"))
w("itier-mono-white.svg", horizontal("#FFFFFF", mono="#FFFFFF"))
# favicon: sin texto, tile con esquinas más suaves
w("favicon.svg", svg(64, 64, tile(14) + marca()))
# avatar redes (cuadrado, 512)
w("itier-avatar.svg", svg(64, 64, tile(0) + marca()))
w("itier-horizontal-white.svg", horizontal("#FFFFFF"))
