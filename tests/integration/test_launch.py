"""The frozen launch and its seal (U14; RF16, RF17; D12, D21; M02-M06; AC18, AC19, AC20, AC22 in part, AC25): the launch fits the model once with the tiny kind on the tiny CORD, measures every candidate epoch on
the validations, chooses by the rule of D12, registers one hashed artifact and writes the launch part of the seal once. A repetition fits nothing and checks the hash; the failures stop it before anything is written."""

from __future__ import annotations

import dataclasses
import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from invoiceflow import cord, launch, warehouse
from invoiceflow.model import artifact
from invoiceflow.model.artifact import ArtifactError, verify_artifact
from invoiceflow.model.choose import ModelChoiceError
from invoiceflow.model.serialize import BlockSizeError
from invoiceflow.registry import RunMeta, SealMismatchError, read_registry, read_seal, seal_state
from invoiceflow.sources import check_sources, fetch_source
from tests.fixtures.make_cord_fixture import FILE_NAMES
from tests.fixtures.projects import make_project

RUN = RunMeta("launch-1", datetime(2026, 10, 1))
OPENED: dict[Path, list[str]] = {}


def context(root: Path, **overrides):
    """A launch context on a project folder at `root`: the tiny CORD indexed, an empty warehouse and the sources hashed."""
    project = make_project(root)
    cfg = project.cfg
    fetch_source(cfg)
    conn = warehouse.connect(cfg.paths.warehouse)
    warehouse.apply_schema(conn)
    return launch.LaunchContext(cfg=cfg, conn=conn, run=RUN, observed=check_sources(cfg), cord_index=cord.read_index(cfg), **overrides)


@pytest.fixture(scope="module")
def launched(tmp_path_factory):
    """The one full launch of the module (the tiny model reads every page it is measured on, so a launch costs seconds): the CORD files whose rows were opened are kept to say the test split was not."""
    ctx = context(tmp_path_factory.mktemp("launch"))
    opened: list[str] = []
    real = cord._iter_rows
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cord, "_iter_rows", lambda path, *a, **k: (opened.append(Path(path).name), real(path, *a, **k))[1])
        result = launch.fit_model(ctx)
    OPENED[ctx.cfg.project_root] = opened
    return ctx, result


def test_every_validation_figure_names_the_splits_its_model_saw(launched):
    """AC22: a figure of a validation says which partitions the model that produced it was fitted with, in the result, the registry and the seal; the checkpoint's says it was not fitted here and the oracle's says there was no model."""
    ctx, result = launched
    assert result.mode == "launch" and result.seal == "written"
    seal = read_seal(ctx.cfg.paths.seal)["launch"]
    row = read_registry(ctx.conn, result.model_version)
    stored = json.loads(row["choice_json"])
    for choice in (result.choice, seal["choice"], stored["choice"]):
        assert [c["epoch"] for c in choice["candidates"]] == [1, 2]
        assert all(c["fit_splits"] == "cord_train+synth_train" for c in choice["candidates"])
        assert {"cord_ted", "exact_known", "exact_new", "score", "status"} <= set(choice["candidates"][0])
    assert seal["checkpoint_cord_val"]["fit_splits"] == "checkpoint" and seal["checkpoint_cord_val"]["receipts"] == 4
    assert seal["oracle_validation"]["fit_splits"] == "none" and seal["oracle_validation"]["wrong"] == 0 and seal["oracle_validation"]["invoices"] == 12


def test_the_launch_registers_one_hashed_artifact_and_seals_once(launched):
    ctx, result = launched
    seal = read_seal(ctx.cfg.paths.seal)["launch"]
    row = read_registry(ctx.conn, "v1")
    folder = Path(ctx.cfg.paths.models_dir) / row["artifact_name"]
    assert row["artifact_name"] == seal["artifact_name"] == f"model-v1-{seal['config_sha256'][:12]}" and row["config_sha256"] == seal["config_sha256"] == result.config_sha256
    assert row["artifact_sha256"] == seal["artifact_sha256"] == result.artifact_sha256 and row["tokenizer_sha256"] == seal["tokenizer_sha256"]
    assert verify_artifact(folder, row["artifact_sha256"]).tokenizer_sha256 == row["tokenizer_sha256"]
    assert row["chosen_epoch"] == seal["chosen_epoch"] == result.choice["chosen_epoch"] and row["trained_as_of"] == "2026-10-01 00:00:00"
    assert seal["run_key"] == "launch-1" and seal["as_of"] == "2026-10-01 00:00:00" and set(seal["source_sha256"]) >= {f.name for f in ctx.cfg.source.cord_files}
    assert [p.name for p in Path(ctx.cfg.paths.models_dir).iterdir()] == [row["artifact_name"]], "the candidate epochs' scratch is gone"
    assert seal_state(ctx.cfg.paths.seal, row["config_sha256"]) == "match" and {r.rule_id for r in result.rules} == {"M04", "M05", "M06"} and all(r.passed for r in result.rules)
    assert len(seal["validation_digest"]) == 64 and result.rows == {"model_registry": 1}


def test_a_repeated_launch_fits_nothing_and_checks_the_hash_of_the_artifact(launched, monkeypatch):
    ctx, first = launched
    monkeypatch.setattr(launch, "fine_tune", lambda *a, **k: pytest.fail("a repetition does not fit"))
    seal_before = Path(ctx.cfg.paths.seal).read_bytes()
    again = launch.fit_model(dataclasses.replace(ctx, run=RunMeta("replay-2", datetime(2026, 10, 2))))
    assert again.mode == "verify" and again.seal == "match" and again.artifact_sha256 == first.artifact_sha256 and again.config_sha256 == first.config_sha256
    assert {r.rule_id for r in again.rules} == {"M02", "M03"} and all(r.passed for r in again.rules)
    assert Path(ctx.cfg.paths.seal).read_bytes() == seal_before and ctx.conn.execute("SELECT count(*) FROM model_registry").fetchone()[0] == 1


def test_an_artifact_whose_bytes_changed_is_refused_before_it_is_read(tmp_path, launched, monkeypatch):
    ctx, first = launched
    work = tmp_path / "copy"
    shutil.copytree(Path(ctx.cfg.paths.models_dir), work)
    victim = work / read_registry(ctx.conn, "v1")["artifact_name"] / "model.safetensors"
    victim.write_bytes(victim.read_bytes() + b"x")
    monkeypatch.setattr(artifact, "_read_model", lambda folder: pytest.fail("a changed artifact is never read"))
    other = dataclasses.replace(ctx, cfg=dataclasses.replace(ctx.cfg, paths=dataclasses.replace(ctx.cfg.paths, models_dir=work)))
    with pytest.raises(ArtifactError, match="R6"):
        launch.fit_model(other)


def test_a_registered_model_with_no_seal_is_an_error_and_another_configuration_is_post_seal(launched, tmp_path):
    ctx, _ = launched
    no_seal = dataclasses.replace(ctx, cfg=dataclasses.replace(ctx.cfg, paths=dataclasses.replace(ctx.cfg.paths, seal=tmp_path / "none" / "model_seal.json")))
    with pytest.raises(launch.LaunchError, match="R7"):
        launch.fit_model(no_seal)
    assert not (tmp_path / "none").exists()
    other_cfg = dataclasses.replace(ctx.cfg, match=dataclasses.replace(ctx.cfg.match, price_tolerance_pct=3.0))
    after = launch.fit_model(dataclasses.replace(ctx, cfg=other_cfg))
    assert after.mode == "verify" and after.seal == "post_seal" and next(r for r in after.rules if r.rule_id == "M03").passed is False


def test_no_candidate_that_keeps_cord_stops_the_launch_writes_no_model_and_keeps_the_candidate_epochs(tmp_path, monkeypatch):
    """R5, and A7 of the evaluation (D62): the choice fails after the whole fit, so the candidate epochs are kept, each a sealed folder, in a folder of their own that the error names; no
    artifact, no registry row and no seal are written."""
    ctx = context(tmp_path)
    figures = iter([90.0, 10.0, 20.0])         # the checkpoint, then the two candidates: both lose more than the tolerance
    monkeypatch.setattr(launch, "cord_ted", lambda *a, **k: next(figures))
    monkeypatch.setattr(launch, "exact_rate", lambda *a, **k: 0.5)
    with pytest.raises(ModelChoiceError, match="R5") as caught:
        launch.fit_model(ctx)
    models = Path(ctx.cfg.paths.models_dir)
    kept = models / f".candidates-{RUN.run_key}"
    assert sorted(p.name for p in models.iterdir()) == [kept.name] and kept.name in str(caught.value)
    assert sorted(p.name for p in kept.iterdir()) == ["epoch-1", "epoch-2"] and all(verify_artifact(f, launch.seal_folder_hash(f)) for f in kept.iterdir())
    assert not Path(ctx.cfg.paths.seal).exists() and ctx.conn.execute("SELECT count(*) FROM model_registry").fetchone()[0] == 0


def test_a_rule_that_is_not_what_the_generator_planted_stops_the_launch_before_the_fit(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    real = launch.oracle

    def wrong(view, master, truths):
        decisions = real(view, master, truths)
        first = next(iter(decisions))
        return {**decisions, first: dataclasses.replace(decisions[first], decision="review" if decisions[first].decision == "auto_post" else "auto_post")}

    monkeypatch.setattr(launch, "oracle", wrong)
    monkeypatch.setattr(launch, "fine_tune", lambda *a, **k: pytest.fail("the rules are wrong: no fit is worth starting"))
    with pytest.raises(launch.OracleMismatchError, match="M06") as caught:
        launch.fit_model(ctx)
    assert "R5" in str(caught.value) and caught.value.EXIT_CODE == 2
    assert_nothing_written(ctx)


def test_a_partition_of_another_size_stops_the_launch_before_anything_is_loaded(tmp_path, monkeypatch):
    ctx = context(tmp_path)
    real = launch.invoices
    monkeypatch.setattr(launch, "invoices", lambda view, master, split, day, **k: real(view, master, split, day, **k)[:-1] if split == "validation_known" else real(view, master, split, day, **k))
    monkeypatch.setattr(launch, "fine_tune", lambda *a, **k: pytest.fail("no fit with a partition of another size"))
    with pytest.raises(BlockSizeError, match="validation_known"):
        launch.fit_model(ctx)
    assert_nothing_written(ctx)


def test_the_same_configuration_with_other_figures_in_the_seal_is_refused_and_nothing_is_written(tmp_path, monkeypatch):
    """A clean warehouse, a seal of the same configuration whose figures are other ones (as a relaunch on a GPU that gave other figures would find): R7, and the artifact, the registry and the seal stay as they were."""
    from invoiceflow.registry import sealed_view
    from tests.fixtures.seals import write_full_seal

    ctx = context(tmp_path)
    monkeypatch.setattr(launch, "cord_ted", lambda *a, **k: 77.0)
    monkeypatch.setattr(launch, "exact_rate", lambda *a, **k: 0.5)
    seal = write_full_seal(ctx.cfg.paths.seal, sealed_view(ctx.cfg, ctx.observed))
    before = seal.read_bytes()
    with pytest.raises(SealMismatchError, match="R7") as caught:
        launch.fit_model(ctx)
    assert "choice" in str(caught.value) and seal.read_bytes() == before
    assert_nothing_written(ctx, seal_kept=True)


def test_an_unregistered_artifact_folder_of_the_same_name_is_refused_and_left_alone(tmp_path):
    ctx = context(tmp_path)
    name = f"model-v1-{launch.config_sha256_of(ctx)[:12]}"
    orphan = Path(ctx.cfg.paths.models_dir) / name
    orphan.mkdir(parents=True)
    (orphan / "note.txt").write_text("not mine", encoding="utf-8")
    with pytest.raises(launch.LaunchError, match="R6"):
        launch.fit_model(ctx)
    assert (orphan / "note.txt").read_text(encoding="utf-8") == "not mine" and ctx.conn.execute("SELECT count(*) FROM model_registry").fetchone()[0] == 0
    assert not Path(ctx.cfg.paths.seal).exists()


def test_the_launch_never_opens_the_cord_test_split_or_the_synthetic_truth(launched):
    ctx, _ = launched
    opened = OPENED[ctx.cfg.project_root]
    assert FILE_NAMES["train"] in opened and FILE_NAMES["validation"] in opened and FILE_NAMES["test"] not in opened
    assert not (Path(ctx.cfg.paths.interim_dir) / "truth").exists(), "no truth file was written or read"


def assert_nothing_written(ctx, *, seal_kept: bool = False):
    models = Path(ctx.cfg.paths.models_dir)
    assert Path(ctx.cfg.paths.seal).exists() == seal_kept and ctx.conn.execute("SELECT count(*) FROM model_registry").fetchone()[0] == 0
    assert not models.exists() or not any(models.iterdir()), "no artifact and no scratch is left"


def test_a_seal_that_cannot_be_written_leaves_no_registry_row_and_no_artifact_and_the_launch_can_run_again(tmp_path, monkeypatch):
    """A5 of the evaluation (U14/W1, D62): the seal is written before the model is registered, so a seal that fails to be written leaves nothing behind, and the next launch is a launch."""
    ctx = context(tmp_path)
    real = launch.write_launch_seal

    def full_disk(*args, **kwargs):
        raise OSError(28, "No space left on device (simulated)")

    monkeypatch.setattr(launch, "write_launch_seal", full_disk)
    with pytest.raises(OSError, match="No space"):
        launch.fit_model(ctx)
    assert_nothing_written(ctx)
    monkeypatch.setattr(launch, "write_launch_seal", real)
    again = launch.fit_model(ctx)
    assert again.mode == "launch" and again.seal == "written" and read_registry(ctx.conn)["artifact_sha256"] == again.artifact_sha256

