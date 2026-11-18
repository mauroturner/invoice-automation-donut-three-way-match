"""The synthetic invoices and their truth (RF05; plan sections 7.2 to 7.4; decisions D04, D05, D10, D21, D22).

``invoices(view, master, split, day, seal_path=...)`` makes the invoices of one partition, or of one test day, from the master of the synthetic ERP. Everything an invoice
is comes from the master (its supplier, its order and what was received) and from random streams named by the seed of the split, the split and the invoice's ordinal,
so an invoice never depends on what was drawn before it and the same configuration gives the same invoices. The truth is what the page shows and what its QR holds:
a planted discrepancy is already in it (a price over the order's, a total that does not add up), and ``planted``, ``expected_decision`` and ``expected_reasons`` say what
the generator meant, so the oracle B3 (U11) can check the rules against the intention and not against themselves.

**How an invoice finds its order.** Each partition has its own share of the orders (``_owner``: the orders of the known suppliers dated from the lead window of the test
belong to ``test_known``, the older ones are split by a fixed draw between ``train`` and ``validation_known``; each new-supplier cohort has its own), and an order is
invoiced once, in the whole world: no order is shared between partitions. A right invoice is invoiced on or after the day the last receipt arrived and carries the lines of
its order; an ``over_receipt`` is invoiced between the two receipts of an order. A partition's plan (``_plan``) walks the ordinals in order and takes each order out
of the pool, so a day can be generated alone and equals its part of the whole.

**The days.** The test is generated per day: ordinal ``n`` of a test split is invoice ``n % per_day`` of day ``n // per_day`` of the calendar. Training and validation
invoices have no delivery day: each gets a business day after its order's last receipt (``received_on``, the day it reached accounts payable, which is also the day of its
date unless the class says otherwise). A ``duplicate`` re-scans an invoice of an earlier day of the same partition (an earlier ordinal in a partition without days).

**The guard of the test (D10, D21).** A test split is generated only when the seal has a ``launch`` part with the fingerprint of this configuration; otherwise
``GenerationRefusedError``, and nothing is written.
"""

from __future__ import annotations

import random
from bisect import bisect_right
from dataclasses import asdict, dataclass, replace
from itertools import chain
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from invoiceflow.config import SealedConfig
from invoiceflow.synth import qrpayload
from invoiceflow.synth.cuit import require_synthetic_cuit
from invoiceflow.synth.master import DOL_DIVISOR, TEST_LEAD_DAYS, Master, PurchaseOrder, Receipt, Supplier

SPLITS = ("train", "validation_known", "validation_new", "test_known", "test_new")
FIT_SPLITS = ("train", "validation_known", "validation_new")
TEST_SPLITS = ("test_known", "test_new")
COHORT_OF = {"train": "known", "validation_known": "known", "test_known": "known", "validation_new": "new_validation", "test_new": "new_test"}
SEED_FIELD = {"train": "seed_train", "validation_known": "seed_validation", "validation_new": "seed_validation", "test_known": "seed_test", "test_new": "seed_test"}
PLANTED_CLASSES = ("price_off", "over_receipt", "over_order", "total_mismatch", "supplier_mismatch", "no_po", "duplicate", "date_before_po", "currency_mismatch",
                   "extra_line", "qr_missing", "qr_tampered", "credit_note")
FIRST_REASON = {
    "price_off": ("price_out_of_tolerance",), "over_receipt": ("qty_over_receipt",), "over_order": ("qty_over_receipt", "qty_over_order"),
    "total_mismatch": ("total_arithmetic",), "supplier_mismatch": ("supplier_mismatch",), "no_po": ("no_po",), "duplicate": ("duplicate",),
    "date_before_po": ("date_out_of_range",), "currency_mismatch": ("currency_mismatch",), "extra_line": ("line_not_on_po",), "qr_missing": ("qr_not_read",),
    "qr_tampered": ("qr_print_mismatch",), "credit_note": ("unsupported_type",),
}
FIRST_NUMBER = 1000          # the first voucher number of the first invoice of the first partition; a partition's numbers follow the previous one's
TYPE_INVOICE_A, TYPE_CREDIT_NOTE_A = 1, 3
RECEIVER_DOCUMENT_TYPE = 80  # CUIT
CENT = Decimal("0.01")
NONEXISTENT_PO_BASE = 900_000
ATTEMPTS = 200               # random draws of a pool before it is scanned in order


class GenerationRefusedError(RuntimeError):
    """The test was asked for before the launch was sealed with this configuration (D10, D21). The command line exits with 2."""

    EXIT_CODE = 2


@dataclass(frozen=True)
class InvoiceLine:
    code: str
    description: str
    qty: Decimal
    unit: str
    unit_price: Decimal
    amount: Decimal


@dataclass(frozen=True)
class InvoiceHeader:
    tipo: int
    pto_vta: int
    nro: int
    fecha: date
    cuit_emisor: str
    razon_social: str
    cuit_receptor: str
    orden_compra: str | None       # the order the page cites; it may be missing or another supplier's
    moneda: str
    cotizacion: Decimal
    cae: str
    cae_vto: date


@dataclass(frozen=True)
class InvoiceTotals:
    neto: Decimal                  # as printed: a ``total_mismatch`` prints a net that is not the sum of its lines
    iva_rate: Decimal
    iva: Decimal
    total: Decimal


@dataclass(frozen=True)
class InvoiceTruth:
    invoice_key: str               # ``<split>-<ordinal>``
    split: str
    cohort: str
    supplier_key: str
    layout: str
    font: str
    received_on: date              # the day it reached accounts payable
    po_number: str                 # the order its lines come from (not always the one it cites)
    header: InvoiceHeader
    lines: tuple[InvoiceLine, ...]
    totals: InvoiceTotals
    qr_text: str | None
    qr_cm: float
    scan_profile: str
    file_format: str
    planted: str                   # "none" or a class of PLANTED_CLASSES
    expected_decision: str
    expected_reasons: tuple[str, ...]


@dataclass(frozen=True)
class _Slot:
    ordinal: int
    received_on: date
    planted: str
    po: PurchaseOrder
    cited: str | None = None       # an order of another supplier (supplier_mismatch)
    source: int | None = None      # the ordinal of the invoice a duplicate re-scans


def split_seed(view: SealedConfig, split: str) -> int:
    """The seed of a split: ``seed_train``, ``seed_validation`` (both validations) or ``seed_test`` (both test partitions)."""
    return getattr(view.synth, SEED_FIELD[split])


def _stream(view: SealedConfig, split: str, ordinal: int, aspect: str) -> random.Random:
    return random.Random(f"invoiceflow.invoice:{split_seed(view, split)}:{split}:{ordinal}:{aspect}")


def _weekdays(first: date, last: date) -> list[date]:
    return [day for n in range((last - first).days + 1) if (day := first + timedelta(days=n)).weekday() < 5]


def calendar_days(view: SealedConfig) -> list[date]:
    """The days of the test calendar: every day from the first to the last, only the business days when the calendar says so."""
    first, last = view.calendar.test_first_day, view.calendar.test_last_day
    every = [first + timedelta(days=n) for n in range((last - first).days + 1)]
    return [day for day in every if not view.calendar.weekdays_only or day.weekday() < 5]


def per_day(view: SealedConfig, split: str) -> int:
    return view.synth.test_per_day_known if split == "test_known" else view.synth.test_per_day_new


def count(view: SealedConfig, split: str) -> int:
    """How many invoices a partition has: the size of a fit split, or the days of the test times its per-day amount."""
    synth = view.synth
    if split in TEST_SPLITS:
        return len(calendar_days(view)) * per_day(view, split)
    return {"train": synth.n_train, "validation_known": synth.n_validation_known, "validation_new": synth.n_validation_new}[split]


def _number_offset(view: SealedConfig, split: str) -> int:
    """Where the voucher numbers of a partition start: after the ones of the partitions before it, so no (CUIT, point of sale, type, number) repeats between them."""
    return FIRST_NUMBER + sum(count(view, earlier) for earlier in SPLITS[:SPLITS.index(split)])


# --- the guard of the test ---------------------------------------------------------------------------------------------------


def _require_launch_seal(view: SealedConfig, seal_path: Path) -> None:
    from invoiceflow.registry import config_fingerprint, seal_state

    try:
        state = seal_state(Path(seal_path), config_fingerprint(view))
    except ValueError as error:
        raise GenerationRefusedError(f"no se genera la prueba: {error}") from error
    if state == "none":
        raise GenerationRefusedError("no se genera la prueba: el sello no tiene la parte del lanzamiento (el sello se escribe al terminar el lanzamiento)")
    if state == "post_seal":
        raise GenerationRefusedError("no se genera la prueba: el sello del lanzamiento es de otra configuración (la huella no es la de esta)")


# --- the plan: which order each invoice takes ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class _OrderFacts:
    po: PurchaseOrder
    first_receipt: date
    last_receipt: date
    receipts: int


def _order_facts(master: Master) -> dict[str, _OrderFacts]:
    by_po: dict[str, list[Receipt]] = {}
    for receipt in master.receipts:
        by_po.setdefault(receipt.po_number, []).append(receipt)
    facts = {}
    for po in master.orders:
        dates = sorted(r.receipt_date for r in by_po.get(po.po_number, []))
        if dates:
            facts[po.po_number] = _OrderFacts(po, dates[0], dates[-1], len(dates))
    return facts


def _owner(view: SealedConfig, supplier: Supplier, po: PurchaseOrder) -> str:
    """The partition that may invoice `po`: each order belongs to exactly one."""
    if supplier.cohort == "new_validation":
        return "validation_new"
    if supplier.cohort == "new_test":
        return "test_new"
    if po.po_date >= view.calendar.test_first_day - timedelta(days=TEST_LEAD_DAYS):
        return "test_known"
    synth = view.synth
    share = synth.n_train / (synth.n_train + synth.n_validation_known)
    return "train" if random.Random(f"invoiceflow.owner:{synth.seed_master}:{po.po_number}").random() < share else "validation_known"


def _pool(view: SealedConfig, master: Master, split: str, facts: dict[str, _OrderFacts]) -> list[_OrderFacts]:
    suppliers = {s.supplier_key: s for s in master.suppliers}
    return [facts[po.po_number] for po in master.orders
            if po.po_number in facts and _owner(view, suppliers[po.supplier_key], po) == split]


def _class_of(rng: random.Random, view: SealedConfig) -> str:
    rate = view.synth.planted_rate
    draw = rng.random()
    return PLANTED_CLASSES[int(draw / rate)] if rate > 0 and draw < rate * len(PLANTED_CLASSES) else "none"


def _fits(planted: str, order: _OrderFacts, day: date) -> bool:
    """Whether `order` can carry an invoice of class `planted` that reaches accounts payable on `day`."""
    if planted == "over_receipt":
        return order.receipts == 2 and order.first_receipt <= day < order.last_receipt
    if planted == "currency_mismatch" and order.po.currency != "PES":
        return False
    return order.last_receipt <= day


def _fit_dates(planted: str, order: _OrderFacts, view: SealedConfig) -> list[date]:
    """The business days a training or validation invoice of this class and this order may reach accounts payable on."""
    first = view.calendar.history_start
    last = view.calendar.launch_last_day
    if planted == "over_receipt":
        if order.receipts != 2:
            return []
        return _weekdays(max(first, order.first_receipt), min(last, order.last_receipt - timedelta(days=1)))
    if planted == "currency_mismatch" and order.po.currency != "PES":
        return []
    return _weekdays(max(first, order.last_receipt), last)


def _plan(view: SealedConfig, master: Master, split: str) -> list[_Slot]:
    """Which order, which day and which class each invoice of `split` has, ordinal by ordinal. Deterministic, and independent of every other partition but for the
    split of the orders (``_owner``). A class the world cannot give (a duplicate on the first day, a partly received order when none is left) falls back to a right
    invoice, so a class never comes out above its rate; a right invoice that cannot find an order is an error that says the master is too small."""
    facts = _order_facts(master)
    unused = sorted(_pool(view, master, split, facts), key=lambda order: order.po.po_number)
    daily = split in TEST_SPLITS
    days = calendar_days(view) if daily else []
    everything = sorted(master.orders, key=lambda order: (order.po_date, order.po_number))
    order_dates = [order.po_date for order in everything]
    slots: list[_Slot] = []
    for ordinal in range(count(view, split)):
        rng = _stream(view, split, ordinal, "plan")
        planted = _class_of(rng, view)
        day = days[ordinal // per_day(view, split)] if daily else None
        if planted == "duplicate":
            earlier = [s for s in slots if s.planted == "none" and (not daily or s.received_on < day)]
            if earlier:
                source = rng.choice(earlier)
                received_on = day if daily else rng.choice(_weekdays(source.received_on, view.calendar.launch_last_day))
                slots.append(_Slot(ordinal, received_on, "duplicate", source.po, source=source.ordinal))
                continue
            planted = "none"
        choice = _pick(rng, unused, planted, day, daily, view)
        if choice is None and planted != "none":
            planted = "none"
            choice = _pick(rng, unused, planted, day, daily, view)
        if choice is None:
            raise ValueError(f"{split}: no hay una orden recibida y sin facturar para la factura {ordinal + 1} (el maestro no alcanza: ver synth.master)")
        index, received_on = choice
        order = unused[index]
        unused[index] = unused[-1]
        unused.pop()
        cited = None
        if planted == "supplier_mismatch":
            cited = _other_supplier_order(rng, everything, order_dates, order.po, received_on)
            if cited is None:
                planted = "none"
        slots.append(_Slot(ordinal, received_on, planted, order.po, cited=cited))
    return slots


def _pick(rng: random.Random, unused: list[_OrderFacts], planted: str, day: date | None, daily: bool, view: SealedConfig) -> tuple[int, date] | None:
    """The index of an unused order that can carry an invoice of class `planted`, and the day it reaches accounts payable on; None when there is none."""
    if daily:
        candidates = [index for index, order in enumerate(unused) if _fits(planted, order, day)]
        return (rng.choice(candidates), day) if candidates else None
    drawn = (rng.randrange(len(unused)) for _ in range(min(ATTEMPTS, len(unused))))
    for index in chain(drawn, range(len(unused))):
        dates = _fit_dates(planted, unused[index], view)
        if dates:
            return index, rng.choice(dates)
    return None


def _other_supplier_order(rng: random.Random, everything: list[PurchaseOrder], dates: list[date], own: PurchaseOrder, day: date) -> str | None:
    """An order of another supplier dated on or before `day`."""
    upto = bisect_right(dates, day)
    if upto == 0:
        return None
    for _ in range(ATTEMPTS):
        other = everything[rng.randrange(upto)]
        if other.supplier_key != own.supplier_key:
            return other.po_number
    return None


# --- the invoice of a slot -----------------------------------------------------------------------------------------------------


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _weighted(rng: random.Random, shares: dict[str, float]) -> str:
    return rng.choices(list(shares), weights=list(shares.values()))[0]


def _line(code: str, description: str, qty: Decimal, unit: str, price: Decimal) -> InvoiceLine:
    return InvoiceLine(code, description, qty, unit, price, _money(qty * price))


class _World:
    """What building an invoice reads: the master by key, the slots of the partition and the invoices already built (a duplicate re-scans one of them)."""

    def __init__(self, view: SealedConfig, master: Master, split: str) -> None:
        self.view, self.master, self.split = view, master, split
        self.suppliers = {s.supplier_key: s for s in master.suppliers}
        self.items = {item.code: item for item in master.items}
        self.orders = {po.po_number for po in master.orders}
        self.slots = _plan(view, master, split)
        self.offset = _number_offset(view, split)
        self._built: dict[int, InvoiceTruth] = {}

    def truth(self, ordinal: int) -> InvoiceTruth:
        if ordinal not in self._built:
            self._built[ordinal] = self._build(self.slots[ordinal])
        return self._built[ordinal]

    def _build(self, slot: _Slot) -> InvoiceTruth:
        view, split, synth = self.view, self.split, self.view.synth
        supplier = self.suppliers[slot.po.supplier_key]
        draw = _stream(view, split, slot.ordinal, "content")
        point_of_sale = draw.choice(supplier.points_of_sale)
        cae = str(draw.randint(10**13, 10**14 - 1))
        reduced_iva = draw.random() < synth.iva105_share
        exchange = Decimal(str(round(draw.uniform(850, 1500), 2)))
        qr_cm = draw.choice(synth.qr_cm)
        profile = _weighted(draw, asdict(synth.scan_profiles))
        file_format = _weighted(draw, asdict(synth.file_formats))
        key = f"{split}-{slot.ordinal:05d}"
        if slot.planted == "duplicate":
            return replace(self.truth(slot.source), invoice_key=key, received_on=slot.received_on, scan_profile=profile, file_format=file_format, planted="duplicate",
                           expected_decision="review", expected_reasons=FIRST_REASON["duplicate"])
        planted, po, rng = slot.planted, slot.po, _stream(view, split, slot.ordinal, "class")
        currency = "DOL" if planted == "currency_mismatch" else po.currency
        lines = []
        for order_line in po.lines:
            price = order_line.unit_price
            if planted == "currency_mismatch":
                price = max(_money(price / DOL_DIVISOR), CENT)
            lines.append(_line(order_line.item_code, order_line.description_es, order_line.qty, order_line.unit, price))
        if planted == "price_off":
            at = rng.randrange(len(lines))
            old = lines[at]
            new = _money(old.unit_price * (1 + Decimal(str(round(rng.uniform(0.03, 0.10), 4)))))
            while (new - old.unit_price) / old.unit_price * 100 < 3:
                new += CENT
            lines[at] = _line(old.code, old.description, old.qty, old.unit, new)
        elif planted == "over_order":
            at = rng.randrange(len(lines))
            old = lines[at]
            lines[at] = _line(old.code, old.description, old.qty + rng.randint(1, 10), old.unit, old.unit_price)
        elif planted == "extra_line":
            codes = {line.code for line in lines}
            item = rng.choice([candidate for candidate in self.master.items if candidate.code not in codes])
            price = max(_money(item.list_price / DOL_DIVISOR), CENT) if currency == "DOL" else item.list_price
            lines.append(_line(item.code, item.description_es, Decimal(rng.randint(1, 10)), item.unit, price))
        lines_tuple = tuple(lines)
        neto = sum((line.amount for line in lines_tuple), Decimal(0))
        if planted == "total_mismatch":
            neto += max(_money(neto * Decimal(str(round(rng.uniform(0.02, 0.10), 4)))), CENT * (len(lines_tuple) + 2))
        rate = Decimal("10.5") if reduced_iva else Decimal("21")
        iva = _money(neto * rate / 100)
        totals = InvoiceTotals(neto, rate, iva, neto + iva)
        tipo = TYPE_CREDIT_NOTE_A if planted == "credit_note" else TYPE_INVOICE_A
        fecha = slot.received_on
        if planted == "date_before_po":
            fecha = po.po_date - timedelta(days=rng.randint(1, 10))
        cited = po.po_number
        if planted == "supplier_mismatch":
            cited = slot.cited
        elif planted == "no_po":
            cited = None if rng.random() < 0.5 else f"OC-2026-{NONEXISTENT_PO_BASE + slot.ordinal:06d}"
            assert cited is None or cited not in self.orders
        header = InvoiceHeader(tipo, point_of_sale, self.offset + slot.ordinal, fecha, supplier.cuit, supplier.name_es, self.master.buyer.cuit, cited, currency,
                               Decimal(1) if currency == "PES" else exchange, cae, fecha + timedelta(days=10))
        shown = totals.total
        if planted == "qr_tampered":
            shown = _money(totals.total * (1 + Decimal(str(round(rng.uniform(0.05, 0.5), 4)))))
            if abs(shown - totals.total) <= Decimal("0.05"):
                shown = totals.total + Decimal("0.10")
        qr = None if planted == "qr_missing" else qrpayload.qr_text({
            "ver": 1, "fecha": fecha.isoformat(), "cuit": int(supplier.cuit), "ptoVta": header.pto_vta, "tipoCmp": tipo, "nroCmp": header.nro, "importe": shown,
            "moneda": currency, "ctz": header.cotizacion, "tipoDocRec": RECEIVER_DOCUMENT_TYPE, "nroDocRec": int(header.cuit_receptor), "tipoCodAut": "E",
            "codAut": int(cae)})
        reasons = FIRST_REASON.get(planted, ())
        return InvoiceTruth(key, split, split, supplier.supplier_key, supplier.layout, supplier.font, slot.received_on, po.po_number, header, lines_tuple, totals,
                            qr, qr_cm, profile, file_format, planted, "review" if planted != "none" else "auto_post", reasons)


def invoices(view: SealedConfig, master: Master, split: str, day: date | None = None, *, seal_path: Path) -> list[InvoiceTruth]:
    """The invoices of partition `split`: all of a training or validation split (`day` None), or those of one test day (or all the test days with `day` None).

    A test split is generated only after the launch seal (``GenerationRefusedError``); nothing is written either way. Every CUIT of the master is checked to be synthetic
    before anything is drawn (M07)."""
    if split not in SPLITS:
        raise ValueError(f"la partición {split!r} no existe ({', '.join(SPLITS)})")
    if split in TEST_SPLITS:
        _require_launch_seal(view, seal_path)
        days = calendar_days(view)
        if day is not None and day not in days:
            raise ValueError(f"{day.isoformat()} no es un día de prueba (de {days[0].isoformat()} a {days[-1].isoformat()})")
    elif day is not None:
        raise ValueError(f"la partición {split} no tiene un día: solo las de prueba se generan por día")
    for supplier in master.suppliers:
        require_synthetic_cuit(supplier.cuit, "synth.invoices (proveedor)")
    require_synthetic_cuit(master.buyer.cuit, "synth.invoices (comprador)")
    world = _World(view, master, split)
    ordinals = range(len(world.slots))
    if day is not None:
        each = per_day(view, split)
        first = days.index(day) * each
        ordinals = range(first, first + each)
    return [world.truth(ordinal) for ordinal in ordinals]


__all__ = ["FIRST_REASON", "FIT_SPLITS", "GenerationRefusedError", "InvoiceHeader", "InvoiceLine", "InvoiceTotals", "InvoiceTruth", "PLANTED_CLASSES", "SPLITS", "TEST_SPLITS",
           "count", "invoices", "per_day", "split_seed", "calendar_days"]
