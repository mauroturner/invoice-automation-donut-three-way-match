"""Command line of the pipeline: ``python -m invoiceflow <command>`` (RF25, RNF05; AC30, AC31, AC32; plan, section 22; D26, D29, D30, D56). Adapted from project 06's ``cli.py``.

``run``, ``task`` and ``replay`` call ``pipeline.run_all``, ``pipeline.run_task`` and ``pipeline.replay``: the same task list, order and functions as the Airflow DAG and the entry API, so the whole
flow runs on Windows without Airflow. ``make-fixture`` writes the tiny CORD of the quick mode, ``synth-sample`` writes sample invoices of a validation partition, ``figures`` calls ``figures.write_figures`` and ``docs-sync`` calls
``reporting.replace_block``: no step is written here. ``serve`` runs the entry API (``api.py``, D27) with uvicorn on 127.0.0.1 only: it has no task and writes nothing; a host that is not the local one,
a port out of range or a missing token is exit 2 before a server starts.

Exit codes: 0 ok (also a run with no delivery left and a replay with nothing pending), 1 a task failed, 2 wrong usage or configuration (also a task refused by the state of its run, because another
writer holds the data folder, or the test generated before the seal), 3 the delivery or the source was rejected. A combination that cannot do what it says is refused with 2 before anything is
written: no option is ignored in silence (AC32). Every relative path of ``--config`` and of ``synth-sample`` is taken from ``$INVOICEFLOW_HOME`` or the project folder, never from the working
directory (RNF05). ``main(argv)`` is the entry the tests use, in the same process.

The top-level help imports only the configuration: the pipeline is imported by the commands that run tasks. A new command is a ``commands.add_parser`` in ``build_parser`` and a branch of ``_execute``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from pathlib import Path

from invoiceflow.config import DEFAULT_PROJECT_ROOT, ENV_CONFIG, ENV_HOME, load_config

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_REJECTED = 0, 1, 2, 3
TASK_COMMANDS = ("run", "task")          # the commands whose options name tasks: only they need the pipeline's list to build the parser
SUMMARY_MD = "evaluation_summary.md"
NOTHING_WRITTEN = "No se escribió nada."
QUIET_KEYS = ("seconds", "sha256", "peak_working_set_mb", "peak_commit_mb", "peak_gpu_mb", "memory_counter", "message")     # what a line of a task leaves out: it is in the state of the run
SAMPLE_SPLITS = ("validation_known", "validation_new")
LOOPBACK_HOST = "127.0.0.1"             # the only interface ``serve`` listens on (D27)


class _UsageError(ValueError):
    """A combination of options that cannot do what it says (before anything is written)."""


def _home() -> Path:
    home = os.environ.get(ENV_HOME)
    return Path(home).resolve() if home else DEFAULT_PROJECT_ROOT


def _common() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, metavar="RUTA",
                        help=f"archivo de configuración (por defecto ${ENV_CONFIG} o config/pipeline.toml); una ruta relativa se toma desde ${ENV_HOME} o la carpeta del proyecto")
    common.add_argument("-v", "--verbose", action="store_true", help="muestra el detalle de cada paso y la traza de la tarea que falla")
    return common


def build_parser(task_ids: tuple[str, ...] | None = None) -> argparse.ArgumentParser:
    """The parser. `task_ids` are the choices of ``--only``, ``--from`` and ``task`` (``pipeline.TASK_IDS``); without them those options take any name and the pipeline refuses an unknown one
    (the top-level help does not import the pipeline)."""
    parser = argparse.ArgumentParser(prog="python -m invoiceflow", description="Facturas de proveedores que se cargan solas al ERP: se leen, se cruzan con la orden y el remito y se cargan las que cierran.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMANDO")
    common = _common()
    tasks = f" ({', '.join(task_ids)})" if task_ids else ""

    run = commands.add_parser("run", parents=[common], help="una corrida: la siguiente entrega, con las trece tareas en orden", description=f"Una corrida con las tareas de pipeline.TASKS{tasks}.")
    run.add_argument("--only", nargs="+", choices=task_ids, metavar="TAREA", help="corre solo estas tareas de la corrida, en su orden")
    run.add_argument("--from", dest="start", choices=task_ids, metavar="TAREA", help="sigue la corrida desde esta tarea")
    run.add_argument("--run-key", metavar="CLAVE", help="la corrida que se continúa (con --only o --from)")

    replay = commands.add_parser("replay", parents=[common], help="corridas seguidas, una por entrega pendiente, hasta agotarlas o hasta una entrega")
    replay.add_argument("--until", metavar="ENTREGA", help="la última entrega que se corre, por su identificador (por ejemplo D2026-10-28; por defecto, todas las pendientes; una anterior a la siguiente "
                                                           "pendiente, o cualquiera si no queda ninguna, sale con 2)")

    task = commands.add_parser("task", parents=[common], help="una tarea de una corrida", description=f"Una tarea de una corrida{tasks}.")
    task.add_argument("task", choices=task_ids, metavar="TAREA")
    task.add_argument("--run-key", metavar="CLAVE", help="la corrida a la que pertenece (una nueva si se omite; una tarea desde load necesita la de una corrida que tiene su entrega: sin ella sale con 2)")

    sample = commands.add_parser("synth-sample", parents=[common], help="escribe facturas sintéticas de ejemplo de una partición de validación",
                                 description="Escribe n facturas de ejemplo de validation_known o validation_new (nunca de la prueba) con el generador y la configuración dados.")
    sample.add_argument("output", type=Path, metavar="SALIDA_DIR", help="la carpeta donde se escriben; una ruta relativa se toma desde $INVOICEFLOW_HOME o, sin él, desde la carpeta del proyecto")
    sample.add_argument("--split", default="validation_known", help="la partición: validation_known (por defecto) o validation_new; test_known y test_new se niegan (salida 2)")
    sample.add_argument("--n", type=int, default=10, help="cuántas facturas (por defecto 10; no más que las de la partición)")

    fixture = commands.add_parser("make-fixture", help="escribe el CORD diminuto (16, 4 y 4 recibos) y un reemplazo de la especificación de ARCA para el modo rápido",
                                  description="Escribe en SALIDA_DIR/cord los tres Parquet del CORD diminuto con el esquema real y en SALIDA_DIR/arca un reemplazo de la especificación del QR de ARCA: "
                                              "lo que el modo rápido lee en data/quick (con SALIDA_DIR = data/quick).")
    fixture.add_argument("output", type=Path, metavar="SALIDA_DIR", help="la carpeta donde se escriben (data/quick para el modo rápido); una ruta relativa se toma desde $INVOICEFLOW_HOME o, sin él, desde la carpeta del proyecto")

    serve = commands.add_parser("serve", parents=[common], help=f"levanta la API de entrada en {LOOPBACK_HOST} (POST /runs corre la siguiente entrega; GET /runs/CLAVE, /runs/CLAVE/review y /health), con token",
                                description="Sirve la API de entrada con FastAPI y uvicorn: POST /runs corre la siguiente entrega con el mismo bloqueo que la CLI y el DAG (otro escritor: 409); GET /runs/CLAVE y "
                                            "/runs/CLAVE/review leen la corrida y su cola de revisión. Cada pedido lleva Authorization: Bearer con el token de la variable de entorno que dice [service] token_env. "
                                            "Solo escucha en la interfaz local.")
    serve.add_argument("--host", metavar="DIRECCIÓN", help=f"interfaz en la que escucha: solo {LOOPBACK_HOST} (por defecto el [service] host de la configuración, que tampoco puede ser otro); otra sale con 2 antes de cargar nada")
    serve.add_argument("--port", type=int, metavar="N", help="puerto, de 1 a 65535 (por defecto el [service] port de la configuración)")

    commands.add_parser("figures", parents=[common], help="dibuja las figuras de la documentación desde los archivos del sitio verificados de la última corrida")
    docs_sync = f"copia el resumen de resultados (reports/{SUMMARY_MD}) en el bloque de resultados del README"
    commands.add_parser("docs-sync", parents=[common], help=docs_sync, description=docs_sync[0].upper() + docs_sync[1:] + ". Un segundo uso no cambia nada; si el README no tiene sus dos marcadores no se escribe nada.")
    return parser


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")


def _fail(text: str) -> None:
    sys.stderr.write(text + "\n")


class _LineFormatter(logging.Formatter):
    """One line per record: the traceback of a failed task is printed once, by ``main`` with ``-v``."""

    def formatException(self, ei) -> str:
        return ""


def _configure_logging(verbose: bool) -> None:
    """Log to the standard error from the package's own logger, one line per record, replacing the handler of an earlier call in this process (the tests make several): information with ``-v``,
    warnings and errors without it."""
    package = logging.getLogger("invoiceflow")
    for handler in [h for h in package.handlers if getattr(h, "_invoiceflow_cli", False)]:
        package.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler._invoiceflow_cli = True   # type: ignore[attr-defined]
    handler.setFormatter(_LineFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    package.addHandler(handler)
    package.setLevel(logging.INFO if verbose else logging.WARNING)


def _check_run(args: argparse.Namespace, task_ids: tuple[str, ...]) -> None:
    """The combinations of ``run`` that cannot do what they say, refused before a state file is created (plan, section 22)."""
    if args.only and args.start is not None:
        raise _UsageError("--only y --from no se combinan: una elige las tareas y la otra desde cuál se sigue. " + NOTHING_WRITTEN)
    if args.run_key and not args.only and args.start is None:
        raise _UsageError("--run-key continúa una corrida y necesita --only o --from para decir qué tareas corren; una corrida nueva no lleva clave. " + NOTHING_WRITTEN)
    first = next(t for t in task_ids if t in args.only) if args.only else (args.start or task_ids[0])
    if args.run_key is None and first != task_ids[0]:
        raise _UsageError(f"una corrida nueva empieza por {task_ids[0]} y esta empezaría por {first}: para seguir una corrida que ya empezó, dé su clave con --run-key. " + NOTHING_WRITTEN)


def _brief(result: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in result.items() if k not in QUIET_KEYS and v is not None and not isinstance(v, (dict, list)))


def _show_run(outcome: list[dict]) -> None:
    for step in outcome:
        result = step["result"]
        if step["task"] == "deliver" and result.get("delivered"):
            _say(f"entrega {result['delivery_id']} (cálculo al {result['as_of']})")
        _say(f"{step['task']:<18} {result.get('seconds', 0):>8.1f} s  {_brief(result)}")
    if outcome and not outcome[-1]["result"].get("delivered", True):
        _say(outcome[-1]["result"]["message"])


def _output_folder(path: Path) -> Path:
    home = _home()
    output = path if path.is_absolute() else Path(os.path.normpath(home / path))   # like the paths of the configuration: never the working directory
    if not path.is_absolute() and home not in output.parents:
        raise _UsageError(f"{path} sale de la carpeta del proyecto ({home}): dé una ruta absoluta si el destino está fuera a propósito. {NOTHING_WRITTEN}")
    return output


def _make_fixture(args: argparse.Namespace) -> int:
    root = DEFAULT_PROJECT_ROOT
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))   # the generator lives with the tests, next to src/
    try:
        from tests.fixtures.make_cord_fixture import make_cord_fixture
        from tests.fixtures.projects import PDF_BYTES, PDF_NAME
    except ModuleNotFoundError as error:
        if error.name not in ("tests", "tests.fixtures", "tests.fixtures.make_cord_fixture", "tests.fixtures.projects"):
            raise   # a module the generator itself needs is missing: a real error, not an absent tests tree
        raise _UsageError(f"el generador del CORD diminuto vive en tests/fixtures/ de la carpeta del proyecto ({root}) y no está: make-fixture solo corre desde una copia completa. {NOTHING_WRITTEN}") from error
    output = _output_folder(args.output)
    paths = make_cord_fixture(output / "cord")
    (output / "arca").mkdir(parents=True, exist_ok=True)
    (output / "arca" / PDF_NAME).write_bytes(PDF_BYTES)
    _say(f"escribí {', '.join(p.name for p in paths.values())} y {PDF_NAME} en {output}")
    return EXIT_OK


def _synth_sample(args: argparse.Namespace, cfg) -> int:
    from invoiceflow.pipeline import _plan_of  # noqa: F401  (the plan needs no hash of a source; the view below is for the generator only)
    from invoiceflow.registry import sealed_view
    from invoiceflow.sources import ARCA_KEY
    from invoiceflow.synth.invoices import SPLITS, invoices
    from invoiceflow.synth.master import master as make_master
    from invoiceflow.synth.render import render

    if args.split.startswith("test"):
        raise _UsageError(f"{args.split}: la prueba no se muestra ni se genera aquí (solo la parte del cierre la usa); las particiones de ejemplo son {', '.join(SAMPLE_SPLITS)}. {NOTHING_WRITTEN}")
    if args.split not in SAMPLE_SPLITS:
        raise _UsageError(f"--split {args.split}: no es una partición de ejemplo ({', '.join(SAMPLE_SPLITS)}; las de SPLITS son {', '.join(SPLITS)}). {NOTHING_WRITTEN}")
    if args.n < 1:
        raise _UsageError(f"--n {args.n}: tiene que ser al menos 1. {NOTHING_WRITTEN}")
    output = _output_folder(args.output)
    view = sealed_view(cfg, {**{f.name: "0" * 64 for f in cfg.source.cord_files}, ARCA_KEY: "0" * 64})
    truths = invoices(view, make_master(view), args.split, None, seal_path=Path(cfg.paths.seal))
    if args.n > len(truths):
        raise _UsageError(f"--n {args.n}: la partición {args.split} tiene {len(truths)} facturas. {NOTHING_WRITTEN}")
    extension = {"png": ".png", "jpeg": ".jpg", "pdf": ".pdf"}
    output.mkdir(parents=True, exist_ok=True)
    for truth in truths[: args.n]:
        (output / f"{truth.invoice_key}{extension[truth.file_format]}").write_bytes(render(truth, view))
    _say(f"escribí {args.n} facturas sintéticas de {args.split} en {output}")
    return EXIT_OK


def _docs_sync(cfg) -> int:
    """The results block of the README from the summary: check, then write (a README without its two markers is left as it was, exit 2)."""
    from invoiceflow.files import write_atomic
    from invoiceflow.reporting import RESULTS_END, RESULTS_START, replace_block

    summary, readme = Path(cfg.paths.reports_dir) / SUMMARY_MD, Path(cfg.paths.readme)
    if not readme.is_file():   # the quick mode has no README of its own
        raise _UsageError(f"la configuración no tiene un README que sincronizar: {readme.name} no existe (el modo rápido no lo tiene). {NOTHING_WRITTEN}")
    if not summary.is_file():
        raise _UsageError(f"falta el resumen de resultados {summary.name}: lo escribe la tarea report. {NOTHING_WRITTEN}")
    text = readme.read_text(encoding="utf-8")
    try:
        new = replace_block(text, readme.name, RESULTS_START, RESULTS_END, summary.read_text(encoding="utf-8"))
    except ValueError as error:
        raise _UsageError(f"{error}. {NOTHING_WRITTEN}") from error
    if new != text:
        write_atomic(readme, new)
    _say(f"{readme.name} actualizado" if new != text else f"{readme.name} ya estaba al día")
    return EXIT_OK


def _figures(cfg) -> int:
    from invoiceflow.figures import write_figures   # matplotlib: only the command that draws pays for it

    try:
        paths = write_figures(cfg.paths.site_dir, cfg.paths.figures_dir)
    except FileNotFoundError as error:   # no site files, or a folder that is not one export (SiteSetError): nothing was written
        raise _UsageError(f"las figuras se dibujan solo desde los archivos del sitio de una misma exportación: {error}. {NOTHING_WRITTEN}") from error
    for path in paths:
        _say(f"escribí {path.name}")
    return EXIT_OK


def _execute(args: argparse.Namespace, cfg, pipeline) -> int:
    if args.command == "docs-sync":
        return _docs_sync(cfg)
    if args.command == "figures":
        return _figures(cfg)
    if args.command == "synth-sample":
        return _synth_sample(args, cfg)
    if args.command == "task":
        result = pipeline.run_task(args.task, args.run_key, cfg)
        _say(f"{args.task}: {_brief(result)}")
        return EXIT_OK
    if args.command == "replay":
        runs = pipeline.replay(cfg, args.until)
        for outcome in runs:
            _show_run(outcome)
        _say(f"{len(runs)} corrida" + ("" if len(runs) == 1 else "s") if runs else "no hay entregas pendientes: las del plan ya están cargadas")
        return EXIT_OK
    _show_run(pipeline.run_all(cfg, only=args.only, start=args.start, run_key=args.run_key))
    return EXIT_OK


def _serve(args: argparse.Namespace) -> int:
    """``serve``: the refusals that need nothing loaded (the host and the port), then the configuration (whose host must be the local one too), then the application (without a token it does not
    start), then the server. Nothing is written."""
    try:
        if args.host is not None and args.host != LOOPBACK_HOST:
            raise _UsageError(f"--host {args.host!r}: la API solo escucha en {LOOPBACK_HOST} (D27); en Docker la sirve el servicio api del compose, en su red interna. {NOTHING_WRITTEN}")
        if args.port is not None and not 1 <= args.port <= 65535:
            raise _UsageError(f"--port {args.port}: se esperaba un puerto de 1 a 65535. {NOTHING_WRITTEN}")
        cfg = load_config(args.config)
        host = LOOPBACK_HOST if args.host is not None else cfg.service.host
        if host != LOOPBACK_HOST:
            raise _UsageError(f"[service] host = {host!r}: la API solo escucha en {LOOPBACK_HOST} (D27); use --host {LOOPBACK_HOST} o corrija la configuración. {NOTHING_WRITTEN}")
        from invoiceflow.api import ApiStartError, create_app      # the API, its libraries and the pipeline are loaded only here

        try:
            app = create_app(cfg)
        except ApiStartError as error:
            raise _UsageError(f"{error}. {NOTHING_WRITTEN}") from error
    except (_UsageError, ValueError, FileNotFoundError) as error:
        _fail(f"error: {error}")
        return EXIT_USAGE
    import uvicorn

    uvicorn.run(app, host=host, port=cfg.service.port if args.port is None else args.port, log_level="info" if args.verbose else "warning")
    return EXIT_OK


def _command_of(argv: list[str]) -> str | None:
    return next((a for a in argv if not a.startswith("-")), None)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    task_ids = None
    if _command_of(argv) in TASK_COMMANDS:
        from invoiceflow.pipeline import TASK_IDS

        task_ids = TASK_IDS
    try:
        args = build_parser(task_ids).parse_args(argv)
    except SystemExit as stop:   # argparse exits on a wrong option (2) and on --help (0)
        return int(stop.code or 0)
    _configure_logging(getattr(args, "verbose", False))
    if args.command == "serve":
        return _serve(args)
    if args.command == "make-fixture":
        try:
            return _make_fixture(args)
        except (_UsageError, ValueError, OSError) as error:   # a path that cannot be written, or the generator absent
            _fail(f"error: {error}")
            return EXIT_USAGE
    try:
        if args.command == "run":
            _check_run(args, task_ids)
        cfg = load_config(args.config)
    except (ValueError, FileNotFoundError) as error:   # a wrong option or configuration: nothing has been written
        _fail(f"error: {error}")
        return EXIT_USAGE
    from invoiceflow import pipeline
    from invoiceflow.sources import SourceError
    from invoiceflow.synth.invoices import GenerationRefusedError
    from invoiceflow.validate import ValidationError

    try:
        return _execute(args, cfg, pipeline)
    except (ValidationError, SourceError) as error:
        _fail(f"rechazada: {error}")
        return EXIT_REJECTED
    except (_UsageError, pipeline.UsageError, pipeline.RunRefusedError, GenerationRefusedError) as error:   # refused before anything ran or was written
        _fail(f"error: {error}")
        return getattr(error, "exit_code", EXIT_USAGE)
    except Exception as error:   # a task failed: its record is in the state of the run
        _fail(f"falló: {type(error).__name__}: {error}")
        if getattr(args, "verbose", False):
            _fail("".join(traceback.format_exception(error)).rstrip())
        return EXIT_FAILED


__all__ = ["EXIT_FAILED", "EXIT_OK", "EXIT_REJECTED", "EXIT_USAGE", "build_parser", "main"]
