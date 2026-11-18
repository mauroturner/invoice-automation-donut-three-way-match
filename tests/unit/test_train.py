"""The fine-tuning code (U10; AC17, RF16; plan sections 9.1 and 9.2, decisions D09, D11, D13): a training set that mixes CORD and the synthetic invoices with their tasks, the new
special tokens of the invoice task and the resized decoder, the schedule, the loop (accumulation, bf16 on CUDA, clipping, seeds) and one safetensors folder per candidate epoch, run
on the tiny model on the CPU. The real run needs CUDA and is the launch's (U14, U22): without it the real configuration is refused before anything is touched."""

from __future__ import annotations

import dataclasses
import random

import pytest
from PIL import Image

from invoiceflow.cord import CordIndex, IndexEntry
from invoiceflow.model.serialize import invoice_target, json2token
from invoiceflow.model.train import (
    EpochReport,
    TrainingRefusedError,
    TrainItem,
    build_train_set,
    fine_tune,
    lr_at,
    prepare_tokens,
)
from tests.fixtures.tiny_kit import make_kit, with_model


def fast(view, **changes):
    """The quick view with a training setup that moves a tiny model in a few steps: batch of 2, no accumulation, no warm-up, a large rate."""
    values = {"batch_size": 2, "grad_accumulation": 1, "learning_rate": 1e-2, "warmup_steps": 0, "epochs": 5, "candidate_epochs": (2, 5)}
    values.update(changes)
    return with_model(view, **values)


# --- the training set (D09) ---------------------------------------------------------------------------------------------------------------------


def cord_index(count: int) -> CordIndex:
    entries = [IndexEntry(f"train-{i}", f"{i:064x}", 96, 128, {"menu": [{"nm": f"n{i}", "cnt": "1", "price": "10"}], "total": {"total_price": "10"}}, False, False, None)
               for i in range(count)]
    return CordIndex({"train": entries, "validation": []}, {}, "c" * 40)


def test_the_training_set_has_every_cord_receipt_and_every_invoice_with_its_own_task(quick_view, tiny_truths):
    index = cord_index(5)
    items = build_train_set(index, tiny_truths, quick_view, cord_image=lambda key: Image.new("RGB", (96, 128)), synth_image=lambda truth: Image.new("RGB", (50, 50)))
    assert len(items) == 5 + len(tiny_truths) == 13
    cord = [i for i in items if i.source == "cord"]
    synth = [i for i in items if i.source == "synth"]
    assert len(cord) == 5 and len(synth) == len(tiny_truths)
    assert {i.task for i in cord} == {quick_view.model.task_cord} and {i.task for i in synth} == {quick_view.model.task_invoice}
    assert all(i.target.startswith(i.task) for i in items), "the sequence starts with the task token"
    by_target = {i.target for i in synth}
    assert by_target == {quick_view.model.task_invoice + json2token(invoice_target(t)) for t in tiny_truths}
    assert {i.target for i in cord} == {quick_view.model.task_cord + json2token(e.target) for e in index.entries["train"]}
    assert all(isinstance(i, TrainItem) and callable(i.image) and isinstance(i.image(), Image.Image) for i in items)


def test_the_validation_of_cord_never_enters_the_training_set(quick_view, tiny_truths):
    entries = cord_index(2).entries
    entries["validation"] = [IndexEntry("validation-0", "f" * 64, 96, 128, {"total": {"total_price": "ONLY-IN-VALIDATION"}}, False, False, None)]
    index = CordIndex(entries, {}, "c" * 40)
    items = build_train_set(index, tiny_truths[:2], quick_view, cord_image=lambda key: Image.new("RGB", (4, 4)), synth_image=lambda t: Image.new("RGB", (4, 4)))
    assert len(items) == 4 and not any("ONLY-IN-VALIDATION" in i.target for i in items)


def test_each_loader_is_asked_for_the_right_image_and_lazily(quick_view, tiny_truths):
    asked_cord, asked_synth = [], []
    items = build_train_set(cord_index(3), tiny_truths[:2], quick_view,
                            cord_image=lambda key: asked_cord.append(key) or Image.new("RGB", (4, 4)), synth_image=lambda t: asked_synth.append(t.invoice_key) or Image.new("RGB", (4, 4)))
    assert asked_cord == [] and asked_synth == [], "building the set decodes nothing"
    for item in items:
        item.image()
    assert sorted(asked_cord) == ["train-0", "train-1", "train-2"] and sorted(asked_synth) == sorted(t.invoice_key for t in tiny_truths[:2])


def test_the_order_is_a_function_of_the_seed(quick_view, tiny_truths):
    def order(seed):
        view = with_model(quick_view, seed=seed)
        return [i.target for i in build_train_set(cord_index(5), tiny_truths, view, cord_image=lambda k: None, synth_image=lambda t: None)]

    assert order(0) == order(0) and order(0) != order(1)
    assert sorted(order(0)) == sorted(order(1))
    sources = [i.source for i in build_train_set(cord_index(5), tiny_truths, quick_view, cord_image=lambda k: None, synth_image=lambda t: None)]
    assert sources != sorted(sources), "CORD and the invoices are mixed, not one after the other"


# --- the new tokens and the decoder (AC17) ----------------------------------------------------------------------------------------------------


def test_the_new_task_tokens_exist_and_the_decoder_is_resized(quick_view, tiny_truths):
    from invoiceflow.model.tiny import tiny_model, vocab_of

    targets = [invoice_target(t) for t in tiny_truths]
    processor, model = tiny_model(vocab_of([quick_view.model.task_invoice + json2token(t) for t in targets]), quick_view)
    tokenizer = processor.tokenizer
    before = len(tokenizer)
    assert model.decoder.get_input_embeddings().weight.shape[0] == before
    added = prepare_tokens(processor, model, targets, quick_view)
    assert quick_view.model.task_invoice in added and "<s_header>" in added and "</s_lines>" in added and "<sep/>" in added
    assert len(tokenizer) == before + len(added) > before
    for token in added:
        assert tokenizer.convert_tokens_to_ids(token) < len(tokenizer) and tokenizer.convert_tokens_to_ids(token) != tokenizer.unk_token_id
    assert model.decoder.get_input_embeddings().weight.shape[0] == len(tokenizer), "the decoder's input embeddings follow the tokenizer"
    assert model.decoder.get_output_embeddings().weight.shape[0] == len(tokenizer)
    assert model.config.decoder.vocab_size == len(tokenizer)
    ids = tokenizer(quick_view.model.task_invoice + "<s_header>x</s_header>", add_special_tokens=False).input_ids
    assert len(ids) == 4 and tokenizer.unk_token_id not in ids[:2] and ids[0] == tokenizer.convert_tokens_to_ids(quick_view.model.task_invoice)


def test_adding_the_tokens_twice_adds_nothing_the_second_time(quick_view, tiny_truths):
    from invoiceflow.model.tiny import tiny_model, vocab_of

    targets = [invoice_target(t) for t in tiny_truths]
    processor, model = tiny_model(vocab_of([json2token(t) for t in targets]), quick_view)
    first = prepare_tokens(processor, model, targets, quick_view)
    size = len(processor.tokenizer)
    assert prepare_tokens(processor, model, targets, quick_view) == [] and len(processor.tokenizer) == size and first


def test_a_decoder_that_already_has_room_for_the_new_tokens_keeps_its_rows(quick_view, tiny_truths):
    """The pinned checkpoint's tokenizer has exactly as many tokens as the decoder has rows, so it always grows; a model with spare rows is never shrunk (its trained rows stay)."""
    from invoiceflow.model.tiny import tiny_model, vocab_of

    targets = [invoice_target(t) for t in tiny_truths]
    processor, model = tiny_model(vocab_of([json2token(t) for t in targets]), quick_view)
    rows = len(processor.tokenizer) + 500
    model.decoder.resize_token_embeddings(rows)
    prepare_tokens(processor, model, targets, quick_view)
    assert len(processor.tokenizer) < rows
    assert model.decoder.get_input_embeddings().weight.shape[0] == rows


# --- the schedule ----------------------------------------------------------------------------------------------------------------------------------


def test_the_learning_rate_warms_up_linearly_and_decays_linearly_to_zero():
    base = 1e-3
    assert lr_at(0, 100, 10, base) == pytest.approx(0.0 + base / 10), "step 0 is the first warm-up step: a tenth of the rate"
    assert lr_at(4, 100, 10, base) == pytest.approx(base * 5 / 10)
    assert lr_at(9, 100, 10, base) == pytest.approx(base), "the last warm-up step reaches the rate"
    assert lr_at(10, 100, 10, base) == pytest.approx(base * 90 / 90), "decay starts at the rate"
    assert lr_at(55, 100, 10, base) == pytest.approx(base * (100 - 55) / 90)
    assert lr_at(99, 100, 10, base) == pytest.approx(base * 1 / 90)
    assert lr_at(100, 100, 10, base) == 0.0 and lr_at(500, 100, 10, base) == 0.0
    assert lr_at(0, 100, 0, base) == pytest.approx(base) and lr_at(50, 100, 0, base) == pytest.approx(base * 0.5)
    assert lr_at(0, 5, 10, base) > 0, "a run shorter than its warm-up still moves"


# --- the loop --------------------------------------------------------------------------------------------------------------------------------------


def test_the_tiny_model_loss_drops_in_twenty_cpu_steps(quick_view, tiny_truths, tmp_path):
    """AC17: 20 optimizer steps (8 images, batches of 2, 5 epochs) and the loss is lower at the end than at the start."""
    view = fast(quick_view, epochs=5, candidate_epochs=(5,))
    kit = make_kit(view, tiny_truths)
    reports: list[EpochReport] = []
    folders = fine_tune(kit.items, kit.processor, kit.model, view, out_dir=tmp_path / "fit", on_epoch=reports.append)
    assert [r.epoch for r in reports] == [1, 2, 3, 4, 5] and sum(r.steps for r in reports) == 20
    losses = [loss for r in reports for loss in r.step_losses]
    assert len(losses) == 20 and all(loss == loss for loss in losses), "twenty finite losses"
    assert losses[-1] < losses[0] * 0.8, f"the loss went from {losses[0]:.3f} to {losses[-1]:.3f}"
    assert reports[-1].mean_loss < reports[0].mean_loss
    assert [f.name for f in folders] == ["epoch-5"]


def test_the_same_seed_gives_the_same_losses_and_another_seed_other_ones(quick_view, tiny_truths, tmp_path):
    def run(seed, tag):
        view = fast(quick_view, epochs=2, candidate_epochs=(2,), seed=seed)
        kit = make_kit(view, tiny_truths)
        reports = []
        fine_tune(kit.items, kit.processor, kit.model, view, out_dir=tmp_path / tag, on_epoch=reports.append)
        return [loss for r in reports for loss in r.step_losses]

    assert run(0, "a") == run(0, "b")
    assert run(0, "a2") != run(1, "c")


def test_every_epoch_repeats_all_of_cord_and_all_the_invoices(quick_view, tiny_truths, tmp_path):
    """D09: CORD is revisited in every epoch, or the model forgets it (C01)."""
    view = fast(quick_view, epochs=3, candidate_epochs=(3,), batch_size=4, grad_accumulation=1)
    kit = make_kit(view, tiny_truths, extra_cord=4)
    reports = []
    fine_tune(kit.items, kit.processor, kit.model, view, out_dir=tmp_path / "fit", on_epoch=reports.append)
    assert [(r.cord_items, r.synth_items) for r in reports] == [(4, 8)] * 3
    assert all(r.steps == 3 for r in reports), "12 items in batches of 4: three steps per epoch"


def test_the_order_changes_from_one_epoch_to_the_next_and_each_epoch_sees_every_item(quick_view, tiny_truths, tmp_path):
    view = fast(quick_view, epochs=3, candidate_epochs=(3,), batch_size=1, grad_accumulation=1)
    kit = make_kit(view, tiny_truths, extra_cord=2)
    seen = []

    def spying(item):
        inner = item.image

        def load():
            seen.append(item.target)
            return inner()

        return dataclasses.replace(item, image=load)

    items = [spying(item) for item in kit.items]
    fine_tune(items, kit.processor, kit.model, view, out_dir=tmp_path / "fit")
    per_epoch = [seen[i:i + len(items)] for i in range(0, len(seen), len(items))]
    assert len(per_epoch) == 3
    assert per_epoch[0] != per_epoch[1] and per_epoch[1] != per_epoch[2]
    assert all(sorted(epoch) == sorted(per_epoch[0]) for epoch in per_epoch)


def test_gradient_accumulation_makes_one_step_of_several_batches(quick_view, tiny_truths, tmp_path):
    view = fast(quick_view, epochs=1, candidate_epochs=(1,), batch_size=2, grad_accumulation=4)
    kit = make_kit(view, tiny_truths)   # 8 items: one optimizer step of four batches of two
    reports = []
    fine_tune(kit.items, kit.processor, kit.model, view, out_dir=tmp_path / "fit", on_epoch=reports.append)
    assert reports[0].steps == 1 and len(reports[0].step_losses) == 1
    view = fast(quick_view, epochs=1, candidate_epochs=(1,), batch_size=2, grad_accumulation=3)
    kit = make_kit(view, tiny_truths)   # 8 items: groups of 6 and 2
    reports = []
    fine_tune(kit.items, kit.processor, kit.model, view, out_dir=tmp_path / "fit2", on_epoch=reports.append)
    assert reports[0].steps == 2


def test_accumulated_batches_average_their_gradient_and_the_loss_they_report(quick_view, tiny_truths, tmp_path, monkeypatch):
    """Two batches of one image, one optimizer step: the step's gradient is the mean of the two batches' (not the sum) and the reported loss is their mean. A probe module returns the
    losses 1.0 and 3.0 times its one parameter."""
    import types

    import torch

    kit = make_kit(quick_view, tiny_truths[:2])

    class Probe(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.w = torch.nn.Parameter(torch.tensor(1.0))
            self.values = iter([1.0, 3.0])

        def forward(self, **batch):
            return types.SimpleNamespace(loss=self.w * next(self.values))

    probe, grads = Probe(), []
    real_clip = torch.nn.utils.clip_grad_norm_
    monkeypatch.setattr(torch.nn.utils, "clip_grad_norm_", lambda parameters, max_norm, *a, **k: (grads.append(float(probe.w.grad)), real_clip(parameters, max_norm, *a, **k))[1])
    view = fast(quick_view, epochs=1, candidate_epochs=(2,), batch_size=1, grad_accumulation=2, max_length=256)
    reports = []
    fine_tune(kit.items, kit.processor, probe, view, out_dir=tmp_path / "fit", on_epoch=reports.append)
    assert grads == [pytest.approx(2.0)], "mean of 1.0 and 3.0, not their sum"
    assert reports[0].step_losses == (pytest.approx(2.0),) and reports[0].mean_loss == pytest.approx(2.0)


def test_the_gradient_is_clipped_to_one(quick_view, tiny_truths, tmp_path, monkeypatch):
    import torch

    calls = []
    real = torch.nn.utils.clip_grad_norm_

    def spy(parameters, max_norm, *args, **kwargs):
        calls.append(max_norm)
        return real(parameters, max_norm, *args, **kwargs)

    monkeypatch.setattr(torch.nn.utils, "clip_grad_norm_", spy)
    view = fast(quick_view, epochs=1, candidate_epochs=(1,))
    kit = make_kit(view, tiny_truths)
    fine_tune(kit.items, kit.processor, kit.model, view, out_dir=tmp_path / "fit")
    assert calls == [1.0] * 4


def test_the_rate_follows_the_schedule_step_by_step(quick_view, tiny_truths, tmp_path, monkeypatch):
    import torch

    seen = []
    real_step = torch.optim.AdamW.step

    def spy(self, *args, **kwargs):
        seen.append(self.param_groups[0]["lr"])
        return real_step(self, *args, **kwargs)

    monkeypatch.setattr(torch.optim.AdamW, "step", spy)
    view = fast(quick_view, epochs=2, candidate_epochs=(2,), warmup_steps=2)
    kit = make_kit(view, tiny_truths)
    fine_tune(kit.items, kit.processor, kit.model, view, out_dir=tmp_path / "fit")
    assert seen == pytest.approx([lr_at(step, 8, 2, view.model.learning_rate) for step in range(8)])
    seen.clear()
    uneven = fast(quick_view, epochs=2, candidate_epochs=(2,), warmup_steps=1, batch_size=2, grad_accumulation=3)   # 8 items: groups of 6 and 2, two steps per epoch
    kit = make_kit(uneven, tiny_truths)
    fine_tune(kit.items, kit.processor, kit.model, uneven, out_dir=tmp_path / "fit2")
    assert seen == pytest.approx([lr_at(step, 4, 1, uneven.model.learning_rate) for step in range(4)]), "the last, smaller group of an epoch is a step of its own and counts in the schedule"


def test_one_safetensors_folder_per_candidate_epoch_and_no_other_epoch(quick_view, tiny_truths, tmp_path):
    view = fast(quick_view, epochs=4, candidate_epochs=(2, 4))
    kit = make_kit(view, tiny_truths)
    reports = []
    folders = fine_tune(kit.items, kit.processor, kit.model, view, out_dir=tmp_path / "fit", on_epoch=reports.append)
    assert [f.name for f in folders] == ["epoch-2", "epoch-4"]
    assert sorted(p.name for p in (tmp_path / "fit").iterdir()) == ["epoch-2", "epoch-4"]
    assert [r.folder.name if r.folder else None for r in reports] == [None, "epoch-2", None, "epoch-4"]
    for folder in folders:
        names = {p.name for p in folder.iterdir()}
        assert "model.safetensors" in names and "config.json" in names and "tokenizer.json" in names
        assert not [n for n in names if n.endswith((".bin", ".pt", ".pth", ".pkl", ".ckpt"))]


def test_the_two_candidates_hold_different_weights(quick_view, tiny_truths, tmp_path):
    from safetensors.torch import load_file

    view = fast(quick_view, epochs=4, candidate_epochs=(2, 4))
    kit = make_kit(view, tiny_truths)
    first, second = fine_tune(kit.items, kit.processor, kit.model, view, out_dir=tmp_path / "fit")
    a, b = load_file(first / "model.safetensors"), load_file(second / "model.safetensors")
    assert a.keys() == b.keys() and any((a[k] != b[k]).any() for k in a)


def test_the_real_configuration_without_cuda_is_refused_before_anything_is_touched(cfg, tiny_kit, tmp_path, monkeypatch):
    """D11: the real fine-tune needs CUDA; without it the run exits with 2 (runbook R5) and writes nothing."""
    import torch

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    view = with_model(tiny_kit.view, kind="donut")
    before = {k: v.clone() for k, v in tiny_kit.model.state_dict().items()}
    with pytest.raises(TrainingRefusedError, match="CUDA") as error:
        fine_tune(tiny_kit.items, tiny_kit.processor, tiny_kit.model, view, out_dir=tmp_path / "fit")
    assert TrainingRefusedError.EXIT_CODE == 2 and "R5" in str(error.value)
    assert not (tmp_path / "fit").exists()
    assert all(torch.equal(before[k], v) for k, v in tiny_kit.model.state_dict().items())


def test_a_batch_shifts_the_sequence_by_one_masks_the_padding_and_stacks_the_pages(quick_view, tiny_truths):
    from invoiceflow.model.train import IGNORE, _batch, _encode

    assert IGNORE == -100

    kit = make_kit(quick_view, tiny_truths[:3])
    ids = _encode(kit.items, kit.processor.tokenizer, quick_view.model.max_length)
    pad = kit.processor.tokenizer.pad_token_id
    batch = _batch([0, 1, 2], kit.items, ids, kit.processor, pad, "cpu")
    width = max(len(i) for i in ids) - 1
    assert batch["pixel_values"].shape[0] == 3 and batch["decoder_input_ids"].shape == (3, width) == batch["labels"].shape
    for row, sequence in enumerate(ids):
        n = len(sequence) - 1
        assert batch["decoder_input_ids"][row, :n].tolist() == sequence[:-1], "the decoder reads the sequence without its last token"
        assert batch["labels"][row, :n].tolist() == sequence[1:], "and is asked for the sequence without its first (the task token)"
        assert batch["labels"][row, n:].tolist() == [-100] * (width - n) and batch["decoder_input_ids"][row, n:].tolist() == [pad] * (width - n)
    assert ids[0][-1] == kit.processor.tokenizer.eos_token_id, "every target ends with the end token"
    assert ids[0][0] == kit.processor.tokenizer.convert_tokens_to_ids(quick_view.model.task_invoice)


def test_a_target_of_exactly_max_length_is_accepted_and_one_token_more_is_refused(quick_view, tiny_truths, tmp_path):
    from invoiceflow.model.train import _encode

    kit = make_kit(quick_view, tiny_truths[:2])
    longest = max(len(i) for i in _encode(kit.items, kit.processor.tokenizer, 10_000))
    view = fast(quick_view, epochs=1, candidate_epochs=(1,), max_length=longest)
    fine_tune(kit.items, kit.processor, kit.model, view, out_dir=tmp_path / "fit")
    with pytest.raises(ValueError, match=f"max_length es {longest - 1}"):
        fine_tune(kit.items, kit.processor, kit.model, fast(quick_view, epochs=1, candidate_epochs=(1,), max_length=longest - 1), out_dir=tmp_path / "fit2")


def test_a_fit_does_not_write_over_candidates_that_are_already_there(quick_view, tiny_truths, tmp_path):
    view = fast(quick_view, epochs=1, candidate_epochs=(1,))
    kit = make_kit(view, tiny_truths)
    fine_tune(kit.items, kit.processor, kit.model, view, out_dir=tmp_path / "fit")
    first = (tmp_path / "fit" / "epoch-1" / "model.safetensors").read_bytes()
    with pytest.raises(TrainingRefusedError, match="ya tiene"):
        fine_tune(kit.items, kit.processor, kit.model, view, out_dir=tmp_path / "fit")
    assert (tmp_path / "fit" / "epoch-1" / "model.safetensors").read_bytes() == first


def test_importing_the_model_modules_does_not_import_torch(project_root):
    """The flow and the default session do not pay for torch (plan section 24.1): the code of the model imports it inside its functions."""
    import subprocess
    import sys

    code = (f"import sys; sys.path.insert(0, {str(project_root / 'src')!r}); import invoiceflow, invoiceflow.model.serialize, invoiceflow.model.extractors, invoiceflow.model.donut, invoiceflow.model.train, invoiceflow.model.choose, "
            "invoiceflow.model.artifact, invoiceflow.model.tiny; assert 'torch' not in sys.modules and 'transformers' not in sys.modules, sorted(m for m in sys.modules if m.startswith(('torch', 'transformers')))[:5]")
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=project_root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-400:]


def test_a_target_longer_than_max_length_is_refused_and_not_cut(quick_view, tiny_truths, tmp_path):
    """Cutting a target would remove its end token: the model would learn never to stop. M05 checks the sizes before; the loop does not trust it."""
    view = fast(quick_view, epochs=1, candidate_epochs=(1,), max_length=10)
    kit = make_kit(with_model(quick_view, max_length=256), tiny_truths)
    with pytest.raises(ValueError, match="max_length"):
        fine_tune(kit.items, kit.processor, kit.model, view, out_dir=tmp_path / "fit")
    assert not (tmp_path / "fit").exists()


def test_an_empty_training_set_or_an_unknown_source_is_refused(quick_view, tiny_kit, tmp_path):
    with pytest.raises(ValueError, match="vac"):
        fine_tune([], tiny_kit.processor, tiny_kit.model, quick_view, out_dir=tmp_path / "fit")
    odd = [dataclasses.replace(tiny_kit.items[0], source="other")]
    with pytest.raises(ValueError, match="other"):
        fine_tune(odd, tiny_kit.processor, tiny_kit.model, quick_view, out_dir=tmp_path / "fit")
    assert not (tmp_path / "fit").exists()


def test_a_fit_is_reproducible_whatever_the_caller_did_with_the_generators_before(quick_view, tiny_truths, tmp_path):
    """The run sets the seeds of Python, NumPy and torch itself: the same losses even when the caller used the generators first."""
    import numpy as np
    import torch

    def run(tag):
        random.random(); np.random.rand(); torch.rand(1)
        view = fast(quick_view, epochs=1, candidate_epochs=(1,))
        kit = make_kit(view, tiny_truths)
        reports = []
        fine_tune(kit.items, kit.processor, kit.model, view, out_dir=tmp_path / tag, on_epoch=reports.append)
        return reports[0].step_losses

    assert run("a") == run("b")


@pytest.mark.gpu
def test_the_tiny_model_trains_in_bf16_on_cuda_with_the_real_code_path(quick_view, tiny_truths, tmp_path):
    import torch

    """The default test of the loss on the CPU, once more on the GPU with ``bf16 = true`` (the autocast path of the real fine-tune) and the real configuration's ``kind``."""
    view = fast(quick_view, epochs=3, candidate_epochs=(3,), bf16=True, kind="donut")
    kit = make_kit(with_model(quick_view, kind="tiny"), tiny_truths)
    reports = []
    fine_tune(kit.items, kit.processor, kit.model, view, out_dir=tmp_path / "fit", on_epoch=reports.append)
    losses = [loss for r in reports for loss in r.step_losses]
    assert losses[-1] < losses[0] and all(loss == loss for loss in losses)
    assert next(kit.model.parameters()).device.type == "cuda"
    seen = []
    layer = kit.model.decoder.model.decoder.layers[0].fc1
    handle = layer.register_forward_hook(lambda module, args, output: seen.append(output.dtype))
    view_again = fast(quick_view, epochs=1, candidate_epochs=(2,), bf16=True, kind="donut")
    kit.model.cpu()
    fine_tune(kit.items, kit.processor, kit.model, view_again, out_dir=tmp_path / "fit2")
    handle.remove()
    assert seen and set(seen) == {torch.bfloat16}, f"the forward ran in {set(seen)}: bf16 autocast is on"
