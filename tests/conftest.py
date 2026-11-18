"""Shared fixtures, the automatic skipping of the marks whose requirement is missing and the fast suite's time limit (plan, section 24.1).

The fixtures of the units that come later (the synthetic invoices, the test extractor registered by ``extractors.register``, the tiny model, the
loaded warehouses, the launch and the replayed days, the site files and the rendered design) are added by the unit that writes the code they need.
"""

from __future__ import annotations

import gc
import json
import os
import shutil
from pathlib import Path

import pytest

from tests.fixtures.suite_budget import pytest_addoption, pytest_sessionfinish, pytest_sessionstart  # noqa: F401  (the time limit, D31)

pytest_plugins = ["pytester"]

# The session fixtures keep the fixtures' frames, their warehouses and the launches alive for the whole run, and every full collection of the garbage
# collector walks all of them again. The thresholds are raised so that it runs far less often; garbage in cycles is still freed, only later. It
# changes no value and no test (project 04, D93).
gc.set_threshold(200_000, 100, 100)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_CORD = PROJECT_ROOT / "data" / "raw" / "cord"
CHECKPOINT_CACHE = Path.home() / ".cache" / "huggingface" / "hub" / "models--naver-clova-ix--donut-base-finetuned-cord-v2"


@pytest.fixture(scope="session", autouse=True)
def _no_flush_to_the_device():
    """``os.fsync`` makes a file survive a power cut, which no test can cause, and it takes a good part of the time of a suite that writes many
    small files (every atomic write, the registry and the lock call it; project 06's speed unit). The session runs without it; a test that is about
    the flush replaces ``os.fsync`` with its own spy, which wins over this one."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(os, "fsync", lambda descriptor: None)
        yield


def _cuda_missing() -> bool:
    try:
        import torch
    except ImportError:
        return True
    return not torch.cuda.is_available()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip, instead of failing, the tests whose external requirement is missing (they are not selected by default either). The checks run only for
    a mark that a selected test carries, so a default session never imports torch for them."""
    needs = {
        "real_data": (lambda: not any(RAW_CORD.glob("*.parquet")), "the real CORD Parquet files are not in data/raw/cord/"),
        "model": (lambda: not CHECKPOINT_CACHE.exists(), "the pinned Donut checkpoint is not in the Hugging Face cache"),
        "gpu": (_cuda_missing, "CUDA is not available"),
        "docker": (lambda: shutil.which("docker") is None, "docker is not installed"),
        "erpnext": (lambda: shutil.which("docker") is None, "docker is not installed (the ERPNext stack runs in it)"),
    }
    cache: dict[str, bool] = {}
    for item in items:
        for mark, (missing, reason) in needs.items():
            if mark in item.keywords:
                cache.setdefault(mark, missing())
                if cache[mark]:
                    item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(scope="session")
def project_root() -> Path:
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def cfg():
    """The default configuration, loaded once (it is immutable)."""
    from invoiceflow.config import load_config

    return load_config(PROJECT_ROOT / "config" / "pipeline.toml")


@pytest.fixture(scope="session")
def quick_cfg():
    """The quick-mode configuration, loaded once."""
    from invoiceflow.config import load_config

    return load_config(PROJECT_ROOT / "config" / "pipeline.quick.toml")


@pytest.fixture(scope="session")
def cord_fixture(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """The tiny CORD (16, 4 and 4 receipts), written once per session outside the working tree: split -> path. Tests that change anything copy it
    (``tests.fixtures.make_cord_fixture.make_malformed`` does)."""
    from tests.fixtures.make_cord_fixture import make_cord_fixture

    return make_cord_fixture(tmp_path_factory.mktemp("cord") / "cord")


@pytest.fixture(scope="session")
def view(cfg):
    """The sealed view of the default configuration with placeholder hashes of the sources: what the generator and the match read does not depend on them."""
    from invoiceflow.config import SealedConfig

    return SealedConfig.of(cfg, tuple("0" * 64 for _ in cfg.source.cord_files), "0" * 64)


@pytest.fixture(scope="session")
def quick_view(quick_cfg):
    """The sealed view of the quick configuration, with placeholder hashes."""
    from invoiceflow.config import SealedConfig

    return SealedConfig.of(quick_cfg, tuple("0" * 64 for _ in quick_cfg.source.cord_files), "0" * 64)


@pytest.fixture(scope="session")
def default_master(view):
    """The master of the default configuration (63 suppliers, about 4,400 orders), made once: it is a pure function of the view."""
    from invoiceflow.synth.master import master

    return master(view)


@pytest.fixture(scope="session")
def quick_master(quick_view):
    """The master of the quick configuration, made once."""
    from invoiceflow.synth.master import master

    return master(quick_view)


@pytest.fixture(scope="session")
def tiny_truths(quick_view, quick_master, tmp_path_factory):
    """The eight invoices of ``validation_known`` of the quick configuration, made once: what the tiny model is trained and read on."""
    from invoiceflow.synth.invoices import invoices

    return invoices(quick_view, quick_master, "validation_known", None, seal_path=tmp_path_factory.mktemp("seal") / "none.json")


@pytest.fixture
def tiny_kit(quick_view, tiny_truths):
    """A fresh tiny processor, model and training items (``tests/fixtures/tiny_kit.py``): 8 synthetic invoices and 4 CORD-like receipts. Torch is imported here, once per session."""
    from tests.fixtures.tiny_kit import make_kit

    return make_kit(quick_view, tiny_truths, extra_cord=4)


@pytest.fixture(scope="session")
def evaluated(quick_view, tmp_path_factory):
    """The quick world's three days issued, the model registered, the launch sealed and the close evaluated, once per session (``tests/fixtures/evaluated.py``): what the star model, the design and
    the site files are made from. Read-only for the tests: one that changes anything copies it."""
    from tests.fixtures.evaluated import build_evaluated

    return build_evaluated(quick_view, tmp_path_factory.mktemp("evaluated"))


@pytest.fixture(scope="session")
def star_export(evaluated, tmp_path_factory):
    """The star model exported from ``evaluated`` into a folder of the session: ``.out`` (the Parquet files), ``.paths``, ``.run`` and the state it was made from."""
    from types import SimpleNamespace

    from invoiceflow.dashboard.semantic_model import MODEL
    from invoiceflow.export import export_star_schema

    out = tmp_path_factory.mktemp("star") / "powerbi"
    paths = export_star_schema(evaluated.conn, out, MODEL, evaluated.run, evaluated.view, seal_path=evaluated.seal)
    return SimpleNamespace(out=out, paths=paths, run=evaluated.run, view=evaluated.view, db=evaluated.db, conn=evaluated.conn, folder=evaluated.root, state=evaluated)


@pytest.fixture(scope="session")
def rendered_html(star_export, tmp_path_factory):
    """The HTML design rendered once from ``star_export``: ``.path`` and ``.html``."""
    from types import SimpleNamespace

    from invoiceflow.dashboard.render import render_design
    from invoiceflow.dashboard.semantic_model import MODEL

    path = render_design(MODEL, star_export.out, tmp_path_factory.mktemp("design") / "powerbi-design.html", star_export.run)
    return SimpleNamespace(path=path, html=path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def site_files(star_export, tmp_path_factory):
    """The five site files exported once from ``evaluated`` (the star model is already in its warehouse): ``.out``, ``.paths``, ``.site`` (the verified documents) and ``.cord_index``."""
    from types import SimpleNamespace

    import dataclasses

    from invoiceflow.cord import build_index
    from invoiceflow.site_export import export_site, read_site

    out = tmp_path_factory.mktemp("site") / "site"
    state = star_export.state
    index = build_index(state.cfg, {e.name: "0" * 64 for e in state.cfg.source.cord_files})
    paths = export_site(state.conn, out, state.run, state.view, seal_path=state.seal, max_total_bytes=204800, cord_index=index, expected_rows=dataclasses.asdict(state.cfg.source.expected_rows))
    return SimpleNamespace(out=out, paths=paths, site=read_site(out), cord_index=index, state=state)


@pytest.fixture(scope="session")
def figures_written(site_files, tmp_path_factory):
    """The figures of the documentation drawn once from ``site_files``."""
    from types import SimpleNamespace

    from invoiceflow.figures import write_figures

    out = tmp_path_factory.mktemp("figures") / "figures"
    return SimpleNamespace(out=out, paths=write_figures(site_files.out, out))


@pytest.fixture
def before_the_seal(site_files, tmp_path):
    """The warehouse and the seal as they were before the close: the launch is there, the evaluation is not."""
    state = site_files.state
    copy = tmp_path / "warehouse.sqlite"
    shutil.copy(state.db, copy)
    from invoiceflow.warehouse import connect

    conn = connect(copy)
    for table in ("eval_criteria", "eval_invoice", "eval_field", "eval_baseline", "eval_cord", "eval_variants"):
        for kind in ("no_delete",):
            conn.execute(f"DROP TRIGGER IF EXISTS {table}_{kind}")
        conn.execute(f"DELETE FROM {table}")
    for name in ("star_fact_invoice", "star_fact_field_read", "star_fact_qr_read", "star_fact_baseline", "star_fact_cord_eval", "star_fact_evaluation_summary"):
        conn.execute(f"DELETE FROM {name}")
    seal = tmp_path / "model_seal.json"
    document = json.loads(state.seal.read_text(encoding="utf-8"))
    document.pop("test")
    seal.write_text(json.dumps(document), encoding="utf-8")
    return conn, seal, state


