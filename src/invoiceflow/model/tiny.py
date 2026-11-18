"""The tiny model (AC17, RNF04; plan section 9.2; decisions D11, D36): the code of the fine-tune and of the reading, run for real on the CPU in the default suite and in the quick mode.

``tiny_model(vocab, view)`` builds, in memory and with no download, a ``VisionEncoderDecoderModel`` with a two-stage ``DonutSwin`` encoder (``embed_dim`` 16, window 4) and a one-layer
``MBart`` decoder (``d_model`` 32), a ``WordLevel`` tokenizer made with ``tokenizers`` from `vocab` (a word is what is between two blanks; a word it does not know is ``<unk>``) and a
``DonutProcessor`` that resizes a page to ``[model] input_size``. It is seeded with ``[model] seed`` and leaves the global random state as it found it. The tags of the targets are not in
the vocabulary: ``train.prepare_tokens`` adds them as special tokens, as it does to the real tokenizer.

``register_extractor()`` registers the extractor ``tiny`` (only this module may; ``extractors.register`` checks who asks): the quick mode and the tests read a page with the very same
loader as the real model (``donut.load_donut``, which checks the artifact's hashes first), on the CPU.

A tiny model fitted for a few steps never emits the end token, so every page it reads would run to ``max_length`` (256 tokens in the quick mode), and that decoding was most of the time
of a quick launch (D31, the speed unit). It is a double of the real model, so its reading length is a property of the double, like its sizes: ``read_length(view)`` is at most
``READ_LENGTH`` tokens, the prompt included, and never more than ``[model] max_length``; ``donut.load_donut`` reads a tiny model with it and a real one with ``max_length``. What the tiny
model writes has no tag in it, so a shorter read is the same tree, ``{}``.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from invoiceflow.config import SealedConfig
from invoiceflow.model import extractors

SPECIALS = ("<pad>", "<s>", "</s>", "<unk>")
EMBED_DIM, WINDOW, DEPTHS, HEADS = 16, 4, (1, 1), (1, 2)
D_MODEL, DECODER_LAYERS, DECODER_HEADS, DECODER_FFN = 32, 1, 2, 64
_TAG = re.compile(r"</?s_[^>\s]*>|<sep/>")
READ_LENGTH = 16          # the most tokens the tiny model decodes for one page, the prompt included (D31, the speed unit)


def vocab_of(sequences: Iterable[str]) -> list[str]:
    """The words of `sequences` outside their tags (``<s_key>``, ``</s_key>``, ``<sep/>``), sorted and without repeats: what the tiny tokenizer must know."""
    words: set[str] = set()
    for sequence in sequences:
        words.update(_TAG.sub(" ", sequence).split())
    return sorted(words)


def read_length(view: SealedConfig) -> int:
    """The decoding limit of a tiny model under `view`: ``READ_LENGTH``, or ``[model] max_length`` when that is shorter."""
    return min(READ_LENGTH, view.model.max_length)


def tiny_model(vocab: Iterable[str], view: SealedConfig) -> tuple[Any, Any]:
    """``(processor, model)``: the tiny Donut for `view` (``input_size`` and ``max_length`` come from ``[model]``) with `vocab` as its words. Nothing is read from disk or the network."""
    import torch
    from tokenizers import Tokenizer, models, pre_tokenizers
    from transformers import (
        DonutImageProcessorPil,
        DonutProcessor,
        DonutSwinConfig,
        MBartConfig,
        PreTrainedTokenizerFast,
        VisionEncoderDecoderConfig,
        VisionEncoderDecoderModel,
    )

    words = [*SPECIALS, *(word for word in dict.fromkeys(vocab) if word not in SPECIALS)]
    tokenizer_core = Tokenizer(models.WordLevel({word: index for index, word in enumerate(words)}, unk_token="<unk>"))
    tokenizer_core.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    tokenizer = PreTrainedTokenizerFast(tokenizer_object=tokenizer_core, pad_token="<pad>", bos_token="<s>", eos_token="</s>", unk_token="<unk>")
    height, width = view.model.input_size
    encoder = DonutSwinConfig(image_size=(height, width), patch_size=4, embed_dim=EMBED_DIM, depths=list(DEPTHS), num_heads=list(HEADS), window_size=WINDOW)
    decoder = MBartConfig(vocab_size=len(tokenizer), d_model=D_MODEL, decoder_layers=DECODER_LAYERS, decoder_attention_heads=DECODER_HEADS, decoder_ffn_dim=DECODER_FFN,
                          max_position_embeddings=view.model.max_length, is_decoder=True, add_cross_attention=True, pad_token_id=tokenizer.pad_token_id,
                          bos_token_id=tokenizer.bos_token_id, eos_token_id=tokenizer.eos_token_id)
    config = VisionEncoderDecoderConfig.from_encoder_decoder_configs(encoder, decoder)
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(view.model.seed)
        model = VisionEncoderDecoderModel(config)
    model.config.decoder_start_token_id = tokenizer.bos_token_id
    model.config.pad_token_id = tokenizer.pad_token_id
    images = DonutImageProcessorPil(size={"height": height, "width": width}, do_align_long_axis=False, do_thumbnail=True, do_pad=True, do_normalize=True,
                                    image_mean=[0.5, 0.5, 0.5], image_std=[0.5, 0.5, 0.5])
    return DonutProcessor(image_processor=images, tokenizer=tokenizer), model


def _make_tiny(view: SealedConfig, registry_row: Mapping[str, Any], models_dir: Path) -> Any:
    """The extractor of the kind ``tiny``: the real loader (hashes first) on the CPU, whatever ``[model] infer_device`` says (the tiny model is for CPUs)."""
    from invoiceflow.model.donut import load_donut

    return load_donut(dataclasses.replace(view, model=dataclasses.replace(view.model, infer_device="cpu")), registry_row, models_dir)


def register_extractor() -> None:
    """Make ``tiny`` a kind that ``extractors.make_extractor`` builds; calling it again changes nothing."""
    if "tiny" not in extractors.registered():
        extractors.register("tiny", _make_tiny)
