#!/usr/bin/env python3
"""Genera el catálogo público de Comprame Wacho desde la API de Jumpseller.
Sólo lee productos; no modifica el inventario de la tienda.
"""
from __future__ import annotations
import argparse
import base64
from collections import defaultdict
from datetime import datetime
from html import escape
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "index.html"
OUT = ROOT / "public" / "index.html"
SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL"]
STATUSES = {
    "available": ("Disponible", "ok"),
    "not-available": ("No disponible", "warn"),
    "disabled": ("Deshabilitado", "off"),
}

def safe(v):
    return escape(str("" if v is None else v), quote=True)

def amount(v):
    try:
        return max(0, int(float(v or 0)))
    except (TypeError, ValueError):
        return 0

def list_products():
    login = os.environ.get("JUMPSELLER_LOGIN", "").strip()
    token = os.environ.get("JUMPSELLER_AUTH_TOKEN", "").strip()
    if not login or not token:
        raise RuntimeError("Faltan los secretos de API de Jumpseller.")
    auth = base64.b64encode(f"{login}:{token}".encode()).decode()
    products = []
    for page in range(1, 101):
        url = "https://api.jumpseller.com/v1/products.json?" + urlencode({"limit": 100, "page": page})
        req = Request(url, headers={
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "User-Agent": "ComprameWachoCatalog/1.0",
        })
        with urlopen(req, timeout=35) as response:
            body = json.load(response)
        if not isinstance(body, list):
            raise ValueError("La API no devolvió una lista de productos.")
        batch = []
        for entry in body:
            item = entry.get("product", entry) if isinstance(entry, dict) else None
            if not isinstance(item, dict):
                raise ValueError("Producto con formato desconocido.")
            batch.append(item)
        products.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise RuntimeError("Se excedió el límite de paginación.")
    if not products:
        raise RuntimeError("La API devolvió cero productos; se conserva el catálogo existente.")
    return products

def stock_matrix(product):
    variants = product.get("variants") or []
    matrix = defaultdict(dict)
    if not variants:
        value = "∞" if product.get("stock_unlimited") else amount(product.get("stock"))
        matrix["Sin especificar"]["Única"] = value
    else:
        for wrapped in variants:
            variant = wrapped.get("variant", wrapped) if isinstance(wrapped, dict) else None
            if not isinstance(variant, dict):
                raise ValueError("La API devolvió una variante inválida.")
            color, size = "Sin especificar", "Única"
            for option in variant.get("options") or []:
                if not isinstance(option, dict):
                    continue
                name = str(option.get("name") or "").strip().casefold()
                kind = str(option.get("option_type") or "").strip().casefold()
                value = str(option.get("value") or "").strip() or "Sin especificar"
                if name in ("color", "colour", "colores") or kind == "color":
                    color = value
                elif name in ("talla", "tallas", "size", "tamaño", "tamano"):
                    size = value.upper()
            qty = "∞" if variant.get("stock_unlimited") else amount(variant.get("stock"))
            old = matrix[color].get(size, 0)
            matrix[color][size] = "∞" if old == "∞" or qty == "∞" else old + qty
    finite = sum(x for row in matrix.values() for x in row.values() if isinstance(x, int))
    infinite = any(x == "∞" for row in matrix.values() for x in row.values())
    return dict(matrix), finite, infinite, finite > 0 or infinite

def image_url(product):
    for raw in product.get("images") or []:
        if isinstance(raw, str):
            url = raw
        elif isinstance(raw, dict):
            pic = raw.get("image", raw)
            url = pic.get("url", "") if isinstance(pic, dict) else ""
        else:
            url = ""
        if isinstance(url, str) and url.startswith("https://"):
            return url
    return ""

def card(product, index):
    name = str(product.get("name") or f"Modelo {index}")
    brand = str(product.get("brand") or "Sin marca indicada").strip()
    status = str(product.get("status") or "unknown").strip().lower()
    label, cls = STATUSES.get(status, ("Estado no identificado", "off"))
    matrix, finite, infinite, has_stock = stock_matrix(product)
    stock_text = f"{finite:,}".replace(",", ".")
    if infinite:
        stock_text = stock_text + " + ∞" if finite else "Ilimitado"
    image = image_url(product)
    if image:
        media = (
            f'<a class="photo" href="{safe(image)}" target="_blank" rel="noopener noreferrer">'
            f'<img loading="lazy" src="{safe(image)}" alt="Foto de {safe(name)}" '
            'onerror="this.style.display=\'none\';this.parentElement.classList.add(\'image-missing\')">'
            '<span class="photo-fallback">Ver foto ↗</span></a>'
        )
    else:
        media = '<div class="photo image-missing"><span class="photo-fallback">Sin fotografía</span></div>'
    flag = '<span class="flag">Revisar publicación</span>' if status != "available" and has_stock else ""
    units = max(finite, 1) if infinite else finite
    html = (
        f'<article class="product {"with" if has_stock else "without"}" '
        f'data-name="{safe(name.lower())}" data-brand="{safe(brand.lower())}" '
        f'data-status="{safe(status)}" data-stock="{units}" data-finite="{finite}" data-infinite="{str(infinite).lower()}">'
        f'<div class="card-top">{media}<div class="card-meta"><div class="brand">{safe(brand)}</div>'
        f'<h3>{safe(name)}</h3><div class="badges"><span class="state {cls}">{safe(label)}</span>{flag}</div>'
        f'<div class="stock-title"><b>{safe(stock_text)}</b><span> unidades registradas</span>'
        '</div></div></div>'
    )
    colors = sorted(matrix, key=str.casefold)
    sizes = sorted(
        {size for row in matrix.values() for size in row},
        key=lambda x: (SIZE_ORDER.index(x) if x in SIZE_ORDER else 99, x),
    )
    if has_stock:
        html += '<div class="table-scroll"><table class="matrix"><thead><tr><th>Color</th>'
        html += "".join(f"<th>{safe(size)}</th>" for size in sizes)
        html += "<th>Total</th></tr></thead><tbody>"
        for color in colors:
            html += f"<tr><th>{safe(color)}</th>"
            for size in sizes:
                v = matrix[color].get(size)
                kind = "missing" if v is None else "zero" if v == 0 else "positive"
                html += f'<td class="{kind}">{"—" if v is None else safe(v)}</td>'
            values = list(matrix[color].values())
            total = sum(v for v in values if isinstance(v, int))
            display = f"{total:,}".replace(",", ".")
            if "∞" in values:
                display = display + " + ∞" if total else "∞"
            html += f'<td class="total">{display}</td></tr>'
        html += "</tbody></table></div>"
    else:
        html += (
            f'<div class="emptydetails"><strong>Colores:</strong> {safe(", ".join(colors))}'
            f'<br><strong>Tallas cargadas:</strong> {safe(", ".join(sizes))}</div>'
        )
    link = f'<a href="{safe(image)}" target="_blank" rel="noopener noreferrer">Abrir imagen ↗</a>' if image else ""
    html += f'<div class="foot"><small>ID: {safe(product.get("permalink") or product.get("id") or "")}</small>{link}</div></article>'
    return html, finite, infinite, has_stock, status, brand

def replace_one(html, pattern, value, *, flags=0):
    html, count = re.subn(pattern, lambda _: value, html, count=1, flags=flags)
    if count != 1:
        raise ValueError(f"La plantilla no tiene el campo requerido: {pattern[:40]}")
    return html

def build(template, products):
    cards = [card(p, i) for i, p in enumerate(products, 1)]
    yes = "".join(c[0] for c in cards if c[3])
    no = "".join(c[0] for c in cards if not c[3])
    models, with_stock = len(cards), sum(c[3] for c in cards)
    finite = sum(c[1] for c in cards)
    alerts = [c for c in cards if c[3] and c[4] != "available"]
    brands = sorted({c[5].lower() for c in cards}, key=str.casefold)
    options = '<option value="all">Todas las marcas</option>' + "".join(
        f'<option value="{safe(b)}">{safe(b.upper() if b=="bape" else b.title())}</option>'
        for b in brands
    )
    template = replace_one(
        template, r'(<select id="brandfilter"[^>]*>).*?(</select>)',
        None if False else "", flags=re.S
    ) if False else template
    template, n = re.subn(
        r'(<select id="brandfilter"[^>]*>).*?(</select>)',
        lambda m: m.group(1) + options + m.group(2), template, count=1, flags=re.S
    )
    if n != 1:
        raise ValueError("Falta el selector de marca.")
    for grid_id, html_cards in (("con-stock", yes), ("sin-stock", no)):
        template, n = re.subn(
            r'(<div class="model-grid" id="' + grid_id + r'">).*?(</div></section>)',
            lambda m: m.group(1) + html_cards + m.group(2),
            template, count=1, flags=re.S
        )
        if n != 1:
            raise ValueError("Falta la sección " + grid_id)
    for label, count in (
        ("Modelos en catálogo", models),
        ("Modelos con unidades", with_stock),
        ("Unidades registradas", f"{finite:,}".replace(",", ".")),
        ("Modelos sin unidades", models - with_stock),
    ):
        pattern = r'(<div class="stat"><label>' + re.escape(label) + r'</label><strong>).*?(</strong></div>)'
        template, n = re.subn(pattern, lambda m: m.group(1) + str(count) + m.group(2), template, count=1)
        if n != 1:
            raise ValueError("Falta estadística: " + label)
    for id_, count in (("count-stock", with_stock), ("count-no-stock", models - with_stock)):
        template, n = re.subn(
            rf'(<span id="{id_}">).*?(</span>)',
            lambda m: m.group(1) + str(count) + m.group(2), template, count=1
        )
        if n != 1:
            raise ValueError("Falta el contador " + id_)
    template = re.sub(r'>\d+ modelos encontrados</span>', f'>{models} modelos encontrados</span>', template, count=1)
    now = datetime.now(ZoneInfo("America/Santiago")).strftime("%d/%m/%Y a las %H:%M")
    template = replace_one(
        template, r'<div class="snapshot">.*?</div></header>',
        f'<div class="snapshot">Actualizado desde Jumpseller<br><b>{now} (Chile)</b></div></header>',
        flags=re.S,
    )
    template = re.sub(
        r'<div class="sub">Fotografías y cantidades tomadas del CSV de Jumpseller[^<]*</div>',
        '<div class="sub">Inventario sincronizado desde Jumpseller · stock por variantes</div>',
        template, count=1
    )
    qty = sum(c[1] for c in alerts)
    alert = (
        f'{len(alerts)} modelos con stock registrado figuran como no disponibles o deshabilitados '
        f'({qty:,} unidades). El estado de publicación es independiente del stock físico.'
    ).replace(",", ".")
    template = replace_one(
        template, r'<div class="alert"><strong>Atención:</strong>.*?</div>',
        f'<div class="alert"><strong>Atención:</strong> {alert}</div>', flags=re.S
    )
    template, n = re.subn(
        r'<footer>Comprame Wacho \| Fuente:.*?</footer>',
        f'<footer>Comprame Wacho | Fuente: API Jumpseller · actualización {now} (Chile) | {models} modelos</footer>',
        template, count=1
    )
    return template, {"modelos": models, "con_stock": with_stock, "unidades": finite}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", help="Archivo JSON de prueba, sin usar la API.")
    args = parser.parse_args()
    products = json.loads(Path(args.fixture).read_text(encoding="utf-8")) if args.fixture else list_products()
    products = [p.get("product", p) for p in products]
    output, summary = build(TEMPLATE.read_text(encoding="utf-8"), products)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(output, encoding="utf-8")
    print("Catálogo generado:", summary)

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Error de sincronización: {error}", file=sys.stderr)
        sys.exit(1)
