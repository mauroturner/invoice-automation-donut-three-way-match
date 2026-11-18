"""Configuration (U02): paths against the project folder, the seal's two lists of keys, the sources' guarantees, the reserved layouts, fonts and days
of the synthetic partitions, and every key refused by name (RNF05, AC20 in part, AC32, AC33; D10, D21, D30, D35)."""

from __future__ import annotations

import dataclasses
import re
from datetime import date
from pathlib import Path

import pytest

from invoiceflow import config as config_module
from invoiceflow.config import (
    DEFAULT_CONFIG,
    ENV_CONFIG,
    ENV_HOME,
    FINGERPRINT_KEYS,
    INFORMATIVE_KEYS,
    UNSEALED_KEYS,
    PipelineConfig,
    SealedConfig,
    config_leaves,
    load_config,
)

PROJECT = Path(__file__).resolve().parents[2]
HASH_A = "da3994eee1bf9bd3c57f0d53a72c3a6812c8696c5ba26245987949ddf73483cc"
HASH_PDF = "a6d0f71e14f282836f07834af76a975dd885386bf972d860878d4b44278deaac"
COMMIT = "7f0115a4b758a71d6473b8d085751692da2fef98"
REVISION = "8003d433113256b4ce3a0f5bf604b29ff78a7451"
SHARDS = {  # name -> (split, bytes, sha256), the six files of research/s00 (data/raw/cord/manifest.json)
    "train-00000-of-00004-b4aaeceff1d90ecb.parquet": ("train", 490224630, HASH_A),
    "train-00001-of-00004-7dbbe248962764c5.parquet": ("train", 441418432, "cce4def16a0d6a6c75f80be712f7494c56c318a8829b712f5c62650155c9e58e"),
    "train-00002-of-00004-688fe1305a55e5cc.parquet": ("train", 443802181, "591e2db8fe8b1d364b054f46e8c375b7f00e72578914ba46a573b6858162cab2"),
    "train-00003-of-00004-2d0cd200555ed7fd.parquet": ("train", 455555434, "1ffd9de8d6fbcee7630fd4cdfedff05b9b7fabc0fae4fc17557c5fe7cf178748"),
    "validation-00000-of-00001-cc3c5779fe22e8ca.parquet": ("validation", 242080800, "0d0f6dac11fdcc549de2746aa9f53136a3bc22a2a1aff2b0b847f7622ad60c15"),
    "test-00000-of-00001-9c204eb3f4e11791.parquet": ("test", 234202795, "51c65f1788faff392abe2a0b55b023eb23e9be551c509138eaa3a832514224e7"),
}


@pytest.fixture(scope="module")
def cfg() -> PipelineConfig:
    return load_config(DEFAULT_CONFIG)


def variant(tmp_path: Path, snippet: str = "", replace: tuple[str, str] | None = None, name: str = "pipeline.toml") -> Path:
    """A configuration inside `tmp_path/config/`: the real file with one textual replacement, or an ``extends`` of it plus `snippet`."""
    folder = tmp_path / "config"
    folder.mkdir(exist_ok=True)
    target = folder / name
    if replace is not None:
        text = DEFAULT_CONFIG.read_text(encoding="utf-8")
        assert replace[0] in text, f"{replace[0]!r} is not in the default configuration"
        target.write_text(text.replace(*replace, 1), encoding="utf-8")
    else:
        target.write_text(f'extends = "{DEFAULT_CONFIG.as_posix()}"\n{snippet}', encoding="utf-8")
    return target


def refused(tmp_path: Path, match: str, snippet: str = "", replace: tuple[str, str] | None = None) -> None:
    with pytest.raises(ValueError, match=match):
        load_config(variant(tmp_path, snippet, replace))


# --- the default configuration -------------------------------------------------------------------------------------


def test_default_configuration_has_the_values_of_the_plan(cfg):
    assert isinstance(cfg, PipelineConfig) and cfg.project_root == PROJECT
    s = cfg.source
    assert s.cord_dir == PROJECT / "data" / "raw" / "cord" and s.cord_commit == COMMIT
    assert (s.max_shard_bytes, s.max_rows, s.max_image_pixels, s.max_gt_depth) == (600_000_000, 1000, 16_000_000, 8)
    assert s.arca_spec_path == PROJECT / "data" / "raw" / "arca" / "QRespecificaciones.pdf"
    assert (s.arca_spec_bytes, s.arca_spec_sha256) == (91626, HASH_PDF)
    assert {f.name: (f.split, f.bytes, f.sha256) for f in s.cord_files} == SHARDS
    assert dataclasses.astuple(s.expected_rows) == (800, 100, 100)   # the split sizes of CORD: 800, 100 and 100 receipts
    c = cfg.calendar
    assert (c.history_start, c.launch_last_day, c.test_first_day, c.test_last_day, c.weekdays_only) == (
        date(2026, 1, 5), date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 28), True)
    y = cfg.synth
    assert (y.seed_master, y.seed_train, y.seed_validation, y.seed_test) == (11, 101, 202, 303)
    assert (y.n_train, y.n_validation_known, y.n_validation_new, y.test_per_day_known, y.test_per_day_new) == (2000, 200, 100, 15, 15)
    assert (y.suppliers_known, y.suppliers_new_validation, y.suppliers_new_test) == (40, 8, 15)
    assert y.layouts_known == ("L1", "L2", "L3") and (y.layout_validation_new, y.layout_test_new) == ("L4", "L5")
    assert y.fonts_known == ("DejaVuSans", "DejaVuSansMono") and (y.font_validation_new, y.font_test_new) == ("DejaVuSansMono-Bold", "DejaVuSerif")
    assert y.dpi == 200 and dataclasses.astuple(y.scan_profiles) == (0.4, 0.4, 0.2) and y.qr_cm == (2.5, 3.0, 4.0)
    assert dataclasses.astuple(y.file_formats) == (0.3, 0.3, 0.4) and (y.max_lines, y.usd_share, y.iva105_share) == (12, 0.10, 0.20)
    assert y.planted_rate == 0.023 and y.cuit_serial_range == (1, 9999)
    q = cfg.qr
    assert q.readers == ("pyzbar", "aruco") and q.max_text_bytes == 1024 and q.hosts == ("www.arca.gob.ar", "www.afip.gob.ar") and q.path == "/fe/qr/"
    i = cfg.intake
    assert (i.max_file_bytes, i.max_pixels, i.formats, i.pdf_max_objects) == (10485760, 40_000_000, ("png", "jpeg", "pdf"), 200)
    m = cfg.model
    assert (m.kind, m.base, m.base_revision) == ("donut", "naver-clova-ix/donut-base-finetuned-cord-v2", REVISION)
    assert (m.task_cord, m.task_invoice, m.input_size, m.max_length) == ("<s_cord-v2>", "<s_factura_ar>", (1280, 960), 768)
    assert (m.epochs, m.candidate_epochs, m.learning_rate, m.warmup_steps) == (6, (2, 4, 6), 3e-5, 200)
    assert (m.batch_size, m.grad_accumulation, m.bf16, m.seed, m.cord_val_tolerance, m.infer_device) == (1, 8, True, 0, 2.0, "auto")
    a = cfg.match
    assert (a.price_tolerance_pct, a.qty_over_receipt, a.rounding_per_line, a.total_qr_tolerance) == (2.0, 0.0, 0.01, 0.01)
    assert a.supported_types == (1,) and a.require_qr is True
    e = cfg.erp
    assert (e.mode, e.base_url, e.token_env, e.post_as, e.timeout_s) == ("standin", "", "INVOICEFLOW_ERP_TOKEN", "draft", 30)
    assert (cfg.evaluation.resamples, cfg.evaluation.seed) == (2000, 0)
    k = cfg.criteria
    assert (k.c01_min_ted_diff, k.c02_min_line_exact, k.c03_min_line_exact, k.c04_min_qr_read) == (-2.0, 0.90, 0.80, 0.97)
    assert (k.c05_max_bad_post_upper, k.c06_min_straight_through, k.c07_min_review_recall) == (0.02, 0.85, 0.99)
    assert (cfg.service.host, cfg.service.port, cfg.service.token_env) == ("127.0.0.1", 8000, "INVOICEFLOW_API_TOKEN")
    assert cfg.site.max_total_bytes == 204800


def test_the_configuration_is_immutable(cfg):
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.match.price_tolerance_pct = 3.0  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.synth.scan_profiles.poor = 0.5  # type: ignore[misc]


# --- paths (D30, RNF05, AC33) --------------------------------------------------------------------------------------


def test_relative_paths_resolve_against_project_root_from_another_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_HOME, raising=False)
    monkeypatch.delenv(ENV_CONFIG, raising=False)
    for load in (lambda: load_config(PROJECT / "config" / "pipeline.toml"), lambda: load_config(Path("config") / "pipeline.toml"), load_config):
        cfg = load()
        assert cfg.project_root == PROJECT
        for field in dataclasses.fields(cfg.paths):
            path = getattr(cfg.paths, field.name)
            assert path.is_absolute() and PROJECT in path.parents, field.name
        for path in (cfg.source.cord_dir, cfg.source.arca_spec_path):
            assert path.is_absolute() and PROJECT in path.parents
        assert cfg.paths.warehouse == PROJECT / "data" / "warehouse" / "invoiceflow.sqlite"
        assert cfg.paths.erp_db == PROJECT / "data" / "erp" / "standin.sqlite"
        assert cfg.paths.seal == PROJECT / "reports" / "model_seal.json"
    # the working directory holds another copy's configuration: it is never read in silence
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "pipeline.toml").write_text("this is not the project's configuration", encoding="utf-8")
    assert load_config(Path("config") / "pipeline.toml").project_root == PROJECT


def test_a_relative_invoiceflow_config_is_taken_from_the_project_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_HOME, raising=False)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "pipeline.quick.toml").write_text("[evaluation]\nresamples = 999\n", encoding="utf-8")
    monkeypatch.setenv(ENV_CONFIG, "config/pipeline.quick.toml")
    cfg = load_config()
    assert cfg.evaluation.resamples == 100 and cfg.paths.warehouse == PROJECT / "data" / "quick" / "invoiceflow.sqlite"


def test_invoiceflow_home_moves_the_project_folder_and_must_exist(tmp_path, monkeypatch):
    home = tmp_path / "elsewhere"
    (home / "config").mkdir(parents=True)
    (home / "config" / "pipeline.toml").write_text(DEFAULT_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv(ENV_HOME, str(home))
    assert load_config(DEFAULT_CONFIG).paths.warehouse == home.resolve() / "data" / "warehouse" / "invoiceflow.sqlite"
    monkeypatch.setenv(ENV_CONFIG, "config/pipeline.toml")
    assert load_config().project_root == home.resolve()
    monkeypatch.setenv(ENV_HOME, str(tmp_path / "does-not-exist"))
    with pytest.raises(ValueError, match=ENV_HOME):
        load_config(DEFAULT_CONFIG)


def test_quick_configuration_keeps_every_path_under_data_quick(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(ENV_HOME, raising=False)
    monkeypatch.delenv(ENV_CONFIG, raising=False)
    cfg = load_config(PROJECT / "config" / "pipeline.quick.toml")
    quick = PROJECT / "data" / "quick"
    assert cfg.source.cord_dir == quick / "cord" and quick in cfg.source.arca_spec_path.parents
    assert (cfg.source.arca_spec_bytes, cfg.source.arca_spec_sha256) == (0, "")
    assert {f.split for f in cfg.source.cord_files} == {"train", "validation", "test"}
    assert all((f.bytes, f.sha256) == (0, "") for f in cfg.source.cord_files)
    assert dataclasses.astuple(cfg.source.expected_rows) == (16, 4, 4), "the tiny CORD of the tests"
    for field in dataclasses.fields(cfg.paths):
        assert quick in getattr(cfg.paths, field.name).parents, field.name
    default = load_config(DEFAULT_CONFIG)
    assert (cfg.synth.n_train, cfg.synth.n_validation_known, cfg.synth.n_validation_new, cfg.synth.dpi) == (24, 8, 4, 50)
    assert (cfg.synth.test_per_day_known, cfg.synth.test_per_day_new) == (3, 3)
    assert dataclasses.replace(cfg.synth, n_train=2000, n_validation_known=200, n_validation_new=100, dpi=200,
                               test_per_day_known=15, test_per_day_new=15) == default.synth
    assert cfg.calendar.test_last_day == date(2026, 10, 5) and dataclasses.replace(cfg.calendar, test_last_day=date(2026, 10, 28)) == default.calendar
    m = cfg.model
    assert (m.kind, m.epochs, m.candidate_epochs, m.input_size, m.max_length, m.bf16) == ("tiny", 2, (1, 2), (128, 96), 256, False)
    assert dataclasses.replace(m, kind="donut", epochs=6, candidate_epochs=(2, 4, 6), input_size=(1280, 960), max_length=768, bf16=True) == default.model
    assert cfg.evaluation.resamples == 100 and cfg.evaluation.seed == default.evaluation.seed
    assert (cfg.qr, cfg.intake, cfg.match, cfg.erp, cfg.criteria, cfg.service, cfg.site) == (
        default.qr, default.intake, default.match, default.erp, default.criteria, default.service, default.site)


def test_a_relative_path_cannot_leave_the_project_folder(tmp_path):
    refused(tmp_path, r"\[paths\] raw_dir.*sale de la carpeta del proyecto", replace=('raw_dir = "data/raw"', 'raw_dir = "../elsewhere"'))
    refused(tmp_path, r"\[paths\] seal.*sale de la carpeta", replace=('seal = "reports/model_seal.json"', 'seal = "data/../../x.json"'))
    refused(tmp_path, r"\[source\] cord_dir.*sale de la carpeta", snippet='[source]\ncord_dir = "../x"\n')


def test_a_dotdot_that_stays_inside_the_project_is_normalised_and_an_absolute_path_is_kept(tmp_path):
    path = variant(tmp_path, replace=('raw_dir = "data/raw"', 'raw_dir = "data/quick/../raw"'))
    assert load_config(path).paths.raw_dir == tmp_path / "data" / "raw"
    outside = (tmp_path / "outside").as_posix()
    path = variant(tmp_path, replace=('raw_dir = "data/raw"', f'raw_dir = "{outside}"'))
    assert load_config(path).paths.raw_dir == Path(outside)


def test_an_empty_path_is_refused(tmp_path):
    refused(tmp_path, r"\[paths\] figures_dir.*texto vacío", replace=('figures_dir = "docs/figures"', 'figures_dir = ""'))


# --- the seal's keys (D21, AC20) -----------------------------------------------------------------------------------


def test_every_config_key_is_fingerprinted_or_declared_unsealed():
    leaves = [leaf for leaf in config_leaves(PipelineConfig) if leaf != "project_root"]
    assert len(leaves) == len(set(leaves)) and len(leaves) > 100
    fingerprinted, unsealed = set(FINGERPRINT_KEYS), set(UNSEALED_KEYS)
    assert len(FINGERPRINT_KEYS) == len(fingerprinted) and len(UNSEALED_KEYS) == len(unsealed), "a key is listed twice"
    assert fingerprinted & unsealed == set(), "a key is both fingerprinted and declared unsealed"
    assert sorted(set(leaves) - fingerprinted - unsealed) == [], "a key is neither fingerprinted nor declared unsealed"
    assert sorted((fingerprinted | unsealed) - set(leaves)) == [], "a listed key is not in the configuration"
    # the plan, section 4: three keys of [source] and erp.post_as enter; every key of the model's and the generator's sections does too
    for section in ("calendar", "synth", "qr", "intake", "model", "match", "evaluation", "criteria"):
        assert {leaf for leaf in leaves if leaf.startswith(f"{section}.")} <= fingerprinted, section
    assert {leaf for leaf in fingerprinted if leaf.startswith("source.")} == {"source.cord_commit", "source.max_image_pixels", "source.max_gt_depth"}
    assert {leaf for leaf in fingerprinted if leaf.startswith("erp.")} == {"erp.post_as"}
    assert {leaf for leaf in leaves if leaf.startswith(("service.", "site.", "paths."))} <= unsealed
    assert {"source.cord_dir", "source.cord_files", "source.expected_rows.train", "source.expected_rows.validation", "source.expected_rows.test",
            "source.max_shard_bytes", "source.max_rows", "source.arca_spec_path", "source.arca_spec_bytes",
            "source.arca_spec_sha256", "erp.mode", "erp.base_url", "erp.token_env", "erp.timeout_s"} <= unsealed
    assert set(INFORMATIVE_KEYS) <= set(leaves), "a key declared informative is not in the configuration"


def test_the_sealed_view_holds_exactly_the_fingerprinted_keys_and_the_observed_hashes(cfg):
    shards = tuple(f.sha256 for f in cfg.source.cord_files)
    assert set(config_leaves(SealedConfig)) == set(FINGERPRINT_KEYS) | {"cord_sha256_seen", "arca_sha256_seen"}
    view = SealedConfig.of(cfg, shards, HASH_PDF)
    for key in FINGERPRINT_KEYS:
        found, original = view, cfg
        for part in key.split("."):
            found, original = getattr(found, part), getattr(original, part)
        assert found == original, key
    assert (view.cord_sha256_seen, view.arca_sha256_seen) == (shards, HASH_PDF)
    assert not any(hasattr(view, name) for name in ("paths", "site", "service")) and not hasattr(view.source, "cord_dir")
    assert not hasattr(view.erp, "mode") and not hasattr(view.erp, "base_url")
    for bad in (HASH_A.upper(), HASH_A[:-1], ""):
        with pytest.raises(ValueError, match="arca_sha256_seen"):
            SealedConfig.of(cfg, shards, bad)
        with pytest.raises(ValueError, match=r"cord_sha256_seen\[1\]"):
            SealedConfig.of(cfg, (shards[0], bad, *shards[2:]), HASH_PDF)
    with pytest.raises(ValueError, match="cord_sha256_seen.*6"):
        SealedConfig.of(cfg, shards[:5], HASH_PDF)


def test_a_model_kind_is_registered_and_forgotten_by_name(tmp_path):
    """``kind = "stub"`` is accepted only while the tests have registered it (extractors.register does, in U09); "tiny" only under data/quick/."""
    snippet = '[model]\nkind = "stub"\n'
    refused(tmp_path, r"\[model\] kind.*stub", snippet=snippet)
    config_module.register_model_kind("stub")
    try:
        assert load_config(variant(tmp_path, snippet=snippet)).model.kind == "stub"
    finally:
        config_module.unregister_model_kind("stub")
    refused(tmp_path, r"\[model\] kind.*stub", snippet=snippet)
    with pytest.raises(ValueError, match="donut"):
        config_module.register_model_kind("donut")


def test_a_tiny_model_is_only_for_a_configuration_whose_every_path_is_under_data_quick(tmp_path):
    refused(tmp_path, r"\[model\] kind.*tiny.*data/quick.*\[source\] cord_dir", snippet='[model]\nkind = "tiny"\n')
    quick = variant(tmp_path, name="pipeline.quick.toml", snippet="")
    quick.write_text((PROJECT / "config" / "pipeline.quick.toml").read_text(encoding="utf-8").replace(
        'extends = "pipeline.toml"', f'extends = "{DEFAULT_CONFIG.as_posix()}"'), encoding="utf-8")
    assert load_config(quick).model.kind == "tiny"
    # one path of the quick configuration put back outside data/quick/ is refused, whichever it is
    text = quick.read_text(encoding="utf-8")
    for key in ("seal", "readme", "site_dir", "erp_db", "figures_dir", "warehouse"):
        moved = re.sub(rf'^{key} = "data/quick/[^"]*"', f'{key} = "elsewhere/x"', text, flags=re.M)
        assert moved != text, key
        (tmp_path / "config" / "moved.toml").write_text(moved, encoding="utf-8")
        with pytest.raises(ValueError, match=rf"\[model\] kind.*tiny.*{key}"):
            load_config(tmp_path / "config" / "moved.toml")
    # the specification put back outside data/quick/ with its accepted size and hash, so only the tiny model's rule can refuse it
    moved = text.replace('arca_spec_bytes = 0', 'arca_spec_bytes = 91626').replace('arca_spec_sha256 = ""', f'arca_spec_sha256 = "{HASH_PDF}"')
    moved = moved.replace('arca_spec_path = "data/quick/arca/QRespecificaciones.pdf"', 'arca_spec_path = "data/raw/arca/QRespecificaciones.pdf"')
    (tmp_path / "config" / "moved.toml").write_text(moved, encoding="utf-8")
    with pytest.raises(ValueError, match=r"\[model\] kind.*tiny.*arca_spec_path"):
        load_config(tmp_path / "config" / "moved.toml")


# --- the sources (RF01, V01, V06) ----------------------------------------------------------------------------------


def shard_table(name: str, split: str, size: int, sha: str) -> str:
    return f'[[source.cord_files]]\nsplit = "{split}"\nname = "{name}"\nbytes = {size}\nsha256 = "{sha}"\n'


def test_the_six_files_are_a_list_of_tables_and_a_replacement_replaces_the_whole_list(tmp_path):
    only = shard_table("only.parquet", "train", 10, HASH_A) + shard_table("v.parquet", "validation", 10, HASH_A) + shard_table("t.parquet", "test", 10, HASH_A)
    cfg = load_config(variant(tmp_path, snippet=f"[source]\n{only}".replace("[source]\n[[", "[[")))
    assert [f.name for f in cfg.source.cord_files] == ["only.parquet", "v.parquet", "t.parquet"]


@pytest.mark.parametrize(("snippet", "match"), [
    (shard_table("a.parquet", "train", 10, HASH_A) + shard_table("b.parquet", "validation", 10, HASH_A), r"\[source\] cord_files.*test"),
    (shard_table("a.parquet", "train", 10, HASH_A) + shard_table("a.parquet", "validation", 10, HASH_A) + shard_table("t.parquet", "test", 10, HASH_A),
     r"\[source\] cord_files.*a\.parquet.*repetido"),
    (shard_table("a.parquet", "dev", 10, HASH_A) + shard_table("b.parquet", "validation", 10, HASH_A) + shard_table("t.parquet", "test", 10, HASH_A),
     r"\[source\] cord_files\[0\] split.*dev"),
    (shard_table("../a.parquet", "train", 10, HASH_A) + shard_table("b.parquet", "validation", 10, HASH_A) + shard_table("t.parquet", "test", 10, HASH_A),
     r"\[source\] cord_files\[0\] name"),
    (shard_table("..", "train", 10, HASH_A) + shard_table("b.parquet", "validation", 10, HASH_A) + shard_table("t.parquet", "test", 10, HASH_A),
     r"\[source\] cord_files\[0\] name"),
    (shard_table(".", "train", 10, HASH_A) + shard_table("b.parquet", "validation", 10, HASH_A) + shard_table("t.parquet", "test", 10, HASH_A),
     r"\[source\] cord_files\[0\] name"),
    (shard_table("sub/a.parquet", "train", 10, HASH_A) + shard_table("b.parquet", "validation", 10, HASH_A) + shard_table("t.parquet", "test", 10, HASH_A),
     r"\[source\] cord_files\[0\] name"),
    (shard_table("a\\\\b.parquet", "train", 10, HASH_A) + shard_table("b.parquet", "validation", 10, HASH_A) + shard_table("t.parquet", "test", 10, HASH_A),
     r"\[source\] cord_files\[0\] name"),
    (shard_table("", "train", 10, HASH_A) + shard_table("b.parquet", "validation", 10, HASH_A) + shard_table("t.parquet", "test", 10, HASH_A),
     r"\[source\] cord_files\[0\] name"),
    (shard_table("a.parquet", "train", 10, HASH_A.upper()) + shard_table("b.parquet", "validation", 10, HASH_A) + shard_table("t.parquet", "test", 10, HASH_A),
     r"\[source\] cord_files\[0\] sha256.*64 caracteres"),
    (shard_table("a.parquet", "train", 600_000_001, HASH_A) + shard_table("b.parquet", "validation", 10, HASH_A) + shard_table("t.parquet", "test", 10, HASH_A),
     r"\[source\] cord_files\[0\] bytes.*max_shard_bytes"),
    (shard_table("a.parquet", "train", 0, HASH_A) + shard_table("b.parquet", "validation", 10, HASH_A) + shard_table("t.parquet", "test", 10, HASH_A),
     r"\[source\] cord_files\[0\] bytes.*mayor que 0"),
    (shard_table("a.parquet", "train", 10, "") + shard_table("b.parquet", "validation", 10, HASH_A) + shard_table("t.parquet", "test", 10, HASH_A),
     r"\[source\] cord_files\[0\] sha256.*data/quick"),
    ("", r"\[source\] cord_files.*train"),
])
def test_the_list_of_sources_is_checked_file_by_file(tmp_path, snippet, match):
    body = f"[source]\n{snippet}".replace("[source]\n[[", "[[") if snippet else "[source]\ncord_files = []\n"
    refused(tmp_path, match, snippet=body)


def test_a_file_of_the_list_with_an_unknown_or_missing_key_is_named(tmp_path):
    three = shard_table("b.parquet", "validation", 10, HASH_A) + shard_table("t.parquet", "test", 10, HASH_A)
    extra = shard_table("a.parquet", "train", 10, HASH_A) + 'color = "red"\n'
    refused(tmp_path, r"\[source.cord_files\[0\]\].*'color'", snippet=extra + three)
    missing = '[[source.cord_files]]\nsplit = "train"\nname = "a.parquet"\nbytes = 10\n'
    refused(tmp_path, r"\[source.cord_files\[0\]\]: falta la clave 'sha256'", snippet=missing + three)


def test_an_empty_accepted_hash_is_allowed_only_for_sources_under_data_quick(tmp_path):
    for key, size_key in (("arca_spec_sha256", "arca_spec_bytes"),):
        empty = f'[source]\n{key} = ""\n{size_key} = 0\n'
        refused(tmp_path, rf"\[source\] {key}.*data/quick", snippet=empty)
        quick = tmp_path / "data" / "quick" / "x.pdf"
        assert load_config(variant(tmp_path, snippet=empty + f'arca_spec_path = "{quick.as_posix()}"\n')).source.arca_spec_sha256 == ""
        assert load_config(variant(tmp_path, snippet=empty + 'arca_spec_path = "data/quick/x.pdf"\n')).source.arca_spec_path == quick
        refused(tmp_path, rf"\[source\] {key}.*data/quick", snippet=empty + 'arca_spec_path = "data/quick_not/x.pdf"\n')
    # and the other way round: an accepted hash with an empty size, or a size with an empty hash, never goes together
    refused(tmp_path, r"\[source\] arca_spec_bytes.*mayor que 0", snippet="[source]\narca_spec_bytes = 0\n")
    refused(tmp_path, r"\[source\] arca_spec_bytes.*0", snippet='[source]\narca_spec_sha256 = ""\narca_spec_bytes = 5\narca_spec_path = "data/quick/x.pdf"\n')


@pytest.mark.parametrize("bad", [HASH_PDF.upper(), HASH_PDF[:-1], HASH_PDF + "0", "z" * 64, " " + HASH_PDF])
def test_an_accepted_hash_of_the_specification_is_64_lower_case_hex_characters(tmp_path, bad):
    refused(tmp_path, r"\[source\] arca_spec_sha256.*64 caracteres", snippet=f'[source]\narca_spec_sha256 = "{bad}"\n')


@pytest.mark.parametrize("snippet", ['[source]\ncord_commit = ""\n', '[source]\ncord_commit = "7f0115a4"\n', '[source]\ncord_commit = "' + "G" * 40 + '"\n'])
def test_the_dataset_commit_is_40_lower_case_hex_characters(tmp_path, snippet):
    refused(tmp_path, r"\[source\] cord_commit.*40 caracteres", snippet=snippet)


# --- the reserved layouts, fonts and days, the generator's shares (D10, D22, D05) ---------------------------------------


@pytest.mark.parametrize(("snippet", "match"), [
    ('[synth]\nlayout_test_new = "L1"\n', r"\[synth\] layout_test_new.*layouts_known"),
    ('[synth]\nlayout_validation_new = "L2"\n', r"\[synth\] layout_validation_new.*layouts_known"),
    ('[synth]\nlayout_validation_new = "L5"\n', r"\[synth\] layout_test_new.*layout_validation_new"),
    ('[synth]\nlayout_test_new = "L9"\n', r"\[synth\] layout_test_new.*L9"),
    ('[synth]\nlayouts_known = ["L1", "L1"]\n', r"\[synth\] layouts_known.*repetidos"),
    ('[synth]\nlayouts_known = []\n', r"\[synth\] layouts_known"),
    ('[synth]\nfont_test_new = "DejaVuSans"\n', r"\[synth\] font_test_new.*fonts_known"),
    ('[synth]\nfont_validation_new = "DejaVuSansMono"\n', r"\[synth\] font_validation_new.*fonts_known"),
    ('[synth]\nfont_validation_new = "DejaVuSerif"\n', r"\[synth\] font_test_new.*font_validation_new"),
    ('[synth]\nfonts_known = []\n', r"\[synth\] fonts_known"),
    ('[synth]\nfont_test_new = ""\n', r"\[synth\] font_test_new"),
    ("[synth]\nscan_profiles = { clean = 0.5, mild = 0.4, poor = 0.2 }\n", r"\[synth\] scan_profiles.*1"),
    ("[synth]\nscan_profiles = { clean = 1.2, mild = -0.2, poor = 0.0 }\n", r"\[synth\] scan_profiles.*mild"),
    ("[synth]\nfile_formats = { png = 0.3, jpeg = 0.3, pdf = 0.3 }\n", r"\[synth\] file_formats.*1"),
    ("[synth]\nfile_formats = { png = 0.3, jpeg = 0.3, pdf = 0.4, gif = 0.0 }\n", r"\[synth.file_formats\].*'gif'"),
    ("[synth]\nqr_cm = []\n", r"\[synth\] qr_cm"),
    ("[synth]\nqr_cm = [2.5, 0.0]\n", r"\[synth\] qr_cm"),
    ("[synth]\ncuit_serial_range = [0, 9999]\n", r"\[synth\] cuit_serial_range.*1 y 9999"),
    ("[synth]\ncuit_serial_range = [1, 10000]\n", r"\[synth\] cuit_serial_range.*1 y 9999"),
    ("[synth]\ncuit_serial_range = [500, 100]\n", r"\[synth\] cuit_serial_range.*ordenado"),
    ("[synth]\ncuit_serial_range = [1]\n", r"\[synth\] cuit_serial_range.*dos"),
    ("[synth]\ncuit_serial_range = [1, 50]\n", r"\[synth\] cuit_serial_range.*64"),
    ("[synth]\nplanted_rate = 0.08\n", r"\[synth\] planted_rate"),
    ("[synth]\nplanted_rate = -0.01\n", r"\[synth\] planted_rate"),
    ("[synth]\nusd_share = 1.5\n", r"\[synth\] usd_share"),
    ("[synth]\niva105_share = -0.1\n", r"\[synth\] iva105_share"),
    ("[synth]\ndpi = 49\n", r"\[synth\] dpi.*50 a 600"),
    ("[synth]\ndpi = 601\n", r"\[synth\] dpi.*50 a 600"),
    ("[synth]\nmax_lines = 0\n", r"\[synth\] max_lines"),
    ("[synth]\nmax_lines = 31\n", r"\[synth\] max_lines"),
    ("[synth]\nn_train = 0\n", r"\[synth\] n_train"),
    ("[synth]\nn_validation_known = 0\n", r"\[synth\] n_validation_known"),
    ("[synth]\nn_validation_new = 0\n", r"\[synth\] n_validation_new"),
    ("[synth]\ntest_per_day_known = 0\n", r"\[synth\] test_per_day_known"),
    ("[synth]\ntest_per_day_new = 0\n", r"\[synth\] test_per_day_new"),
    ("[synth]\nsuppliers_known = 0\n", r"\[synth\] suppliers_known"),
    ("[synth]\nsuppliers_new_validation = 0\n", r"\[synth\] suppliers_new_validation"),
    ("[synth]\nsuppliers_new_test = 0\n", r"\[synth\] suppliers_new_test"),
    ("[synth]\nseed_master = -1\n", r"\[synth\] seed_master"),
    ("[synth]\nseed_train = 4294967296\n", r"\[synth\] seed_train"),
    ("[synth]\nseed_validation = -1\n", r"\[synth\] seed_validation"),
    ("[synth]\nseed_test = 4294967296\n", r"\[synth\] seed_test"),
])
def test_the_generator_keys_are_checked_with_the_name_of_the_partition_they_protect(tmp_path, snippet, match):
    refused(tmp_path, match, snippet=snippet)


def test_a_missing_key_of_an_inline_table_is_named_when_the_whole_file_leaves_it_out(tmp_path):
    """An ``extends`` merges tables key by key, so the key is missing only in a file that stands alone."""
    refused(tmp_path, r"\[synth.file_formats\]: falta la clave 'pdf'",
            replace=("file_formats = { png = 0.3, jpeg = 0.3, pdf = 0.4 }", "file_formats = { png = 0.3, jpeg = 0.3 }"))
    refused(tmp_path, r"\[synth.scan_profiles\]: falta la clave 'poor'",
            replace=("scan_profiles = { clean = 0.4, mild = 0.4, poor = 0.2 }", "scan_profiles = { clean = 0.5, mild = 0.5 }"))


@pytest.mark.parametrize(("snippet", "match"), [
    ("[source]\nexpected_rows = { train = 0 }\n", r"\[source\] expected_rows.*train.*1 o más"),
    ("[source]\nexpected_rows = { validation = -4 }\n", r"\[source\] expected_rows.*validation"),
    ("[source]\nexpected_rows = { test = 0 }\n", r"\[source\] expected_rows.*test"),
    ("[source]\nexpected_rows = { dev = 1 }\n", r"\[source.expected_rows\].*'dev'"),
    ("[source]\nexpected_rows = { train = 800.5 }\n", r"\[source.expected_rows\] train.*un entero"),
    ('[source]\nexpected_rows = "800"\n', r"\[source.expected_rows\].*una tabla"),
])
def test_the_expected_rows_of_each_split_are_checked(tmp_path, snippet, match):
    refused(tmp_path, match, snippet=snippet)


def test_a_missing_split_of_the_expected_rows_is_named_when_the_whole_file_leaves_it_out(tmp_path):
    refused(tmp_path, r"\[source.expected_rows\]: falta la clave 'test'",
            replace=("expected_rows = { train = 800, validation = 100, test = 100 }", "expected_rows = { train = 800, validation = 100 }"))


def test_the_edges_of_the_generator_ranges_are_accepted(tmp_path):
    ok = ("[synth]\ncuit_serial_range = [1, 64]\nplanted_rate = 0.0769\nusd_share = 1.0\niva105_share = 0.0\ndpi = 50\nmax_lines = 30\nseed_test = 4294967295\n"
          "scan_profiles = { clean = 1.0, mild = 0.0, poor = 0.0 }\n")
    cfg = load_config(variant(tmp_path, snippet=ok))
    assert cfg.synth.cuit_serial_range == (1, 64) and cfg.synth.dpi == 50 and cfg.synth.scan_profiles.clean == 1.0


# --- the calendar (D22) -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("snippet", "match"), [
    ("[calendar]\ntest_first_day = 2026-09-30\n", r"\[calendar\] test_first_day.*launch_last_day"),
    ("[calendar]\ntest_last_day = 2026-09-30\n", r"\[calendar\] test_last_day.*test_first_day"),
    ("[calendar]\nhistory_start = 2026-09-30\n", r"\[calendar\] history_start.*launch_last_day"),
    ("[calendar]\nhistory_start = 2026-10-05\n", r"\[calendar\] history_start.*launch_last_day"),
    ("[calendar]\ntest_first_day = 2026-10-03\ntest_last_day = 2026-10-04\n", r"\[calendar\] test_first_day.*día hábil"),
    ('[calendar]\nhistory_start = "2026-01-05"\n', r"\[calendar\] history_start.*una fecha"),
    ("[calendar]\nweekdays_only = 1\n", r"\[calendar\] weekdays_only.*verdadero o falso"),
])
def test_the_calendar_is_checked_with_the_name_of_the_boundary(tmp_path, snippet, match):
    refused(tmp_path, match, snippet=snippet)


def test_a_weekend_only_test_range_is_accepted_when_weekdays_are_not_required(tmp_path):
    cfg = load_config(variant(tmp_path, snippet="[calendar]\nweekdays_only = false\ntest_first_day = 2026-10-03\ntest_last_day = 2026-10-04\n"))
    assert cfg.calendar.weekdays_only is False


# --- the model, the matching and the other sections ---------------------------------------------------------------------


@pytest.mark.parametrize(("snippet", "match"), [
    ('[model]\nkind = "gpt"\n', r"\[model\] kind.*gpt"),
    ('[model]\nbase = ""\n', r"\[model\] base"),
    ('[model]\nbase_revision = "main"\n', r"\[model\] base_revision.*40 caracteres"),
    ('[model]\ntask_cord = "cord"\n', r"\[model\] task_cord.*<s_"),
    ('[model]\ntask_invoice = "<s_cord-v2>"\n', r"\[model\] task_invoice.*task_cord"),
    ("[model]\ninput_size = [1280]\n", r"\[model\] input_size.*dos"),
    ("[model]\ninput_size = [1280, 0]\n", r"\[model\] input_size"),
    ("[model]\nmax_length = 0\n", r"\[model\] max_length"),
    ("[model]\nepochs = 0\n", r"\[model\] epochs"),
    ("[model]\ncandidate_epochs = []\n", r"\[model\] candidate_epochs"),
    ("[model]\ncandidate_epochs = [4, 2, 6]\n", r"\[model\] candidate_epochs.*crecientes"),
    ("[model]\ncandidate_epochs = [2, 2, 6]\n", r"\[model\] candidate_epochs.*crecientes"),
    ("[model]\ncandidate_epochs = [2, 4, 7]\n", r"\[model\] candidate_epochs.*epochs"),
    ("[model]\ncandidate_epochs = [0, 4]\n", r"\[model\] candidate_epochs"),
    ("[model]\nlearning_rate = 0.0\n", r"\[model\] learning_rate"),
    ("[model]\nwarmup_steps = -1\n", r"\[model\] warmup_steps"),
    ("[model]\nbatch_size = 0\n", r"\[model\] batch_size"),
    ("[model]\ngrad_accumulation = 0\n", r"\[model\] grad_accumulation"),
    ("[model]\nseed = -1\n", r"\[model\] seed"),
    ("[model]\ncord_val_tolerance = -0.1\n", r"\[model\] cord_val_tolerance"),
    ('[model]\ninfer_device = "tpu"\n', r"\[model\] infer_device.*tpu"),
    ('[qr]\nreaders = []\n', r"\[qr\] readers"),
    ('[qr]\nreaders = ["pyzbar", "pyzbar"]\n', r"\[qr\] readers.*repetidos"),
    ('[qr]\nreaders = ["zxing"]\n', r"\[qr\] readers.*zxing"),
    ("[qr]\nmax_text_bytes = 0\n", r"\[qr\] max_text_bytes"),
    ("[qr]\nhosts = []\n", r"\[qr\] hosts"),
    ('[qr]\nhosts = ["https://www.arca.gob.ar"]\n', r"\[qr\] hosts.*https"),
    ('[qr]\nhosts = ["WWW.ARCA.GOB.AR"]\n', r"\[qr\] hosts"),
    ('[qr]\npath = "fe/qr/"\n', r"\[qr\] path.*/"),
    ('[qr]\npath = "/fe/qr"\n', r"\[qr\] path.*/"),
    ("[intake]\nmax_file_bytes = 0\n", r"\[intake\] max_file_bytes"),
    ("[intake]\nmax_pixels = 0\n", r"\[intake\] max_pixels"),
    ('[intake]\nformats = []\n', r"\[intake\] formats"),
    ('[intake]\nformats = ["png", "gif"]\n', r"\[intake\] formats.*gif"),
    ("[intake]\npdf_max_objects = 0\n", r"\[intake\] pdf_max_objects"),
    ("[match]\nprice_tolerance_pct = -0.5\n", r"\[match\] price_tolerance_pct"),
    ("[match]\nqty_over_receipt = -1.0\n", r"\[match\] qty_over_receipt"),
    ("[match]\nrounding_per_line = -0.01\n", r"\[match\] rounding_per_line"),
    ("[match]\ntotal_qr_tolerance = -0.01\n", r"\[match\] total_qr_tolerance"),
    ("[match]\nsupported_types = []\n", r"\[match\] supported_types"),
    ("[match]\nsupported_types = [1, 1]\n", r"\[match\] supported_types.*repetidos"),
    ("[match]\nsupported_types = [0]\n", r"\[match\] supported_types"),
    ('[erp]\nmode = "sap"\n', r"\[erp\] mode.*sap"),
    ('[erp]\nmode = "http"\n', r"\[erp\] base_url.*http"),
    ('[erp]\nmode = "http"\nbase_url = "ftp://x"\n', r"\[erp\] base_url.*http"),
    ('[erp]\npost_as = "submitted"\n', r"\[erp\] post_as.*draft"),
    ('[erp]\ntoken_env = "my token"\n', r"\[erp\] token_env"),
    ("[erp]\ntimeout_s = 0\n", r"\[erp\] timeout_s"),
    ("[evaluation]\nresamples = 0\n", r"\[evaluation\] resamples"),
    ("[evaluation]\nseed = -1\n", r"\[evaluation\] seed"),
    ("[criteria]\nc01_min_ted_diff = 0.5\n", r"\[criteria\] c01_min_ted_diff"),
    ("[criteria]\nc02_min_line_exact = 0.0\n", r"\[criteria\] c02_min_line_exact"),
    ("[criteria]\nc03_min_line_exact = 1.5\n", r"\[criteria\] c03_min_line_exact"),
    ("[criteria]\nc04_min_qr_read = 0.0\n", r"\[criteria\] c04_min_qr_read"),
    ("[criteria]\nc05_max_bad_post_upper = 0.0\n", r"\[criteria\] c05_max_bad_post_upper"),
    ("[criteria]\nc05_max_bad_post_upper = 1.0\n", r"\[criteria\] c05_max_bad_post_upper"),
    ("[criteria]\nc06_min_straight_through = 1.5\n", r"\[criteria\] c06_min_straight_through"),
    ("[criteria]\nc07_min_review_recall = 0.0\n", r"\[criteria\] c07_min_review_recall"),
    ('[service]\nhost = ""\n', r"\[service\] host"),
    ("[service]\nport = 0\n", r"\[service\] port"),
    ("[service]\nport = 65536\n", r"\[service\] port"),
    ('[service]\ntoken_env = "x y"\n', r"\[service\] token_env"),
    ("[site]\nmax_total_bytes = 0\n", r"\[site\] max_total_bytes"),
])
def test_every_limit_and_name_of_the_other_sections_is_checked_naming_the_key(tmp_path, snippet, match):
    refused(tmp_path, match, snippet=snippet)


def test_the_edges_of_the_other_ranges_are_accepted(tmp_path):
    ok = ("[match]\nprice_tolerance_pct = 0.0\nrounding_per_line = 0.0\n[criteria]\nc01_min_ted_diff = 0.0\nc05_max_bad_post_upper = 0.999\n"
          "[service]\nport = 65535\n[model]\ncord_val_tolerance = 0.0\nwarmup_steps = 0\ncandidate_epochs = [6]\n"
          '[erp]\nmode = "http"\nbase_url = "http://erp.internal:8080"\n')
    cfg = load_config(variant(tmp_path, snippet=ok))
    assert cfg.match.price_tolerance_pct == 0.0 and cfg.service.port == 65535 and cfg.model.candidate_epochs == (6,) and cfg.erp.mode == "http"


# --- mistakes in the file are named (AC32) -----------------------------------------------------------------------------


def test_unknown_key_is_rejected_with_its_name(tmp_path):
    refused(tmp_path, r"\[match\].*'price_tolerance'", snippet="[match]\nprice_tolerance = 2.0\n")
    refused(tmp_path, r"\[evaluation\].*'resample'", snippet="[evaluation]\nresample = 500\n")
    refused(tmp_path, r"\[site\].*'cors'", snippet='[site]\ncors = "*"\n')


def test_unknown_section_missing_section_and_missing_key_are_named(tmp_path):
    refused(tmp_path, r"sección desconocida \[extras\]", snippet="[extras]\nx = 1\n")
    text = DEFAULT_CONFIG.read_text(encoding="utf-8")
    without_site = re.sub(r"\[site\].*?(?=\[paths\])", "", text, flags=re.S)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "a.toml").write_text(without_site, encoding="utf-8")
    with pytest.raises(ValueError, match=r"falta la sección \[site\]"):
        load_config(tmp_path / "config" / "a.toml")
    refused(tmp_path, r"\[evaluation\]: falta la clave 'resamples'", replace=("resamples = 2000", ""))


@pytest.mark.parametrize(("snippet", "match"), [
    ('[match]\nprice_tolerance_pct = "2"\n', r"\[match\] price_tolerance_pct.*un número"),
    ("[match]\nrequire_qr = 1\n", r"\[match\] require_qr.*verdadero o falso"),
    ("[match]\nsupported_types = 1\n", r"\[match\] supported_types.*una lista"),
    ('[match]\nsupported_types = ["1"]\n', r"\[match\] supported_types.*un entero"),
    ("[evaluation]\nresamples = 2000.5\n", r"\[evaluation\] resamples.*un entero"),
    ("[evaluation]\nresamples = true\n", r"\[evaluation\] resamples.*un entero"),
    ("[model]\nlearning_rate = 'fast'\n", r"\[model\] learning_rate.*un número"),
    ("[model]\nbf16 = 'yes'\n", r"\[model\] bf16.*verdadero o falso"),
    ("[synth]\nqr_cm = 2.5\n", r"\[synth\] qr_cm.*una lista"),
    ('[synth]\nscan_profiles = "clean"\n', r"\[synth.scan_profiles\].*una tabla"),
    ('[synth]\nfile_formats = { png = "a", jpeg = 0.3, pdf = 0.4 }\n', r"\[synth.file_formats\] png.*un número"),
    ("[calendar]\ntest_last_day = 2026-10-28T00:00:00\n", r"\[calendar\] test_last_day.*una fecha"),
    ("[paths]\nraw_dir = 3\n", r"\[paths\] raw_dir.*ruta"),
    ("[source]\ncord_files = 3\n", r"\[source\] cord_files.*una lista"),
    ("[source]\ncord_files = [3]\n", r"\[source.cord_files\[0\]\].*una tabla"),
])
def test_a_value_of_the_wrong_type_is_named(tmp_path, snippet, match):
    refused(tmp_path, match, snippet=snippet)


def test_extends_cycle_and_missing_base_are_refused(tmp_path):
    folder = tmp_path / "config"
    folder.mkdir()
    (folder / "a.toml").write_text('extends = "b.toml"\n', encoding="utf-8")
    (folder / "b.toml").write_text('extends = "a.toml"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="ciclo"):
        load_config(folder / "a.toml")
    (folder / "c.toml").write_text('extends = "nothing.toml"\n', encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        load_config(folder / "c.toml")
    with pytest.raises(FileNotFoundError):
        load_config(folder / "absent.toml")
    (folder / "d.toml").write_text("extends = 3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="extends"):
        load_config(folder / "d.toml")
