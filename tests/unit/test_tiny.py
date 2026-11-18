"""The tiny model (U10; AC17; plan section 9.2, decisions D11 and D36): a ``VisionEncoderDecoderModel`` with a two-stage Swin encoder and a one-layer MBart decoder, a WordLevel tokenizer
built in memory, no download and no checkpoint, so that the default suite and the quick mode run the code of the fine-tune on the CPU. It is also an extractor of its own kind, ``tiny``,
which only ``model/tiny.py`` may register."""

from __future__ import annotations

import socket

import pytest
from PIL import Image

from invoiceflow.model import extractors
from invoiceflow.model.artifact import save_artifact
from invoiceflow.model.extractors import ExtractorError, make_extractor
from invoiceflow.model.tiny import register_extractor, tiny_model, vocab_of


@pytest.fixture(autouse=True)
def clean_registry():
    yield
    extractors.unregister("tiny")


def test_the_vocabulary_of_a_set_of_sequences_is_its_words_without_the_tags():
    sequences = ["<s_factura_ar><s_b>Hola mundo</s_b><s_a>1.234,56</s_a><sep/><s_a>Hola</s_a>", "<s_cord-v2><s_menu><s_nm>pan</s_nm></s_menu>"]
    assert vocab_of(sequences) == ["1.234,56", "Hola", "mundo", "pan"]
    assert vocab_of([]) == []


def test_the_tiny_model_is_built_with_no_network_and_is_tiny(quick_view, monkeypatch):
    """transformers 5.22.1 builds the DonutSwin and MBart configs and the WordLevel tokenizer from nothing: any attempt to open a socket would fail this test."""
    def no_network(*args, **kwargs):
        raise AssertionError("the tiny model opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    processor, model = tiny_model(["uno", "dos"], quick_view)
    parameters = sum(p.numel() for p in model.parameters())
    assert 10_000 < parameters < 500_000, f"{parameters} parameters is not a tiny model"
    assert model.config.encoder.model_type == "donut-swin" and model.config.encoder.embed_dim == 16 and model.config.encoder.window_size == 4 and len(model.config.encoder.depths) == 2
    assert model.config.decoder.model_type == "mbart" and model.config.decoder.d_model == 32 and model.config.decoder.decoder_layers == 1
    assert list(model.config.encoder.image_size) == list(quick_view.model.input_size)
    assert model.config.decoder.max_position_embeddings == quick_view.model.max_length
    assert model.decoder.get_input_embeddings().weight.shape[0] == len(processor.tokenizer)


def test_the_tiny_tokenizer_knows_the_special_tokens_and_the_words_and_maps_the_rest_to_unknown(quick_view):
    processor, _ = tiny_model(["uno", "dos"], quick_view)
    tokenizer = processor.tokenizer
    for special in ("<pad>", "<s>", "</s>", "<unk>"):
        assert special in tokenizer.get_vocab()
    assert tokenizer.pad_token == "<pad>" and tokenizer.eos_token == "</s>" and tokenizer.unk_token == "<unk>"
    ids = tokenizer("uno dos tres", add_special_tokens=False).input_ids
    assert ids[:2] == [tokenizer.convert_tokens_to_ids("uno"), tokenizer.convert_tokens_to_ids("dos")]
    assert ids[2] == tokenizer.unk_token_id


def test_the_same_seed_builds_the_same_weights_and_another_seed_builds_other_ones(quick_view):
    import dataclasses

    import torch

    first = tiny_model(["uno"], quick_view)[1].state_dict()
    again = tiny_model(["uno"], quick_view)[1].state_dict()
    other_view = dataclasses.replace(quick_view, model=dataclasses.replace(quick_view.model, seed=7))
    other = tiny_model(["uno"], other_view)[1].state_dict()
    assert all(torch.equal(first[k], again[k]) for k in first)
    assert any(not torch.equal(first[k], other[k]) for k in first)


def test_building_the_tiny_model_does_not_move_the_global_random_state(quick_view):
    import torch

    torch.manual_seed(123)
    expected = torch.rand(3)
    torch.manual_seed(123)
    tiny_model(["uno"], quick_view)
    assert torch.equal(torch.rand(3), expected)


def test_the_tiny_extractor_is_registered_by_its_module_only_and_reads_a_saved_artifact(quick_view, tiny_kit, tmp_path):
    assert "tiny" not in extractors.registered(), "importing the module registers nothing"
    register_extractor()
    assert "tiny" in extractors.registered()
    record = save_artifact(tiny_kit.model, tiny_kit.processor, tmp_path / "tiny-1")
    row = {"artifact_name": "tiny-1", "artifact_sha256": record.sha256, "model_version": "tiny-1"}
    extractor = make_extractor(quick_view, row, tmp_path)
    assert extractor.model_version == "tiny-1" and extractor.device == "cpu", "the tiny model reads on the CPU whatever infer_device says"
    tree = extractor.extract(Image.new("RGB", (413, 585), "white"))
    assert isinstance(tree, dict), "an untrained tiny model writes something unreadable: the tree is a dict, empty at worst"


def test_the_tiny_extractor_refuses_an_artifact_whose_hash_is_not_the_registered_one(quick_view, tiny_kit, tmp_path):
    from invoiceflow.model.artifact import ArtifactError

    register_extractor()
    save_artifact(tiny_kit.model, tiny_kit.processor, tmp_path / "tiny-1")
    with pytest.raises(ArtifactError):
        make_extractor(quick_view, {"artifact_name": "tiny-1", "artifact_sha256": "0" * 64, "model_version": "tiny-1"}, tmp_path)


def test_register_is_idempotent_and_a_tiny_kind_nobody_registered_is_refused(quick_view):
    register_extractor()
    register_extractor()
    assert extractors.registered().count("tiny") == 1
    extractors.unregister("tiny")
    with pytest.raises(ExtractorError, match="tiny"):
        make_extractor(quick_view, {}, "models")
