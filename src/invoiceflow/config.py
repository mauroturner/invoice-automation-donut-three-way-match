"""Pipeline configuration: one TOML file, typed, immutable and checked key by key (plan, section 4).

``config/pipeline.toml`` holds every default. A file may start from another one with a top-level ``extends = "<path>"``
(``config/pipeline.quick.toml`` does). Every relative path is resolved against the project folder, never against the working
directory (decision D30): the paths inside the configuration (``[source]`` and ``[paths]``) against ``$INVOICEFLOW_HOME`` when it
is set, otherwise the parent of the folder that holds the main configuration file; a relative configuration file
(``$INVOICEFLOW_CONFIG`` or the ``path`` argument, which ``--config`` passes) against ``$INVOICEFLOW_HOME`` or, without it, the folder of
this package's project.

Anything unknown, missing, mistyped or inconsistent is a ``ValueError`` that names the key: an option that is accepted and silently
not used is the defect projects 01 and 02 both had.

Every leaf of the configuration is either in ``FINGERPRINT_KEYS`` (it can change a sealed value, so it enters the seal's
``config_sha256``) or in ``UNSEALED_KEYS`` (it cannot), never both and never neither (decision D21). The sealed calculations
receive ``SealedConfig``, which holds only the fingerprinted keys and the observed hashes of the CORD files and the ARCA PDF. The hash of
the TOML file itself is a different thing (``config_file_sha256``, project 05's A8) and never appears here. ``INFORMATIVE_KEYS`` names the
keys that change nothing a run produces (none yet): ``tests/e2e/test_cli.py`` (U19) walks every key and each must change the result or be
listed there.
"""

from __future__ import annotations

import dataclasses
import math
import os
import re
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = DEFAULT_PROJECT_ROOT / "config" / "pipeline.toml"

ENV_CONFIG = "INVOICEFLOW_CONFIG"
ENV_HOME = "INVOICEFLOW_HOME"
CORD_SPLIT_NAMES = ("train", "validation", "test")
LAYOUT_NAMES = ("L1", "L2", "L3", "L4", "L5")
QR_READER_NAMES = ("pyzbar", "aruco")
INTAKE_FORMAT_NAMES = ("png", "jpeg", "pdf")
MODEL_KINDS = ("donut", "tiny")   # "stub" and any other kind exist only while the tests register them (register_model_kind)
ERP_MODES = ("standin", "http")
INFER_DEVICES = ("auto", "cuda", "cpu")
PLANTED_CLASSES = 13              # the classes of plan section 7.3: planted_rate is per class, so 13 of them must fit in one invoice's chance
CUIT_SERIAL_LIMITS = (1, 9999)    # the serial of 30-0000xxxx-d is four digits and 0 is left out (D05)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_TASK_TOKEN = re.compile(r"<s_[A-Za-z0-9_-]+>")
_HOST = re.compile(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MAX_SEED = 2**32 - 1   # the largest seed that numpy's legacy generators accept
_TYPE_NAMES = {
    bool: "verdadero o falso", int: "un entero", float: "un número", str: "un texto", date: "una fecha (AAAA-MM-DD, sin hora)",
    Path: "una ruta",
}
_registered_kinds: set[str] = set()


@dataclass(frozen=True)
class CordFileConfig:
    split: str
    name: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class SourceConfig:
    cord_dir: Path
    cord_commit: str
    max_shard_bytes: int
    max_rows: int
    max_image_pixels: int
    max_gt_depth: int
    arca_spec_path: Path
    arca_spec_bytes: int
    arca_spec_sha256: str
    cord_files: tuple[CordFileConfig, ...]


@dataclass(frozen=True)
class CalendarConfig:
    history_start: date
    launch_last_day: date
    test_first_day: date
    test_last_day: date
    weekdays_only: bool


@dataclass(frozen=True)
class ScanProfilesConfig:
    clean: float
    mild: float
    poor: float


@dataclass(frozen=True)
class FileFormatsConfig:
    png: float
    jpeg: float
    pdf: float


@dataclass(frozen=True)
class SynthConfig:
    seed_master: int
    seed_train: int
    seed_validation: int
    seed_test: int
    n_train: int
    n_validation_known: int
    n_validation_new: int
    test_per_day_known: int
    test_per_day_new: int
    suppliers_known: int
    suppliers_new_validation: int
    suppliers_new_test: int
    layouts_known: tuple[str, ...]
    layout_validation_new: str
    layout_test_new: str
    fonts_known: tuple[str, ...]
    font_validation_new: str
    font_test_new: str
    dpi: int
    scan_profiles: ScanProfilesConfig
    qr_cm: tuple[float, ...]
    file_formats: FileFormatsConfig
    max_lines: int
    usd_share: float
    iva105_share: float
    planted_rate: float
    cuit_serial_range: tuple[int, ...]


@dataclass(frozen=True)
class QrConfig:
    readers: tuple[str, ...]
    max_text_bytes: int
    hosts: tuple[str, ...]
    path: str


@dataclass(frozen=True)
class IntakeConfig:
    max_file_bytes: int
    max_pixels: int
    formats: tuple[str, ...]
    pdf_max_objects: int


@dataclass(frozen=True)
class ModelConfig:
    kind: str
    base: str
    base_revision: str
    task_cord: str
    task_invoice: str
    input_size: tuple[int, ...]
    max_length: int
    epochs: int
    candidate_epochs: tuple[int, ...]
    learning_rate: float
    warmup_steps: int
    batch_size: int
    grad_accumulation: int
    bf16: bool
    seed: int
    cord_val_tolerance: float
    infer_device: str


@dataclass(frozen=True)
class MatchConfig:
    price_tolerance_pct: float
    qty_over_receipt: float
    rounding_per_line: float
    total_qr_tolerance: float
    supported_types: tuple[int, ...]
    require_qr: bool


@dataclass(frozen=True)
class ErpConfig:
    mode: str
    base_url: str
    token_env: str
    post_as: str
    timeout_s: int


@dataclass(frozen=True)
class EvaluationConfig:
    resamples: int
    seed: int


@dataclass(frozen=True)
class CriteriaConfig:
    c01_min_ted_diff: float
    c02_min_line_exact: float
    c03_min_line_exact: float
    c04_min_qr_read: float
    c05_max_bad_post_upper: float
    c06_min_straight_through: float
    c07_min_review_recall: float


@dataclass(frozen=True)
class ServiceConfig:
    host: str
    port: int
    token_env: str


@dataclass(frozen=True)
class SiteConfig:
    max_total_bytes: int


@dataclass(frozen=True)
class PathsConfig:
    raw_dir: Path
    interim_dir: Path
    warehouse: Path
    models_dir: Path
    erp_db: Path
    outputs_dir: Path
    site_dir: Path
    reports_dir: Path
    seal: Path
    dashboard_html: Path
    readme: Path
    figures_dir: Path


@dataclass(frozen=True)
class PipelineConfig:
    source: SourceConfig
    calendar: CalendarConfig
    synth: SynthConfig
    qr: QrConfig
    intake: IntakeConfig
    model: ModelConfig
    match: MatchConfig
    erp: ErpConfig
    evaluation: EvaluationConfig
    criteria: CriteriaConfig
    service: ServiceConfig
    site: SiteConfig
    paths: PathsConfig
    project_root: Path


# --- the seal's view of the configuration (D21) ------------------------------------------------------------------------


@dataclass(frozen=True)
class SealedSourceConfig:
    """The three keys of ``[source]`` that can change a sealed value: the commit of the dataset and the two limits of what is read from it. The
    paths, the accepted sizes and hashes and the other limits cannot; the hashes of the files that were actually read enter as
    ``SealedConfig.cord_sha256_seen`` and ``arca_sha256_seen``."""

    cord_commit: str
    max_image_pixels: int
    max_gt_depth: int


@dataclass(frozen=True)
class SealedErpConfig:
    """The one key of ``[erp]`` that can change a sealed value: what is written to the ERP (a draft). Where the ERP is and how it is reached cannot."""

    post_as: str


@dataclass(frozen=True)
class SealedConfig:
    """What the sealed calculations receive (the generator, the QR, the intake, the model, the match and the evaluation): only the keys of
    ``FINGERPRINT_KEYS`` and the observed hashes of the files. A key outside it cannot reach a sealed value."""

    source: SealedSourceConfig
    calendar: CalendarConfig
    synth: SynthConfig
    qr: QrConfig
    intake: IntakeConfig
    model: ModelConfig
    match: MatchConfig
    erp: SealedErpConfig
    evaluation: EvaluationConfig
    criteria: CriteriaConfig
    cord_sha256_seen: tuple[str, ...]
    arca_sha256_seen: str

    @classmethod
    def of(cls, cfg: PipelineConfig, cord_sha256: tuple[str, ...], arca_sha256: str) -> SealedConfig:
        """The sealed view of `cfg` for the CORD files (in the order of ``source.cord_files``) and the ARCA PDF that hashed to these values."""
        wanted = len(cfg.source.cord_files)
        if len(cord_sha256) != wanted:
            raise ValueError(f"cord_sha256_seen: se esperaban {wanted} huellas (una por fragmento de source.cord_files), se recibieron {len(cord_sha256)}")
        for index, value in enumerate(cord_sha256):
            _check_hash_seen(value, f"cord_sha256_seen[{index}]")
        _check_hash_seen(arca_sha256, "arca_sha256_seen")
        skipped = ("source", "erp", "cord_sha256_seen", "arca_sha256_seen")
        sections = {f.name: getattr(cfg, f.name) for f in dataclasses.fields(cls) if f.name not in skipped}
        source = SealedSourceConfig(cord_commit=cfg.source.cord_commit, max_image_pixels=cfg.source.max_image_pixels,
                                    max_gt_depth=cfg.source.max_gt_depth)
        return cls(source=source, erp=SealedErpConfig(post_as=cfg.erp.post_as), **sections,
                   cord_sha256_seen=tuple(cord_sha256), arca_sha256_seen=arca_sha256)


def _check_hash_seen(value: str, where: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{where}: se esperaban 64 caracteres hexadecimales en minúscula, se recibió {value!r}")


def config_leaves(cls: type, prefix: str = "") -> Iterator[str]:
    """The full name of every leaf of a configuration dataclass, ``section.key`` (``section.table.key`` inside an inline table), in
    declaration order. A list of tables (``source.cord_files``) is one leaf."""
    hints = get_type_hints(cls)
    for f in dataclasses.fields(cls):
        name = f"{prefix}{f.name}"
        if dataclasses.is_dataclass(hints[f.name]):
            yield from config_leaves(hints[f.name], f"{name}.")
        else:
            yield name


# Every key that can change a sealed value, named one by one so that a new key fails tests/unit/test_config.py until it is classified.
# Including one too many only turns a cosmetic change into a post_seal run; one too few is project 03's defect B4 (D21). The observed
# hashes of the CORD files and the PDF (cord_sha256_seen, arca_sha256_seen) are not configured: SealedConfig adds them, and the master's and
# the fonts' hashes join the fingerprint where the seal is written (U14).
FINGERPRINT_KEYS: tuple[str, ...] = (
    "source.cord_commit", "source.max_image_pixels", "source.max_gt_depth",
    "calendar.history_start", "calendar.launch_last_day", "calendar.test_first_day", "calendar.test_last_day", "calendar.weekdays_only",
    "synth.seed_master", "synth.seed_train", "synth.seed_validation", "synth.seed_test",
    "synth.n_train", "synth.n_validation_known", "synth.n_validation_new", "synth.test_per_day_known", "synth.test_per_day_new",
    "synth.suppliers_known", "synth.suppliers_new_validation", "synth.suppliers_new_test",
    "synth.layouts_known", "synth.layout_validation_new", "synth.layout_test_new",
    "synth.fonts_known", "synth.font_validation_new", "synth.font_test_new",
    "synth.dpi", "synth.scan_profiles.clean", "synth.scan_profiles.mild", "synth.scan_profiles.poor", "synth.qr_cm",
    "synth.file_formats.png", "synth.file_formats.jpeg", "synth.file_formats.pdf",
    "synth.max_lines", "synth.usd_share", "synth.iva105_share", "synth.planted_rate", "synth.cuit_serial_range",
    "qr.readers", "qr.max_text_bytes", "qr.hosts", "qr.path",
    "intake.max_file_bytes", "intake.max_pixels", "intake.formats", "intake.pdf_max_objects",
    "model.kind", "model.base", "model.base_revision", "model.task_cord", "model.task_invoice", "model.input_size", "model.max_length",
    "model.epochs", "model.candidate_epochs", "model.learning_rate", "model.warmup_steps", "model.batch_size", "model.grad_accumulation",
    "model.bf16", "model.seed", "model.cord_val_tolerance", "model.infer_device",
    "match.price_tolerance_pct", "match.qty_over_receipt", "match.rounding_per_line", "match.total_qr_tolerance", "match.supported_types",
    "match.require_qr",
    "erp.post_as",
    "evaluation.resamples", "evaluation.seed",
    "criteria.c01_min_ted_diff", "criteria.c02_min_line_exact", "criteria.c03_min_line_exact", "criteria.c04_min_qr_read",
    "criteria.c05_max_bad_post_upper", "criteria.c06_min_straight_through", "criteria.c07_min_review_recall",
)
# Every key that cannot: where the files are and their accepted sizes, hashes and limits (the hashes of the files that were read enter
# instead), where the ERP is and how it is reached, the services, the site's limit and where the outputs go.
UNSEALED_KEYS: tuple[str, ...] = (
    "source.cord_dir", "source.max_shard_bytes", "source.max_rows", "source.arca_spec_path", "source.arca_spec_bytes", "source.arca_spec_sha256",
    "source.cord_files",
    "erp.mode", "erp.base_url", "erp.token_env", "erp.timeout_s",
    "service.host", "service.port", "service.token_env",
    "site.max_total_bytes",
    "paths.raw_dir", "paths.interim_dir", "paths.warehouse", "paths.models_dir", "paths.erp_db", "paths.outputs_dir", "paths.site_dir",
    "paths.reports_dir", "paths.seal", "paths.dashboard_html", "paths.readme", "paths.figures_dir",
)
# Keys that change nothing a run produces (none yet). A key listed here is declared informative and exempt from the test that each key changes
# the result (plan section 24.2, "Una clave de configuración validada y nunca leída").
INFORMATIVE_KEYS: tuple[str, ...] = ()


def register_model_kind(kind: str) -> None:
    """Accept ``[model] kind = <kind>`` while it is registered: ``extractors.register`` (U09) does it for the tests' stub, and only for them: the real
    configuration never accepts a kind nobody registered."""
    if kind in MODEL_KINDS:
        raise ValueError(f"[model] kind: {kind!r} es un valor propio ({', '.join(MODEL_KINDS)}) y no se registra")
    _registered_kinds.add(kind)


def unregister_model_kind(kind: str) -> None:
    _registered_kinds.discard(kind)


# --- loading --------------------------------------------------------------------------------------------------------


def _resolve(value: str, root: Path, where: str) -> Path:
    if not value.strip():
        raise ValueError(f"{where}: se esperaba una ruta, se recibió un texto vacío")
    path = Path(value)
    if path.is_absolute():
        return path  # an absolute path is explicit: it may point anywhere on purpose
    joined = Path(os.path.normpath(root / path))
    base = Path(os.path.normpath(root))
    if joined != base and base not in joined.parents:
        raise ValueError(
            f"{where}: la ruta relativa {value!r} sale de la carpeta del proyecto ({base}); "
            "use una ruta absoluta si el destino está fuera a propósito"
        )
    return joined


def _coerce(value: Any, annotation: Any, where: str, root: Path, name: str) -> Any:
    """Convert a TOML value to the annotated type or raise ValueError naming `where`; `name` is the dotted name of the section that holds it."""
    origin = get_origin(annotation)
    if origin is tuple:
        (element, _ellipsis) = get_args(annotation)
        if not isinstance(value, list):
            raise ValueError(f"{where}: se esperaba una lista, se recibió {type(value).__name__}")
        if dataclasses.is_dataclass(element):
            key = where.split("] ", 1)[1]
            return tuple(_build_section(element, v, f"{name}.{key}[{index}]", root) for index, v in enumerate(value))
        return tuple(_coerce(v, element, where, root, name) for v in value)
    if dataclasses.is_dataclass(annotation):
        key = where.split("] ", 1)[1]
        return _build_section(annotation, value, f"{name}.{key}", root)
    if annotation is bool:
        ok = isinstance(value, bool)
    elif annotation is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif annotation is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        value = float(value) if ok else value
    elif annotation is str:
        ok = isinstance(value, str)
    elif annotation is date:
        ok = type(value) is date   # a local date: no date-time (a subclass of date), no text
    elif annotation is Path:
        if isinstance(value, str):
            return _resolve(value, root, where)
        ok = False
    else:  # pragma: no cover - only reachable by adding an unsupported annotation
        raise TypeError(f"{where}: unsupported annotation {annotation!r}")
    if not ok:
        raise ValueError(f"{where}: se esperaba {_TYPE_NAMES.get(annotation, annotation)}, se recibió {value!r}")
    return value


def _build_section(cls: type, data: Any, name: str, root: Path) -> Any:
    if not isinstance(data, dict):
        raise ValueError(f"[{name}]: se esperaba una tabla, se recibió {type(data).__name__}")
    hints = get_type_hints(cls)
    fields = [f.name for f in dataclasses.fields(cls)]
    unknown = sorted(set(data) - set(fields))
    if unknown:
        raise ValueError(f"[{name}]: clave desconocida {unknown[0]!r}")
    missing = [f for f in fields if f not in data]
    if missing:
        raise ValueError(f"[{name}]: falta la clave {missing[0]!r}")
    return cls(**{f: _coerce(data[f], hints[f], f"[{name}] {f}", root, name) for f in fields})


# --- consistency checks ---------------------------------------------------------------------------------------------


def _positive(value: float, where: str) -> None:
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"{where}: se esperaba un valor finito mayor que 0, se recibió {value!r}")


def _non_negative(value: float, where: str) -> None:
    if not (math.isfinite(value) and value >= 0):
        raise ValueError(f"{where}: se esperaba un valor finito desde 0, se recibió {value!r}")


def _at_least(value: int, minimum: int, where: str) -> None:
    if value < minimum:
        raise ValueError(f"{where}: se esperaba {minimum} o más, se recibió {value!r}")


def _between(value: int, low: int, high: int, where: str) -> None:
    if not low <= value <= high:
        raise ValueError(f"{where}: se esperaba un valor de {low} a {high}, se recibió {value!r}")


def _seed(value: int, where: str) -> None:
    if not 0 <= value <= MAX_SEED:
        raise ValueError(f"{where}: se esperaba un entero de 0 a {MAX_SEED}, se recibió {value!r}")


def _fraction(value: float, where: str, *, closed_high: bool = False) -> None:
    if not (math.isfinite(value) and (0 < value <= 1 if closed_high else 0 < value < 1)):
        interval = "(0, 1]" if closed_high else "(0, 1)"
        raise ValueError(f"{where}: se esperaba un valor en {interval}, se recibió {value!r}")


def _share(value: float, where: str) -> None:
    if not (math.isfinite(value) and 0 <= value <= 1):
        raise ValueError(f"{where}: se esperaba un valor en [0, 1], se recibió {value!r}")


def _names(values: tuple[str, ...], allowed: tuple[str, ...], where: str) -> None:
    if not values or len(set(values)) != len(values):
        raise ValueError(f"{where}: se esperaba una lista no vacía y sin repetidos, se recibió {list(values)}")
    for value in values:
        if value not in allowed:
            raise ValueError(f"{where}: {value!r} no es un valor conocido ({', '.join(allowed)})")


def _text(value: str, where: str) -> None:
    if not value.strip() or value != value.strip():
        raise ValueError(f"{where}: se esperaba un texto no vacío y sin espacios en los bordes, se recibió {value!r}")


def _check_hash(value: str, where: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{where}: se esperaban 64 caracteres hexadecimales en minúscula, se recibió {value!r}")


def _quick_folder(cfg: PipelineConfig) -> Path:
    return Path(os.path.normpath(cfg.project_root / "data" / "quick"))


def _under(path: Path, folder: Path) -> bool:
    return folder in Path(os.path.normpath(path)).parents


def _check_accepted(path: Path, size: int, digest: str, bytes_where: str, sha_where: str, limit: int | None, limit_name: str, quick: Path) -> None:
    """The accepted size and hash of one source file go together: both set, or both empty and the file under data/quick/ (the first view is
    recorded there by the quick mode)."""
    _at_least(size, 0, bytes_where)
    if digest:
        _check_hash(digest, sha_where)
        if size == 0:
            raise ValueError(f"{bytes_where}: con una huella aceptada se esperaba un tamaño mayor que 0")
        if limit is not None and size > limit:
            raise ValueError(f"{bytes_where}: el tamaño aceptado ({size}) supera {limit_name} ({limit})")
    else:
        if not _under(path, quick):
            raise ValueError(
                f"{sha_where}: vacía solo se admite en el modo rápido, con la fuente bajo data/quick/ ({quick}); "
                f"la fuente real exige los 64 caracteres de su huella (se recibió la ruta {path})"
            )
        if size != 0:
            raise ValueError(f"{bytes_where}: con la huella vacía (modo rápido) se esperaba 0, se recibió {size}")


def _check_source(cfg: PipelineConfig) -> None:
    s = cfg.source
    for key in ("max_shard_bytes", "max_rows", "max_image_pixels", "max_gt_depth"):
        _at_least(getattr(s, key), 1, f"[source] {key}")
    if not _COMMIT.fullmatch(s.cord_commit):
        raise ValueError(f"[source] cord_commit: se esperaban 40 caracteres hexadecimales en minúscula, se recibió {s.cord_commit!r}")
    quick = _quick_folder(cfg)
    _check_accepted(s.arca_spec_path, s.arca_spec_bytes, s.arca_spec_sha256, "[source] arca_spec_bytes", "[source] arca_spec_sha256", None, "", quick)
    seen: set[str] = set()
    for index, f in enumerate(s.cord_files):
        where = f"[source] cord_files[{index}]"
        if f.split not in CORD_SPLIT_NAMES:
            raise ValueError(f"{where} split: se esperaba uno de {', '.join(CORD_SPLIT_NAMES)}, se recibió {f.split!r}")
        if not f.name or f.name in (".", "..") or any(char in f.name for char in "/\\\0"):
            raise ValueError(f"{where} name: se esperaba el nombre de un archivo sin carpetas, se recibió {f.name!r}")
        if f.name in seen:
            raise ValueError(f"[source] cord_files: el nombre {f.name!r} está repetido")
        seen.add(f.name)
        _check_accepted(s.cord_dir / f.name, f.bytes, f.sha256, f"{where} bytes", f"{where} sha256", s.max_shard_bytes, "max_shard_bytes", quick)
    for split in CORD_SPLIT_NAMES:
        if not any(f.split == split for f in s.cord_files):
            raise ValueError(f"[source] cord_files: falta un archivo de la partición {split!r} (se esperaba al menos uno de cada una de {', '.join(CORD_SPLIT_NAMES)})")


def _check_calendar(cfg: PipelineConfig) -> None:
    c = cfg.calendar
    if not c.history_start < c.launch_last_day:
        raise ValueError(f"[calendar] history_start: se esperaba history_start < launch_last_day, se recibió {c.history_start} y {c.launch_last_day}")
    if not c.launch_last_day < c.test_first_day:
        raise ValueError(f"[calendar] test_first_day: se esperaba un día posterior a launch_last_day ({c.launch_last_day}), se recibió {c.test_first_day}")
    if not c.test_first_day <= c.test_last_day:
        raise ValueError(f"[calendar] test_last_day: se esperaba test_last_day >= test_first_day ({c.test_first_day}), se recibió {c.test_last_day}")
    if c.weekdays_only:
        days = (c.test_first_day + timedelta(days=n) for n in range((c.test_last_day - c.test_first_day).days + 1))
        if not any(day.weekday() < 5 for day in days):
            raise ValueError(f"[calendar] test_first_day: con weekdays_only no hay ningún día hábil entre {c.test_first_day} y {c.test_last_day}")


def _check_synth(cfg: PipelineConfig) -> None:
    y = cfg.synth
    for key in ("seed_master", "seed_train", "seed_validation", "seed_test"):
        _seed(getattr(y, key), f"[synth] {key}")
    for key in ("n_train", "n_validation_known", "n_validation_new", "test_per_day_known", "test_per_day_new", "suppliers_known",
                "suppliers_new_validation", "suppliers_new_test"):
        _at_least(getattr(y, key), 1, f"[synth] {key}")
    _names(y.layouts_known, LAYOUT_NAMES, "[synth] layouts_known")
    _names((y.layout_validation_new,), LAYOUT_NAMES, "[synth] layout_validation_new")
    _names((y.layout_test_new,), LAYOUT_NAMES, "[synth] layout_test_new")
    if y.layout_validation_new in y.layouts_known:
        raise ValueError(f"[synth] layout_validation_new: se esperaba un diseño que no esté en layouts_known, se recibió {y.layout_validation_new!r}")
    if y.layout_test_new in y.layouts_known:
        raise ValueError(f"[synth] layout_test_new: se esperaba un diseño que no esté en layouts_known, se recibió {y.layout_test_new!r}")
    if y.layout_test_new == y.layout_validation_new:
        raise ValueError(f"[synth] layout_test_new: se esperaba un diseño distinto de layout_validation_new ({y.layout_validation_new!r})")
    if not y.fonts_known or len(set(y.fonts_known)) != len(y.fonts_known):
        raise ValueError(f"[synth] fonts_known: se esperaba una lista no vacía y sin repetidos, se recibió {list(y.fonts_known)}")
    for font in y.fonts_known:
        _text(font, "[synth] fonts_known")
    _text(y.font_validation_new, "[synth] font_validation_new")
    _text(y.font_test_new, "[synth] font_test_new")
    if y.font_validation_new in y.fonts_known:
        raise ValueError(f"[synth] font_validation_new: se esperaba una fuente que no esté en fonts_known, se recibió {y.font_validation_new!r}")
    if y.font_test_new in y.fonts_known:
        raise ValueError(f"[synth] font_test_new: se esperaba una fuente que no esté en fonts_known, se recibió {y.font_test_new!r}")
    if y.font_test_new == y.font_validation_new:
        raise ValueError(f"[synth] font_test_new: se esperaba una fuente distinta de font_validation_new ({y.font_validation_new!r})")
    _between(y.dpi, 50, 600, "[synth] dpi")
    for name, table in (("scan_profiles", y.scan_profiles), ("file_formats", y.file_formats)):
        parts = dataclasses.asdict(table)
        for key, value in parts.items():
            if not (math.isfinite(value) and value >= 0):
                raise ValueError(f"[synth] {name}: la parte {key} debe ser un número finito desde 0, se recibió {value!r}")
        if abs(sum(parts.values()) - 1.0) > 1e-9:
            raise ValueError(f"[synth] {name}: las partes deben sumar 1, suman {sum(parts.values())!r}")
    if not y.qr_cm:
        raise ValueError("[synth] qr_cm: se esperaba una lista no vacía de tamaños en centímetros")
    for size in y.qr_cm:
        _positive(size, "[synth] qr_cm")
    _between(y.max_lines, 1, 30, "[synth] max_lines")
    _share(y.usd_share, "[synth] usd_share")
    _share(y.iva105_share, "[synth] iva105_share")
    if not (math.isfinite(y.planted_rate) and 0 <= y.planted_rate <= 1 / PLANTED_CLASSES):
        raise ValueError(f"[synth] planted_rate: se esperaba un valor de 0 a 1/{PLANTED_CLASSES} (las {PLANTED_CLASSES} clases suman a lo más 1), se recibió {y.planted_rate!r}")
    if len(y.cuit_serial_range) != 2:
        raise ValueError(f"[synth] cuit_serial_range: se esperaban dos números (el menor y el mayor), se recibió {list(y.cuit_serial_range)}")
    low, high = y.cuit_serial_range
    if not (CUIT_SERIAL_LIMITS[0] <= low and high <= CUIT_SERIAL_LIMITS[1]):
        raise ValueError(f"[synth] cuit_serial_range: se esperaba un rango entre {CUIT_SERIAL_LIMITS[0]} y {CUIT_SERIAL_LIMITS[1]}, se recibió {list(y.cuit_serial_range)}")
    if low > high:
        raise ValueError(f"[synth] cuit_serial_range: se esperaba un rango ordenado (menor ≤ mayor), se recibió {list(y.cuit_serial_range)}")
    needed = y.suppliers_known + y.suppliers_new_validation + y.suppliers_new_test + 1   # and the buyer
    if high - low + 1 < needed:
        raise ValueError(f"[synth] cuit_serial_range: el rango ({high - low + 1} series) no alcanza para {needed} CUIT distintos (proveedores y comprador)")


def _check_model(cfg: PipelineConfig) -> None:
    m = cfg.model
    if m.kind not in MODEL_KINDS and m.kind not in _registered_kinds:
        raise ValueError(f"[model] kind: {m.kind!r} no es un valor conocido ({', '.join(MODEL_KINDS)}) ni lo registró un extractor de prueba")
    if m.kind == "tiny":
        quick = _quick_folder(cfg)
        places = [("[source] cord_dir", cfg.source.cord_dir), ("[source] arca_spec_path", cfg.source.arca_spec_path)]
        places += [(f"[paths] {f.name}", getattr(cfg.paths, f.name)) for f in dataclasses.fields(cfg.paths)]
        for where, path in places:
            if not _under(path, quick):
                raise ValueError(f"[model] kind: 'tiny' solo se admite con todo bajo data/quick/ ({quick}); {where} ({path}) está fuera")
    _text(m.base, "[model] base")
    if not _COMMIT.fullmatch(m.base_revision):
        raise ValueError(f"[model] base_revision: se esperaban 40 caracteres hexadecimales en minúscula, se recibió {m.base_revision!r}")
    for key in ("task_cord", "task_invoice"):
        if not _TASK_TOKEN.fullmatch(getattr(m, key)):
            raise ValueError(f"[model] {key}: se esperaba una ficha de tarea como <s_nombre>, se recibió {getattr(m, key)!r}")
    if m.task_invoice == m.task_cord:
        raise ValueError(f"[model] task_invoice: se esperaba una tarea distinta de task_cord ({m.task_cord!r})")
    if len(m.input_size) != 2 or min(m.input_size) < 1:
        raise ValueError(f"[model] input_size: se esperaban dos enteros mayores que 0 (alto y ancho), se recibió {list(m.input_size)}")
    for key in ("max_length", "epochs", "batch_size", "grad_accumulation"):
        _at_least(getattr(m, key), 1, f"[model] {key}")
    if not m.candidate_epochs:
        raise ValueError("[model] candidate_epochs: se esperaba una lista no vacía de épocas")
    if min(m.candidate_epochs) < 1:
        raise ValueError(f"[model] candidate_epochs: se esperaban épocas de 1 o más, se recibió {list(m.candidate_epochs)}")
    if any(later <= earlier for earlier, later in zip(m.candidate_epochs, m.candidate_epochs[1:])):
        raise ValueError(f"[model] candidate_epochs: se esperaban épocas estrictamente crecientes, se recibió {list(m.candidate_epochs)}")
    if m.candidate_epochs[-1] > m.epochs:
        raise ValueError(f"[model] candidate_epochs: la época {m.candidate_epochs[-1]} supera epochs ({m.epochs})")
    _positive(m.learning_rate, "[model] learning_rate")
    _at_least(m.warmup_steps, 0, "[model] warmup_steps")
    _seed(m.seed, "[model] seed")
    _non_negative(m.cord_val_tolerance, "[model] cord_val_tolerance")
    if m.infer_device not in INFER_DEVICES:
        raise ValueError(f"[model] infer_device: {m.infer_device!r} no es un valor conocido ({', '.join(INFER_DEVICES)})")


def _check_qr_intake_match(cfg: PipelineConfig) -> None:
    q = cfg.qr
    _names(q.readers, QR_READER_NAMES, "[qr] readers")
    _at_least(q.max_text_bytes, 1, "[qr] max_text_bytes")
    if not q.hosts or len(set(q.hosts)) != len(q.hosts):
        raise ValueError(f"[qr] hosts: se esperaba una lista no vacía y sin repetidos, se recibió {list(q.hosts)}")
    for host in q.hosts:
        if not _HOST.fullmatch(host):
            raise ValueError(f"[qr] hosts: {host!r} no es un nombre de host (sin esquema https ni ruta, en minúscula)")
    if not (q.path.startswith("/") and q.path.endswith("/")):
        raise ValueError(f"[qr] path: se esperaba una ruta que empiece y termine en /, se recibió {q.path!r}")
    i = cfg.intake
    _at_least(i.max_file_bytes, 1, "[intake] max_file_bytes")
    _at_least(i.max_pixels, 1, "[intake] max_pixels")
    _names(i.formats, INTAKE_FORMAT_NAMES, "[intake] formats")
    _at_least(i.pdf_max_objects, 1, "[intake] pdf_max_objects")
    a = cfg.match
    for key in ("price_tolerance_pct", "qty_over_receipt", "rounding_per_line", "total_qr_tolerance"):
        _non_negative(getattr(a, key), f"[match] {key}")
    if not a.supported_types or len(set(a.supported_types)) != len(a.supported_types):
        raise ValueError(f"[match] supported_types: se esperaba una lista no vacía y sin repetidos, se recibió {list(a.supported_types)}")
    for kind in a.supported_types:
        _between(kind, 1, 999, "[match] supported_types")


def _check_erp_service(cfg: PipelineConfig) -> None:
    e = cfg.erp
    if e.mode not in ERP_MODES:
        raise ValueError(f"[erp] mode: {e.mode!r} no es un valor conocido ({', '.join(ERP_MODES)})")
    if e.mode == "http" and not re.fullmatch(r"https?://[^\s/]+(/\S*)?", e.base_url):
        raise ValueError(f"[erp] base_url: con mode = \"http\" se esperaba una dirección http:// o https://, se recibió {e.base_url!r}")
    if e.post_as != "draft":
        raise ValueError(f"[erp] post_as: el único valor aceptado es 'draft' (D17: la confirmación es de una persona), se recibió {e.post_as!r}")
    if not _ENV_NAME.fullmatch(e.token_env):
        raise ValueError(f"[erp] token_env: se esperaba el nombre de una variable de entorno, se recibió {e.token_env!r}")
    _at_least(e.timeout_s, 1, "[erp] timeout_s")
    s = cfg.service
    _text(s.host, "[service] host")
    _between(s.port, 1, 65535, "[service] port")
    if not _ENV_NAME.fullmatch(s.token_env):
        raise ValueError(f"[service] token_env: se esperaba el nombre de una variable de entorno, se recibió {s.token_env!r}")


def _check_evaluation(cfg: PipelineConfig) -> None:
    _at_least(cfg.evaluation.resamples, 1, "[evaluation] resamples")
    _seed(cfg.evaluation.seed, "[evaluation] seed")
    k = cfg.criteria
    if not (math.isfinite(k.c01_min_ted_diff) and k.c01_min_ted_diff <= 0):
        raise ValueError(f"[criteria] c01_min_ted_diff: se esperaba un valor finito de 0 o menos (puntos de TED), se recibió {k.c01_min_ted_diff!r}")
    for key in ("c02_min_line_exact", "c03_min_line_exact", "c04_min_qr_read", "c06_min_straight_through", "c07_min_review_recall"):
        _fraction(getattr(k, key), f"[criteria] {key}", closed_high=True)
    _fraction(k.c05_max_bad_post_upper, "[criteria] c05_max_bad_post_upper")
    _positive(cfg.site.max_total_bytes, "[site] max_total_bytes")


def _check(cfg: PipelineConfig) -> None:
    _check_source(cfg)
    _check_calendar(cfg)
    _check_synth(cfg)
    _check_model(cfg)
    _check_qr_intake_match(cfg)
    _check_erp_service(cfg)
    _check_evaluation(cfg)


# --- reading --------------------------------------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], top: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in top.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value   # a list (the CORD files too) replaces the whole list
    return merged


def _read_toml(path: Path, _seen: tuple[Path, ...] = ()) -> dict[str, Any]:
    """Read a TOML file, following ``extends`` (relative to the file) and merging over its base."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no se encontró el archivo de configuración: {path}")
    resolved = path.resolve()
    if resolved in _seen:
        raise ValueError(f"los archivos de configuración se extienden entre sí en un ciclo en {path}")
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    parent = data.pop("extends", None)
    if parent is None:
        return data
    if not isinstance(parent, str):
        raise ValueError(f"{path}: extends debe ser una ruta escrita como texto")
    parent_path = Path(parent)
    if not parent_path.is_absolute():
        parent_path = path.parent / parent_path
    return _deep_merge(_read_toml(parent_path, (*_seen, resolved)), data)


def _home() -> Path | None:
    home = os.environ.get(ENV_HOME)
    if not home:
        return None
    root = Path(home).resolve()
    if not root.is_dir():
        raise ValueError(f"{ENV_HOME}: {home!r} no es una carpeta existente")
    return root


def load_config(path: Path | None = None) -> PipelineConfig:
    """Load the configuration.

    `path` is the main file; without it, ``$INVOICEFLOW_CONFIG`` and then ``config/pipeline.toml`` of the project. A relative `path` or
    ``$INVOICEFLOW_CONFIG`` is taken from ``$INVOICEFLOW_HOME`` or, without it, from this package's project: the working directory may be
    another copy's folder, and its configuration is never read in silence. The paths inside are resolved against ``$INVOICEFLOW_HOME`` or
    the parent of the folder that holds the main file. Anything unknown, missing, mistyped or inconsistent is a ``ValueError`` naming the key.
    """
    home = _home()
    if path is None:
        env = os.environ.get(ENV_CONFIG)
        path = Path(env) if env else DEFAULT_CONFIG
    path = Path(path)
    if not path.is_absolute():
        path = (home or DEFAULT_PROJECT_ROOT) / path
    data = _read_toml(path)
    root = home or path.resolve().parent.parent

    sections = [f.name for f in dataclasses.fields(PipelineConfig) if f.name != "project_root"]
    unknown = sorted(set(data) - set(sections))
    if unknown:
        raise ValueError(f"sección desconocida [{unknown[0]}]")
    missing = [s for s in sections if s not in data]
    if missing:
        raise ValueError(f"falta la sección [{missing[0]}]")
    hints = get_type_hints(PipelineConfig)
    built = {name: _build_section(hints[name], data[name], name, root) for name in sections}
    cfg = PipelineConfig(**built, project_root=root)
    _check(cfg)
    return cfg


__all__ = [
    "DEFAULT_CONFIG", "DEFAULT_PROJECT_ROOT", "ENV_CONFIG", "ENV_HOME", "FINGERPRINT_KEYS", "INFORMATIVE_KEYS", "UNSEALED_KEYS",
    "PipelineConfig", "SealedConfig", "config_leaves", "load_config", "register_model_kind", "unregister_model_kind",
]
