"""The quick mode, isolated (U19; AC33; plan, sections 4 and 24.1; D30; lesson B3 of project 01): from a clean clone and from another directory the quick run ends, and nothing outside ``data/quick/``
changes, neither in the clone nor in the project folder of the tests. The run is the session's launch of ``quick_launch`` (``make-fixture`` and ``replay --until`` through the command line, ``$INVOICEFLOW_HOME``
on the clone and the working directory elsewhere); the days and the close are the same quick mode, in the states built from it."""

from __future__ import annotations

from pathlib import Path


def test_quick_replay_from_other_cwd_touches_only_data_quick(quick_launch):
    evidence, state = quick_launch.evidence, quick_launch.state
    assert evidence["fixture"].code == 0 and evidence["launch"].code == 0 and "L2026-09-30" in evidence["launch"].out, (evidence["fixture"].err, evidence["launch"].err)
    assert Path(evidence["cwd"]) != state.root
    assert evidence["project_before"] == evidence["project_after"], "the project folder of the tests is not touched by a quick run in a clone"
    before, after = evidence["clone_before"], evidence["clone_after"]
    assert {k: v for k, v in after.items() if not k.startswith("data/quick/")} == before and {k for k in after if k.startswith("data/quick/")}, "only data/quick/ changed in the clone"
    assert evidence["elsewhere"] == {}, "nothing was written in the working directory the command ran from"
    for field in ("raw_dir", "interim_dir", "warehouse", "models_dir", "erp_db", "outputs_dir", "site_dir", "reports_dir", "seal", "dashboard_html"):
        path = Path(getattr(state.cfg.paths, field))
        assert (state.root / "data" / "quick") in path.parents, field
    assert Path(state.cfg.paths.warehouse).is_file() and Path(state.cfg.paths.seal).is_file() and Path(state.cfg.paths.dashboard_html).is_file()


def test_the_quick_mode_draws_at_a_resolution_where_the_reader_sees_the_qr(quick_cfg):
    """L101: at 50 dpi no reader sees the QR, so every quick invoice went to review for its QR. The quick configuration draws one 4 cm code per clean page at 150 dpi; the whole quick run
    reading every printed QR is ``tests/integration/test_pipeline.py``'s."""
    assert quick_cfg.synth.dpi >= 150 and quick_cfg.synth.qr_cm == (4.0,) and quick_cfg.synth.scan_profiles.clean == 1.0
