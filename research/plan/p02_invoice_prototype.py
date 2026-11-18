"""Planner check 2: a throwaway prototype of the synthetic ARCA-format invoice, to decide three things.

Every invoice here is SYNTHETIC: fictional suppliers, fictional buyer, CUITs drawn only from the range
30-0000xxxx-d (the range of ARCA's own worked example, 30000000007), fictional CAE numbers, and the text
"DOCUMENTO SINTETICO - SIN VALIDEZ FISCAL" printed on the page. Nothing is sent anywhere.

1. QR reading on a full rendered page (not a bare code, as research/s04 did): pyzbar against OpenCV's
   QRCodeDetectorAruco, for two printed QR sizes (2.5 cm and 4.0 cm), two resolutions (150 and 200 dpi) and five
   conditions (clean, JPEG quality 35, blur radius 1, rotation 2 degrees, salt noise 1 %).
2. The published Donut CORD checkpoint, unchanged, reading the clean pages: does a receipt model read the lines,
   quantities, amounts and total of an Argentine invoice without tuning? (the baseline B2 of docs/requirements.md)
3. Practical sizes: render time per page, PNG and JPEG bytes per page, and how many synthetic CUITs the range holds.

The real generator is built in the project (docs/plan.md, section 7); this prototype only answers the plan's
questions and is not reused. Reads no CORD shard. Writes data/interim/plan/p02.json and sample pages under
data/interim/plan/p02_pages/ (git-ignored); the summary is versioned as research/plan/p02.out.
Run from the project folder:  .venv\\Scripts\\python -I research/plan/p02_invoice_prototype.py
"""
import base64
import io
import json
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import qrcode
import torch
from PIL import Image, ImageDraw, ImageFilter, ImageFont

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "research"))
from s02_donut import load_model, normalise, predict  # noqa: E402
from s04_arca_qr import synthetic_cuit, valid_cuit  # noqa: E402

OUT = PROJECT / "data" / "interim" / "plan"
PAGES = OUT / "p02_pages"
PAGES.mkdir(parents=True, exist_ok=True)
SEED = 20261119
N = 30
QR_URL = "https://www.arca.gob.ar/fe/qr/?p="
ITEMS = [
    ("Resma papel A4 75 g", "u", 4500, 9000), ("Tornillo hexagonal 8 x 50 mm", "u", 40, 120),
    ("Guante de nitrilo talle M", "caja", 6000, 14000), ("Cinta de embalar 48 mm", "u", 900, 2200),
    ("Aceite hidraulico ISO 68", "l", 3500, 7000), ("Cable unipolar 2,5 mm2", "m", 300, 900),
    ("Pallet de madera 1,2 x 1 m", "u", 15000, 32000), ("Detergente industrial 5 l", "u", 7000, 16000),
    ("Rollo film stretch 50 cm", "u", 9000, 21000), ("Caja carton corrugado 40 x 30", "u", 700, 1800),
    ("Filtro de aire motor", "u", 12000, 30000), ("Lampara LED 18 W", "u", 2500, 6000),
    ("Harina 000 bolsa 25 kg", "u", 9000, 18000), ("Etiqueta termica 100 x 50", "rollo", 3000, 8000),
]
SUPPLIERS = ["Insumos del Sur (ficticio) S.A.", "Distribuidora Ejemplo (ficticia) SRL", "Ferreteria Modelo (ficticia) S.A.",
             "Logistica Prueba (ficticia) SRL", "Papelera Demo (ficticia) S.A.", "Quimica Muestra (ficticia) SRL"]
BUYER = "Comprador Ejemplo (ficticio) S.A."


def ar(x):
    """Argentine number format: dot thousands, comma decimals."""
    s = f"{x:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_cuit(c):
    return f"{c[:2]}-{c[2:10]}-{c[10]}"


def make_invoice(rng, i, cuits):
    lines = []
    for _ in range(rng.randint(1, 8)):
        name, unit, lo, hi = rng.choice(ITEMS)
        qty = rng.randint(1, 60)
        price = round(rng.uniform(lo, hi), 2)
        lines.append({"code": f"{rng.randint(100, 999)}-{rng.randint(10, 99)}", "desc": name, "qty": qty, "unit": unit,
                      "unit_price": price, "amount": round(qty * price, 2)})
    neto = round(sum(l["amount"] for l in lines), 2)
    iva = round(neto * 0.21, 2)
    total = round(neto + iva, 2)
    s = rng.randrange(len(SUPPLIERS))
    inv = {"supplier": SUPPLIERS[s], "cuit": cuits[s], "buyer_cuit": cuits[-1], "pto_vta": rng.randint(1, 20),
           "nro": rng.randint(1, 99999), "fecha": f"2026-{rng.randint(1, 10):02d}-{rng.randint(1, 28):02d}",
           "po": f"OC-2026-{rng.randint(1, 9999):06d}", "lines": lines, "neto": neto, "iva": iva, "total": total,
           "cae": str(rng.randint(10**13, 10**14 - 1)), "layout": "A" if i % 2 == 0 else "B"}
    payload = {"ver": 1, "fecha": inv["fecha"], "cuit": int(inv["cuit"]), "ptoVta": inv["pto_vta"], "tipoCmp": 1,
               "nroCmp": inv["nro"], "importe": total, "moneda": "PES", "ctz": 1, "tipoDocRec": 80,
               "nroDocRec": int(inv["buyer_cuit"]), "tipoCodAut": "E", "codAut": int(inv["cae"])}
    inv["qr_text"] = QR_URL + base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return inv


def qr_image(text, side_px):
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=4)
    q.add_data(text)
    q.make(fit=True)
    img = q.make_image(fill_color="black", back_color="white").convert("L")
    return img.resize((side_px, side_px), Image.NEAREST), q.version


def render(inv, dpi, qr_cm):
    k = dpi / 150
    W, H = int(1240 * k), int(1754 * k)
    page = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(page)

    def font(size):
        return ImageFont.load_default(size=max(8, int(size * k)))

    def t(x, y, s, size=18, anchor="la"):
        d.text((int(x * k), int(y * k)), s, fill=0, font=font(size), anchor=anchor)

    t(620, 30, "DOCUMENTO SINTETICO - SIN VALIDEZ FISCAL", 14, "ma")
    if inv["layout"] == "A":
        d.rectangle([int(60 * k), int(60 * k), int(1180 * k), int(300 * k)], outline=0, width=max(1, int(2 * k)))
        d.rectangle([int(580 * k), int(60 * k), int(660 * k), int(140 * k)], outline=0, width=max(1, int(2 * k)))
        t(620, 70, "A", 48, "ma"); t(620, 122, "COD. 01", 12, "ma")
        t(80, 80, inv["supplier"], 26); t(80, 130, f"CUIT: {fmt_cuit(inv['cuit'])}", 18)
        t(80, 160, "IVA Responsable Inscripto", 16)
        t(700, 80, "FACTURA", 30); t(700, 130, f"Punto de Venta: {inv['pto_vta']:05d}   Comp. Nro: {inv['nro']:08d}", 18)
        t(700, 165, f"Fecha de Emision: {inv['fecha'][8:]}/{inv['fecha'][5:7]}/{inv['fecha'][:4]}", 18)
        t(80, 230, f"Cliente: {BUYER}   CUIT: {fmt_cuit(inv['buyer_cuit'])}", 18)
        t(80, 262, f"Orden de compra: {inv['po']}   Moneda: PES", 18)
        y0, cols = 340, [(70, "Codigo"), (200, "Descripcion"), (650, "Cant."), (740, "U."), (830, "P. Unit."), (1010, "Subtotal")]
    else:
        t(80, 70, "FACTURA A  (Cod. 01)", 30); t(80, 120, f"Nro {inv['pto_vta']:05d}-{inv['nro']:08d}", 22)
        t(80, 160, f"Fecha {inv['fecha'][8:]}/{inv['fecha'][5:7]}/{inv['fecha'][:4]}", 20)
        t(1160, 70, inv["supplier"], 22, "ra"); t(1160, 105, f"C.U.I.T. {fmt_cuit(inv['cuit'])}", 18, "ra")
        t(1160, 135, "Resp. Inscripto", 16, "ra")
        t(80, 220, f"Senores: {BUYER}", 18); t(80, 250, f"CUIT {fmt_cuit(inv['buyer_cuit'])}  -  O.C. {inv['po']}", 18)
        y0, cols = 320, [(70, "Cant."), (160, "Detalle"), (620, "Cod."), (760, "Unid."), (860, "Precio"), (1030, "Importe")]
    for x, h in cols:
        t(x, y0, h, 18)
    if inv["layout"] == "A":
        d.line([int(60 * k), int((y0 + 30) * k), int(1180 * k), int((y0 + 30) * k)], fill=0, width=max(1, int(2 * k)))
    y = y0 + 45
    for ln in inv["lines"]:
        vals = ([ln["code"], ln["desc"], str(ln["qty"]), ln["unit"], ar(ln["unit_price"]), ar(ln["amount"])]
                if inv["layout"] == "A" else
                [str(ln["qty"]), ln["desc"], ln["code"], ln["unit"], ar(ln["unit_price"]), ar(ln["amount"])])
        for (x, _), v in zip(cols, vals):
            t(x, y, v, 17)
        y += 36
    ty = max(y + 40, 900)
    t(1160, ty, f"Importe Neto Gravado: $ {ar(inv['neto'])}", 20, "ra")
    t(1160, ty + 36, f"IVA 21%: $ {ar(inv['iva'])}", 20, "ra")
    t(1160, ty + 80, f"Importe Total: $ {ar(inv['total'])}", 24, "ra")
    side = int(qr_cm / 2.54 * dpi)
    qr, version = qr_image(inv["qr_text"], side)
    page.paste(qr, (int(70 * k), H - side - int(90 * k)))
    t(70 + side / k + 30, 1754 - 90 - side / k + 10, f"CAE N: {inv['cae']}", 18)
    t(70 + side / k + 30, 1754 - 90 - side / k + 40, "Comprobante Autorizado (sintetico)", 16)
    return page, version


def degrade(img, cond, rng):
    if cond == "clean":
        return img
    if cond == "jpeg35":
        b = io.BytesIO(); img.save(b, "JPEG", quality=35); return Image.open(io.BytesIO(b.getvalue())).convert("L")
    if cond == "blur1":
        return img.filter(ImageFilter.GaussianBlur(1))
    if cond == "rot2":
        return img.rotate(2, expand=True, fillcolor=255, resample=Image.BICUBIC)
    if cond == "salt1":
        a = np.array(img).copy(); m = np.random.default_rng(rng.randint(0, 10**6)).random(a.shape) < 0.01
        a[m] = 255 - a[m]; return Image.fromarray(a)
    raise ValueError(cond)


def read_pyzbar(img):
    from pyzbar.pyzbar import ZBarSymbol, decode
    return [r.data.decode("utf-8", "replace") for r in decode(img, symbols=[ZBarSymbol.QRCODE])]


def read_aruco(img):
    det = cv2.QRCodeDetectorAruco()
    ok, texts, _, _ = det.detectAndDecodeMulti(np.array(img))
    return [x for x in texts if x] if ok else []


def main():
    rng = random.Random(SEED)
    pool = [c for c in (synthetic_cuit(s) for s in range(1, 10000)) if c]
    assert all(valid_cuit(c) and c.startswith("300000") for c in pool)
    cuits = rng.sample(pool, len(SUPPLIERS) + 1)
    invoices = [make_invoice(rng, i, cuits) for i in range(N)]
    result = {"seed": SEED, "invoices": N, "synthetic": True, "cuit_pool_serials_1_9999": len(pool)}

    t0 = time.perf_counter(); page, version = render(invoices[0], 150, 2.5); render_s = time.perf_counter() - t0
    b_png, b_jpg = io.BytesIO(), io.BytesIO()
    page.save(b_png, "PNG", optimize=True); page.save(b_jpg, "JPEG", quality=85)
    result["page"] = {"render_seconds": round(render_s, 3), "png_bytes": b_png.tell(), "jpeg85_bytes": b_jpg.tell(),
                      "qr_version": version}

    qr = {}
    for dpi in (150, 200):
        for qr_cm in (2.5, 4.0):
            pages = [render(inv, dpi, qr_cm)[0] for inv in invoices]
            if dpi == 150 and qr_cm == 2.5:
                for j in (0, 1):
                    pages[j].save(PAGES / f"invoice_{j:02d}_layout{invoices[j]['layout']}.png")
            for cond in ("clean", "jpeg35", "blur1", "rot2", "salt1"):
                hits = {"pyzbar": 0, "aruco": 0}
                for inv, p in zip(invoices, pages):
                    img = degrade(p, cond, rng)
                    hits["pyzbar"] += int(inv["qr_text"] in read_pyzbar(img))
                    hits["aruco"] += int(inv["qr_text"] in read_aruco(img))
                qr[f"{dpi}dpi_{qr_cm}cm_{cond}"] = {"px_side": int(qr_cm / 2.54 * dpi), **hits, "n": N}
    result["qr_on_page"] = qr

    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    processor, model = load_model(dev)
    tot = {"qty": [0, 0], "amount": [0, 0], "unit_price": [0, 0], "desc": [0, 0], "total": [0, 0]}
    all_ok = 0
    by_layout = {"A": [0, 0], "B": [0, 0]}
    samples = []
    for inv in invoices:
        p = render(inv, 150, 2.5)[0].convert("RGB")
        b = io.BytesIO(); p.save(b, "PNG")
        pred = predict(processor, model, dev, b.getvalue())
        if not isinstance(pred, dict):  # token2json returns a list when the output has several top-level trees
            pred = next((x for x in pred if isinstance(x, dict)), {}) if isinstance(pred, list) else {}
        menu = pred.get("menu", [])
        menu = [menu] if isinstance(menu, dict) else [m for m in menu if isinstance(m, dict)]
        pools = {f: [normalise(m.get(k)) for m in menu] for f, k in
                 (("qty", "cnt"), ("amount", "price"), ("unit_price", "unitprice"), ("desc", "nm"))}
        ok_inv = True
        for ln in inv["lines"]:
            for f, v in (("qty", ln["qty"]), ("amount", ar(ln["amount"])), ("unit_price", ar(ln["unit_price"])),
                         ("desc", ln["desc"])):
                nv = normalise(v); hit = nv in pools[f]
                if hit:
                    pools[f].remove(nv)
                tot[f][0] += int(hit); tot[f][1] += 1
                ok_inv &= hit or f in ("unit_price", "desc")
        tp = pred.get("total", {}); tp = tp.get("total_price") if isinstance(tp, dict) else None
        hit = normalise(tp) == normalise(ar(inv["total"])); tot["total"][0] += int(hit); tot["total"][1] += 1
        ok_inv &= hit
        all_ok += int(ok_inv)
        by_layout[inv["layout"]][0] += int(ok_inv); by_layout[inv["layout"]][1] += 1
        if len(samples) < 2:
            samples.append(pred)
    result["donut_cord_checkpoint_on_invoices"] = {
        "recall": {f: round(a / b * 100, 1) for f, (a, b) in tot.items()},
        "counts": tot, "invoices_all_qty_amount_and_total_right": all_ok, "by_layout": by_layout,
        "sample_predictions": samples}
    (OUT / "p02.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[p02] {N} synthetic invoices (seed {SEED}), two layouts; CUIT range 30-0000xxxx-d holds "
          f"{len(pool)} valid CUITs for serials 1-9999")
    pg = result["page"]
    print(f"[p02] page A4 at 150 dpi: render {pg['render_seconds']} s, PNG {pg['png_bytes']} B, JPEG q85 "
          f"{pg['jpeg85_bytes']} B, QR version {pg['qr_version']}")
    for key, v in qr.items():
        print(f"[p02] QR {key} ({v['px_side']} px): pyzbar {v['pyzbar']}/{v['n']}, aruco {v['aruco']}/{v['n']}")
    dn = result["donut_cord_checkpoint_on_invoices"]
    print(f"[p02] Donut CORD checkpoint unchanged on the clean invoices: recall " +
          ", ".join(f"{k} {v} %" for k, v in dn["recall"].items()) +
          f"; invoices with every quantity, every line amount and the total right: {all_ok}/{N} "
          f"(layout A {by_layout['A'][0]}/{by_layout['A'][1]}, B {by_layout['B'][0]}/{by_layout['B'][1]})")
    print("[p02] no CORD shard was opened; every invoice is synthetic")
    return 0


if __name__ == "__main__":
    sys.exit(main())
