"""The peak memory of the process, with the name of its counter, and the peak of the GPU when torch is loaded (U19; RNF03, D39): every task of every run records its peaks with their counter, and a peak
that cannot be measured never discards what a task did."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from invoiceflow import pipeline, sysinfo
from invoiceflow.sysinfo import MemoryPeaks, peak_memory
from tests.fixtures.pipeline_state import copy_state


def test_the_peak_is_the_working_set_on_windows_and_ru_maxrss_elsewhere_with_its_counter_named():
    """RNF03, D39: the peak is ``PeakWorkingSetSize`` on Windows and ``ru_maxrss`` elsewhere (``PeakPagefileUsage`` informative, only on Windows)."""
    peaks = peak_memory()
    assert isinstance(peaks, MemoryPeaks) and peaks.working_set_mb > 0
    assert peaks.counter == ("PeakWorkingSetSize" if sys.platform == "win32" else "ru_maxrss")
    assert (peaks.commit_mb is not None and peaks.commit_mb > 0) == (sys.platform == "win32")


def test_a_system_that_reports_no_peak_is_an_error(monkeypatch):
    """The counter is read, not assumed: a zero from the system is an ``OSError`` with its reason, and never a peak of 0 MB."""
    monkeypatch.setattr(sysinfo, "_windows_peaks", lambda: (0, 0), raising=False)
    monkeypatch.setattr(sysinfo, "_posix_peak", lambda: 0, raising=False)
    with pytest.raises(OSError, match="no informó el pico de memoria"):
        peak_memory()


def test_the_gpu_peak_is_torchs_when_it_is_loaded_and_cuda_is_there_and_never_imports_torch(monkeypatch):
    """RNF03: ``torch.cuda.max_memory_allocated`` in megabytes, read only if torch is already loaded (the pipeline module never loads it for this), and nothing otherwise."""
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    assert peak_memory().gpu_mb is None and "torch" not in sys.modules, "asking for the peak does not load torch"
    cuda = SimpleNamespace(is_available=lambda: True, max_memory_allocated=lambda: 3 * 1024 * 1024)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=cuda))
    assert peak_memory().gpu_mb == 3.0
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False, max_memory_allocated=lambda: 1)))
    assert peak_memory().gpu_mb is None
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: (_ for _ in ()).throw(RuntimeError("no driver")), max_memory_allocated=lambda: 1)))
    assert peak_memory().gpu_mb is None


def test_every_task_of_every_run_of_the_flow_names_its_counter(pipeline_replayed):
    """AC29, RNF03: in the state of every run of a whole replay, each task that ran says its peak with the counter's name, and the closed runs say it in SQLite too."""
    cfg, counter = pipeline_replayed.cfg, peak_memory().counter
    states = sorted((Path(cfg.paths.interim_dir) / "runs").glob("*.json"))
    assert len(states) == 5
    tasks = [(path.stem, task_id, task) for path in states for task_id, task in json.loads(path.read_text(encoding="utf-8"))["tasks"].items()]
    assert len(tasks) == 5 * len(pipeline.TASK_IDS) and all(task["memory_counter"] == counter and task["peak_working_set_mb"] > 0 and task["seconds"] >= 0 for _, _, task in tasks)
    import sqlite3

    rows = sqlite3.connect(cfg.paths.warehouse).execute("SELECT memory_counter, peak_working_set_mb FROM pipeline_runs").fetchall()
    assert len(rows) == 5 and all(counter == c and mb > 0 for c, mb in rows)


def test_a_task_whose_peak_cannot_be_measured_keeps_its_result(pipeline_launched, tmp_path, monkeypatch):
    """Telemetry never discards what a task did: when the peak cannot be read, the task's result is saved with no peak and no counter."""
    def refuses() -> MemoryPeaks:
        raise OSError("the system will not say")

    monkeypatch.setattr(pipeline, "peak_memory", refuses)
    state = copy_state(pipeline_launched, tmp_path / "state")
    result = pipeline.run_task("fetch_source", "S2", state.cfg)
    assert result["peak_working_set_mb"] is None and result["peak_commit_mb"] is None and result["peak_gpu_mb"] is None and result["memory_counter"] is None and result["source_sha256"]
    saved = json.loads((Path(state.cfg.paths.interim_dir) / "runs" / "S2.json").read_text(encoding="utf-8"))["tasks"]["fetch_source"]
    assert saved["memory_counter"] is None and saved["files"] == result["files"]
