"""The synthetic CUIT (U05, AC04; decision D05): the module-11 check digit, the one range the project may produce (``30-0000xxxx-d``), the guard of every
write (M07) and the scan of published text. No real taxpayer's number is ever generated."""

from __future__ import annotations

import re

import pytest

from invoiceflow.synth.cuit import (
    SyntheticCuitError,
    check_digit,
    is_synthetic_cuit,
    non_synthetic_cuits,
    require_synthetic_cuit,
    synthetic_cuit,
)
from tests.fixtures import hand_cases as h

RANGE = re.compile(r"300000\d{4}\d")


def test_every_generated_cuit_is_in_the_synthetic_range():
    made = {serial: synthetic_cuit(serial) for serial in range(0, 10_000)}
    present = {serial: cuit for serial, cuit in made.items() if cuit is not None}
    assert len(present) == 9090 and sum(1 for serial in present if serial >= 1) == 9089, "9,089 of the serials 1 to 9,999 have a CUIT (plan, p02); serial 0 is the specification's"
    assert len(set(present.values())) == len(present), "one CUIT per serial"
    for serial, cuit in present.items():
        assert RANGE.fullmatch(cuit) and cuit[6:10] == f"{serial:04d}" and is_synthetic_cuit(cuit), cuit
        assert check_digit(cuit[:10]) == int(cuit[10])
    assert all(is_synthetic_cuit(cuit) for cuit in present.values())
    assert [serial for serial, cuit in made.items() if cuit is None][:3] == [4, 18, 21], "the serials with no CUIT: the remainder 10 (3000000004, 3000000018, 3000000021)"


def test_the_check_digit_is_the_modulo_11_rule_of_afip_on_every_hand_case():
    for first10, digit in h.CHECK_DIGIT_CASES:
        assert check_digit(first10) == digit, first10
    assert check_digit("3000010000") == 1 and check_digit("3000001234") is None


@pytest.mark.parametrize("bad", ["", "300000000", "30000000000", "abcdefghij", "30000000a0", "3000000000 ", "３０００００００００"])
def test_the_check_digit_takes_exactly_ten_ascii_digits(bad):
    with pytest.raises(ValueError, match="10 dígitos"):
        check_digit(bad)


def test_the_synthetic_cuits_of_the_hand_cases_and_the_serial_that_has_none():
    for serial, cuit in h.SYNTHETIC_CUITS.items():
        assert synthetic_cuit(serial) == cuit, serial
    assert synthetic_cuit(0) == "30000000007" == str(__import__("json").loads(h.SPEC_EXAMPLE_JSON)["cuit"]), "the specification's example is serial 0"


@pytest.mark.parametrize("serial", [-1, 10_000, 100_000, 1.5, "7", None, True])
def test_a_serial_outside_the_range_is_refused_so_no_other_cuit_can_be_made(serial):
    with pytest.raises(ValueError, match="serie"):
        synthetic_cuit(serial)


def test_the_guard_of_the_range_on_the_hand_cases():
    for cuit in h.SYNTHETIC_CUITS.values():
        if cuit is not None:
            assert is_synthetic_cuit(cuit)
    for cuit, valid_digit in h.OUTSIDE_THE_RANGE:
        assert not is_synthetic_cuit(cuit), cuit
    assert not is_synthetic_cuit(None) and not is_synthetic_cuit(30000000007) and not is_synthetic_cuit(b"30000000007")
    assert not is_synthetic_cuit("300000００００7"), "fullwidth digits are not ASCII digits"
    assert not is_synthetic_cuit("30000000007\n") and not is_synthetic_cuit(" 30000000007") and not is_synthetic_cuit("３００００００００７７")


def test_the_guard_of_a_write_names_where_and_never_repeats_the_number():
    require_synthetic_cuit("30000000007", "erp.supplier")
    for bad in ("30000100001", "30000000008", "20123456786", "no es un cuit"):
        with pytest.raises(SyntheticCuitError, match=r"erp\.supplier.*30-0000xxxx-d") as caught:
            require_synthetic_cuit(bad, "erp.supplier")
        assert bad not in str(caught.value) and isinstance(caught.value, ValueError)
    with pytest.raises(SyntheticCuitError):
        require_synthetic_cuit(30000000007, "output")


def test_the_scan_of_a_text_finds_every_cuit_that_is_not_synthetic_and_ignores_longer_numbers():
    ok = "Emisor 30-00000000-7, receptor 30000000015; CAE 70417054367476; OC-2026-000123; total 1234567,89; teléfono 4444-5555"
    assert non_synthetic_cuits(ok) == []
    text = ok + " y 20-12345678-6, 27123456786, 30-00010000-1 y 30000000008"
    assert non_synthetic_cuits(text) == ["20123456786", "27123456786", "30000100001", "30000000008"]
    assert non_synthetic_cuits("12345678901234") == [], "a 14-digit number holds no CUIT"
    assert non_synthetic_cuits("a30000000007b") == [] and non_synthetic_cuits("a20123456786b") == ["20123456786"]
    assert non_synthetic_cuits(b"x 20123456786 y".decode()) == ["20123456786"] and non_synthetic_cuits("") == []
    assert non_synthetic_cuits("2012345678 6") == [] and non_synthetic_cuits("20 12345678 6") == []
    assert non_synthetic_cuits(text, allowed={"20123456786"}) == ["27123456786", "30000100001", "30000000008"]
    assert non_synthetic_cuits("20-12345678-6\n20-12345678-6") == ["20123456786", "20123456786"], "every occurrence is reported"
