"""The extractors (U09; RF09, RF16; plan section 9.5, decisions D11, D23): the protocol and its registry, the refusal of a stub or an unregistered kind by the real configuration,
the Donut extractor (decoding greedy, the prompt of the invoice task, a sequence turned into a tree) with a fake model that needs no torch and no checkpoint, and the stub
that reads the truth with seeded errors, which exists only in the tests."""

from __future__ import annotations

import contextlib
import dataclasses
import shutil
import types
from pathlib import Path

import pytest
from PIL import Image

from invoiceflow import config as config_module
from invoiceflow.model import extractors
from invoiceflow.model.donut import DonutExtractor, parse_generation, resolve_device
from invoiceflow.model.extractors import ExtractorError, make_extractor
from invoiceflow.model.serialize import invoice_target, json2token
from invoiceflow.normalize import normalize
from invoiceflow.synth.invoices import invoices
from tests.fixtures.stub_extractor import ERROR_KINDS, StubExtractor


@pytest.fixture(scope="module")
def truths(quick_view, quick_master, tmp_path_factory):
    return invoices(quick_view, quick_master, "validation_known", None, seal_path=tmp_path_factory.mktemp("s") / "none.json")


def with_kind(view, kind):
    return dataclasses.replace(view, model=dataclasses.replace(view.model, kind=kind))


@pytest.fixture(autouse=True)
def clean_registry():
    yield
    for kind in ("stub", "other", "tiny"):
        extractors.unregister(kind)


def test_the_real_config_refuses_an_unregistered_or_stub_extractor(cfg, view, tmp_path):
    assert cfg.model.kind == "donut" and "donut" in extractors.registered()
    for kind in ("stub", "other", "tiny"):
        with pytest.raises(ExtractorError, match=kind):
            make_extractor(with_kind(view, kind), {}, "models")
    folder = tmp_path / "config"
    folder.mkdir()
    shutil.copy(config_module.DEFAULT_CONFIG, folder / "pipeline.toml")
    (folder / "stub.toml").write_text('extends = "pipeline.toml"\n\n[model]\nkind = "stub"\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"\[model\] kind.*stub"):
        config_module.load_config(folder / "stub.toml")
    extractors.register("stub", lambda *args: None)
    assert config_module.load_config(folder / "stub.toml").model.kind == "stub", "a test that registers a stub is the only way in"
    extractors.unregister("stub")
    with pytest.raises(ValueError, match=r"\[model\] kind.*stub"):
        config_module.load_config(folder / "stub.toml")


def test_a_test_may_register_a_stub_and_the_configuration_then_accepts_its_kind(view):
    extractors.register("stub", lambda v, row, models: StubExtractor({}))
    assert "stub" in config_module._registered_kinds
    made = make_extractor(with_kind(view, "stub"), {}, "models")
    assert isinstance(made, StubExtractor) and made.model_version == "stub-1"
    extractors.unregister("stub")
    assert "stub" not in config_module._registered_kinds
    with pytest.raises(ExtractorError, match="stub"):
        make_extractor(with_kind(view, "stub"), {}, "models")


def test_only_the_tests_and_the_tiny_model_may_register_an_extractor():
    forged = types.ModuleType("invoiceflow.daily")
    exec("def go(register):\n    register('stub', lambda *a: None)\n", forged.__dict__)
    with pytest.raises(ExtractorError, match="invoiceflow.daily"):
        forged.go(extractors.register)
    wrong_tiny = types.ModuleType("invoiceflow.daily")
    exec("def go(register):\n    register('tiny', lambda *a: None)\n", wrong_tiny.__dict__)
    with pytest.raises(ExtractorError, match="tiny"):
        wrong_tiny.go(extractors.register)
    assert "tiny" not in extractors.registered()
    tiny = types.ModuleType("invoiceflow.model.tiny")
    exec("def go(register):\n    register('tiny', lambda *a: None)\n", tiny.__dict__)
    tiny.go(extractors.register)
    assert "tiny" in extractors.registered()
    other = types.ModuleType("invoiceflow.model.tiny")
    exec("def go(register):\n    register('other', lambda *a: None)\n", other.__dict__)
    with pytest.raises(ExtractorError, match="other"):
        other.go(extractors.register)
    with pytest.raises(ExtractorError, match="donut"):
        extractors.register("donut", lambda *a: None)


def test_donut_is_built_in_and_built_only_when_it_is_asked_for(view, monkeypatch):
    assert "donut" in extractors.registered()
    calls = []
    monkeypatch.setitem(extractors._FACTORIES, "donut", lambda *args: calls.append(args) or "built")
    assert calls == []
    assert make_extractor(view, {"model_version": "m"}, "models") == "built" and calls == [(view, {"model_version": "m"}, Path("models"))], "the folder goes in as a path"


# --- the Donut extractor with a fake model -----------------------------------------------------------------------------------------


class Tensor:
    def __init__(self, label):
        self.label = label

    def to(self, device):
        return Tensor(f"{self.label}@{device}")


class FakeTokenizer:
    pad_token_id, eos_token_id, unk_token_id, pad_token, eos_token = 1, 2, 3, "<pad>", "</s>"

    def __init__(self):
        self.calls = []

    def __call__(self, text, add_special_tokens, return_tensors):
        self.calls.append((text, add_special_tokens, return_tensors))
        return types.SimpleNamespace(input_ids=Tensor("prompt"))

    def batch_decode(self, sequences):
        return [self.sequence]


class FakeProcessor:
    def __init__(self):
        self.tokenizer = FakeTokenizer()
        self.seen = []

    def __call__(self, image, return_tensors):
        self.seen.append((image.mode, image.size, return_tensors))
        return types.SimpleNamespace(pixel_values=Tensor("pixels"))


class FakeModel:
    def __init__(self):
        self.kwargs = None

    def generate(self, pixel_values, **kwargs):
        self.kwargs = {"pixel_values": pixel_values, **kwargs}
        return types.SimpleNamespace(sequences=[object()])


def donut(**changes):
    processor, model = FakeProcessor(), FakeModel()
    base = dict(model=model, processor=processor, task_token="<s_factura_ar>", max_length=768, device="cpu", model_version="m1", no_grad=contextlib.nullcontext)
    extractor = DonutExtractor(**{**base, **changes})
    return extractor, processor, model


def test_the_donut_extractor_decodes_greedily_from_the_prompt_of_the_invoice_task(truths):
    extractor, processor, model = donut()
    processor.tokenizer.sequence = "<s_factura_ar>" + json2token(invoice_target(truths[0])) + "</s><pad><pad>"
    tree = extractor.extract(Image.new("L", (20, 30)))
    assert normalize(tree).lines[0].codigo == truths[0].lines[0].code and tree["header"]["nro"] == f"{truths[0].header.nro:08d}"
    assert processor.seen == [("RGB", (20, 30), "pt")], "the page goes in as RGB"
    assert processor.tokenizer.calls == [("<s_factura_ar>", False, "pt")]
    k = model.kwargs
    assert (k["pixel_values"].label, k["decoder_input_ids"].label) == ("pixels@cpu", "prompt@cpu")
    assert (k["max_length"], k["num_beams"], k["pad_token_id"], k["eos_token_id"], k["use_cache"], k["bad_words_ids"]) == (768, 1, 1, 2, True, [[3]])
    assert extractor.model_version == "m1"
    other, processor2, model2 = donut(max_length=300, device="cuda", task_token="<s_cord-v2>")
    processor2.tokenizer.sequence = ""
    other.extract(Image.new("RGB", (4, 4)))
    assert model2.kwargs["max_length"] == 300 and model2.kwargs["pixel_values"].label == "pixels@cuda" and processor2.tokenizer.calls[0][0] == "<s_cord-v2>"


def test_a_sequence_is_cut_of_its_task_prompt_and_its_end_and_padding_tokens(truths):
    sequence = json2token(invoice_target(truths[1]))
    for wrapped in ("<s_factura_ar>" + sequence + "</s>", "<s_factura_ar>" + sequence + "</s><pad><pad><pad>", sequence):
        assert parse_generation(wrapped, "<s_factura_ar>", "</s>", "<pad>") == parse_generation(sequence, "<s_factura_ar>", "</s>", "<pad>") and parse_generation(wrapped, "<s_factura_ar>", "</s>", "<pad>")["header"]
    assert parse_generation("<s_factura_ar>", "<s_factura_ar>", "</s>", "<pad>") == {}
    assert parse_generation("<s_factura_ar>no tokens</s>", "<s_factura_ar>", "</s>", "<pad>") == {}, "an output that cannot be read is empty fields"
    assert parse_generation("<s_factura_ar><s_a>1</s_a><sep/><s_a>2</s_a>", "<s_factura_ar>", None, None) == {"a": "1"}, "a list at the top is its first tree"
    assert parse_generation("x", "<s_factura_ar>", None, None) == {}
    assert normalize(parse_generation("<s_factura_ar>garbage", "<s_factura_ar>", "</s>", "<pad>")).complete is False


def test_the_device_is_auto_cuda_or_cpu_and_cuda_without_cuda_is_refused():
    assert resolve_device("auto", True) == "cuda" and resolve_device("auto", False) == "cpu" and resolve_device("cpu", True) == "cpu" and resolve_device("cuda", True) == "cuda"
    with pytest.raises(ExtractorError, match="CUDA"):
        resolve_device("cuda", False)
    with pytest.raises(ValueError, match="gpu"):
        resolve_device("gpu", True)


def test_the_donut_module_imports_no_torch_until_it_runs(project_root):
    import subprocess
    import sys

    code = (f"import sys; sys.path.insert(0, {str(project_root / 'src')!r}); import invoiceflow.model.donut, invoiceflow.model.extractors, invoiceflow.model.serialize; "
            "sys.exit(1 if 'torch' in sys.modules else 0)")
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# --- the stub of the tests -----------------------------------------------------------------------------------------------------------


def test_the_stub_reads_the_truth_by_the_page_and_seeds_the_errors_it_is_told_to(quick_view, truths, tmp_path):
    errors = {truths[0].invoice_key: "quantity", truths[1].invoice_key: "price", truths[2].invoice_key: "total", truths[3].invoice_key: "empty_field"}
    stub = StubExtractor.from_truths(truths[:5], quick_view, tmp_path, errors=errors)
    assert set(ERROR_KINDS) == {"quantity", "price", "total", "empty_field"} and stub.model_version == "stub-1"
    from invoiceflow.intake import load_page

    for truth in truths[:5]:
        page = load_page(next(tmp_path.glob(f"{truth.invoice_key}.*")), quick_view)
        fields = normalize(stub.extract(page))
        exact = normalize(invoice_target(truth))
        kind = errors.get(truth.invoice_key)
        if kind is None:
            assert fields == exact
        elif kind == "quantity":
            assert fields.lines[0].cantidad != exact.lines[0].cantidad and fields.lines[1:] == exact.lines[1:]
        elif kind == "price":
            assert fields.lines[0].precio_unitario != exact.lines[0].precio_unitario and fields.lines[0].importe == exact.lines[0].importe
        elif kind == "total":
            assert fields.total != exact.total and fields.neto == exact.neto
        else:
            assert fields.lines[0].importe is None and "lines[0].importe" in fields.missing
    assert normalize(stub.extract(Image.new("RGB", (9, 9)))).complete is False, "a page it does not know is read as nothing"


class BatchTensor:
    """What the fakes pass for a batch: its label, its rows and where it went."""

    def __init__(self, label, rows=1):
        self.label, self.rows = label, rows

    def to(self, device):
        return BatchTensor(f"{self.label}@{device}", self.rows)

    def repeat(self, rows, columns):
        assert columns == 1
        return BatchTensor(self.label, self.rows * rows)


class BatchTokenizer(FakeTokenizer):
    def __call__(self, text, add_special_tokens, return_tensors):
        self.calls.append((text, add_special_tokens, return_tensors))
        return types.SimpleNamespace(input_ids=BatchTensor("prompt"))

    def batch_decode(self, sequences):
        return list(sequences)


class BatchProcessor(FakeProcessor):
    def __init__(self):
        super().__init__()
        self.tokenizer = BatchTokenizer()

    def __call__(self, images, return_tensors):
        images = images if isinstance(images, list) else [images]
        self.seen.append([(image.mode, image.size) for image in images])
        return types.SimpleNamespace(pixel_values=BatchTensor("pixels", len(images)))


class BatchModel:
    """Answers each page by its width, so the order of the trees can be checked; a shorter sequence is padded after its end, as a batch is."""

    def __init__(self, processor):
        self.processor, self.calls = processor, []

    def generate(self, pixel_values, **kwargs):
        self.calls.append({"pixel_values": pixel_values, **kwargs})
        widths = [size[0] for _, size in self.processor.seen[-1]]
        return types.SimpleNamespace(sequences=[f"<s_cord-v2><s_nm>{w}</s_nm></s>" + "<pad>" * (5 - len(str(w))) for w in widths])


def test_many_pages_are_read_in_batches_with_the_generation_of_one_page_and_in_their_order():
    """D61 (B4 of the evaluation): the checkpoint's pass of B2 reads the pages a batch at a time; each batch has one prompt per page and the generation of ``extract``, and the trees come back in
    the order of the pages, with the padding after a shorter sequence cut as ``extract`` cuts it."""
    processor = BatchProcessor()
    model = BatchModel(processor)
    extractor = DonutExtractor(model=model, processor=processor, task_token="<s_cord-v2>", max_length=768, device="cuda", model_version="checkpoint", no_grad=contextlib.nullcontext)
    pages = [Image.new("L", (w, 30)) for w in (11, 222, 33, 4, 55)]
    trees = extractor.extract_many(pages, batch_size=2)
    assert trees == [{"nm": "11"}, {"nm": "222"}, {"nm": "33"}, {"nm": "4"}, {"nm": "55"}]
    assert [len(batch) for batch in processor.seen] == [2, 2, 1] and all(mode == "RGB" for batch in processor.seen for mode, _ in batch)
    assert [(c["pixel_values"].rows, c["decoder_input_ids"].rows, c["decoder_input_ids"].label) for c in model.calls] == [(2, 2, "prompt@cuda"), (2, 2, "prompt@cuda"), (1, 1, "prompt@cuda")]
    one = {k: v for k, v in model.calls[0].items() if k not in ("pixel_values", "decoder_input_ids")}
    assert one == {"max_length": 768, "early_stopping": True, "pad_token_id": 1, "eos_token_id": 2, "use_cache": True, "num_beams": 1, "bad_words_ids": [[3]], "return_dict_in_generate": True}
    single, single_processor, single_model = donut(task_token="<s_cord-v2>")
    single_processor.tokenizer.sequence = "<s_cord-v2><s_nm>11</s_nm></s>"
    assert single.extract(pages[0]) == trees[0]
    assert {k: v for k, v in single_model.kwargs.items() if k not in ("pixel_values", "decoder_input_ids")} == one, "the same generation as one page"
    assert extractor.extract_many([], batch_size=2) == []
    with pytest.raises(ValueError, match="batch_size"):
        extractor.extract_many(pages, batch_size=0)
