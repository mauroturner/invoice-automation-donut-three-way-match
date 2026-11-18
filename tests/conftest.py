"""Shared fixtures, the automatic skipping of the marks whose requirement is missing and the fast suite's time limit (plan, section 24.1).

The fixtures of the units that come later (the synthetic invoices, the test extractor registered by ``extractors.register``, the tiny model, the
loaded warehouses, the launch and the replayed days, the site files and the rendered design) are added by the unit that writes the code they need.
"""

from __future__ import annotations

import gc
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
