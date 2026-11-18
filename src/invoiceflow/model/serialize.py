"""The sequences of the document model (RF09, RF16; plan section 9.1; decision D09).

``json2token`` is Donut's own writer (the keys in reverse order, a list joined by ``<sep/>``, a scalar as its text) without a tokenizer; ``token2json`` is this project's reader of what
the model generates. It is the inverse of ``json2token`` on every target of the project (the invoice task's and CORD's canonical ones) when the keys that are always lists are named
(``list_keys``: ``lines``, ``menu``, ``void_menu``), because a list of one element and its element have the same sequence; and it never raises on what a model may write: an
unfinished or unbalanced sequence is read up to what closed, a start token with no end is dropped and a sequence with no structure is ``{}``. The empty dictionary and the empty list
have the same sequence as the empty string; no target of the project holds one, except the empty ``lines`` that ``list_keys`` restores.

``invoice_target`` is the target of the ``<s_factura_ar>`` task: ``header``, ``lines`` and ``totals`` with the values as they are printed (``synth.printed``'s formats, so that the
normalizer is their inverse). ``new_special_tokens`` lists the ``<s_k>`` and ``</s_k>`` of every key the tokenizer lacks, and ``check_block_sizes`` is M05.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from decimal import Decimal
from typing import Any

from invoiceflow.config import SealedConfig
from invoiceflow.synth import printed
from invoiceflow.synth.invoices import SPLITS, count

SEP = "<sep/>"
LIST_KEYS = ("menu", "void_menu", "lines")
_START = re.compile(r"<s_(.*?)>", re.IGNORECASE)


class BlockSizeError(RuntimeError):
    """A partition has other than the configured size or a target does not fit in ``max_length`` tokens (M05). The message names the partition or the invoice."""


def json2token(obj: Any, *, sort_keys: bool = True) -> str:
    """Donut's writer: ``{"a": "x", "b": "y"}`` is ``<s_b>y</s_b><s_a>x</s_a>``, a list's items are joined by ``<sep/>`` and a scalar is ``str(value)``."""
    if isinstance(obj, dict):
        keys = sorted(obj, reverse=True) if sort_keys else list(obj)
        return "".join(f"<s_{key}>{json2token(obj[key], sort_keys=sort_keys)}</s_{key}>" for key in keys)
    if isinstance(obj, list):
        return SEP.join(json2token(item, sort_keys=sort_keys) for item in obj)
    return str(obj)


def _parse(tokens: str, inner: bool) -> dict[str, Any] | list[Any]:
    """Donut's reader (``DonutProcessor.token2json``) without the tokenizer's added vocabulary and without its ``text_sequence`` fallback."""
    output: dict[str, Any] = {}
    while tokens:
        start = _START.search(tokens)
        if start is None:
            break
        key, start_text = start.group(1), start.group()
        end = re.search(rf"</s_{re.escape(key)}>", tokens, re.IGNORECASE)
        if end is None:
            tokens = tokens.replace(start_text, "")
            continue
        end_text = end.group()
        content = re.search(f"{re.escape(start_text)}(.*?){re.escape(end_text)}", tokens, re.IGNORECASE | re.DOTALL)
        if content is not None:
            text = content.group(1).strip()
            if "<s_" in text and "</s_" in text:
                value = _parse(text, True)
                if value:
                    output[key] = value[0] if len(value) == 1 else value
            else:
                leaves = [leaf.strip() for leaf in text.split(SEP)]
                output[key] = leaves[0] if len(leaves) == 1 else leaves
        tokens = tokens[tokens.find(end_text) + len(end_text):].strip()
        if tokens[:len(SEP)] == SEP:
            return [output, *_parse(tokens[len(SEP):], True)]
    if output:
        return [output] if inner else output
    return [] if inner else {}


def _listify(value: Any, list_keys: Collection[str]) -> Any:
    if isinstance(value, dict):
        fixed = {}
        for key, item in value.items():
            item = _listify(item, list_keys)
            if key in list_keys and not isinstance(item, list):
                item = [] if item == "" else [item]
            fixed[key] = item
        return fixed
    if isinstance(value, list):
        return [_listify(item, list_keys) for item in value]
    return value


def token2json(seq: str, *, list_keys: Collection[str] = LIST_KEYS) -> dict[str, Any] | list[Any]:
    """The tree a sequence says (see the module's text); never raises. ``list_keys`` are the keys whose value is always a list."""
    return _listify(_parse(str(seq), False), list_keys)


def invoice_target(truth) -> dict[str, Any]:
    """The target of the invoice task for `truth`: the page's text, as printed."""
    h, t = truth.header, truth.totals
    reduced = t.iva_rate == Decimal("10.5")
    return {
        "header": {"tipo": f"{h.tipo:02d}", "pto_vta": printed.pto_vta(h.pto_vta), "nro": printed.nro(h.nro), "fecha": printed.date_es(h.fecha),
                   "cuit_emisor": printed.cuit(h.cuit_emisor), "razon_social": h.razon_social, "cuit_receptor": printed.cuit(h.cuit_receptor),
                   "orden_compra": h.orden_compra or "", "moneda": h.moneda, "cae": h.cae},
        "lines": [{"codigo": line.code, "descripcion": line.description, "cantidad": printed.quantity(line.qty), "unidad": line.unit,
                   "precio_unitario": printed.amount(line.unit_price), "importe": printed.amount(line.amount)} for line in truth.lines],
        "totals": {"neto": printed.amount(t.neto), "iva_21": "" if reduced else printed.amount(t.iva), "iva_105": printed.amount(t.iva) if reduced else "",
                   "total": printed.amount(t.total)},
    }


def _keys(value: Any, found: set[str], lists: list[bool]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            found.add(key)
            _keys(item, found, lists)
    elif isinstance(value, list):
        lists.append(True)
        for item in value:
            _keys(item, found, lists)


def new_special_tokens(targets: Iterable[Any], existing: Iterable[str] = (), extra: Iterable[str] = ()) -> list[str]:
    """The ``<s_k>`` and ``</s_k>`` of every key of `targets`, ``<sep/>`` when a target has a list, and the `extra` tokens, minus the `existing` ones; sorted, without repeats."""
    keys: set[str] = set()
    lists: list[bool] = []
    for target in targets:
        _keys(target, keys, lists)
    tokens = {f"<s_{key}>" for key in keys} | {f"</s_{key}>" for key in keys} | set(extra)
    if lists:
        tokens.add(SEP)
    return sorted(tokens - set(existing))


def check_block_sizes(view: SealedConfig, sizes: Mapping[str, int], lengths: Mapping[str, int]) -> None:
    """M05: each partition of `sizes` has the size the configuration says and each target of `lengths` (invoice or receipt key -> tokens) is at most ``max_length``."""
    for split, size in sizes.items():
        if split not in SPLITS:
            raise BlockSizeError(f"la partición {split!r} no es otra de las del plan ({', '.join(SPLITS)})")
        if size != count(view, split):
            raise BlockSizeError(f"la partición {split} tiene {size} facturas y la configuración dice {count(view, split)}")
    for key, length in lengths.items():
        if length > view.model.max_length:
            raise BlockSizeError(f"el objetivo de {key} tiene {length} fichas y max_length es {view.model.max_length}")
