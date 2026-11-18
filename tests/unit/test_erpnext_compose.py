"""The ERPNext stack of the smoke test (U22, AC36, RNF11; plan section 12; D16, D50): ``docker/erpnext/compose.yaml`` is static here, nothing is started (``tests/e2e/test_erpnext_smoke.py``, mark
``erpnext``, runs it once). Every image is pinned by version and digest, no secret has a value (each is ``${VAR:?}``), the one published port is on 127.0.0.1, and the file and the test that starts it name
the same image and the same project."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.unit.test_compose import REQUIRED, find_literal_secrets

PROJECT = Path(__file__).resolve().parents[2]
COMPOSE = PROJECT / "docker" / "erpnext" / "compose.yaml"
SMOKE_TEST = PROJECT / "tests" / "e2e" / "test_erpnext_smoke.py"
PINNED = re.compile(r"^(?P<name>[\w./-]+):(?P<tag>[\w.-]+)@sha256:[0-9a-f]{64}$")


@pytest.fixture(scope="module")
def compose() -> str:
    return COMPOSE.read_text(encoding="utf-8")


def test_every_image_is_pinned_by_version_and_by_digest(compose):
    images = re.findall(r"^\s*image:\s*(\S+)\s*$", compose, flags=re.MULTILINE)
    assert len(images) >= 3 and all(PINNED.fullmatch(image) for image in images), images
    erpnext = next(image for image in images if image.startswith("frappe/erpnext:"))
    assert PINNED.fullmatch(erpnext).group("tag").startswith("v15."), "ERPNext v15"


def test_no_secret_has_a_value_and_the_two_the_stack_needs_are_required_from_the_environment(compose):
    assert find_literal_secrets(compose) == []
    required = {m.group("name") for m in REQUIRED.finditer(compose)}
    assert required == {"INVOICEFLOW_ERPNEXT_DB_PASSWORD", "INVOICEFLOW_ERPNEXT_ADMIN_PASSWORD"}


def test_the_only_published_port_is_the_backends_and_it_is_bound_to_the_local_interface(compose):
    blocks = re.findall(r"^\s*ports:\s*\n((?:\s*-\s*.+\n)+)", compose, flags=re.MULTILINE)
    entries = [line.strip().lstrip("- ").strip("\"'") for block in blocks for line in block.splitlines()]
    assert entries == ["127.0.0.1:${INVOICEFLOW_ERPNEXT_PORT:-18080}:8000"]


def test_the_compose_file_and_the_smoke_test_name_the_same_image_and_the_same_project(compose):
    test = SMOKE_TEST.read_text(encoding="utf-8")
    image = re.search(r'^IMAGE = "(?P<image>[^"]+)"', test, flags=re.MULTILINE).group("image")
    project = re.search(r'^PROJECT_NAME = "(?P<name>[^"]+)"', test, flags=re.MULTILINE).group("name")
    assert f"image: {image}" in compose and re.search(rf"^name:\s*{re.escape(project)}\s*$", compose, flags=re.MULTILINE)
    for reference in re.findall(r'^    "([^"]+@sha256:[0-9a-f]{64})",$', test, flags=re.MULTILINE):
        assert f"image: {reference}" in compose, reference
