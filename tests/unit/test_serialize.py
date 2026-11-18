"""The sequences the document model is trained on and decodes (U09; AC17 in part, RF09, RF16; plan section 9.1, decisions D09, D45): Donut's own ``json2token`` and a
``token2json`` of this project that is its inverse on every target of the fixtures, the target of the invoice task, the new special tokens and the length of a block (M05)."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest

from invoiceflow import cord
from invoiceflow.model.serialize import (
    SEP,
    BlockSizeError,
    check_block_sizes,
    invoice_target,
    json2token,
    new_special_tokens,
    token2json,
)
from invoiceflow.normalize import normalize
from invoiceflow.synth import printed
from invoiceflow.synth.invoices import invoices
from tests.fixtures.projects import make_project


@pytest.fixture(scope="module")
def invoice_truths(quick_view, quick_master, tmp_path_factory):
    seal = tmp_path_factory.mktemp("s") / "none.json"
    return [t for split in ("train", "validation_known", "validation_new") for t in invoices(quick_view, quick_master, split, None, seal_path=seal)]


@pytest.fixture(scope="module")
def cord_targets(tmp_path_factory):
    project = make_project(tmp_path_factory.mktemp("cord"))
    return [receipt.gt_parse for split in ("train", "validation") for receipt in cord.read_split(project.cfg, split)]


def test_json2token_and_token2json_are_inverse_on_every_fixture_target(invoice_truths, cord_targets):
    targets = [invoice_target(t) for t in invoice_truths] + cord_targets
    assert len(targets) == 36 + 20 and any(len(t["lines"]) == 1 for t in targets if "lines" in t) and any(isinstance(t.get("total", {}).get("total_price"), list) for t in cord_targets)
    for target in targets:
        sequence = json2token(target)
        assert token2json(sequence) == target, sequence[:200]
        assert token2json(json2token(target, sort_keys=False)) == target


def test_json2token_is_donuts_the_keys_in_reverse_order_and_the_lists_joined_by_sep():
    assert json2token({"a": "x", "b": "y"}) == "<s_b>y</s_b><s_a>x</s_a>"
    assert json2token({"a": "x", "b": "y"}, sort_keys=False) == "<s_a>x</s_a><s_b>y</s_b>"
    assert json2token({"menu": [{"nm": "A", "cnt": "1"}, {"nm": "B", "cnt": "2"}]}) == "<s_menu><s_nm>A</s_nm><s_cnt>1</s_cnt><sep/><s_nm>B</s_nm><s_cnt>2</s_cnt></s_menu>"
    assert json2token({"total_price": ["1", "2"]}) == "<s_total_price>1<sep/>2</s_total_price>"
    assert json2token({"n": 5, "ok": True, "none": None}) == "<s_ok>True</s_ok><s_none>None</s_none><s_n>5</s_n>", "a scalar is its text"
    assert json2token({"k": ""}) == "<s_k></s_k>" and json2token({"lines": []}) == "<s_lines></s_lines>" and SEP == "<sep/>"
    assert json2token("plain") == "plain" and json2token([]) == ""


def test_token2json_forces_a_list_for_the_keys_that_are_always_lists():
    assert token2json("<s_lines><s_importe>1</s_importe></s_lines>") == {"lines": [{"importe": "1"}]}
    assert token2json("<s_lines></s_lines>") == {"lines": []}
    assert token2json("<s_menu><s_nm>A</s_nm></s_menu>") == {"menu": [{"nm": "A"}]}
    assert token2json("<s_other><s_x>1</s_x></s_other>") == {"other": {"x": "1"}}, "a key that is not in the list keeps the shape it has"
    assert token2json("<s_lines><s_importe>1</s_importe></s_lines>", list_keys=()) == {"lines": {"importe": "1"}}
    assert token2json("<s_total_price>1<sep/>2</s_total_price>") == {"total_price": ["1", "2"]}


@pytest.mark.parametrize("garbage", ["", "no tokens at all", "<s_a>never closed", "</s_a> closing first", "<s_a><s_b>1</s_a></s_b>", "<sep/><sep/>", "<s_>x</s_>", "<s_a></s_b>", "\n\n  ",
                                     "<s_a>" * 50, "<s_lines>" + "<s_x>1</s_x><sep/>" * 30])
def test_a_sequence_that_is_not_a_target_never_raises_and_gives_something_empty_or_partial(garbage):
    result = token2json(garbage)
    assert isinstance(result, (dict, list))


def test_what_the_model_leaves_unfinished_is_read_up_to_what_closed():
    unfinished = token2json("<s_header><s_nro>00001</s_nro></s_header><s_lines><s_cantidad>3</s_cantidad>")
    assert unfinished["header"] == {"nro": "00001"} and "lines" not in unfinished, "a block that never closed is not a list of lines (what closed inside it is left at the top, where nothing reads it)"
    assert normalize(unfinished).lines == () and "lines" in normalize(unfinished).missing
    assert token2json("junk <s_a>1</s_a> more junk") == {"a": "1"}


def test_the_invoice_target_is_the_page_as_printed(invoice_truths):
    truth = next(t for t in invoice_truths if t.planted == "none" and t.totals.iva_rate == Decimal("21") and t.header.orden_compra)
    target = invoice_target(truth)
    h, t = truth.header, truth.totals
    assert list(target) == ["header", "lines", "totals"]
    assert target["header"] == {"tipo": "01", "pto_vta": printed.pto_vta(h.pto_vta), "nro": printed.nro(h.nro), "fecha": printed.date_es(h.fecha), "cuit_emisor": printed.cuit(h.cuit_emisor),
                                "razon_social": h.razon_social, "cuit_receptor": printed.cuit(h.cuit_receptor), "orden_compra": h.orden_compra, "moneda": h.moneda, "cae": h.cae}
    assert target["lines"] == [{"codigo": l.code, "descripcion": l.description, "cantidad": printed.quantity(l.qty), "unidad": l.unit, "precio_unitario": printed.amount(l.unit_price),
                                "importe": printed.amount(l.amount)} for l in truth.lines]
    assert target["totals"] == {"neto": printed.amount(t.neto), "iva_21": printed.amount(t.iva), "iva_105": "", "total": printed.amount(t.total)}


def test_the_reduced_iva_goes_to_its_own_field_and_a_missing_order_is_empty(invoice_truths):
    reduced = next(t for t in invoice_truths if t.totals.iva_rate == Decimal("10.5"))
    assert invoice_target(reduced)["totals"]["iva_21"] == "" and invoice_target(reduced)["totals"]["iva_105"] == printed.amount(reduced.totals.iva)
    no_order = dataclasses.replace(invoice_truths[0], header=dataclasses.replace(invoice_truths[0].header, orden_compra=None))
    assert invoice_target(no_order)["header"]["orden_compra"] == ""
    credit = dataclasses.replace(invoice_truths[0], header=dataclasses.replace(invoice_truths[0].header, tipo=3))
    assert invoice_target(credit)["header"]["tipo"] == "03"


def test_what_the_model_is_asked_to_write_is_what_the_normalizer_reads_back_exactly(invoice_truths):
    """The whole chain of U06 to U09: the page's truth, its target, the sequence, the tree decoded from it and the typed fields equal the truth."""
    for truth in invoice_truths:
        fields = normalize(token2json(json2token(invoice_target(truth))))
        h, t = truth.header, truth.totals
        assert (fields.tipo, fields.pto_vta, fields.nro, fields.fecha, fields.cuit_emisor, fields.cuit_receptor) == (h.tipo, h.pto_vta, h.nro, h.fecha, h.cuit_emisor, h.cuit_receptor)
        assert (fields.razon_social, fields.moneda, fields.cae) == (h.razon_social, h.moneda, h.cae) and fields.orden_compra == h.orden_compra
        assert [(l.codigo, l.descripcion, l.cantidad, l.unidad, l.precio_unitario, l.importe) for l in fields.lines] == [
            (l.code, l.description, l.qty, l.unit, l.unit_price, l.amount) for l in truth.lines]
        assert (fields.neto, fields.iva, fields.iva_rate, fields.total) == (t.neto, t.iva, t.iva_rate, t.total) and fields.complete


def test_the_new_special_tokens_are_the_open_and_close_of_each_key_the_tokenizer_lacks(invoice_truths):
    targets = [invoice_target(t) for t in invoice_truths[:5]]
    tokens = new_special_tokens(targets)
    keys = {"header", "lines", "totals", "tipo", "pto_vta", "nro", "fecha", "cuit_emisor", "razon_social", "cuit_receptor", "orden_compra", "moneda", "cae", "codigo", "descripcion",
            "cantidad", "unidad", "precio_unitario", "importe", "neto", "iva_21", "iva_105", "total"}
    assert set(tokens) == {f"<s_{k}>" for k in keys} | {f"</s_{k}>" for k in keys} | {SEP} and len(tokens) == len(set(tokens)) and tokens == sorted(tokens)
    known = ["<s_header>", "</s_header>", SEP, "<s_total>"]
    assert not set(known) & set(new_special_tokens(targets, existing=known)) and "<s_lines>" in new_special_tokens(targets, existing=known)
    assert "<s_factura_ar>" in new_special_tokens(targets, extra=("<s_factura_ar>",)) and new_special_tokens([]) == []
    assert new_special_tokens([{"a": ["x", "y"]}]) == ["</s_a>", "<s_a>", SEP] or set(new_special_tokens([{"a": ["x", "y"]}])) == {"<s_a>", "</s_a>", SEP}
    assert SEP not in new_special_tokens([{"a": "x"}]), "<sep/> is needed only when a list is"


def test_the_partition_sizes_and_the_length_of_every_target_are_held(quick_view):
    from invoiceflow.synth.invoices import count

    sizes = {split: count(quick_view, split) for split in ("train", "validation_known", "validation_new")}
    lengths = {"train-00000": quick_view.model.max_length, "train-00001": 3}
    check_block_sizes(quick_view, sizes, lengths)
    for split in sizes:
        with pytest.raises(BlockSizeError, match=split):
            check_block_sizes(quick_view, {**sizes, split: sizes[split] + 1}, lengths)
    with pytest.raises(BlockSizeError, match="train-00001") as error:
        check_block_sizes(quick_view, sizes, {"train-00000": 1, "train-00001": quick_view.model.max_length + 1})
    assert "train-00000" not in str(error.value) and str(quick_view.model.max_length) in str(error.value)
    with pytest.raises(BlockSizeError, match="otra"):
        check_block_sizes(quick_view, {"otra": 1}, {})
