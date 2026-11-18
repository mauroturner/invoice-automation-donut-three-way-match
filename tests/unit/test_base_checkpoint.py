"""What the fine-tune assumes about the pinned base checkpoint (U10; plan section 9.2, decisions D09 and D13; marks ``model``): its configuration and tokenizer, read from the local
Hugging Face cache offline. Only the configuration, the tokenizer and the processor are read; no weight is (the fine-tune of the real model is the launch's, U14, and the real run's, U22)."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.model


@pytest.fixture(scope="module")
def pinned(view):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer, DonutProcessor, VisionEncoderDecoderConfig

    common = {"revision": view.model.base_revision, "local_files_only": True}
    folder = snapshot_download(view.model.base, allow_patterns=["*.json"], **common)
    return {
        "files": sorted(os.listdir(folder)),
        "config": VisionEncoderDecoderConfig.from_pretrained(view.model.base, **common),
        "tokenizer": AutoTokenizer.from_pretrained(view.model.base, **common),
        "processor": DonutProcessor.from_pretrained(view.model.base, **common),
    }


def test_the_decoder_has_exactly_one_row_per_token_so_the_new_tokens_always_grow_it(pinned):
    """57,580 tokens and 57,580 rows: ``prepare_tokens`` resizes (it only grows a decoder that is too small), and the 768 positions are ``max_length``."""
    config, tokenizer = pinned["config"], pinned["tokenizer"]
    assert len(tokenizer) == config.decoder.vocab_size == 57580
    assert config.decoder.max_position_embeddings == 768


def test_the_input_size_of_the_configuration_is_the_one_the_checkpoint_was_trained_with(pinned, view):
    assert list(pinned["config"].encoder.image_size) == list(view.model.input_size) == [1280, 960]
    assert view.model.max_length <= pinned["config"].decoder.max_position_embeddings


def test_the_cord_task_token_is_in_the_vocabulary_and_the_invoice_task_is_not_yet(pinned, view):
    vocab = pinned["tokenizer"].get_vocab()
    assert view.model.task_cord in vocab and view.model.task_invoice not in vocab
    assert pinned["tokenizer"].eos_token == "</s>" and pinned["tokenizer"].pad_token == "<pad>"


def test_the_real_targets_add_a_few_dozen_tokens_to_the_real_tokenizer(pinned, view, default_master, tmp_path):
    from invoiceflow.model.serialize import invoice_target, new_special_tokens
    from invoiceflow.synth.invoices import invoices

    truths = invoices(view, default_master, "validation_known", None, seal_path=tmp_path / "none.json")[:20]
    tokens = new_special_tokens([invoice_target(t) for t in truths], existing=pinned["tokenizer"].get_vocab(), extra=(view.model.task_cord, view.model.task_invoice))
    assert {view.model.task_invoice, "<s_header>", "</s_lines>"} <= set(tokens)
    assert 20 < len(tokens) < 100, f"{len(tokens)} new tokens"


def test_the_processor_reads_its_size_as_width_then_height_and_the_loader_sets_it_from_the_configuration(pinned, view):
    size = pinned["processor"].image_processor.size
    assert (size["height"], size["width"]) == tuple(view.model.input_size)


def test_what_the_pinned_revision_ships(pinned, view):
    """The fact decision D47 rests on: at the pinned revision the weights are a ``pytorch_model.bin`` (``load_base`` reads it once with ``weights_only=True``) and there is no
    ``model.safetensors`` (the safetensors copy in the cache is another revision, a conversion pull request). Neither file is opened: only the cache is asked where they are."""
    from huggingface_hub import try_to_load_from_cache

    revision = view.model.base_revision
    binary = try_to_load_from_cache(view.model.base, "pytorch_model.bin", revision=revision)
    safe = try_to_load_from_cache(view.model.base, "model.safetensors", revision=revision)
    print("\njson files of the pinned revision:", pinned["files"], "| pytorch_model.bin cached:", bool(binary), "| model.safetensors cached at the pinned revision:", isinstance(safe, str))
    assert isinstance(binary, str), "load_base needs the .bin of the pinned revision in the cache"
    assert not isinstance(safe, str), "the pinned revision shipped a model.safetensors after all: D47 and load_base's fallback can be simplified"
