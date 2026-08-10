#!/usr/bin/env python3
"""Genera landing/manual.html a partir de docs/07-manual-operativo.md.

Convierte el manual (Markdown) a una página HTML autónoma, con el mismo diseño
que la landing (tema claro, misma marca), índice lateral navegable y estilos de
documento largo. El HTML resultante no tiene dependencias externas.

Uso:
    pip install markdown        # única dependencia, solo para regenerar
    python3 landing/build-manual.py

Se ejecuta desde la raíz del repo (o desde cualquier lado: resuelve rutas por
su propia ubicación).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import markdown
    from markdown.extensions.toc import TocExtension
except ImportError:
    sys.exit("Falta la librería 'markdown'. Instalá con: pip install markdown")

RAIZ = Path(__file__).resolve().parents[1]
ORIGEN = RAIZ / "docs" / "07-manual-operativo.md"
DESTINO = RAIZ / "landing" / "manual.html"


def gh_slug(value: str, separator: str) -> str:
    """Slug estilo GitHub: coincide con los anclajes escritos en el manual."""
    v = value.strip().lower()
    v = re.sub(r"[^\w\s-]", "", v, flags=re.UNICODE)  # \w cubre acentos
    v = v.replace(" ", "-")
    return v


def construir_toc(tokens, nivel_max=3) -> str:
    """Índice lateral anidado a partir de los tokens del TOC (h2 y h3)."""
    def render(items):
        salida = []
        for it in items:
            if it["level"] > nivel_max:
                continue
            hijos = render(it.get("children", [])) if it["level"] < nivel_max else ""
            salida.append(
                f'<li><a href="#{it["id"]}" data-lvl="{it["level"]}">{it["name"]}</a>{hijos}</li>'
            )
        return f"<ul>{''.join(salida)}</ul>" if salida else ""
    # arrancamos en los h2 (saltamos el h1 título)
    top = []
    for t in tokens:
        if t["level"] == 1:
            top.extend(t.get("children", []))
        else:
            top.append(t)
    return render(top)


def quitar_indice(md_text: str) -> str:
    """Elimina la sección '## Índice ... ---' del markdown: el lateral la reemplaza."""
    return re.sub(r"\n## Índice\n.*?\n---\n", "\n", md_text, count=1, flags=re.DOTALL)


def deslinkar_internos(html: str) -> str:
    """Convierte enlaces a archivos internos del repo (no publicados) en referencias
    no navegables, para no dejar enlaces rotos en el sitio público. Conserva los
    anclajes (#...) y los enlaces http(s)."""
    def repl(m):
        href, texto = m.group(1), m.group(2)
        if href.startswith("#"):
            return m.group(0)
        if href.startswith(("http://", "https://", "mailto:")):
            return f'<a href="{href}" target="_blank" rel="noopener">{texto}</a>'
        return f'<span class="xref" title="Documento interno del repositorio: {href}">{texto}</span>'
    return re.sub(r'<a href="([^"]+)">(.*?)</a>', repl, html, flags=re.DOTALL)


def envolver_tablas(html: str) -> str:
    """Envuelve cada tabla para permitir scroll horizontal en pantallas chicas."""
    html = html.replace("<table>", '<div class="table-wrap"><table>')
    return html.replace("</table>", "</table></div>")


def quitar_thead_vacio(html: str) -> str:
    """Elimina encabezados de tabla sin texto (la tabla de metadatos usa '| |')."""
    def repl(m):
        interno = re.sub(r"<[^>]+>", "", m.group(0))
        return "" if not interno.strip() else m.group(0)
    return re.sub(r"<thead>.*?</thead>", repl, html, flags=re.DOTALL)


def marcar_callouts(html: str) -> str:
    """Marca las citas de advertencia (⚠️) y las badges [Fase 3+]."""
    html = re.sub(r"<blockquote>\s*<p>⚠️", '<blockquote class="warn"><p>⚠️', html)
    html = html.replace("<code>[Fase 3+]</code>", '<span class="badge-fase">Fase&nbsp;3+</span>')
    return html


def main() -> None:
    md_text = ORIGEN.read_text(encoding="utf-8")
    md_text = quitar_indice(md_text)

    md = markdown.Markdown(
        extensions=[
            "extra",            # tablas, fenced_code, attr_list, etc.
            "sane_lists",
            TocExtension(slugify=gh_slug, separator="-", toc_depth="2-4"),
        ]
    )
    cuerpo = md.convert(md_text)
    toc_html = construir_toc(md.toc_tokens)

    # Post-proceso
    cuerpo = deslinkar_internos(cuerpo)
    cuerpo = quitar_thead_vacio(cuerpo)
    cuerpo = envolver_tablas(cuerpo)
    cuerpo = marcar_callouts(cuerpo)

    html = PLANTILLA.replace("{{TOC}}", toc_html).replace("{{CUERPO}}", cuerpo)
    DESTINO.write_text(html, encoding="utf-8")

    # Verificación: todo anclaje interno debe tener su id destino
    ids = set(re.findall(r'id="([^"]+)"', html))
    hrefs = set(re.findall(r'href="#([^"]+)"', html))
    rotos = sorted(h for h in hrefs if h not in ids)
    print(f"OK → {DESTINO.relative_to(RAIZ)}  ({len(html):,} bytes)")
    print(f"   anclajes internos: {len(hrefs)} · ids: {len(ids)} · rotos: {rotos or 'ninguno'}")


PLANTILLA = r"""<!doctype html>
<html lang="es-AR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="theme-color" content="#4f46e5">
<meta name="robots" content="noindex, nofollow">
<title>Manual de operaciones · iTier</title>
<meta name="description" content="Manual de procesos y procedimientos para operar la plataforma iTier.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%234f46e5'/%3E%3Cstop offset='1' stop-color='%230ea5e9'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width='32' height='32' rx='8' fill='url(%23g)'/%3E%3Cg fill='%23fff'%3E%3Crect x='8' y='19' width='16' height='4' rx='2'/%3E%3Crect x='10' y='13' width='12' height='4' rx='2' opacity='.8'/%3E%3Crect x='12' y='7' width='8' height='4' rx='2' opacity='.6'/%3E%3C/g%3E%3C/svg%3E">
<style>
  :root{
    --bg:#ffffff;--bg-soft:#f5f7fc;--bg-tint:#eef1ff;
    --ink:#141a2e;--ink-soft:#41485f;--ink-muted:#7c8399;
    --line:#e6e9f2;--line-soft:#eef1f7;
    --brand:#4f46e5;--brand-dark:#4035c9;--brand-2:#0ea5e9;--accent:#f59e0b;--ok:#10b981;
    --grad:linear-gradient(135deg,#4f46e5 0%,#6366f1 45%,#0ea5e9 100%);
    --r:12px;--font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  }
  *,*::before,*::after{box-sizing:border-box}
  html{scroll-behavior:smooth}
  body{margin:0;font-family:var(--font);color:var(--ink-soft);background:var(--bg);line-height:1.65;-webkit-font-smoothing:antialiased}
  a{color:var(--brand);text-decoration:none}
  a:hover{text-decoration:underline}

  /* Topbar */
  .topbar{position:sticky;top:0;z-index:50;background:rgba(255,255,255,.9);backdrop-filter:saturate(160%) blur(12px);border-bottom:1px solid var(--line-soft)}
  .topbar-in{max-width:1200px;margin:0 auto;display:flex;align-items:center;gap:16px;height:64px;padding:0 24px}
  .brand{display:flex;align-items:center;gap:10px;font-weight:800;color:var(--ink);font-size:1.12rem}
  .brand .mark{width:30px;height:30px;border-radius:8px}
  .brand small{font-weight:600;color:var(--ink-muted);font-size:.8rem;border-left:1px solid var(--line);padding-left:12px;margin-left:4px}
  .top-actions{margin-left:auto;display:flex;align-items:center;gap:12px}
  .top-actions a{font-size:.9rem;font-weight:600;color:var(--ink-soft)}
  .btn{display:inline-flex;align-items:center;gap:7px;font-weight:700;font-size:.9rem;padding:9px 16px;border-radius:10px;background:var(--grad);color:#fff}
  .btn:hover{text-decoration:none;filter:brightness(1.05)}
  .toc-toggle{display:none;background:none;border:1px solid var(--line);border-radius:9px;padding:8px 12px;font-weight:600;color:var(--ink);cursor:pointer;font-size:.9rem}

  /* Layout */
  .layout{max-width:1200px;margin:0 auto;display:grid;grid-template-columns:290px 1fr;gap:44px;padding:0 24px}
  .toc{position:sticky;top:64px;align-self:start;height:calc(100vh - 64px);overflow-y:auto;padding:30px 8px 40px 0}
  .toc-title{font-size:.75rem;font-weight:800;letter-spacing:.09em;text-transform:uppercase;color:var(--ink-muted);padding:0 12px 12px}
  .toc ul{list-style:none;margin:0;padding:0}
  .toc ul ul{margin-left:12px;border-left:1px solid var(--line-soft)}
  .toc li a{display:block;padding:5px 12px;font-size:.86rem;color:var(--ink-soft);border-left:2px solid transparent;margin-left:-1px;line-height:1.4}
  .toc li a:hover{color:var(--brand);text-decoration:none}
  .toc li a[data-lvl="3"]{font-size:.8rem;color:var(--ink-muted)}
  .toc li a.active{color:var(--brand);border-left-color:var(--brand);background:var(--bg-tint);font-weight:700;border-radius:0 6px 6px 0}

  /* Documento */
  .doc{min-width:0;padding:34px 0 80px;max-width:820px}
  .doc h1{font-size:clamp(1.8rem,4vw,2.5rem);color:var(--ink);letter-spacing:-.02em;line-height:1.15;margin:0 0 .4em}
  .doc h2{font-size:1.5rem;color:var(--ink);letter-spacing:-.02em;margin:2.4em 0 .7em;padding-top:1.1em;border-top:1px solid var(--line-soft)}
  .doc h3{font-size:1.16rem;color:var(--ink);margin:1.9em 0 .5em}
  .doc h4{font-size:1rem;color:var(--ink);margin:1.5em 0 .4em}
  .doc h2,.doc h3,.doc h4{scroll-margin-top:84px}
  .doc p{margin:0 0 1.05em}
  .doc ul,.doc ol{margin:0 0 1.15em;padding-left:1.4em}
  .doc li{margin:.3em 0}
  .doc li::marker{color:var(--brand)}
  .doc strong{color:var(--ink);font-weight:700}
  .doc hr{border:none;border-top:1px solid var(--line);margin:2.4em 0}
  .doc a[href^="#"]{color:var(--brand);font-weight:600}

  /* Referencias internas de-linkadas */
  .xref{color:var(--ink);font-weight:600;border-bottom:1px dotted var(--ink-muted);cursor:help}

  /* Código */
  .doc code{font-family:var(--mono);font-size:.86em;background:var(--bg-tint);color:var(--brand-dark);padding:.13em .4em;border-radius:5px}
  .doc pre{background:#0f1424;color:#e6e9f5;padding:18px 20px;border-radius:var(--r);overflow-x:auto;margin:0 0 1.3em;line-height:1.55;font-size:.85rem}
  .doc pre code{background:none;color:inherit;padding:0;font-size:inherit}

  /* Tablas */
  .table-wrap{overflow-x:auto;margin:0 0 1.4em;border:1px solid var(--line);border-radius:var(--r)}
  .doc table{border-collapse:collapse;width:100%;font-size:.9rem;background:#fff}
  .doc th,.doc td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--line-soft);vertical-align:top}
  .doc thead th{background:var(--bg-soft);color:var(--ink);font-weight:700;font-size:.82rem;letter-spacing:.01em;border-bottom:1px solid var(--line)}
  .doc tbody tr:last-child td{border-bottom:none}
  .doc tbody tr:hover{background:var(--bg-soft)}

  /* Citas / callouts */
  .doc blockquote{margin:0 0 1.3em;padding:14px 20px;background:var(--bg-soft);border-left:3px solid var(--brand-2);border-radius:0 var(--r) var(--r) 0;color:var(--ink-soft)}
  .doc blockquote p{margin:0}
  .doc blockquote p+p{margin-top:.6em}
  .doc blockquote.warn{background:#fffaf0;border-left-color:var(--accent)}

  /* Badge de fase */
  .badge-fase{display:inline-block;background:var(--bg-tint);color:var(--brand);font-size:.72em;font-weight:800;letter-spacing:.02em;padding:.15em .55em;border-radius:999px;border:1px solid #dfe3ff;vertical-align:middle;white-space:nowrap}

  /* Volver arriba */
  .totop{position:fixed;right:22px;bottom:22px;width:44px;height:44px;border-radius:50%;background:var(--grad);color:#fff;border:none;cursor:pointer;box-shadow:0 10px 24px -8px rgba(79,70,229,.6);opacity:0;pointer-events:none;transition:opacity .25s;display:grid;place-items:center;font-size:1.1rem;z-index:40}
  .totop.show{opacity:1;pointer-events:auto}

  /* Footer */
  footer{background:var(--ink);color:#9aa3ba;padding:34px 0}
  .foot-in{max-width:1200px;margin:0 auto;padding:0 24px;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;font-size:.85rem}
  footer a{color:#c7cdde}

  @media (max-width:900px){
    .layout{grid-template-columns:1fr;gap:0}
    .toc{position:static;height:auto;overflow:visible;padding:16px 0;border-bottom:1px solid var(--line);display:none}
    .toc.open{display:block}
    .toc-toggle{display:inline-block}
    .top-actions .hide-sm{display:none}
    .doc{padding:26px 0 70px}
  }
  @media print{
    .topbar,.toc,.totop,footer{display:none}
    .layout{display:block;max-width:none;padding:0}
    .doc{max-width:none}
    .doc h2{border-top:none}
    a{color:inherit}
  }
  @media (prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
</style>
</head>
<body>

<div class="topbar">
  <div class="topbar-in">
    <a class="brand" href="index.html">
      <svg class="mark" viewBox="0 0 32 32" aria-hidden="true"><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4f46e5"/><stop offset="1" stop-color="#0ea5e9"/></linearGradient></defs><rect width="32" height="32" rx="8" fill="url(#lg)"/><g fill="#fff"><rect x="8" y="19" width="16" height="4" rx="2"/><rect x="10" y="13" width="12" height="4" rx="2" opacity=".82"/><rect x="12" y="7" width="8" height="4" rx="2" opacity=".6"/></g></svg>
      iTier <small>Manual de operaciones</small>
    </a>
    <div class="top-actions">
      <button class="toc-toggle" id="tocToggle" aria-expanded="false">Contenido ▾</button>
      <a class="hide-sm" href="index.html">← Volver al inicio</a>
      <a class="btn hide-sm" href="mailto:matiasvaldivia@pymesenlinea.com.ar?subject=Consulta%20sobre%20iTier">Contacto</a>
    </div>
  </div>
</div>

<div class="layout">
  <aside class="toc" id="toc">
    <div class="toc-title">Contenido</div>
    <nav>{{TOC}}</nav>
  </aside>
  <main class="doc">
    {{CUERPO}}
  </main>
</div>

<footer>
  <div class="foot-in">
    <span>© 2026 iTier — una solución de Pymes en Línea.</span>
    <span><a href="index.html">itier.pymesenlinea.com.ar</a> · Documento interno de operaciones</span>
  </div>
</footer>

<button class="totop" id="totop" aria-label="Volver arriba">↑</button>

<script>
  // Volver arriba
  var totop=document.getElementById('totop');
  window.addEventListener('scroll',function(){ totop.classList.toggle('show', window.scrollY>500); });
  totop.addEventListener('click',function(){ window.scrollTo({top:0,behavior:'smooth'}); });

  // Índice móvil
  var toc=document.getElementById('toc'), tg=document.getElementById('tocToggle');
  if(tg){ tg.addEventListener('click',function(){
    var o=toc.classList.toggle('open'); tg.setAttribute('aria-expanded',o?'true':'false');
  });
  toc.addEventListener('click',function(e){ if(e.target.tagName==='A') toc.classList.remove('open'); }); }

  // Resaltado de sección activa
  var links={}, targets=[];
  document.querySelectorAll('.toc a[href^="#"]').forEach(function(a){ links[a.getAttribute('href').slice(1)]=a; });
  document.querySelectorAll('.doc h2, .doc h3').forEach(function(h){ if(links[h.id]) targets.push(h); });
  if('IntersectionObserver' in window && targets.length){
    var cur=null;
    var io=new IntersectionObserver(function(ents){
      ents.forEach(function(en){ if(en.isIntersecting) cur=en.target.id; });
      Object.values(links).forEach(function(a){ a.classList.remove('active'); });
      if(cur&&links[cur]) links[cur].classList.add('active');
    },{rootMargin:'-70px 0px -75% 0px',threshold:0});
    targets.forEach(function(t){ io.observe(t); });
  }
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
