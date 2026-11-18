"""A key that is validated and fingerprinted but that no module reads (U16; lesson B2 of project 06, L85): ``config._check`` accepts it, ``FINGERPRINT_KEYS`` hashes it, and changing it changes
``config_sha256`` and nothing else, so a run under another value is called ``post_seal`` for no reason and the seal says a knob exists that turns nothing. This walks the keys of the configuration
and fails on any that no module outside ``config.py`` reads, by attribute name and section, in the abstract syntax tree of the sources.

What it accepts as a read: an attribute access ``.name`` in a module (not ``config.py``), or the text ``"name"`` where the module reads by name (``getattr(synth, "seed_train")``, a limit looked up in
a dictionary), that also names the key's section, as an attribute (``view.model``) or as a name (``model``). A key inside a table (``synth.scan_profiles.clean``) counts as read when its table is
(``asdict(synth.scan_profiles)``). The generic serializers (``registry``, ``synth.master.to_jsonable``) walk every field and read none by name, so they never count.

The keys that are **not fingerprinted** and have no reader yet are the ones of the units that come later (the services, the site, the outputs); ``NOT_READ_YET`` names each with its unit, and fails when
one of them is read, so the list is only ever shorter.
"""

from __future__ import annotations

import ast
from pathlib import Path

from invoiceflow.config import FINGERPRINT_KEYS, INFORMATIVE_KEYS, UNSEALED_KEYS

SRC = Path(__file__).resolve().parents[2] / "src" / "invoiceflow"
GENERIC = {"config.py"}
# keys whose only reader is a library or a service by name, not an attribute of the configuration object (each one with its reader)
READ_ELSEWHERE: dict[str, str] = {}
NOT_READ_YET: dict[str, str] = {
    "service.host": "U20 (api.py, serve)", "service.port": "U20", "service.token_env": "U20", "site.max_total_bytes": "U18 (site_export.py)",
    "paths.raw_dir": "U19 (pipeline.py)", "paths.warehouse": "U19", "paths.outputs_dir": "U17 (export.py)", "paths.site_dir": "U18", "paths.reports_dir": "U19",
    "paths.dashboard_html": "U17 (render.py)", "paths.readme": "U19 (docs-sync)", "paths.figures_dir": "U18 (figures.py)",
}


def module_names() -> dict[str, tuple[set[str], set[str]]]:
    """module path -> (the attribute names it reads, the attribute and plain names it uses)."""
    out = {}
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        if relative in GENERIC:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        constants = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        names = attributes | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.arg for n in ast.walk(tree) if isinstance(n, ast.arg)}
        out[relative] = (attributes | constants, names)
    return out


def readers_of(key: str, modules) -> list[str]:
    *path, leaf = key.split(".")
    section = path[0]
    if len(path) > 1:             # a key of a table is read when its table is
        leaf = path[1]
    return [name for name, (attributes, names) in modules.items() if leaf in attributes and section in names]


def test_every_fingerprinted_key_is_read_by_a_module_that_it_names():
    modules = module_names()
    unread = [key for key in (*FINGERPRINT_KEYS, *UNSEALED_KEYS) if key not in INFORMATIVE_KEYS and key not in READ_ELSEWHERE and key not in NOT_READ_YET and not readers_of(key, modules)]
    assert unread == [], f"keys that are validated (and fingerprinted or declared unsealed) and that no module reads: {unread}; read them, or take them out of the configuration"


def test_the_keys_waiting_for_a_later_unit_are_not_fingerprinted_and_are_still_unread():
    modules = module_names()
    assert not set(NOT_READ_YET) & set(FINGERPRINT_KEYS), "a key that can change a sealed value is never allowed to wait"
    assert set(NOT_READ_YET) <= set(UNSEALED_KEYS)
    now_read = sorted(key for key in NOT_READ_YET if readers_of(key, modules))
    assert now_read == [], f"{now_read} have a reader now: take them off NOT_READ_YET"


def test_the_walk_finds_a_key_that_nobody_reads_and_names_the_reader_of_one_that_is_read():
    modules = module_names()
    assert "launch.py" in readers_of("model.candidate_epochs", {k: v for k, v in modules.items()}) or readers_of("model.candidate_epochs", modules)
    assert readers_of("model.cord_val_tolerance", modules) and not readers_of("model.not_a_key_at_all", modules) and not readers_of("nosuchsection.epochs", modules)
    assert readers_of("evaluation.resamples", modules) == ["evaluation.py"] or "evaluation.py" in readers_of("evaluation.resamples", modules)


def test_a_key_read_elsewhere_names_its_reader():
    assert all(isinstance(reader, str) and reader for reader in READ_ELSEWHERE.values())
