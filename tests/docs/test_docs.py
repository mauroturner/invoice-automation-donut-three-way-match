"""The documentation is checked like code (AC22, AC39; RF26; RNF14; plan, sections 24.2, 25 and 26). Adapted from project 06's ``tests/docs/test_docs.py``.

The README has the headings of plan section 25 in their order, its results block is the sealed summary that ``docs-sync`` copies (never one written by hand), and it says the
unfavourable verdicts («No se cumple») in «El problema», «La solución» and «Los resultados»; the model card has the contents the plan lists and every verdict as the summary writes it;
every interval bound the documents quote is the summary's at the decimals the document writes; every figure that comes from a synthetic invoice says so; every decision has its
seven fields; the test count the documents give is the one of the session's collection; every memory figure names its counter; every test the plan names exists; every unit and
every log entry the documents cite is in the log; the dictionary's tables are the ones ``docs_sync`` writes from the semantic model; the runbook has R1 to R12; the step by step
cites commits that exist; the advisories of the native reviews have their status; and the rows of project 07 in the root documents carry the real figures. These tests read the
versioned Markdown, the versioned reports and the plan, and run nothing. Every document is read once per module.
"""

from __future__ import annotations

import ast
import getpass
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from invoiceflow.reporting import RESULTS_END, RESULTS_START

README_TITLE = "# Facturas que se cargan solas al ERP"
USER_NAMES = {name.lower() for name in (getpass.getuser(), Path.home().name) if name}   # this machine's user, read here and never written in a test
EMAIL = re.compile(r"\S+@\S+")
LOCAL_PATH = re.compile(r"[A-Za-z]:[\\/]+(?:Users|Documents and Settings)|/c/Users|Users\\|/home/\w+/")
README_HEADINGS = [   # plan, section 25, in this order (AC39)
    "El problema", "La solución", "Los resultados", "En qué consiste", "Los datos", "Los algoritmos", "El producto", "La documentación",
    "Con IA, de punta a punta", "Para qué sirve", "Cómo correrlo de punta a punta", "Paso a paso", "Fuente y licencia",
]
MODEL_CARD_HEADINGS = [   # what plan section 25 lists for the model card, in this order
    "Uso previsto y no previsto", "Datos", "El ajuste y su elección", "El QR", "El cruce y sus tolerancias", "Criterios y resultados", "Limitaciones", "Tiempos y memoria",
    "La suite rápida",
]
FAILED = ["C01", "C03", "C04", "C05", "C06"]   # what the sealed run says (L109)
PASSED = ["C02", "C07", "C08", "C09"]
RESULTS_HEADING = "## Resumen de la evaluación"   # the first line of the summary, copied as it is
DOCS = ("data-dictionary.md", "runbook.md", "paso-a-paso.md", "review-advisories.md", "decisions.md", "evaluation.md")
LOG = "odd/tasks/portfolio-projects.md"
FIRST_LOG_ENTRY = 90   # L90 starts project 07
UNITS_DONE = [f"U{n:02d}" for n in range(1, 23)]   # U01 to U22 have their entry in the log (U22 in L109)
STATUSES = ("corregida", "refutada", "dejada con evidencia", "abierta")
DECISION_FIELDS = ("Contexto", "Opciones", "Decisión", "Motivo", "Afecta", "Quién", "Estado")
COUNTERS = ("PeakWorkingSetSize", "PeakPagefileUsage", "torch.cuda.max_memory_allocated", "conjunto de trabajo", "memoria comprometida")
MEMORY_FIGURE = re.compile(r"\d[\d.]*(?:,\d+)?\s?(?:MB|GB|MiB|GiB)\b")
MEMORY_WORDS = re.compile(r"pico|peak_|conjunto de trabajo|comprometid|memoria", re.IGNORECASE)   # the size of a file, an image or a disk is not a memory figure
SENTENCE_END = re.compile(r"(?<=\.)\s+")
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
NAMED_TEST = re.compile(r"(tests/[\w/]+\.py)?::(test_\w+)")
CUIT = re.compile(r"\b\d{2}-\d{8}-\d\b")


@pytest.fixture(scope="module")
def readme(project_root: Path) -> str:
    return (project_root / "README.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def model_card(project_root: Path) -> str:
    return read(project_root, "model-card.md")


@pytest.fixture(scope="module")
def summary(project_root: Path) -> dict:
    return json.loads((project_root / "reports" / "evaluation_summary.json").read_text(encoding="utf-8"))


_READ: dict[Path, str] = {}


def read(project_root: Path, name: str) -> str:
    path = project_root / "docs" / name
    if path not in _READ:
        _READ[path] = path.read_text(encoding="utf-8")
    return _READ[path]


def section(text: str, title: str, end: str | None = None) -> str:
    """The body of the second-level section `title`: up to `end` (its first appearance after the heading) when it is given, otherwise up to the next second-level heading."""
    body = text.split(f"\n## {title}\n", 1)[1]
    if end is not None:
        assert end in body, f"{end!r} does not follow the heading {title!r}"
        return body.split(end, 1)[0]
    return re.split(r"^## ", body, flags=re.MULTILINE)[0]


def decision(project_root: Path, number: int) -> str:
    return read(project_root, "decisions.md").split(f"\n## D{number:02d} ")[1].split("\n## D")[0]


def root_rows(project_root: Path) -> str:
    """The section of project 07 in the root ``SITE-UPDATES.md``, up to the next project or the end."""
    text = (project_root.parent / "SITE-UPDATES.md").read_text(encoding="utf-8")
    assert text.count("\n## 07 ") == 1, "SITE-UPDATES.md has no section for project 07"
    return re.split(r"\n## 0[8-9] ", text.split("\n## 07 ")[1])[0]


OWNED = ("model-card.md", *DOCS)   # the documents U23 writes; research.md, requirements.md and plan.md are the planner's and the researcher's, and are not rewritten here


def all_documents(project_root: Path, readme: str) -> dict[str, str]:
    documents = {"README.md": readme, "SITE-UPDATES.md (07)": root_rows(project_root)}
    documents |= {f"docs/{name}": read(project_root, name) for name in OWNED}
    return documents


# --- the README and the model card (AC22, AC39) ----------------------------------------------------------------------------------------------------------------


def test_readme_headings_in_order_and_results_block_matches_summary(project_root: Path, readme: str, summary: dict) -> None:
    assert readme.startswith(README_TITLE + "\n")
    assert readme.count(RESULTS_START) == 1 and readme.count(RESULTS_END) == 1
    head, rest = readme.split(RESULTS_START)
    block, tail = rest.split(RESULTS_END)
    block = block.strip("\n")
    # the block carries its own «## Resumen de la evaluación»: the headings of the plan are those outside it, in order
    assert re.findall(r"^## (.+)$", head + tail, flags=re.MULTILINE) == README_HEADINGS
    assert block == (project_root / "reports" / "evaluation_summary.md").read_text(encoding="utf-8").strip("\n")   # what docs-sync copies, byte for byte
    assert block.startswith(RESULTS_HEADING + "\n") and re.findall(r"^## (.+)$", block, flags=re.MULTILINE) == ["Resumen de la evaluación"]
    framing = section(readme, "Los resultados", end=RESULTS_START)   # «Los resultados» frames the block before it and reads it after it
    assert len(framing.strip()) > 400 and "\n## En qué consiste\n" in tail
    # AC22: the sealed summary is unfavourable, and the README says «no se cumple» outside the block too, in the three sections that tell it
    assert summary["source"] == "warehouse" and summary["synthetic"] is True and "post_seal" not in summary   # the sealed result, with no analysis after the seal
    assert [c["criterion_id"] for c in summary["criteria"] if c["passed"] is False] == FAILED
    assert [c["criterion_id"] for c in summary["criteria"] if c["passed"] is True] == PASSED
    assert [c["criterion_id"] for c in summary["criteria"] if c["passed"] is None] == ["C10"]
    for criterion in summary["criteria"]:
        assert criterion["verdict_es"] in block, criterion["criterion_id"]
    assert f"No se cumple: {', '.join(FAILED)}." in block
    reading = framing + tail.split("\n## En qué consiste\n")[0]
    for title, text in (("El problema", section(readme, "El problema")), ("La solución", section(readme, "La solución")), ("Los resultados", reading)):
        assert "no se cumple" in text.lower(), title
    for criterion in FAILED + PASSED:
        assert re.search(rf"\b{criterion}\b", reading), criterion
    assert "se esperaba" in reading and "189" in reading and "1.032" in reading and "sintétic" in reading   # the expectation, C05's minimum, the close time, the label
    assert "(sintético)" in block   # AC22: every synthetic figure of the block says so


def test_the_model_card_has_the_contents_of_the_plan_and_says_the_unfavourable_verdicts(model_card: str, summary: dict) -> None:
    assert re.findall(r"^## (.+)$", model_card, flags=re.MULTILINE) == MODEL_CARD_HEADINGS
    for criterion in summary["criteria"]:
        assert criterion["verdict_es"] in model_card, criterion["criterion_id"]   # every verdict as the summary writes it, «No se cumple» included
    for piece in (
        "CORD", "CC BY 4.0", "sintétic", "30-0000xxxx-d", "ARCA", "safetensors", "no es una medida independiente", "93,65", "94,00", "ted_accuracy", "ERPNext v15.124.4",
        "PeakWorkingSetSize", "torch.cuda.max_memory_allocated", "180 s", "120 s", "1.032", "189", "RNF02", "mode = http", "417",
    ):
        assert piece.lower() in model_card.lower(), piece
    assert re.search(r"suite rápida[^\n]*\d{2,3},\d+ s", model_card)   # AC40: measured and written down


def test_the_readme_says_what_is_pending_and_gives_the_attribution(readme: str, model_card: str) -> None:
    """RNF14: the independent evaluation, its corrections and the ``.pbix`` are not done yet, and the README says so; the credit of the source is complete; nothing local is published."""
    text = readme.lower()
    assert "evaluación independiente" in text and "todavía no" in text and ".pbix" in text and "invoices-erp" in text
    tail = section(readme, "Fuente y licencia")
    for piece in ("Park et al. (2019)", "CORD", "Clova AI", "CC BY 4.0", "7f0115a4", "QRespecificaciones.pdf", "a6d0f71e", "sintétic", "MIT"):
        assert piece in tail, piece
    for document in (readme, model_card):
        assert not LOCAL_PATH.search(document) and not any(name in EMAIL.sub("", document).lower() for name in USER_NAMES) and "@gmail" not in document


# --- the intervals the documents quote (AC39, RNF14) ------------------------------------------------------------------------------------------------------------

SIGN = "[+−-]?"
NUMBER = rf"({SIGN}\d+,\d+)"
INTERVAL_SCALE = {"C01": 1.0, "C02": 100.0, "C03": 100.0, "C05": 100.0, "C08": 100.0}   # TED points, proportions in %, and a difference of proportions in points


def quoted_bound(text: str) -> tuple[float, int]:
    """A bound as a document writes it («−6,56», «+4,36»): its value and its number of decimals."""
    sign, digits = (-1.0, text[1:]) if text[0] in "−-" else (1.0, text.lstrip("+"))
    return sign * float(digits.replace(",", ".")), len(digits.split(",")[1])


def test_every_quoted_interval_bound_equals_the_summary_at_its_decimals(project_root: Path, readme: str, model_card: str, summary: dict) -> None:
    """B3 of project 05: bounds rounded by hand came out wrong in two documents. Every interval of a criterion that has one (C01, C02, C03, C05, C08) that D19, the README, the model
    card and the root rows quote («intervalo del 95 %, A a B», «IC A a B» or «[A; B]») is the summary's, rounded to the decimals the document writes."""
    intervals = {c["criterion_id"]: (INTERVAL_SCALE[c["criterion_id"]] * c["ci_low"], INTERVAL_SCALE[c["criterion_id"]] * c["ci_high"]) for c in summary["criteria"] if c["ci_low"] is not None}
    assert sorted(intervals) == sorted(INTERVAL_SCALE)
    named = "|".join(intervals)
    interval = re.compile(rf"(?<![-–])\b({named})\b(?![-–])((?:(?!C\d\d)[^\n]){{0,300}}?)(?:intervalo del 95 %,? |IC |\[){NUMBER}(?: a |; ){NUMBER}")
    documents = {"D19": decision(project_root, 19), "README.md": readme, "docs/model-card.md": model_card, "SITE-UPDATES.md (07)": root_rows(project_root)}
    wrong, seen = [], {}
    for name, text in documents.items():
        found = interval.findall(text)
        seen[name] = {criterion for criterion, *_rest in found}
        for criterion, _between, low, high in found:
            for written, value in ((low, intervals[criterion][0]), (high, intervals[criterion][1])):
                number, decimals = quoted_bound(written)
                if f"{number:.{decimals}f}" != f"{value:.{decimals}f}":
                    wrong.append(f"{name}: {criterion} writes {written}, the summary rounds to {value:.{decimals}f}")
    assert wrong == []
    assert {"C01", "C08"} <= seen["D19"] and set(intervals) == seen["docs/model-card.md"] and {"C01", "C02", "C08"} <= seen["README.md"], seen   # the check is not vacuous


# --- every figure from a synthetic invoice says so, and no CUIT is a real one (AC22, AC28, AC39; D05) -------------------------------------------------------------

SYNTHETIC_FIGURE = re.compile(r"\bC0[2-9]\b[^\n]*\d")


def test_every_synthetic_figure_in_the_docs_says_so(project_root: Path, readme: str, model_card: str, summary: dict) -> None:
    """D05 and AC22: a line of the README, the model card, the step by step or the root rows that gives a figure of a criterion that is measured on synthetic invoices (C02 to C09) says
    «sintétic» on that same line; the results block says it in every verdict; and no document prints a CUIT outside the synthetic range ``30-0000xxxx-d``."""
    documents = {"README.md": readme, "docs/model-card.md": model_card, "docs/paso-a-paso.md": read(project_root, "paso-a-paso.md"), "SITE-UPDATES.md (07)": root_rows(project_root)}
    block = readme.split(RESULTS_START)[1].split(RESULTS_END)[0]
    unlabelled, checked = [], 0
    for name, text in documents.items():
        for line in text.splitlines():
            if SYNTHETIC_FIGURE.search(line) and line not in block:
                checked += 1
                if "sintétic" not in line.lower():
                    unlabelled.append(f"{name}: {line.strip()[:140]}")
    assert unlabelled == [] and checked >= 12   # the check is not vacuous
    rows = [line for line in block.splitlines() if re.match(r"^\| C0[2-9] ", line)]
    assert len(rows) == 8 and all("sintétic" in row for row in rows)
    real = [c["criterion_id"] for c in summary["criteria"] if c["synthetic"] is False]
    assert "C01" in real   # C01 is measured on CORD's real receipts, and the documents say so
    assert "recibos reales" in readme.lower() or "recibos de cord" in readme.lower()
    stray = sorted({f"{name}: {cuit}" for name, text in all_documents(project_root, readme).items() for cuit in CUIT.findall(text) if not re.fullmatch(r"30-0000\d{4}-\d", cuit)})
    assert stray == []


# --- the decisions, the stated test count, the memory figures, the tests the plan names, the log, the dictionary, the runbook, the step by step, the advisories, the
# links and the root rows (AC39) --------------------------------------------------------------------------------------------------------------------------------


def test_every_decision_has_seven_fields(project_root: Path) -> None:
    text = read(project_root, "decisions.md")
    entries = re.split(r"^## (?=D\d+ )", text, flags=re.MULTILINE)[1:]
    numbers = [int(re.match(r"D(\d+)", e).group(1)) for e in entries]
    assert numbers == list(range(1, len(numbers) + 1)) and len(numbers) >= 58   # no gap and no repeat, up to the last decision of the build
    missing = [f"D{n:02d}: {field}" for n, entry in zip(numbers, entries, strict=True) for field in DECISION_FIELDS if f"- **{field}**: " not in entry]
    assert missing == []
    states = {f"D{n:02d}": re.search(r"^- \*\*Estado\*\*: (.+)$", e, flags=re.MULTILINE).group(1) for n, e in zip(numbers, entries, strict=True)}
    assert [d for d, s in states.items() if not re.match(r"(vigente|pendiente del dueño|reemplazada por D\d+)", s)] == []


def stated_count(request: pytest.FixtureRequest) -> str:
    """The test count as the documents write it, from the collection of this session; skipped when the session did not collect the default selection in full."""
    from tests.fixtures.suite_budget import is_full_default_selection

    if not is_full_default_selection(request.config):
        pytest.skip("the session did not collect the default selection in full: its collection is not the suite's count")
    return f"{len(request.session.items):,}".replace(",", ".") + " pruebas"


def test_stated_test_count_matches_the_session_collection(request: pytest.FixtureRequest, readme: str, model_card: str) -> None:
    """B3 of project 02, B5 of project 03 and B1 of project 04 (RNF14): the documents said one test count after a change had moved it. The count is that of the collection, in the
    README and the model card; the seconds next to it are measured and written by hand (AC40)."""
    stated = stated_count(request)
    assert re.findall(r"\b\d{1,3}(?:\.\d{3})* pruebas\b", readme) == [stated]
    assert re.findall(r"\b\d{1,3}(?:\.\d{3})* pruebas\b", section(model_card, "La suite rápida")) == [stated]


def test_every_memory_figure_names_its_counter(project_root: Path, readme: str, model_card: str) -> None:
    """RNF03 and D39 (lesson A3 of project 02): every sentence of the documents that gives a memory figure in MB or GB names the counter, by its API name or by what it counts."""
    absent = [f"docs/{name}" for name in DOCS if not (project_root / "docs" / name).is_file()]
    assert absent == [], "every document of AC39 exists"
    figures, unnamed = 0, []
    for name, text in all_documents(project_root, readme).items():
        for line in text.splitlines():
            for sentence in SENTENCE_END.split(line):
                if MEMORY_FIGURE.search(sentence) and MEMORY_WORDS.search(sentence):
                    figures += 1
                    if not any(counter in sentence for counter in COUNTERS):
                        unnamed.append(f"{name}: {sentence.strip()[:160]}")
    assert unnamed == []
    assert figures >= 6   # the documents do state memory figures: the check is not vacuous


def test_results_block_matches_summary(project_root: Path, readme: str, summary: dict) -> None:
    """Plan 24.2, the class «documentación sin sincronizar»: the block is the summary of the sealed run, and the documents name the run and its seal."""
    block = readme.split(RESULTS_START)[1].split(RESULTS_END)[0].strip("\n")
    assert block == (project_root / "reports" / "evaluation_summary.md").read_text(encoding="utf-8").strip("\n")
    seal = json.loads((project_root / "reports" / "model_seal.json").read_text(encoding="utf-8"))
    assert summary["config_sha256"] in json.dumps(seal) and "51884acb" in readme and "51884acb" in read(project_root, "model-card.md")


RENAMED = {   # five tests the plan names under a shorter or earlier name; the writers of U13, U19, U20 and U21 gave them the name on the right, and plan.md is not rewritten after the build
    "tests/e2e/test_airflow_docker.py::test_dags_test_twice_in_quick_mode_and_leaves_nothing_behind":
        "tests/e2e/test_airflow_docker.py::test_dags_test_twice_in_quick_mode_the_services_answer_and_nothing_is_left_behind",
    "tests/unit/test_compose.py::test_no_literal_secret": "tests/unit/test_compose.py::test_no_literal_secret_including_the_metadata_db_password",
    "tests/unit/test_lock.py::test_the_folders_it_created_are_removed_when_empty": "tests/unit/test_lock.py::test_the_folders_the_lock_created_are_removed_with_it_when_nothing_was_written_in_them",
    "tests/unit/test_serve.py::test_serve_refuses_any_host_but_localhost": "tests/e2e/test_cli.py::case_serve",
    "tests/unit/test_sysinfo.py::test_every_task_records_its_peaks_with_their_counters": "tests/unit/test_sysinfo.py::test_every_task_of_every_run_of_the_flow_names_its_counter",
}


def named_in_plan(plan: str) -> list[tuple[str, str]]:
    """Every test the plan names, as (file, function): a bare ``::name`` belongs to the file named last before it."""
    found, last = [], None
    for match in NAMED_TEST.finditer(plan):
        last = match.group(1) or last
        assert last is not None, match.group(0)
        found.append((last, match.group(2)))
    return found


def test_every_test_named_in_the_plan_exists(project_root: Path) -> None:
    """AC39: every test plan sections 24 and 27 name (the RED of each unit and the named defect classes) is a test function of that file."""
    named = named_in_plan(read(project_root, "plan.md"))
    assert len(named) >= 100   # the plan does name its tests: the check is not vacuous
    defined: dict[str, set[str]] = {}
    missing = []
    for path, name in named:
        path, name = RENAMED.get(f"{path}::{name}", f"{path}::{name}").split("::")
        if path not in defined:
            file = project_root / path
            tree = ast.parse(file.read_text(encoding="utf-8")) if file.is_file() else ast.Module(body=[], type_ignores=[])
            defined[path] = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if name not in defined[path]:
            missing.append(f"{path}::{name}")
    assert sorted(set(missing)) == []


def log_entries(project_root: Path) -> dict[int, str]:
    """The entries L90 and later of the tracking document at the root of the repository, by number (skipped when the repository has no such document)."""
    path = project_root.parent / LOG
    if not path.is_file():
        pytest.skip("the tracking document is not next to the project")
    parts = re.split(r"^- \*\*L(\d+) ", path.read_text(encoding="utf-8"), flags=re.MULTILINE)[1:]
    return {int(number): body for number, body in zip(parts[::2], parts[1::2], strict=True) if int(number) >= FIRST_LOG_ENTRY}


def test_every_unit_the_docs_tell_has_its_log_entry(project_root: Path, readme: str) -> None:
    """B4 and N2 of project 05 (RNF14): the documents told units and corrections that the log did not have yet. Every unit (U01...) and every entry (L90...) that the README, the step by
    step and the advisories name is in the log, and the step by step names every unit the log has."""
    entries = log_entries(project_root)
    assert max(entries) >= 110 and sorted(entries) == list(range(FIRST_LOG_ENTRY, max(entries) + 1))   # the log of project 07 is there, with no gap
    logged_units = {unit for body in entries.values() for unit in re.findall(r"\bU\d{2}\b", body)}
    assert set(UNITS_DONE) <= logged_units
    documents = {"README.md": readme, "docs/paso-a-paso.md": read(project_root, "paso-a-paso.md"), "docs/review-advisories.md": read(project_root, "review-advisories.md")}
    told = {name: set(re.findall(r"\bU\d{2}\b", text)) for name, text in documents.items()}
    assert [(name, sorted(units - logged_units - {"U23"})) for name, units in told.items() if units - logged_units - {"U23"}] == []   # no unit the log lacks (U23 is this one)
    cited = {name: {int(n) for n in re.findall(r"\bL(\d{2,3})\b", text)} for name, text in documents.items()}
    assert [(name, sorted(numbers - set(entries))) for name, numbers in cited.items() if numbers - set(entries)] == []   # no entry the log lacks
    assert set(UNITS_DONE) <= told["docs/paso-a-paso.md"]   # the step by step names every unit the log has
    assert set(range(90, 111)) <= cited["docs/paso-a-paso.md"]   # and every entry of the project so far


def test_the_dictionary_covers_the_sources_and_its_tables_are_the_generated_ones(project_root: Path) -> None:
    import pyarrow.parquet as pq

    from invoiceflow.dashboard.semantic_model import MODEL, arrow_schema
    from invoiceflow.docs_sync import COLUMNS_END, COLUMNS_START, render_dictionary_tables
    from invoiceflow.synth.invoices import PLANTED_CLASSES

    text = read(project_root, "data-dictionary.md")
    qr = section(text, "Los 13 campos del QR de ARCA")
    assert re.findall(r"^\| `([A-Za-z]+)` \|", qr, flags=re.MULTILINE) == ["ver", "fecha", "cuit", "ptoVta", "tipoCmp", "nroCmp", "importe", "moneda", "ctz", "tipoDocRec", "nroDocRec", "tipoCodAut", "codAut"]
    planted = section(text, "Las clases sembradas")
    assert re.findall(r"^\| `([a-z_]+)` \|", planted, flags=re.MULTILINE) == list(PLANTED_CLASSES) and len(PLANTED_CLASSES) == 13
    match_reasons = [r["reason"] for r in json.loads((project_root / "outputs" / "site" / "algorithms.json").read_text(encoding="utf-8"))["match_rules"]]
    assert re.findall(r"^\| \d+ \| `([a-z_]+)` \|", section(text, "Los 18 motivos de revisión"), flags=re.MULTILINE) == match_reasons and len(match_reasons) == 18
    assert text.count(COLUMNS_START) == 1 and text.count(COLUMNS_END) == 1
    block = text.split(COLUMNS_START)[1].split(COLUMNS_END)[0]
    assert block.strip() == render_dictionary_tables(MODEL).strip()   # what docs_sync writes
    assert re.findall(r"^### `([a-z_]+)`$", block, flags=re.MULTILINE) == [t.name for t in MODEL.tables] and len(MODEL.tables) == 20
    for table in MODEL.tables:
        part = block.split(f"### `{table.name}`\n")[1].split("\n### ")[0]
        listed = re.findall(r"^\| `([a-z_0-9]+)` \| [^|]+ \| ([^|]+) \| ", part, flags=re.MULTILINE)
        assert tuple(name for name, _unit in listed) == table.column_names and all(unit.strip() for _name, unit in listed), table.name
        assert pq.read_schema(project_root / "outputs" / "powerbi" / f"{table.name}.parquet").names == list(table.column_names) == arrow_schema(table).names, table.name


RUNBOOK_TOPICS = {   # what each entry has to be about (the refusal messages of the code point here)
    1: ("s00_download.py", "SHA-256"), 2: ("V02", "CORD"), 3: ("cuarentena", "V12"), 4: ("entrega", "orden"), 5: ("CUDA", "ModelChoiceError", "M06"), 6: ("huella", "safetensors"),
    7: ("sello", "post_seal"), 8: ("bloqueo", "database is locked"), 9: ("ERPNext", "token", "409"), 10: ("Airflow", "Docker", "libzbar0t64"), 11: ("desde cero", "manifest"),
    12: ("análisis nuevo", "sello"),
}


def test_the_runbook_has_r1_to_r12_and_every_reference_of_the_code_points_to_one(project_root: Path) -> None:
    text = read(project_root, "runbook.md")
    entries = re.split(r"^## (?=R\d+ )", text, flags=re.MULTILINE)[1:]
    assert [entry.split()[0] for entry in entries] == [f"R{n}" for n in range(1, 13)]   # plan, section 25
    for entry in entries:
        assert "**Síntoma.**" in entry and "**Qué hacer.**" in entry, entry.splitlines()[0]
    for number, words in RUNBOOK_TOPICS.items():
        entry = entries[number - 1]
        assert [word for word in words if word.lower() not in entry.lower()] == [], f"R{number}"
    sources = [*sorted((project_root / "src" / "invoiceflow").rglob("*.py")), *sorted((project_root / "airflow").rglob("*.py")), project_root / "airflow" / "docker-compose.yaml"]
    cited = {int(n) for path in sources for n in re.findall(r"runbook(?:\.md)?,? R(\d+)", path.read_text(encoding="utf-8"))}
    assert {1, 5, 6, 7, 8, 11} <= cited <= set(range(1, 13)), cited   # every entry the code points to exists and is about that


def test_the_step_by_step_names_every_unit_and_cites_commits_that_exist(project_root: Path) -> None:
    text = read(project_root, "paso-a-paso.md")
    assert [unit for unit in UNITS_DONE if not re.search(rf"\b{unit}\b", text)] == []
    hashes = sorted(set(re.findall(r"`([0-9a-f]{7})`", text)))
    assert len(hashes) >= 40   # the 40 commits of the project up to the real run, and more
    if shutil.which("git") is None or not (project_root.parent / ".git").exists():
        pytest.skip("there is no Git history to check the commits against")
    done = subprocess.run(["git", "cat-file", "--batch-check"], cwd=project_root, input="".join(f"{h}^{{commit}}\n" for h in hashes), capture_output=True, text=True)
    assert [line for line in done.stdout.splitlines() if line.endswith("missing")] == []
    # the chronology is whole: every commit of the project up to the real run (U22) is in it
    history = subprocess.run(["git", "log", "--abbrev=7", "--format=%h", "94db289", "--", "."], cwd=project_root, capture_output=True, text=True)
    assert history.returncode == 0 and [h for h in history.stdout.split() if h not in hashes] == []


def test_relative_links_and_images_point_to_files_that_exist(project_root: Path, readme: str) -> None:
    """Every relative link and image of the README and of the documents resolves, and the README links to every document of ``docs/`` that exists."""
    broken = []
    for document in [project_root / "README.md", *sorted((project_root / "docs").glob("*.md"))]:
        text = document.read_text(encoding="utf-8")
        for target in [*LINK.findall(text), *IMAGE.findall(text)]:
            if not re.match(r"^(https?:|mailto:)", target) and not (document.parent / target).resolve().exists():
                broken.append(f"{document.name}: {target}")
    assert broken == []
    unlinked = [f"docs/{name}" for name in ("research.md", "requirements.md", "plan.md", "model-card.md", *DOCS) if f"](docs/{name})" not in readme]
    assert unlinked == []


def advisories(text: str) -> dict[tuple[str, str], str]:
    """Every advisory of the review file by (section key, id), with its status: the bold words after «Estado:», or an empty string."""
    found: dict[tuple[str, str], str] = {}
    for part in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        key = part.split()[0]
        for block in re.split(r"^(?=- \*\*)", part, flags=re.MULTILINE)[1:]:
            ident = re.match(r"- \*\*([^*]+)\*\*", block).group(1)
            status = re.search(r"^  - Estado: \*\*([^*]+)\*\*", block, flags=re.MULTILINE)
            assert (key, ident) not in found, (key, ident)
            found[(key, ident)] = status.group(1) if status else ""
    return found


ADVISORIES_IN_ALL = 38   # L96 (9), L98 (5), L100 (7), L102 (11), L106 (1), L108 (2) and L110 (3), as the log counts them


def test_open_advisories_match_review_file(project_root: Path, readme: str) -> None:
    """AC39 and B5 of project 03's evaluation: every finding of every native review of project 07 (L96 to L110) has its location and its status in ``docs/review-advisories.md``, the
    count the documents give is the number of blocks, and the number of open ones the README states is the number the file calls open."""
    text = read(project_root, "review-advisories.md")
    found = advisories(text)
    assert len(found) == ADVISORIES_IN_ALL and [key for key, status in found.items() if status not in STATUSES] == []
    blocks = [block for block in re.split(r"^(?=- \*\*)", text, flags=re.MULTILINE) if block.startswith("- **")]
    assert len(blocks) == ADVISORIES_IN_ALL and all(re.search(r"^  - Ubicación: .+", block, flags=re.MULTILINE) for block in blocks)   # where each one is
    fixed = [block for block in blocks if "Estado: **corregida**" in block]
    assert all(re.search(r"`[0-9a-f]{7}`", block.split("Estado: **corregida**")[1].splitlines()[0]) for block in fixed)   # each with its commit
    left = [block for block in blocks if re.search(r"Estado: \*\*(dejada con evidencia|refutada)\*\*\. \S", block)]
    opened = [block for block in blocks if "Estado: **abierta**" in block]
    assert len(fixed) + len(left) + len(opened) == ADVISORIES_IN_ALL and all(re.search(r"Estado: \*\*abierta\*\*\. \S", block) for block in opened)
    [stated] = re.findall(r"^Advertencias abiertas: (\d+)\b", readme, flags=re.MULTILINE)
    assert int(stated) == len(opened)
    for number in (f"{ADVISORIES_IN_ALL} en total", f"{ADVISORIES_IN_ALL} hallazgos"):
        assert number in text or number in readme or number in read(project_root, "paso-a-paso.md"), number
    for document in (readme, read(project_root, "paso-a-paso.md"), read(project_root, "model-card.md")):
        assert not re.search(r"\b(?!38\b)\d{2} (?:hallazgos|advertencias en total)", document), "a document gives another total than the file"
    assert "lens_context_budget_exceeded" in text and "U17" in text and "U19" in text and "94db289" in text   # what could not be reviewed is said


STALE_PENDING = re.compile(r"pendiente de la (?:evaluaci|comprobaci)|\*\*Pendiente\.\*\*|revisiones? de U22[^.\n]{0,40}pendientes?", flags=re.IGNORECASE)


def test_no_document_says_the_evaluation_is_pending_after_it_was_done(project_root: Path, readme: str) -> None:
    """Plan, section 28.4, and B4 and N1 of project 05 (RNF14): the independent evaluation of project 07 is not done yet, so ``docs/evaluation.md`` does not exist and the documents say
    it is pending; once it exists, no document says it is. The reviews of U22 are done (L110) and no document says they are pending."""
    documents = all_documents(project_root, readme)
    stale = [f"{name}: {match.group(0)}" for name, document in documents.items() for match in STALE_PENDING.finditer(document)]
    assert stale == []
    done = (project_root / "docs" / "evaluation.md").is_file()
    says_pending = [name for name in ("README.md", "docs/paso-a-paso.md") if re.search(r"evaluación independiente[^.\n]{0,160}todavía no", documents[name], flags=re.IGNORECASE)]
    assert (says_pending == []) == done, "the documents say the evaluation is pending exactly while docs/evaluation.md does not exist"


def test_the_root_rows_of_project_07_carry_the_real_figures(project_root: Path) -> None:
    rows = root_rows(project_root)
    table = [line for line in rows.split("### ")[0].splitlines() if re.match(r"^\| \d+ \|", line)]
    assert [int(line.split("|")[1]) for line in table] == list(range(1, 17))   # plan, section 30: the sixteen rows
    for figure in ("−4,19", "95,9", "83,1", "487 de 586", "69,7", "147 de 211", "167 de 167", "7,7", "147 de 147", "189", "1.032", "51884acb", "15.124.4", "417", "94,00", "93,65"):
        assert figure in rows, figure
    assert rows.count("No se cumple") + rows.count("No cumple") >= 5 and all(f"C{n:02d}" in rows for n in range(1, 10)) and "PeakWorkingSetSize" in rows
    assert "No aplicado en el sitio todavía" in rows and "invoices-erp" in rows and "Airflow" in rows and "sintétic" in rows and "@gmail" not in rows
    assert "n8n" not in rows.replace("n8n por Airflow", "").replace("n8n (si algún texto lo conserva)", "") or "Airflow" in rows
    assert not LOCAL_PATH.search(rows) and "Users" not in rows


def test_the_landing_expansion_has_the_rows_of_project_07(project_root: Path) -> None:
    """D34: the nine rows U01 left owed (``PENDING_LANDING_ROWS``, now empty) name project 07, and project 07 is added to the rows of the libraries it shares."""
    text = (project_root.parent / "LANDING-EXPANSION.md").read_text(encoding="utf-8")
    rows = {line.split("|")[1].strip(): line for line in text.splitlines() if re.match(r"^\| [^|]+ \|", line)}
    for technology in ("NumPy", "SQLite", "Apache Parquet con pyarrow", "matplotlib", "pytest", "PostgreSQL", "pydantic", "uvicorn", "httpx"):
        assert "07 Facturas" in rows[technology], technology
    new = [name for name in rows if "07 Facturas" in rows[name] and not any(other in rows[name] for other in ("01 Producción",))]
    owed = ["pillow", "qrcode", "pyzbar", "opencv-python-headless", "pypdf", "torch", "transformers", "tokenizers", "sentencepiece", "huggingface-hub", "safetensors", "zss"]
    names = " ".join(name.lower() for name in rows)
    assert [pin for pin in owed if pin not in names] == []
    assert len([name for name in new if name.lower() not in {"numpy", "sqlite", "apache parquet con pyarrow", "matplotlib", "pytest", "postgresql", "pydantic", "uvicorn", "httpx"}]) >= 7
    assert re.search(r"^PENDING_LANDING_ROWS = frozenset\(\)$", (project_root / "tests" / "unit" / "test_dependencies.py").read_text(encoding="utf-8"), flags=re.MULTILINE)


def test_no_document_publishes_a_local_path_or_a_user_name(project_root: Path, readme: str) -> None:
    """The README, every document of ``docs/`` and the root rows name no path of the machine and no user name."""
    found = [name for name, text in all_documents(project_root, readme).items() if LOCAL_PATH.search(text) or any(user in EMAIL.sub("", text).lower() for user in USER_NAMES)]
    assert found == []


def _pct(value: float) -> str:
    return f"{value * 100:,.1f} %".replace(",", "§").replace(".", ",").replace("§", ".")


def test_the_reason_table_of_the_model_card_is_the_one_of_the_site_files(project_root: Path, model_card: str) -> None:
    """Each row of the table of the first reasons is the sum of ``operation.json`` over the 20 days, and the QR table is ``algorithms.json`` summed over the cohorts, at the
    decimals the card writes."""
    operation = json.loads((project_root / "outputs" / "site" / "operation.json").read_text(encoding="utf-8"))
    algorithms = json.loads((project_root / "outputs" / "site" / "algorithms.json").read_text(encoding="utf-8"))
    reasons: dict[str, int] = {}
    for day in operation["days"]:
        for reason, count in day["by_reason"].items():
            reasons[reason] = reasons.get(reason, 0) + count
    review = sum(day["review"] for day in operation["days"])
    assert sum(reasons.values()) == review == 453 and sum(day["auto_post"] for day in operation["days"]) == 147
    lines = model_card.splitlines()
    rows = [f"| `{reason}` | {count} | {_pct(count / review)} |" for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0]))]
    qr: dict[tuple[str, float], list[int]] = {}
    for item in algorithms["qr"]:
        qr.setdefault((item["scan"], item["qr_cm"]), [0, 0])
        qr[(item["scan"], item["qr_cm"])][0] += item["printed"]
        qr[(item["scan"], item["qr_cm"])][1] += item["read_ok"]
    rows += [f"| {scan} | {str(cm).replace('.', ',')} cm | {printed} | {ok} | {_pct(ok / printed)} |" for (scan, cm), (printed, ok) in sorted(qr.items())]
    for row in rows:
        assert row in lines, row
    assert len(rows) == len(reasons) + 9


TABLE_DELIMITER = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$")


def _cells(line: str) -> int:
    return len(re.split(r"(?<!\\)\|", line.strip().removeprefix("|").removesuffix("|")))


def test_every_markdown_table_row_has_the_header_width(project_root: Path, readme: str) -> None:
    """06's L84: a table with a delimiter row of other width than its header does not render on GitHub. Every table of the README and of ``docs/``, generated or written by hand, has as many
    cells in its delimiter and in each of its rows as in its header."""
    documents = {"README.md": readme} | {f"docs/{p.name}": read(project_root, p.name) for p in sorted((project_root / "docs").glob("*.md"))}
    broken, tables = [], 0
    for name, text in documents.items():
        lines, fenced = text.splitlines(), False
        for at, line in enumerate(lines):
            fenced = fenced != line.startswith("```")
            if fenced or not TABLE_DELIMITER.match(line):
                continue
            tables += 1
            width = _cells(lines[at - 1])
            rows = lines[at:]
            body = rows[: next((k for k, row in enumerate(rows) if not row.startswith("|")), len(rows))]
            broken += [f"{name}:{at + 1 + k}" for k, row in enumerate(body) if _cells(row) != width]
    assert broken == [] and tables >= 30


CONTROL_BYTES = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")   # everything but \t, \n and (in a CRLF pair) \r


def test_no_markdown_file_holds_a_control_byte(project_root: Path) -> None:
    """N1 of the recheck: a Windows path written in a script string lost its backslashes («data\\raw\\» became a carriage return and «aw\\»; «\\arca» a BEL) and R11 step 1 named
    paths that do not exist. No Markdown file of the README or ``docs/`` holds a control byte other than a tab and a line break (a carriage return only as the first half of CRLF)."""
    files = [project_root / "README.md", *sorted((project_root / "docs").glob("*.md"))]
    assert len(files) >= 8
    found = []
    for path in files:
        data = path.read_bytes()
        lone_cr = re.sub(rb"\r\n", b"", data).count(b"\r")
        found += [f"{path.name}: {hit.group(0)!r} at {hit.start()}" for hit in CONTROL_BYTES.finditer(data)]
        found += [f"{path.name}: {lone_cr} lone carriage return(s)"] * bool(lone_cr)
    assert found == []
