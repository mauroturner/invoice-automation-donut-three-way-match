"""The artifact of the model (U10; AC19; plan section 9.4, decision D13; risk items 2 and 5): ``safetensors``, a config, the tokenizer and the processor's files, each hashed in a
manifest whose own hash is the registered ``artifact_sha256``. Reading it checks the folder first (no pickle file of any kind, only the files of the manifest, each with its size and
SHA-256) and loads only afterwards, and only with ``use_safetensors=True``. The pinned base checkpoint, which ships a ``pytorch_model.bin``, is read once by ``load_base`` with
``weights_only=True`` and never as a full pickle."""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path

import pytest

from invoiceflow.model import artifact as artifact_module
from invoiceflow.model.artifact import FORBIDDEN_SUFFIXES, MANIFEST, ArtifactError, ArtifactRecord, load_artifact, save_artifact, seal_folder, verify_artifact


@pytest.fixture
def saved(tiny_kit, tmp_path):
    folder = tmp_path / "models" / "tiny-1"
    record = save_artifact(tiny_kit.model, tiny_kit.processor, folder)
    return folder, record


@pytest.fixture
def no_loading(monkeypatch):
    """A spy on the one function that reads weights: a refusal must happen before it."""
    calls = []

    def spy(folder):
        calls.append(folder)
        raise AssertionError("the weights were read")

    monkeypatch.setattr(artifact_module, "_read_model", spy)
    return calls


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_the_artifact_holds_safetensors_config_tokenizer_and_a_manifest_and_nothing_else(saved):
    folder, record = saved
    names = sorted(p.name for p in folder.iterdir())
    assert isinstance(record, ArtifactRecord)
    assert "model.safetensors" in names and "config.json" in names and "tokenizer.json" in names and MANIFEST in names
    assert not [n for n in names if Path(n).suffix.lower() in FORBIDDEN_SUFFIXES]
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    assert sorted(manifest["files"]) == sorted(set(names) - {MANIFEST}), "the manifest lists every file but itself"
    for name, entry in manifest["files"].items():
        assert entry == {"bytes": (folder / name).stat().st_size, "sha256": sha(folder / name)}
    assert record.sha256 == sha(folder / MANIFEST), "artifact_sha256 is the hash of the manifest, which holds the hash of every file"
    assert record.tokenizer_sha256 == sha(folder / "tokenizer.json")
    assert set(record.files) == set(manifest["files"])


def test_the_weights_are_read_with_safetensors_local_files_and_no_remote_code(saved, monkeypatch):
    import transformers

    folder, record = saved
    calls = []
    real = transformers.VisionEncoderDecoderModel.from_pretrained.__func__

    def spy(cls, source, **kwargs):
        calls.append(kwargs)
        return real(cls, source, **kwargs)

    monkeypatch.setattr(transformers.VisionEncoderDecoderModel, "from_pretrained", classmethod(spy))
    load_artifact(folder, record.sha256)
    assert len(calls) == 1
    assert calls[0]["use_safetensors"] is True and calls[0]["local_files_only"] is True and calls[0]["trust_remote_code"] is False


def test_the_weights_are_written_in_the_safetensors_format(saved):
    from safetensors import safe_open

    folder, _ = saved
    with safe_open(folder / "model.safetensors", framework="pt") as handle:
        assert len(list(handle.keys())) > 10


def test_loading_gives_back_the_same_model(saved, tiny_kit):
    import torch

    folder, record = saved
    processor, model = load_artifact(folder, record.sha256)
    assert type(model).__name__ == "VisionEncoderDecoderModel" and not model.training
    tiny_kit.model.eval()
    original, again = tiny_kit.model.state_dict(), model.state_dict()
    assert original.keys() == again.keys() and all(torch.equal(original[k], again[k]) for k in original)
    assert processor.tokenizer.get_vocab() == tiny_kit.processor.tokenizer.get_vocab()


def test_an_artifact_with_another_hash_is_refused_before_loading(saved, no_loading):
    folder, record = saved
    with pytest.raises(ArtifactError, match="huella"):
        load_artifact(folder, "0" * 64)
    with pytest.raises(ArtifactError):
        load_artifact(folder, record.sha256[:-1] + ("0" if record.sha256[-1] != "0" else "1"))
    assert no_loading == [], "nothing was read: the registered hash is compared with the manifest's first"


@pytest.mark.parametrize("damage", ["same_size", "bigger", "smaller", "missing"])
def test_a_changed_or_missing_file_is_refused_before_loading_and_says_which(saved, no_loading, damage):
    folder, record = saved
    weights = folder / "model.safetensors"
    data = weights.read_bytes()
    if damage == "same_size":
        weights.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    elif damage == "bigger":
        weights.write_bytes(data + b"\0")
    elif damage == "smaller":
        weights.write_bytes(data[:-1])
    else:
        weights.unlink()
    with pytest.raises(ArtifactError, match="model.safetensors"):
        load_artifact(folder, record.sha256)
    assert no_loading == []
    with pytest.raises(ArtifactError, match="model.safetensors"):
        verify_artifact(folder, record.sha256)


def test_a_file_of_another_size_is_refused_without_hashing_it(saved, no_loading, monkeypatch):
    """The size is compared first: a file of another size (a truncated or a padded multi-gigabyte weights file) is not read to be hashed."""
    folder, record = saved
    hashed = []
    real = artifact_module.sha256_file
    monkeypatch.setattr(artifact_module, "sha256_file", lambda path, *a, **k: (hashed.append(Path(path).name), real(path, *a, **k))[1])
    weights = folder / "model.safetensors"
    weights.write_bytes(weights.read_bytes() + b"\0")
    with pytest.raises(ArtifactError, match="bytes"):
        load_artifact(folder, record.sha256)
    assert "model.safetensors" not in hashed and MANIFEST in hashed


def test_a_changed_tokenizer_is_refused_too(saved, no_loading):
    folder, record = saved
    tokenizer = folder / "tokenizer.json"
    tokenizer.write_bytes(tokenizer.read_bytes().replace(b"<pad>", b"<pax>", 1))
    with pytest.raises(ArtifactError, match="tokenizer.json"):
        load_artifact(folder, record.sha256)
    assert no_loading == []


PICKLE_SUFFIXES = (".bin", ".pt", ".pth", ".pkl", ".ckpt", ".pickle")


def test_the_forbidden_suffixes_are_the_plans_five_and_pickle():
    assert set(PICKLE_SUFFIXES) <= set(FORBIDDEN_SUFFIXES)


@pytest.mark.parametrize("suffix", PICKLE_SUFFIXES)
def test_a_pickle_file_in_the_artifact_folder_is_refused(saved, no_loading, suffix):
    """Whatever its content and wherever it came from: a ``.bin``, ``.pt``, ``.pth``, ``.pkl`` or ``.ckpt`` next to the weights is never accepted."""
    folder, record = saved
    (folder / f"extra{suffix}").write_bytes(pickle.dumps({"a": 1}))
    with pytest.raises(ArtifactError, match="pickle"):
        load_artifact(folder, record.sha256)
    assert no_loading == []


def test_a_pickle_file_is_refused_even_when_the_manifest_and_the_registered_hash_list_it(saved, no_loading):
    """Someone who can write the folder and the registry could list the file: the suffix is refused anyway, before any hash is compared."""
    folder, _ = saved
    (folder / "pytorch_model.bin").write_bytes(pickle.dumps({"a": 1}))
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    manifest["files"]["pytorch_model.bin"] = {"bytes": (folder / "pytorch_model.bin").stat().st_size, "sha256": sha(folder / "pytorch_model.bin")}
    (folder / MANIFEST).write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match="pytorch_model.bin"):
        load_artifact(folder, sha(folder / MANIFEST))
    assert no_loading == []


def test_a_pickle_in_a_subfolder_or_a_file_the_manifest_does_not_list_is_refused(saved, no_loading):
    folder, record = saved
    (folder / "notes.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(ArtifactError, match="notes.txt"):
        load_artifact(folder, record.sha256)
    (folder / "notes.txt").unlink()
    (folder / "sub").mkdir()
    (folder / "sub" / "w.pt").write_bytes(b"x")
    with pytest.raises(ArtifactError, match="sub"):
        load_artifact(folder, record.sha256)
    assert no_loading == []


def test_a_manifest_rewritten_to_match_changed_files_is_not_the_registered_one(saved, no_loading):
    """The hashes protect against a file that changed, not against whoever changes the file and the registry together (D13); but changing the files and the manifest alone is caught."""
    folder, record = saved
    weights = folder / "model.safetensors"
    weights.write_bytes(weights.read_bytes() + b"\0")
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    manifest["files"]["model.safetensors"] = {"bytes": weights.stat().st_size, "sha256": sha(weights)}
    (folder / MANIFEST).write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match="huella"):
        load_artifact(folder, record.sha256)
    assert no_loading == []


def test_a_registered_hash_that_is_not_64_hex_or_a_folder_without_manifest_is_refused(saved, tmp_path, no_loading):
    folder, record = saved
    for bad in ("", "abc", record.sha256.upper(), record.sha256 + "0", None, 5):
        with pytest.raises(ArtifactError, match="huella"):
            load_artifact(folder, bad)
    empty = tmp_path / "nothing"
    empty.mkdir()
    with pytest.raises(ArtifactError, match=MANIFEST):
        load_artifact(empty, record.sha256)
    with pytest.raises(ArtifactError):
        load_artifact(tmp_path / "does-not-exist", record.sha256)
    (folder / MANIFEST).write_text("not json", encoding="utf-8")
    with pytest.raises(ArtifactError):
        load_artifact(folder, sha(folder / MANIFEST))
    assert no_loading == []


def test_a_manifest_that_is_not_the_exact_shape_is_refused(saved, no_loading):
    folder, _ = saved
    for text in ('[]', '{"files": []}', '{"files": {"a": {"bytes": "x", "sha256": "0"}}}', '{"files": {"../x": {"bytes": 1, "sha256": "' + "0" * 64 + '"}}}',
                 '{"files": {}, "other": 1}'):
        (folder / MANIFEST).write_text(text, encoding="utf-8")
        with pytest.raises(ArtifactError):
            load_artifact(folder, sha(folder / MANIFEST))
    assert no_loading == []


def test_a_manifest_with_a_key_besides_files_is_refused(saved, no_loading):
    folder, _ = saved
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    manifest["other"] = 1
    (folder / MANIFEST).write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match=MANIFEST):
        load_artifact(folder, sha(folder / MANIFEST))
    assert no_loading == []


def test_a_folder_without_its_weights_or_tokenizer_is_not_sealed(tiny_kit, tmp_path):
    folder = tmp_path / "half"
    tiny_kit.processor.save_pretrained(folder)          # the tokenizer and the processor, no model
    with pytest.raises(ArtifactError, match="model.safetensors"):
        seal_folder(folder)
    assert not (folder / MANIFEST).exists()


def test_an_artifact_is_never_written_over_another_one(saved, tiny_kit):
    folder, _ = saved
    with pytest.raises(ArtifactError, match="ya existe|existe"):
        save_artifact(tiny_kit.model, tiny_kit.processor, folder)
    seal = [p.name for p in folder.iterdir()]
    assert MANIFEST in seal


def test_a_folder_saved_by_the_fine_tune_is_sealed_in_place_and_a_bin_or_a_second_seal_is_refused(tiny_kit, tmp_path):
    folder = tmp_path / "epoch-2"
    tiny_kit.model.save_pretrained(folder, safe_serialization=True)
    tiny_kit.processor.save_pretrained(folder)
    record = seal_folder(folder)
    assert verify_artifact(folder, record.sha256).sha256 == record.sha256
    with pytest.raises(ArtifactError, match=MANIFEST):
        seal_folder(folder)
    other = tmp_path / "epoch-4"
    tiny_kit.model.save_pretrained(other, safe_serialization=True)
    (other / "pytorch_model.bin").write_bytes(b"x")
    with pytest.raises(ArtifactError, match="pytorch_model.bin"):
        seal_folder(other)
    assert not (other / MANIFEST).exists(), "a refused folder gets no manifest"


def test_the_hash_does_not_depend_on_where_the_folder_is(tiny_kit, tmp_path):
    first = save_artifact(tiny_kit.model, tiny_kit.processor, tmp_path / "a" / "x")
    second = save_artifact(tiny_kit.model, tiny_kit.processor, tmp_path / "b" / "y")
    assert first.sha256 == second.sha256


# --- the pinned base checkpoint: the one place a pickle format is read, once, and only as tensors -------------------------------------------------------


@pytest.fixture
def base_folders(tiny_kit, tmp_path):
    """Two local stand-ins for the base checkpoint's repository: one with ``model.safetensors`` and one that ships only ``pytorch_model.bin``, as the pinned revision does."""
    import torch

    with_safe = tmp_path / "with_safetensors"
    tiny_kit.model.save_pretrained(with_safe, safe_serialization=True)
    tiny_kit.processor.save_pretrained(with_safe)
    only_bin = tmp_path / "only_bin"
    tiny_kit.model.config.save_pretrained(only_bin)
    tiny_kit.processor.save_pretrained(only_bin)
    torch.save(tiny_kit.model.state_dict(), only_bin / "pytorch_model.bin")
    return with_safe, only_bin


def test_load_base_prefers_safetensors_and_reads_a_bin_only_when_there_is_nothing_else(base_folders, quick_view, monkeypatch):
    import torch

    from invoiceflow.model.train import load_base

    with_safe, only_bin = base_folders
    seen = []
    real_load = torch.load

    def spy(*args, **kwargs):
        seen.append(kwargs.get("weights_only"))
        return real_load(*args, **kwargs)

    monkeypatch.setattr(torch, "load", spy)
    load_base(quick_view, source=with_safe)
    assert seen == [], "with a safetensors file nothing is unpickled"
    _, model = load_base(quick_view, source=only_bin)
    assert seen and all(flag is True for flag in seen), f"the .bin is read with weights_only=True, saw {seen}"
    assert type(model).__name__ == "VisionEncoderDecoderModel"


def test_load_base_asks_for_the_bin_only_with_weights_only_after_safetensors_was_not_there(quick_view, monkeypatch):
    import transformers

    from invoiceflow.model.train import load_base

    calls = []

    def fake_model(source, **kwargs):
        calls.append(kwargs)
        if kwargs["use_safetensors"]:
            raise OSError("no model.safetensors")
        return "model"

    monkeypatch.setattr(transformers.VisionEncoderDecoderModel, "from_pretrained", fake_model)
    monkeypatch.setattr(transformers.DonutProcessor, "from_pretrained", lambda source, **kwargs: "processor")
    assert load_base(quick_view) == ("processor", "model")
    assert [c["use_safetensors"] for c in calls] == [True, False]
    assert calls[1]["weights_only"] is True and all(c["local_files_only"] is True and c["trust_remote_code"] is False for c in calls)
    assert "weights_only" not in calls[0] or calls[0]["weights_only"] is True


def test_a_malicious_bin_does_not_run_its_code_and_is_refused(base_folders, quick_view, tmp_path):
    """A pickle that would write a file when loaded: ``weights_only=True`` refuses its unpickler, the marker is never written and the error says the file is not a checkpoint."""
    from invoiceflow.model.train import load_base

    _, only_bin = base_folders
    marker = tmp_path / "pwned.txt"

    class Evil:
        def __reduce__(self):
            return (Path.write_text, (marker, "executed"))

    (only_bin / "pytorch_model.bin").write_bytes(pickle.dumps(Evil()))
    with pytest.raises(Exception):
        load_base(quick_view, source=only_bin)
    assert not marker.exists(), "the code of the pickle ran"


def test_load_base_reads_the_pinned_revision_from_the_local_cache_only(quick_view, monkeypatch):
    """The default source is the pinned repository at its pinned revision, and every call carries ``local_files_only=True``: a missing copy is an error, never a download."""
    import transformers

    from invoiceflow.model.train import load_base

    calls = []

    def fake(source, **kwargs):
        calls.append((source, kwargs))
        raise OSError("not in the cache")

    monkeypatch.setattr(transformers.VisionEncoderDecoderModel, "from_pretrained", fake)
    with pytest.raises(OSError):
        load_base(quick_view)
    assert calls, "the model was asked for"
    for source, kwargs in calls:
        assert source == quick_view.model.base
        assert kwargs["revision"] == quick_view.model.base_revision and kwargs["local_files_only"] is True and kwargs.get("trust_remote_code", False) is False
    assert [kwargs.get("use_safetensors") for _, kwargs in calls][0] is True, "safetensors is asked for first"


# --- the real loader of the model now checks the hashes first ---------------------------------------------------------------------------------------------


def test_load_donut_still_builds_the_same_extractor_and_now_checks_the_artifact_first(quick_view, tiny_kit, tmp_path, monkeypatch):
    from invoiceflow.model.donut import DonutExtractor, load_donut

    record = save_artifact(tiny_kit.model, tiny_kit.processor, tmp_path / "tiny-1")
    row = {"artifact_name": "tiny-1", "artifact_sha256": record.sha256, "model_version": "tiny-1"}
    extractor = load_donut(quick_view, row, tmp_path)
    assert isinstance(extractor, DonutExtractor) and extractor.model_version == "tiny-1" and extractor.device in ("cpu", "cuda")
    height, width = quick_view.model.input_size
    size = extractor.processor.image_processor.size
    assert (size["height"], size["width"]) == (height, width)
    from invoiceflow.model.tiny import read_length

    # a tiny model reads up to its own short length, a real one up to [model] max_length (tests/unit/test_tiny.py; D31, the speed unit)
    assert extractor.task_token == quick_view.model.task_invoice and extractor.max_length == read_length(quick_view) and not extractor.model.training
    spy = []
    monkeypatch.setattr(artifact_module, "_read_model", lambda folder: spy.append(folder))
    for bad in ({**row, "artifact_sha256": "0" * 64}, {k: v for k, v in row.items() if k != "artifact_sha256"}):
        with pytest.raises(ArtifactError):
            load_donut(quick_view, bad, tmp_path)
    assert spy == []
