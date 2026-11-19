"""States of the pipeline in quick mode, made once per session and copied by the tests that change anything (U19; the session states of project 06's L79): a project folder with its configuration and the tiny
CORD, the launch run, the three daily runs and the close. A copy is a folder: its configuration is read from it, so its paths are its own."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from invoiceflow import pipeline
from invoiceflow.config import PipelineConfig, load_config
from tests.fixtures.projects import make_project

CONFIG_NAME = "test.toml"


@dataclass
class PipelineState:
    root: Path
    cfg: PipelineConfig
    results: list[dict] = field(default_factory=list)       # the outcome of each run made so far, in order ([{"task": ..., "result": ...}, ...])


def fresh_project(root: Path) -> PipelineState:
    """A clean quick clone: the configuration files, the tiny CORD and the stand-in of ARCA's PDF under ``data/quick``; nothing has run."""
    project = make_project(Path(root), accepted=False)
    return PipelineState(Path(root), project.cfg)


def written(root: Path) -> dict[str, tuple[int, int]]:
    """Every file under `root` with its size and its modification time in nanoseconds: two calls give the same answer only if no file was created, removed or written in between (a write
    moves the time even when it leaves the same bytes), and it reads no file's bytes (A2 of the evaluation: hashing whole states was a good part of the suite)."""
    found = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            facts = path.stat()
            found[path.relative_to(root).as_posix()] = (facts.st_size, facts.st_mtime_ns)
    return found


def copy_state(state: PipelineState, dest: Path) -> PipelineState:
    """A copy of the folder of `state`, read with its own configuration."""
    dest = Path(dest)
    shutil.copytree(state.root, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return PipelineState(dest, load_config(dest / "config" / CONFIG_NAME), list(state.results))


def run_next(state: PipelineState, **options) -> list[dict]:
    """One run of the next pending delivery over the folder of `state`."""
    outcome = pipeline.run_all(state.cfg, **options)
    state.results.append(outcome)
    return outcome


def result_of(outcome: list[dict], task: str) -> dict:
    return next(step["result"] for step in outcome if step["task"] == task)


def stage(state: PipelineState, runs: int) -> PipelineState:
    """Run `runs` pending deliveries, one after another, over `state` and return it."""
    for _ in range(runs):
        run_next(state)
    return state


def clean_clone(root: Path) -> PipelineState:
    """What a clone has that the commands read: ``config/`` (and a ``test.toml`` that is the quick configuration, for the tests that name their configuration file). No data, no fixture."""
    from tests.e2e.cli_helpers import PROJECT_ROOT

    root = Path(root)
    shutil.copytree(PROJECT_ROOT / "config", root / "config")
    (root / "config" / CONFIG_NAME).write_text('extends = "pipeline.quick.toml"\n', encoding="utf-8", newline="\n")
    return PipelineState(root, load_config(root / "config" / CONFIG_NAME))


def launched_through_the_cli(root: Path, elsewhere: Path) -> tuple[PipelineState, dict]:
    """A clean clone, the tiny CORD and the launch, all through ``cli.main`` with ``$INVOICEFLOW_HOME`` on the clone and the working directory somewhere else (AC33): the fingerprint of the
    project folder and of the clone's before and after is what the quick-mode test reads."""
    import os

    import pytest

    from tests.e2e.cli_helpers import PROJECT_ROOT, cli, fingerprint

    state = clean_clone(root)
    Path(elsewhere).mkdir(parents=True, exist_ok=True)
    evidence = {"project_before": fingerprint(PROJECT_ROOT), "clone_before": fingerprint(state.root)}
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("INVOICEFLOW_HOME", str(state.root))
        patch.chdir(elsewhere)
        evidence["fixture"] = cli("make-fixture", "data/quick")
        evidence["launch"] = cli("replay", "--until", "L2026-09-30", "--config", f"config/{CONFIG_NAME}")
        evidence["cwd"] = Path(os.getcwd())
    evidence.update(project_after=fingerprint(PROJECT_ROOT), clone_after=fingerprint(state.root), elsewhere=fingerprint(elsewhere))
    return state, evidence
