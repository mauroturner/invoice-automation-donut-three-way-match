"""The command line (U19; RF25; AC30, AC32; plan, section 22): its exit codes, an incompatible combination that exits with 2 before writing, every documented option changing the result, and every key of the
configuration changing something or being declared informative. The runs are the session's states, copied."""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import pytest

from invoiceflow import cli as cli_module
from invoiceflow import pipeline
from invoiceflow.config import ENV_CONFIG, ENV_HOME, FINGERPRINT_KEYS, INFORMATIVE_KEYS, UNSEALED_KEYS
from invoiceflow.registry import config_fingerprint
from tests.e2e.cli_helpers import PROJECT_ROOT, cli, config_of
from tests.fixtures.pipeline_state import copy_state, fresh_project


def runs_of(state) -> list[str]:
    folder = Path(state.cfg.paths.interim_dir) / "runs"
    return sorted(p.stem for p in folder.iterdir()) if folder.is_dir() else []


def tree(state) -> dict[str, bytes]:
    return {p.relative_to(state.root).as_posix(): p.read_bytes() for p in sorted(state.root.rglob("*")) if p.is_file()}


# --- the documented options: each one has its case ------------------------------------------------------------------------------


def documented_options() -> set[str]:
    """Every option the parsers document, by command (``command option``; a positional by its name), without the help of argparse."""
    parser = cli_module.build_parser(pipeline.TASK_IDS)
    found = set()
    for name, sub in next(a for a in parser._actions if a.dest == "command").choices.items():
        for action in sub._actions:
            if action.option_strings and action.option_strings != ["-h", "--help"]:
                found.add(f"{name} {max(action.option_strings, key=len)}")
            elif not action.option_strings and action.dest not in ("help",):
                found.add(f"{name} {action.dest}")
    return found


def case_config(state, tmp_path):
    """--config: the same command over the folder it points at and over the default one gives other results. The default one is a clean quick clone of its own: once the real run has written the real
    outputs of the project folder, ``figures`` over the project's own defaults would succeed too, and would write the real figures (U22)."""
    default = fresh_project(tmp_path / "default")
    with mock.patch.dict(os.environ, {ENV_HOME: str(default.root), ENV_CONFIG: config_of(default)}):
        without = cli("figures")
    with_it = cli("figures", "--config", config_of(state))
    assert with_it.code != without.code or with_it.out != without.out


def case_verbose(state, tmp_path):
    quiet = cli("task", "fetch_source", "--config", config_of(state), "--run-key", "v1")
    loud = cli("task", "fetch_source", "--config", config_of(state), "--run-key", "v2", "-v")
    assert quiet.code == loud.code == 0 and "task fetch_source of run v2" in loud.err and "task fetch_source of run v1" not in quiet.err


def case_only(state, tmp_path):
    result = cli("run", "--only", "fetch_source", "deliver", "--config", config_of(state))
    assert result.code == 0 and "fetch_source" in result.out and "deliver" in result.out and "validate" not in result.out and "fit_model" not in result.out


def case_from(state, tmp_path):
    pipeline.run_all(state.cfg, only=["fetch_source", "deliver"], run_key="k")
    result = cli("run", "--from", "validate", "--run-key", "k", "--config", config_of(state))
    assert result.code == 0 and "validate" in result.out and "fetch_source" not in result.out and "report" in result.out


def case_run_key(state, tmp_path):
    pipeline.run_all(state.cfg, only=["fetch_source", "deliver"], run_key="named")
    result = cli("run", "--only", "validate", "--run-key", "named", "--config", config_of(state))
    assert result.code == 0 and "named" in runs_of(state) and len(runs_of(state)) == 2, "the named run was continued (the launch's run and it are the two), not a new one made"


def case_until(state, tmp_path):
    result = cli("replay", "--until", "D2026-10-02", "--config", config_of(state))
    assert result.code == 0 and "2 corridas" in result.out and "D2026-10-05" not in result.out


def case_task(state, tmp_path):
    first = cli("task", "fetch_source", "--config", config_of(state), "--run-key", "t")
    second = cli("task", "deliver", "--config", config_of(state), "--run-key", "t")
    assert first.code == second.code == 0 and first.out.startswith("fetch_source:") and "files=" in first.out and second.out.startswith("deliver:") and "delivered=True" in second.out and "validate" not in second.out


def case_synth_sample(state, tmp_path):
    target = tmp_path / "out"
    many = cli("synth-sample", str(target / "a"), "--n", "3", "--config", config_of(state))
    new = cli("synth-sample", str(target / "b"), "--split", "validation_new", "--n", "2", "--config", config_of(state))
    assert many.code == new.code == 0 and len(list((target / "a").iterdir())) == 3 and len(list((target / "b").iterdir())) == 2
    assert all(p.name.startswith("validation_known-") for p in (target / "a").iterdir()) and all(p.name.startswith("validation_new-") for p in (target / "b").iterdir())


def case_make_fixture(state, tmp_path):
    result = cli("make-fixture", str(tmp_path / "cord"))
    assert result.code == 0 and sorted(p.name for p in (tmp_path / "cord" / "cord").iterdir()) == sorted(["train-00000-of-00001.parquet", "validation-00000-of-00001.parquet", "test-00000-of-00001.parquet"])
    assert (tmp_path / "cord" / "arca" / "QRespecificaciones.pdf").read_bytes().startswith(b"%PDF-")


def case_figures(state, tmp_path):
    result = cli("figures", "--config", config_of(state))
    assert result.code == 0 and len(list(Path(state.cfg.paths.figures_dir).glob("*.png"))) == 7


def case_docs_sync(state, tmp_path):
    readme = Path(state.cfg.paths.readme)
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text("# Proyecto\n<!-- results:start -->\nviejo\n<!-- results:end -->\nfin\n", encoding="utf-8")
    result = cli("docs-sync", "--config", config_of(state))
    text = readme.read_text(encoding="utf-8")
    assert result.code == 0 and "viejo" not in text and "C01" in text and text.endswith("fin\n") and cli("docs-sync", "--config", config_of(state)).out.strip() == "README.md ya estaba al día"


@contextmanager
def serving(token: str | None = "t0ken"):
    """``uvicorn.run`` replaced by a spy (no socket is opened) and the token of the API in the environment (or, with ``token=None``, out of it)."""
    with mock.patch("uvicorn.run") as run, mock.patch.dict(os.environ):
        os.environ.pop("INVOICEFLOW_API_TOKEN", None)
        if token is not None:
            os.environ["INVOICEFLOW_API_TOKEN"] = token
        yield run


def case_serve(state, tmp_path):
    """``serve`` hands uvicorn the application with the host and the port of the options or, without them, of ``[service]``; it listens on 127.0.0.1 only; it writes nothing."""
    config = config_of(state)
    other = Path(config).with_name("other.toml")
    other.write_text('extends = "test.toml"\n\n[service]\nhost = "0.0.0.0"\nport = 8123\ntoken_env = "INVOICEFLOW_API_TOKEN"\n', encoding="utf-8", newline="\n")
    before = tree(state)
    with serving() as run:                                  # --config: the port (and the host) of the file
        assert cli("serve", "--config", config).code == 0 and run.call_args.kwargs["port"] == 8000 and run.call_args.kwargs["host"] == "127.0.0.1"
        assert cli("serve").code == 0 and run.call_args.kwargs["port"] == 8000
        refused = cli("serve", "--config", str(other))
        assert refused.code == 2 and "0.0.0.0" in refused.err and "127.0.0.1" in refused.err and run.call_count == 2, "the file asks for a host that is not the local one: refused, nothing started"
    with serving() as run:                                  # --host: the one local address; the file's other host is overridden, any other host is refused before anything is loaded
        accepted = cli("serve", "--config", str(other), "--host", "127.0.0.1", "--port", "8124")
        assert accepted.code == 0 and run.call_args.kwargs["host"] == "127.0.0.1" and run.call_args.kwargs["port"] == 8124
        for host in ("0.0.0.0", "localhost", "::1", "192.168.0.10", "127.0.0.2", ""):
            refused = cli("serve", "--host", host, "--config", str(tmp_path / "does-not-exist.toml"))
            assert refused.code == 2 and "127.0.0.1" in refused.err and "No se escribió nada" in refused.err and "does-not-exist" not in refused.err, host
        assert run.call_count == 1
    with serving() as run:                                  # --port: the number, between 1 and 65535, over the one of the file
        assert cli("serve", "--config", str(other), "--host", "127.0.0.1").code == 0 and run.call_args.kwargs["port"] == 8123
        assert cli("serve", "--port", "65535").code == 0 and run.call_args.kwargs["port"] == 65535 and cli("serve", "--port", "1").code == 0 and run.call_args.kwargs["port"] == 1
        calls = run.call_count
        for port in ("0", "-1", "65536", "99999"):
            refused = cli("serve", "--port", port)
            assert refused.code == 2 and "1" in refused.err and "65535" in refused.err, port
        assert run.call_count == calls
    with serving() as run:                                  # --verbose: what uvicorn says
        assert cli("serve").code == 0 and run.call_args.kwargs["log_level"] == "warning"
        assert cli("serve", "-v").code == 0 and run.call_args.kwargs["log_level"] == "info"
    with serving(None) as run:                              # no token: it does not start
        refused = cli("serve", "--config", config)
        assert refused.code == 2 and "INVOICEFLOW_API_TOKEN" in refused.err and run.call_count == 0
    with serving() as run:                                  # the application is the API's, with the token of the environment
        cli("serve", "--config", config)
        app = run.call_args.args[0]
        assert type(app).__name__ == "FastAPI" and {r.path for r in app.routes} >= {"/runs", "/health"}
    assert tree(state) == before, "serve writes nothing"


CASES = {
    "run --run-key": case_run_key, "run --only": case_only, "run --from": case_from, "run --verbose": case_verbose, "run --config": case_config,
    "replay --until": case_until, "replay --config": case_config, "replay --verbose": case_verbose,
    "task task": case_task, "task --run-key": case_task, "task --config": case_config, "task --verbose": case_verbose,
    "synth-sample output": case_synth_sample, "synth-sample --split": case_synth_sample, "synth-sample --n": case_synth_sample, "synth-sample --config": case_config, "synth-sample --verbose": case_verbose,
    "serve --config": case_serve, "serve --host": case_serve, "serve --port": case_serve, "serve --verbose": case_serve,
    "make-fixture output": case_make_fixture, "figures --config": case_figures, "figures --verbose": case_verbose, "docs-sync --config": case_docs_sync, "docs-sync --verbose": case_verbose,
}
# the options that several commands share are the same option: it is shown once, by its case, where it can be seen to change a result


def test_every_option_the_parser_documents_has_its_case():
    assert documented_options() == set(CASES), sorted(documented_options() ^ set(CASES))


@pytest.mark.parametrize("case", sorted(set(CASES.values()), key=lambda f: f.__name__), ids=lambda f: f.__name__)
def test_every_documented_option_changes_the_result(case, pipeline_replayed, pipeline_launched, tmp_path):
    base = pipeline_replayed if case in (case_figures, case_docs_sync) else pipeline_launched
    case(copy_state(base, tmp_path / "state"), tmp_path)


@pytest.mark.parametrize("args, says", [
    (["run", "--only", "fetch_source", "--from", "deliver"], "--only y --from no se combinan"),
    (["run", "--run-key", "x"], "necesita --only o --from"),
    (["run", "--from", "validate"], "una corrida nueva empieza por fetch_source"),
    (["run", "--only", "validate"], "una corrida nueva empieza por fetch_source"),
    (["synth-sample", "out", "--split", "test_known"], "la prueba no se muestra"),
    (["synth-sample", "out", "--split", "test_new"], "la prueba no se muestra"),
    (["synth-sample", "out", "--n", "0"], "tiene que ser al menos 1"),
    (["synth-sample", "../fuera", "--n", "1"], "sale de la carpeta del proyecto"),
    (["synth-sample", "out", "--split", "otra"], "no es una partición de ejemplo"),
])
def test_incompatible_options_exit_2_before_writing(pipeline_launched, tmp_path, args, says):
    state = copy_state(pipeline_launched, tmp_path / "state")
    before = tree(state)
    result = cli(*args, "--config", config_of(state))
    assert result.code == 2 and says in result.err and tree(state) == before and not (state.root / "out").exists()


def test_an_unknown_task_or_option_exits_2_from_the_parser(pipeline_launched, tmp_path):
    state = copy_state(pipeline_launched, tmp_path / "state")
    assert cli("task", "nope", "--config", config_of(state)).code == 2 and cli("run", "--nope").code == 2 and cli("replay", "--until").code == 2 and cli().code == 2


def test_a_task_from_load_on_of_a_new_run_exits_2_and_writes_nothing(pipeline_launched, tmp_path):
    state = copy_state(pipeline_launched, tmp_path / "state")
    before = tree(state)
    for task in pipeline.TASK_IDS[pipeline.TASK_IDS.index("load"):]:
        result = cli("task", task, "--config", config_of(state))
        assert result.code == 2 and "no tiene una entrega" in result.err, task
    assert tree(state) == before


@pytest.mark.parametrize("kind, until, says", [("replayed", "D2026-10-01", "no hay entregas pendientes"), ("launched", "L2026-09-30", "anterior a la siguiente entrega pendiente")])
def test_replay_until_a_delivery_already_loaded_exits_2_whether_or_not_one_is_pending(kind, until, says, pipeline_replayed, pipeline_launched, tmp_path):
    state = copy_state(pipeline_replayed if kind == "replayed" else pipeline_launched, tmp_path / "state")
    before = tree(state)
    result = cli("replay", "--until", until, "--config", config_of(state))
    assert result.code == 2 and says in result.err and tree(state) == before


def test_a_replay_with_nothing_pending_exits_0_without_the_lock(pipeline_replayed, tmp_path):
    state = copy_state(pipeline_replayed, tmp_path / "state")
    before = tree(state)
    result = cli("replay", "--config", config_of(state))
    assert result.code == 0 and "no hay entregas pendientes" in result.out and tree(state) == before


def test_a_task_that_fails_exits_1_and_says_why(pipeline_launched, tmp_path, monkeypatch):
    state = copy_state(pipeline_launched, tmp_path / "state")
    for task in ("fetch_source", "deliver", "validate", "load"):
        assert cli("task", task, "--run-key", "f", "--config", config_of(state)).code == 0
    monkeypatch.setattr(pipeline, "TASKS", tuple(pipeline.TaskSpec(t.task_id, lambda ctx: (_ for _ in ()).throw(ValueError("hubo un problema"))) if t.task_id == "fit_model" else t for t in pipeline.TASKS))
    result = cli("task", "fit_model", "--run-key", "f", "--config", config_of(state), "-v")
    assert result.code == 1 and "falló: ValueError: hubo un problema" in result.err and "Traceback" in result.err


def test_a_rejected_delivery_or_source_exits_3(pipeline_launched, tmp_path):
    state = copy_state(pipeline_launched, tmp_path / "state")
    cord = Path(state.cfg.source.cord_dir)
    (cord / "validation-00000-of-00001.parquet").write_bytes(b"not a parquet file")
    lock = Path(state.cfg.paths.interim_dir) / ".."
    assert lock.exists()
    result = cli("task", "fetch_source", "--config", config_of(state))
    assert result.code == 3 and "rechazada" in result.err
    delivery = copy_state(pipeline_launched, tmp_path / "delivery")
    for task in ("fetch_source", "deliver"):
        assert cli("task", task, "--run-key", "r", "--config", config_of(delivery)).code == 0
    inbox = Path(delivery.cfg.paths.raw_dir) / "deliveries" / "D2026-10-01" / "inbox"
    next(inbox.iterdir()).unlink()
    assert cli("task", "validate", "--run-key", "r", "--config", config_of(delivery)).code == 3


def test_a_held_lock_or_the_test_asked_for_before_the_seal_exit_2_and_write_nothing(pipeline_launched, tmp_path):
    from invoiceflow.lock import sole_writer

    state = copy_state(pipeline_launched, tmp_path / "state")
    before = tree(state)
    answer = {}
    with sole_writer(state.cfg.paths.interim_dir, "someone else", None):
        worker = threading.Thread(target=lambda: answer.update(result=cli("run", "--config", config_of(state))))      # another thread of the process is refused like another process
        worker.start()
        worker.join(timeout=120)
        during = {k: v for k, v in tree(state).items() if not k.endswith("pipeline.lock")}
    result = answer["result"]
    assert result.code == 2 and "otro hilo de este mismo proceso" in result.err.lower() and during == {k: v for k, v in before.items() if not k.endswith("pipeline.lock")}
    fresh = fresh_project(tmp_path / "fresh")
    for task in ("fetch_source",):
        assert cli("task", task, "--run-key", "g", "--config", config_of(fresh)).code == 0
    sample = cli("synth-sample", str(tmp_path / "sample"), "--n", "1", "--config", config_of(fresh))
    assert sample.code == 0, "the validation partitions need no seal"


def test_module_entry_point_runs_from_another_directory(tmp_path):
    result = subprocess.run([sys.executable, "-m", "invoiceflow", "--help"], cwd=tmp_path, capture_output=True, text=True, env={**__import__("os").environ, "PYTHONPATH": str(PROJECT_ROOT / "src")}, timeout=120)
    assert result.returncode == 0 and "replay" in result.stdout and "make-fixture" in result.stdout and "serve" in result.stdout and "torch" not in result.stderr.lower()


# --- every key of the configuration ----------------------------------------------------------------------------------------------


def _changed(value):
    if isinstance(value, date):
        return value + timedelta(days=1)
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value * 1.5 if value else 0.25
    if isinstance(value, str):
        return value + "x"
    if isinstance(value, tuple):
        return (*value, value[-1]) if value else (1,)
    return value


def test_every_config_key_changes_the_result_or_is_declared_informative(quick_cfg):
    """AC32, lesson B2 of project 06: a key that is validated and changes nothing is a defect. Here a key is held to what it can show without running the flow (``tests/unit/test_config_keys_read.py``
    shows that a module reads each one): a key that is sealed changes the fingerprint of the sealed view (``config_sha256``), and every key, sealed or not, is either declared informative or read."""
    from invoiceflow.config import SealedConfig

    assert set(FINGERPRINT_KEYS).isdisjoint(UNSEALED_KEYS)
    view = SealedConfig.of(quick_cfg, tuple("0" * 64 for _ in quick_cfg.source.cord_files), "0" * 64)
    base = config_fingerprint(view)
    checked = 0
    for key in FINGERPRINT_KEYS:
        section, *rest = key.split(".")
        if len(rest) != 1 or not hasattr(view, section):      # a key of a table inside a section (scan_profiles, file_formats) is read when its table is
            continue
        node = getattr(view, section)
        value = getattr(node, rest[0])
        if dataclasses.is_dataclass(value):               # a table of values (scan_profiles, file_formats): read as a whole
            continue
        assert _changed(value) != value, key
        changed = dataclasses.replace(view, **{section: dataclasses.replace(node, **{rest[0]: _changed(value)})})
        try:
            assert config_fingerprint(changed) != base, key
        except ValueError:                                  # a font or a hashed file that the other value names does not exist: the key is read, and refused
            pass
        checked += 1
    assert checked > 40 and all(k not in FINGERPRINT_KEYS for k in INFORMATIVE_KEYS), "an informative key is not sealed: nothing rests on it"
    assert len(UNSEALED_KEYS) > 0
