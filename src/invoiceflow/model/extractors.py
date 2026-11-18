"""The extractors: what reads a page (RF09; plan section 9.5; decisions D11, D23).

An extractor has a ``model_version`` and ``extract(image) -> RawFields``: the tree of printed texts (``header``, ``lines``, ``totals``) that ``normalize.normalize`` types. ``make_extractor``
builds the one the configuration names (``[model] kind``) from a factory ``(view, registry_row, models_dir) -> Extractor``:

* ``donut`` is built in and loads nothing until it is asked for (``model/donut.py``, which imports torch only when it runs);
* ``tiny`` is registered by ``model/tiny.py`` (U10) for the quick mode and the tests of the training code;
* any other kind exists only while a test has registered it (the stub of ``tests/fixtures/stub_extractor.py``, which reads the synthetic truth): ``register`` refuses any other caller
  and tells the configuration to accept the kind for as long as it is registered (``config.register_model_kind``), so the real configuration never accepts a kind nobody registered.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from invoiceflow import config as config_module
from invoiceflow.config import SealedConfig
from invoiceflow.leakage import caller

RawFields = dict[str, Any]
Factory = Callable[[SealedConfig, Mapping[str, Any], Path], "Extractor"]
TEST_PACKAGES = ("tests", "test_", "conftest")
TINY_MODULE = "invoiceflow.model.tiny"


class ExtractorError(RuntimeError):
    """The extractor the configuration asks for is not available, or something that may not register one tried to."""


class Extractor(Protocol):
    model_version: str

    def extract(self, image: Any) -> RawFields: ...


def _make_donut(view: SealedConfig, row: Mapping[str, Any], models_dir: Path) -> Extractor:
    from invoiceflow.model.donut import load_donut

    return load_donut(view, row, models_dir)


_FACTORIES: dict[str, Factory] = {"donut": _make_donut}


def registered() -> tuple[str, ...]:
    return tuple(sorted(_FACTORIES))


def _is_test(module: str) -> bool:
    return module.split(".")[0] in TEST_PACKAGES or module.split(".")[-1].startswith("test_")


def register(kind: str, factory: Factory) -> None:
    """Make `factory` the builder of the kind `kind`. Only a test may register a kind of its own (``stub``); only ``model/tiny.py`` may register ``tiny``; ``donut`` is built in."""
    module, _function = caller({__name__})
    if kind == "donut":
        raise ExtractorError("donut es el extractor propio y no se registra")
    if not (_is_test(module) or (kind == "tiny" and module == TINY_MODULE)):
        raise ExtractorError(f"{module or '?'} intentó registrar el extractor {kind!r}: solo las pruebas registran uno propio y solo model/tiny.py registra tiny")
    _FACTORIES[kind] = factory
    if kind not in config_module.MODEL_KINDS:
        config_module.register_model_kind(kind)


def unregister(kind: str) -> None:
    """Forget the extractor of `kind` (the built-in one stays) and tell the configuration to stop accepting it."""
    if kind != "donut":
        _FACTORIES.pop(kind, None)
        config_module.unregister_model_kind(kind)


def make_extractor(view: SealedConfig, registry_row: Mapping[str, Any], models_dir: Path | str) -> Extractor:
    """The extractor of ``view.model.kind``; a kind nobody registered is an ``ExtractorError`` that names it."""
    kind = view.model.kind
    if kind not in _FACTORIES:
        raise ExtractorError(f"no hay un extractor registrado para [model] kind = {kind!r} (los hay: {', '.join(registered())})")
    return _FACTORIES[kind](view, registry_row, Path(models_dir))
