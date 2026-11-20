"""Monitor de tenis en shop.travisscott.com (Cactus Jack).

Lee la página pública /collections/all (permitida por robots.txt), detecta tenis
nuevos y cambios de disponibilidad, avisa por ntfy y genera index.html.

Uso:
  python tenis.py            # una revisión
  python tenis.py --sin-aviso  # revisa sin mandar notificaciones
"""

import html as htmlmod
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

TIENDA = "https://shop.travisscott.com"
PAGINAS = ["/collections/all", "/"]
UA = "monitor-tenis/1.0 (monitor personal de disponibilidad; 1 consulta cada 15 min)"
AQUI = Path(__file__).parent
ESTADO = AQUI / "estado.json"
PALABRAS = re.compile(r"air[- ]?force|jordan|dunk|\baf1\b|sneaker|shoe|mac attack|air max|slide|clog|boot|mule|foam", re.I)


def get(path):
    req = urllib.request.Request(TIENDA + path, headers={"User-Agent": UA, "Accept-Language": "en-US"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def productos_de(pagina):
    """Cada producto trae un <script class='js-product-json'>; el enlace y la foto van justo antes."""
    out = {}
    for m in re.finditer(r"<script type=.application/json. class=.js-product-json.>(.*?)</script>", pagina, re.S):
        try:
            p = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(p, dict) or not p.get("variants"):
            continue
        antes = pagina[max(0, m.start() - 20000):m.start()]
        if not p.get("handle"):
            hs = re.findall(r"/products/([a-z0-9-]+)", antes)
            if not hs:
                continue
            p["handle"] = hs[-1]
        if not p.get("featured_image"):
            imgs = re.findall(r"(?:src|srcset)=[\"']([^\"' ]*/cdn/shop/(?:files|products)/[^\"' ?]+)", antes)
            p["featured_image"] = imgs[-1] if imgs else ""
        out.setdefault(p["handle"], p)
    return out


def detalle(handle):
    """Página individual del producto: trae etiquetas (fecha) y tipo."""
    try:
        pag = get(f"/products/{handle}")
    except Exception:
        return {}
    m = re.search(r"<script type=.application/json. class=.js-product-json.>(.*?)</script>", pag, re.S)
    try:
        return json.loads(m.group(1)) if m else {}
    except json.JSONDecodeError:
        return {}


def es_tenis(p):
    if PALABRAS.search(p.get("title", "") + " " + p["handle"]):
        return True
    # tallas numéricas de calzado (3.5, 10, 10.5, 13...) en vez de S/M/L
    tallas = [v.get("option1") or "" for v in p.get("variants", [])]
    num = [t for t in tallas if re.fullmatch(r"\d{1,2}(\.5)?", t) and 3 <= float(t) <= 18]
    return len(num) >= 3


def fecha_de_tag(tags):
    """Algunos productos traen una etiqueta tipo 112426 = 24 nov 2026."""
    for t in tags:
        if re.fullmatch(r"\d{6}", t):
            mm, dd, yy = int(t[:2]), int(t[2:4]), int(t[4:])
            if 1 <= mm <= 12 and 1 <= dd <= 31:
                return f"20{yy:02d}-{mm:02d}-{dd:02d}"
    return None


def color_de(handle, titulo):
    """'air-force-1-07-rs-black-placeholder' -> 'Black'."""
    resto = handle
    for pal in re.findall(r"[a-z0-9]+", titulo.lower()):
        resto = re.sub(rf"(^|-){pal}(?=-|$)", "", resto, count=1)
    resto = re.sub(r"(^|-)(placeholder|copy|\d+)(?=-|$)", "", resto).strip("-")
    return resto.replace("-", " ").title()


def titulo_con_color(p):
    t = htmlmod.unescape(p.get("title") or p["handle"])
    c = color_de(p["handle"], t)
    return f"{t} — {c}" if c and len(c) < 30 else t


def resumen(p):
    disp = [v.get("option1") or v.get("public_title") or "" for v in p["variants"] if v.get("available")]
    img = p.get("featured_image") or ""
    return {
        "titulo": titulo_con_color(p),
        "handle": p["handle"],
        "url": f"{TIENDA}/products/{p['handle']}",
        "precio": (p.get("price") or 0) / 100,
        "imagen": ("https:" + img) if img.startswith("//") else img,
        "disponible": bool(p.get("available")),
        "tallas": [v.get("option1") or "" for v in p["variants"]],
        "tallas_disp": disp,
        "tipo": p.get("type") or "",
        "fecha_tag": fecha_de_tag(p.get("tags") or []),
        "es_tenis": es_tenis(p),
    }


def avisar(titulo, mensaje, url=None, prioridad=3, tags=("athletic_shoe",)):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic or "--sin-aviso" in sys.argv:
        print(f"[aviso] {titulo} — {mensaje}")
        return
    msg = {"topic": topic, "title": titulo, "message": mensaje, "priority": prioridad, "tags": list(tags)}
    if url:
        msg["click"] = url
        msg["actions"] = [{"action": "view", "label": "Abrir en la tienda", "url": url}]
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(msg).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        print(f"No se pudo avisar: {e}")


def main():
    ahora = datetime.now().isoformat(timespec="minutes")
    estado = json.loads(ESTADO.read_text()) if ESTADO.exists() else {"productos": {}, "contador": None, "historial": []}
    primera_vez = not estado["productos"]

    vistos, contador = {}, None
    for path in PAGINAS:
        pag = get(path)
        vistos.update(productos_de(pag))
        m = re.search(r"js-countdown[^>]*data-date='([^']+)'|data-date='([^']+)'[^>]*js-countdown", pag)
        if m and not contador:
            contador = m.group(1) or m.group(2)
        time.sleep(2)
    if not vistos:
        raise SystemExit("La tienda no devolvió productos (¿bloqueo o cambio de diseño?)")

    previos = estado["productos"]
    eventos = []
    for handle, p in vistos.items():
        if es_tenis(p):  # para tenis, completar con la página individual (fecha, tipo)
            d = detalle(handle)
            for k in ("tags", "type", "featured_image"):
                if d.get(k):
                    p[k] = d[k]
            time.sleep(2)
        r = resumen(p)
        antes = previos.get(handle)
        r["visto_desde"] = antes["visto_desde"] if antes else ahora
        r["cambio"] = antes.get("cambio", ahora) if antes else ahora
        if r["es_tenis"]:
            if not antes:
                eventos.append(("nuevo", r))
            elif r["disponible"] and not antes["disponible"]:
                eventos.append(("venta", r))
            elif not r["disponible"] and antes["disponible"]:
                eventos.append(("agotado", r))
            elif r["fecha_tag"] and r["fecha_tag"] != antes.get("fecha_tag"):
                eventos.append(("fecha", r))
            if antes and (r["disponible"] != antes["disponible"] or r["tallas_disp"] != antes.get("tallas_disp")):
                r["cambio"] = ahora
        r["activo"] = True
        previos[handle] = r
    for handle, r in previos.items():
        if handle not in vistos and r.get("activo"):
            r["activo"] = False
            r["cambio"] = ahora
            if r["es_tenis"]:
                eventos.append(("retirado", r))

    if contador and contador != estado.get("contador"):
        eventos.append(("contador", {"titulo": contador, "url": TIENDA}))
    estado["contador"] = contador

    tenis = [r for r in previos.values() if r["es_tenis"] and r["activo"]]
    if primera_vez:
        lista = "\n".join(f"• {r['titulo']} ${r['precio']:.0f} — {'DISPONIBLE' if r['disponible'] else 'próximamente'}"
                          for r in tenis) or "Ninguno por ahora."
        avisar("👟 Monitor de tenis activo", f"Siguiendo {len(tenis)} tenis en Cactus Jack:\n{lista}",
               os.environ.get("PAGINA_URL"))
    else:
        for tipo, r in eventos:
            precio = f" ${r['precio']:.0f} USD" if "precio" in r else ""
            if tipo == "nuevo":
                fecha = f"\nPosible fecha: {r['fecha_tag']}" if r.get("fecha_tag") else ""
                avisar(f"👟 Nuevo tenis publicado{precio}",
                       f"{r['titulo']} — {'¡DISPONIBLE YA!' if r['disponible'] else 'todavía no a la venta'}{fecha}",
                       r["url"], 5 if r["disponible"] else 4)
            elif tipo == "venta":
                avisar(f"🔥 ¡YA A LA VENTA!{precio}",
                       f"{r['titulo']}\nTallas: {', '.join(r['tallas_disp']) or '?'}", r["url"], 5,
                       ("rotating_light", "athletic_shoe"))
            elif tipo == "agotado":
                avisar("Agotado", f"{r['titulo']} ya no está disponible.", r["url"], 2)
            elif tipo == "fecha":
                avisar("📅 Fecha nueva", f"{r['titulo']}: posible fecha {r['fecha_tag']}", r["url"], 4)
            elif tipo == "retirado":
                avisar("Retirado de la tienda", f"{r['titulo']} ya no aparece en la tienda.", r["url"], 2)
            elif tipo == "contador":
                avisar("⏱️ Nuevo contador en la tienda", f"La portada cuenta hacia: {r['titulo']}", TIENDA, 4)

    for tipo, r in eventos:
        estado["historial"].insert(0, {"cuando": ahora, "tipo": tipo, "titulo": r["titulo"], "url": r["url"]})
    estado["historial"] = estado["historial"][:100]
    estado.pop("ultima_revision", None)  # no se guarda: así estado.json solo cambia si hay cambios reales
    ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=1))

    print(f"{ahora}: {len(vistos)} productos, {len(tenis)} tenis, {len(eventos)} eventos")
    for t in tenis:
        print(f"  {'✅' if t['disponible'] else '⏳'} {t['titulo']} ${t['precio']:.0f}  {t['handle']}")
    generar_html(estado, ahora)


def generar_html(estado, ahora):
    datos = json.dumps({**estado, "ultima_revision": ahora}, ensure_ascii=False).replace("</", "<\\/")
    plantilla = (AQUI / "plantilla.html").read_text(encoding="utf-8")
    (AQUI / "index.html").write_text(plantilla.replace("/*DATA*/null", datos), encoding="utf-8")


if __name__ == "__main__":
    main()
