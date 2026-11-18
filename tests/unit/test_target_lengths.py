"""The length of the invoice targets in the real tokenizer (U09, M05; plan sections 9.1 and 9.2): the plan assumed about 520 tokens for an invoice of twelve lines and a limit of
768 positions in the decoder. Needs the pinned checkpoint's tokenizer in the Hugging Face cache (mark ``model``): it loads the tokenizer only, never the weights."""

from __future__ import annotations

import os

import pytest

from invoiceflow.model.serialize import check_block_sizes, invoice_target, json2token, new_special_tokens
from invoiceflow.synth.invoices import FIT_SPLITS, invoices


@pytest.mark.model
def test_every_invoice_target_fits_in_max_length_with_the_real_tokenizer(view, default_master, tmp_path):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(view.model.base, revision=view.model.base_revision, local_files_only=True)
    truths = [t for split in FIT_SPLITS for t in invoices(view, default_master, split, None, seal_path=tmp_path / "none.json")]
    targets = [invoice_target(t) for t in truths]
    tokenizer.add_special_tokens({"additional_special_tokens": new_special_tokens(targets, existing=tokenizer.get_vocab(), extra=(view.model.task_invoice,))})
    lengths = {t.invoice_key: len(tokenizer(view.model.task_invoice + json2token(target), add_special_tokens=False).input_ids) + 1 for t, target in zip(truths, targets, strict=True)}
    sizes = {split: sum(1 for t in truths if t.split == split) for split in FIT_SPLITS}
    check_block_sizes(view, sizes, lengths)
    longest = max(lengths.values())
    assert longest <= view.model.max_length, f"the longest invoice has {longest} tokens"
    print(f"\nlongest {longest}, median {sorted(lengths.values())[len(lengths) // 2]}, over {len(lengths)} invoices; the most lines: {max(len(t.lines) for t in truths)}")
