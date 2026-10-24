"""Génère les logos SVG (texte vectorisé) : python tools/logo.py Poppins-Bold.ttf public/img
Police Poppins (licence OFL) : https://github.com/google/fonts/tree/main/ofl/poppins"""
import sys
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

font = TTFont(sys.argv[1]); out = sys.argv[2]
gs = font.getGlyphSet(); cmap = font.getBestCmap(); upm = font["head"].unitsPerEm

def word(text, size, x0, baseline, tracking=-0.02):
    """Chemins SVG d'un mot, origine (x0, baseline), hauteur de corps `size` px."""
    s = size / upm; x = x0; paths = []
    for ch in text:
        g = gs[cmap[ord(ch)]]
        pen = SVGPathPen(gs)
        g.draw(TransformPen(pen, (s, 0, 0, -s, x, baseline)))
        paths.append(pen.getCommands())
        x += g.width * s + tracking * size
    return " ".join(paths), x - tracking * size

# Symbole : un œil (la vigie qui observe) dans une pastille noire ; pupille verte, reflet blanc.
def mark(cx, cy, r, bg="#0b0b0b", eye="#faf4f2"):
    w, h = r * 1.28, r * 0.78   # demi-largeur et hauteur de l'amande
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{bg}"/>'
            f'<path d="M{cx-w/1} {cy} Q{cx} {cy-h*1.25} {cx+w/1} {cy} Q{cx} {cy+h*1.25} {cx-w/1} {cy}Z" fill="{eye}" transform="translate(0 0) scale(1)"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r*0.36}" fill="#2bb673"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r*0.15}" fill="#0b0b0b"/>'
            f'<circle cx="{cx+r*0.13}" cy="{cy-r*0.13}" r="{r*0.06}" fill="#fff"/>')

def clip_mark(cx, cy, r, **kw):
    # l'amande dépasse légèrement du cercle : on la découpe pour garder une pastille nette
    return (f'<defs><clipPath id="c"><circle cx="{cx}" cy="{cy}" r="{r*0.86}"/></clipPath></defs>'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{kw.get("bg", "#0b0b0b")}"/>'
            f'<g clip-path="url(#c)">{mark(cx, cy, r, **kw)}</g>')

open(f"{out}/vigie-mark.svg", "w").write(
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="Vigie SEO">{clip_mark(32, 32, 32)}</svg>')

for theme, ink, seo in (("light", "#0b0b0b", "#2bb673"), ("dark", "#faf4f2", "#2bb673")):
    d1, x1 = word("vigie", 44, 70, 46)
    d2, x2 = word("seo", 44, x1 + 8, 46)
    W = int(x2 + 4)
    bg = "#0b0b0b" if theme == "light" else "#faf4f2"
    eye = "#faf4f2" if theme == "light" else "#0b0b0b"
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} 64" role="img" aria-label="Vigie SEO">'
           f'{clip_mark(30, 32, 30, bg=bg, eye=eye)}<path d="{d1}" fill="{ink}"/><path d="{d2}" fill="{seo}"/></svg>')
    open(f"{out}/vigie-logo-{theme}.svg", "w").write(svg)
print("ok")

# Version intégrée à la page (en-tête du site) : couleurs pilotées par le CSS (thèmes clair et sombre)
# et œil animable (classes eye-lid pour le clignement, eye-pupil pour le regard).
def animated_mark(cx, cy, r, cid):
    w, h = r * 1.28, r * 0.78
    return (f'<defs><clipPath id="{cid}"><circle cx="{cx}" cy="{cy}" r="{r*0.86}"/></clipPath></defs>'
            f'<circle class="eye-bg" cx="{cx}" cy="{cy}" r="{r}"/>'
            f'<g clip-path="url(#{cid})"><g class="eye-lid">'
            f'<path class="eye-white" d="M{cx-w} {cy} Q{cx} {cy-h*1.25} {cx+w} {cy} Q{cx} {cy+h*1.25} {cx-w} {cy}Z"/>'
            f'<g class="eye-pupil"><circle cx="{cx}" cy="{cy}" r="{r*0.36}" fill="#2bb673"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r*0.15}" fill="#0b0b0b"/>'
            f'<circle cx="{cx+r*0.13}" cy="{cy-r*0.13}" r="{r*0.06}" fill="#fff"/></g></g></g>')

d1, x1 = word("vigie", 44, 70, 46)
d2, x2 = word("seo", 44, x1 + 8, 46)
W = int(x2 + 4)
open(f"{out}/vigie-logo-inline.svg", "w").write(
    f'<svg class="logo eye-anim" viewBox="0 0 {W} 64" width="160" height="34" role="img" aria-label="Vigie SEO">'
    f'{animated_mark(30, 32, 30, "eye-clip-head")}<path class="logo-word" d="{d1}"/><path d="{d2}" fill="#2bb673"/></svg>')
print("inline ok")
