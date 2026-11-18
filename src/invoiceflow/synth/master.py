"""The master of the synthetic ERP (RF04; plan section 7.1; decisions D04, D05, D22): the suppliers, the items, the buyer, the purchase orders and the receipts.

``master(view)`` is a pure function of the sealed view's ``[synth]`` and ``[calendar]`` sections and nothing else: the same configuration gives the same
master, every list in a fixed order and every amount a ``Decimal``. Each aspect draws from its own ``random.Random`` stream (named by the seed and the aspect), so
changing the number of invoices moves the orders and not the suppliers' names or CUIT.

* **Suppliers**: ``suppliers_known`` with the layouts and fonts the generator knows, ``suppliers_new_validation`` with the layout and font reserved for the
  validation, ``suppliers_new_test`` with the ones reserved for the test (D10). Names are drawn from a fixed list and end in «(ficticio)» or «(ficticia)»; each CUIT
  is a serial drawn without replacement from ``cuit_serial_range`` and made by ``synth.cuit`` (D05), and so is the buyer's.
* **Items**: 150, in 15 families, with a unit and a list price in pesos.
* **Orders**: sized so that every invoice that will be drawn has one (at least one and a half per invoice, spread over the suppliers of its cohort): the
  known suppliers have orders for the training and the known validation in the history, and for the test days in the two weeks before them and during them; the new
  validation suppliers in the history; the new test suppliers in the test window. Numbered ``OC-2026-NNNNNN`` in the order of their date.
* **Receipts**: 60 % of the orders arrive whole in one receipt and 40 % in two (the second is the rest), one to ten days after the order, numbered ``REM-NNNNNN``
  in the order they arrive. What a receipt of the first kind holds is exactly what was ordered; the match of U11 and the class ``over_receipt`` of U06 read that.
* **Events**: the orders and the receipts by date, an order before a receipt of the same day: the ERP loads each on its day (D22).
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from invoiceflow.config import SealedConfig
from invoiceflow.synth.cuit import require_synthetic_cuit, synthetic_cuit

PO_YEAR = 2026
OVER_ORDER_MARGIN = 1.5          # orders per invoice that will be drawn
TEST_LEAD_DAYS = 14              # the orders of the test days start this long before the first one
RECEIPT_DAYS = (1, 10)           # a receipt arrives one to ten days after its order
WHOLE_SHARE = 0.60               # orders that arrive in one receipt
MAX_PO_LINES = 8
QTY_RANGE = (1, 50)
PRICE_NOISE = 0.05
DOL_DIVISOR = Decimal(1000)      # the list price is in pesos: an order in dollars prices an item at a thousandth of it
IVA_CONDITION = "Responsable Inscripto"
CENT = Decimal("0.01")
BUYER_NAME = "Comprador Ejemplo (ficticio) S.A."
KINDS = ("known", "new_validation", "new_test")

TRADES = (("Distribuidora", "(ficticia)"), ("Ferretería", "(ficticia)"), ("Papelera", "(ficticia)"), ("Suministros", "(ficticio)"), ("Insumos", "(ficticio)"),
          ("Comercial", "(ficticia)"), ("Mayorista", "(ficticia)"), ("Proveeduría", "(ficticia)"), ("Industrial", "(ficticia)"))
PLACES = ("del Plata", "Andina", "Pampeana", "Patagónica", "Cuyana", "del Litoral", "Norteña", "Austral")
LEGAL_FORMS = ("S.A.", "S.R.L.")
# family, unit, base list price in pesos (the first of the ten items of the family; the others scale it)
FAMILIES = (
    ("Tornillo hexagonal", "caja", 4200), ("Tuerca", "caja", 3100), ("Arandela plana", "caja", 2400), ("Cinta adhesiva", "u", 950), ("Resma de papel", "u", 7800),
    ("Detergente industrial", "l", 2100), ("Lavandina", "l", 650), ("Guantes de nitrilo", "caja", 9800), ("Cable unipolar", "m", 480), ("Pintura látex", "l", 5200),
    ("Lubricante", "l", 6400), ("Bolsa de residuos", "u", 1200), ("Cartucho de tóner", "u", 38500), ("Carpeta archivadora", "u", 2300), ("Bidón de agua", "u", 4100),
)
GRADES = (("estándar", Decimal("1.00")), ("reforzado", Decimal("1.35")))
PACKS = ((1, "x 1"), (5, "x 5"), (10, "x 10"), (25, "x 25"), (50, "x 50"))


@dataclass(frozen=True)
class Supplier:
    supplier_key: str
    name_es: str
    cuit: str
    iva_condition: str
    layout: str
    font: str
    cohort: str
    points_of_sale: tuple[int, ...]


@dataclass(frozen=True)
class Item:
    code: str
    description_es: str
    unit: str
    list_price: Decimal


@dataclass(frozen=True)
class Buyer:
    name_es: str
    cuit: str


@dataclass(frozen=True)
class PoLine:
    item_code: str
    description_es: str
    qty: Decimal
    unit: str
    unit_price: Decimal


@dataclass(frozen=True)
class PurchaseOrder:
    po_number: str
    supplier_key: str
    po_date: date
    currency: str
    lines: tuple[PoLine, ...]


@dataclass(frozen=True)
class ReceiptLine:
    item_code: str
    qty: Decimal


@dataclass(frozen=True)
class Receipt:
    receipt_number: str
    po_number: str
    receipt_date: date
    lines: tuple[ReceiptLine, ...]


@dataclass(frozen=True)
class MasterEvent:
    date: date
    kind: str                       # "po" or "receipt"
    document: PurchaseOrder | Receipt


@dataclass(frozen=True)
class Master:
    suppliers: tuple[Supplier, ...]
    items: tuple[Item, ...]
    buyer: Buyer
    orders: tuple[PurchaseOrder, ...]
    receipts: tuple[Receipt, ...]
    events: tuple[MasterEvent, ...]


def _stream(seed: int, aspect: str) -> random.Random:
    return random.Random(f"invoiceflow.master:{seed}:{aspect}")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _weekdays(first: date, last: date) -> list[date]:
    return [day for n in range((last - first).days + 1) if (day := first + timedelta(days=n)).weekday() < 5]


def _test_days(view: SealedConfig) -> int:
    first, last = view.calendar.test_first_day, view.calendar.test_last_day
    every = (first + timedelta(days=n) for n in range((last - first).days + 1))
    return sum(1 for day in every if not view.calendar.weekdays_only or day.weekday() < 5)


def _cuits(view: SealedConfig, count: int) -> list[str]:
    """`count` distinct synthetic CUIT: serials drawn without replacement from the configured range, the ones with no CUIT skipped."""
    low, high = view.synth.cuit_serial_range
    serials = list(range(low, high + 1))
    _stream(view.synth.seed_master, "cuit").shuffle(serials)
    chosen: list[str] = []
    for serial in serials:
        cuit = synthetic_cuit(serial)
        if cuit is not None:
            chosen.append(cuit)
            if len(chosen) == count:
                return chosen
    raise ValueError(f"[synth] cuit_serial_range: el rango {low} a {high} da {len(chosen)} CUIT y hacen falta {count} (proveedores y comprador)")


def _suppliers(view: SealedConfig, cuits: list[str]) -> tuple[Supplier, ...]:
    synth = view.synth
    cohorts = [("known", synth.suppliers_known), ("new_validation", synth.suppliers_new_validation), ("new_test", synth.suppliers_new_test)]
    total = sum(count for _, count in cohorts)
    combos = [(trade, place) for trade in TRADES for place in PLACES]
    chosen = _stream(synth.seed_master, "names").sample(range(len(combos)), total)
    pos = _stream(synth.seed_master, "points_of_sale")
    suppliers = []
    for number in range(total):
        kind = KINDS[0] if number < cohorts[0][1] else KINDS[1] if number < cohorts[0][1] + cohorts[1][1] else KINDS[2]
        (trade, mark), place = combos[chosen[number]]
        legal = LEGAL_FORMS[number % len(LEGAL_FORMS)]
        if kind == "known":
            layout, font = synth.layouts_known[number % len(synth.layouts_known)], synth.fonts_known[number % len(synth.fonts_known)]
        elif kind == "new_validation":
            layout, font = synth.layout_validation_new, synth.font_validation_new
        else:
            layout, font = synth.layout_test_new, synth.font_test_new
        points = tuple(sorted(pos.sample(range(1, 21), pos.randint(1, 3))))
        suppliers.append(Supplier(f"S{number + 1:03d}", f"{trade} {place} {legal} {mark}", cuits[number], IVA_CONDITION, layout, font, kind, points))
    return tuple(suppliers)


def _items(view: SealedConfig) -> tuple[Item, ...]:
    jitter = _stream(view.synth.seed_master, "items")
    items = []
    for family, unit, base in FAMILIES:
        for grade, grade_factor in GRADES:
            for pack, label in PACKS:
                price = _money(Decimal(base) * grade_factor * Decimal(pack) * Decimal(str(round(0.85 + 0.30 * jitter.random(), 4))) * Decimal("0.9") ** (1 if pack > 1 else 0))
                items.append(Item(f"ART-{len(items) + 1:04d}", f"{family} {grade} {label}", unit, max(price, CENT)))
    return tuple(items)


def _count(needed: int, suppliers: int) -> int:
    """Orders per supplier so that a cohort has at least `OVER_ORDER_MARGIN` orders for each invoice it will get."""
    return math.ceil(OVER_ORDER_MARGIN * needed / suppliers)


def _orders(view: SealedConfig, suppliers: tuple[Supplier, ...], items: tuple[Item, ...]) -> tuple[PurchaseOrder, ...]:
    synth, calendar = view.synth, view.calendar
    days = _test_days(view)
    history = _weekdays(calendar.history_start, calendar.launch_last_day)
    test = _weekdays(calendar.test_first_day - timedelta(days=TEST_LEAD_DAYS), calendar.test_last_day - timedelta(days=1))
    plan = {
        "known": [(history, _count(synth.n_train + synth.n_validation_known, synth.suppliers_known)),
                  (test, _count(days * synth.test_per_day_known, synth.suppliers_known))],
        "new_validation": [(history, _count(synth.n_validation_new, synth.suppliers_new_validation))],
        "new_test": [(test, _count(days * synth.test_per_day_new, synth.suppliers_new_test))],
    }
    draw = _stream(synth.seed_master, "orders")
    drafts = []
    for supplier in suppliers:
        for window, count in plan[supplier.cohort]:
            for _ in range(count):
                po_date = draw.choice(window)
                currency = "DOL" if draw.random() < synth.usd_share else "PES"
                lines = []
                for item in draw.sample(items, draw.randint(1, min(MAX_PO_LINES, synth.max_lines))):
                    reference = item.list_price / DOL_DIVISOR if currency == "DOL" else item.list_price
                    price = max(_money(reference * Decimal(str(round(1 + draw.uniform(-PRICE_NOISE, PRICE_NOISE), 4)))), CENT)
                    lines.append(PoLine(item.code, item.description_es, Decimal(draw.randint(*QTY_RANGE)), item.unit, price))
                drafts.append((po_date, supplier.supplier_key, currency, tuple(lines)))
    drafts.sort(key=lambda draft: (draft[0], draft[1]))
    return tuple(PurchaseOrder(f"OC-{PO_YEAR}-{number:06d}", key, po_date, currency, lines)
                 for number, (po_date, key, currency, lines) in enumerate(drafts, start=1))


def _receipts(view: SealedConfig, orders: tuple[PurchaseOrder, ...]) -> tuple[Receipt, ...]:
    draw = _stream(view.synth.seed_master, "receipts")
    drafts = []
    for po in orders:
        arrivals = [draw.randint(*RECEIPT_DAYS)]
        halves = [{line.item_code: line.qty // 2 for line in po.lines}]   # what the first receipt holds when there are two; the second holds the rest
        two = draw.random() >= WHOLE_SHARE and any(qty > 0 for qty in halves[0].values())
        if two:
            arrivals = sorted(draw.sample(range(RECEIPT_DAYS[0], RECEIPT_DAYS[1] + 1), 2))
            first = [ReceiptLine(code, qty) for code, qty in halves[0].items() if qty > 0]
            second = [ReceiptLine(line.item_code, line.qty - halves[0][line.item_code]) for line in po.lines if line.qty - halves[0][line.item_code] > 0]
            parts = [first, second]
        else:
            parts = [[ReceiptLine(line.item_code, line.qty) for line in po.lines]]
        for days, lines in zip(arrivals, parts, strict=True):
            drafts.append((po.po_date + timedelta(days=days), po.po_number, tuple(lines)))
    drafts.sort(key=lambda draft: (draft[0], draft[1]))
    return tuple(Receipt(f"REM-{number:06d}", po_number, receipt_date, lines) for number, (receipt_date, po_number, lines) in enumerate(drafts, start=1))


def master(view: SealedConfig) -> Master:
    """The master of the synthetic ERP for the sealed view `view` (only its ``[synth]`` and ``[calendar]`` sections are read). Every CUIT it holds is
    synthetic (M07) and is checked as it is made."""
    synth = view.synth
    total = synth.suppliers_known + synth.suppliers_new_validation + synth.suppliers_new_test
    cuits = _cuits(view, total + 1)
    for cuit in cuits:
        require_synthetic_cuit(cuit, "synth.master")
    suppliers = _suppliers(view, cuits[:total])
    items = _items(view)
    orders = _orders(view, suppliers, items)
    receipts = _receipts(view, orders)
    events = sorted([MasterEvent(po.po_date, "po", po) for po in orders] + [MasterEvent(r.receipt_date, "receipt", r) for r in receipts],
                    key=lambda event: (event.date, 0 if event.kind == "po" else 1, event.document.po_number if event.kind == "po" else event.document.receipt_number))
    return Master(suppliers, items, Buyer(BUYER_NAME, cuits[total]), orders, receipts, tuple(events))


def to_jsonable(value: Any) -> Any:
    """The master (or any part of it) as JSON-able values: dataclasses as objects, ``Decimal`` as its text, dates as ISO text, tuples as lists."""
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (tuple, list)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _canonical(value: Master) -> list:
    """What the fingerprint hashes: every fact of the master once, as plain lists and texts (a line's description and unit repeat its item's, and the events
    repeat the orders and the receipts, so they are left out: the hash would take ten times as long for the same information)."""
    return [
        [[s.supplier_key, s.name_es, s.cuit, s.iva_condition, s.layout, s.font, s.cohort, list(s.points_of_sale)] for s in value.suppliers],
        [[i.code, i.description_es, i.unit, str(i.list_price)] for i in value.items],
        [value.buyer.name_es, value.buyer.cuit],
        [[po.po_number, po.supplier_key, po.po_date.isoformat(), po.currency, [[line.item_code, str(line.qty), str(line.unit_price)] for line in po.lines]]
         for po in value.orders],
        [[r.receipt_number, r.po_number, r.receipt_date.isoformat(), [[line.item_code, str(line.qty)] for line in r.lines]] for r in value.receipts],
    ]


def fingerprint(value: Master) -> str:
    """The SHA-256 of the master's canonical JSON: it enters the seal's fingerprint (D21)."""
    text = json.dumps(_canonical(value), separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
