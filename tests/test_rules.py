"""Tests for the 17-rule engine (engine/rules.py). Run from the repo root with: py -m pytest -v"""
import os
from dataclasses import replace

import pytest

from engine.checker import check, summary
from engine.checklist import checklist
from engine.models import BoardCpuSupport
from engine.normalize import (normalize_form_factor, normalize_mem_type, normalize_psu_form_factor,
                              normalize_socket, parse_pg_array, parse_speed)
from engine.rules import FAIL, PASS, RULES, SKIP, WARN, compare_bios

try:                                   # pick up TEST_DATABASE_URL from .env, if present
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def results(build):
    return {r.rule_id: r for r in check(build)}


def only_failure(build, rule_id):
    """Assert rule_id FAILs and is the only FAIL, so the fixture change tripped exactly that rule."""
    res = results(build)
    assert res[rule_id].status == FAIL, res[rule_id]
    assert [r for r in res if res[r].status == FAIL] == [rule_id]
    assert summary(list(res.values())) == FAIL
    return res[rule_id]


# ---------------------------------------------------------------- registry / good build

def test_registry_has_17_rules_in_order():
    assert [r.rule_id for r in RULES] == [f"R{i:02d}" for i in range(1, 18)]


def test_known_good_build_passes(good_build):
    res = check(good_build)
    assert summary(res) == PASS
    assert not [r for r in res if r.status in (FAIL, WARN)]
    assert {r.rule_id for r in res if r.status == SKIP} == {"R15"}    # air cooler, so AIO rule skips


# ---------------------------------------------------------------- FAIL rules

def test_r01_socket_mismatch(good_build, cpu):
    # AMD CPU on the Intel board. R02 is satisfied by keeping the support row, so only R01 trips.
    # The cooler still lists AM5, so R13 passes.
    r = only_failure(replace(good_build, cpu=replace(cpu, socket="AM5")), "R01")
    assert "AM5" in r.message and "LGA1851" in r.message


def test_r02_cpu_not_on_support_list(good_build):
    r = only_failure(replace(good_build, supported_cpu=None), "R02")
    assert "not on" in r.message
    assert results(replace(good_build, supported_cpu=None))["R03"].status == SKIP


def test_r04_ram_type_vs_board(good_build, board):
    r = only_failure(replace(good_build, board=replace(board, mem_type="DDR4")), "R04")
    assert "DDR5 kit" in r.message and "DDR4 board" in r.message


def test_r05_ram_type_vs_cpu(good_build, cpu):
    r = only_failure(replace(good_build, cpu=replace(cpu, mem_type="DDR4")), "R05")
    assert "DDR4" in r.message and "DDR5" in r.message


def test_r06_too_many_modules(good_build):
    r = only_failure(replace(good_build, kit_qty=3), "R06")          # 6 modules, 4 slots
    assert "6 modules" in r.message and "4 slots" in r.message


def test_r07_capacity_over_board_max(good_build, board):
    r = only_failure(replace(good_build, board=replace(board, max_mem_gb=24)), "R07")
    assert "32 GB" in r.message and "24 GB" in r.message


def test_r10_board_does_not_fit_case(good_build, case):
    r = only_failure(replace(good_build, case=replace(case, supported_form_factors=["mATX", "ITX"])), "R10")
    assert "ATX board" in r.message


def test_r11_gpu_too_long(good_build, case):
    r = only_failure(replace(good_build, case=replace(case, max_gpu_mm=280)), "R11")
    assert "300 mm" in r.message and "280 mm" in r.message


def test_r12_no_display_output(good_build, cpu):
    r = only_failure(replace(good_build, gpu=None, cpu=replace(cpu, has_igpu=False)), "R12")
    assert "no integrated graphics" in r.message


def test_r13_cooler_wrong_socket(good_build, air_cooler):
    r = only_failure(replace(good_build, cooler=replace(air_cooler, sockets=["AM5", "AM4"])), "R13")
    assert "LGA1851" in r.message


def test_r14_air_cooler_too_tall(good_build, case):
    r = only_failure(replace(good_build, case=replace(case, max_cooler_mm=150)), "R14")
    assert "155 mm" in r.message and "150 mm" in r.message


def test_r15_radiator_too_big(good_build, case, aio_cooler):
    r = only_failure(replace(good_build, cooler=aio_cooler, case=replace(case, max_radiator_mm=240)), "R15")
    assert "360 mm" in r.message and "240 mm" in r.message


def test_r16_psu_below_draw_fails(good_build, psu):
    # 125 + 220 + 100 = 445 W estimated draw
    r = only_failure(replace(good_build, psu=replace(psu, watts=400)), "R16")
    assert "400 W" in r.message and "445 W" in r.message


def test_r17_missing_12v_2x6(good_build, gpu, psu):
    b = replace(good_build, gpu=replace(gpu, requires_12v_2x6=True), psu=replace(psu, has_12v_2x6=False))
    assert "12V-2x6" in only_failure(b, "R17").message


def test_r17_not_enough_8pin(good_build, gpu):
    r = only_failure(replace(good_build, gpu=replace(gpu, pcie_8pin_required=5)), "R17")
    assert "5 PCIe 8-pin" in r.message and "has 4" in r.message


def test_r17_atx_psu_in_sfx_case(good_build, case):
    r = only_failure(replace(good_build, case=replace(case, psu_form_factor="SFX")), "R17")
    assert "ATX PSU" in r.message


def test_r17_sfx_psu_in_atx_case_passes_with_note(good_build, psu):
    b = replace(good_build, psu=replace(psu, form_factor="SFX"))
    r = results(b)["R17"]
    assert r.status == PASS and "SFX PSU in an ATX case" in r.message
    assert any("SFX-to-ATX" in s for s in checklist(b, check(b)))


# ---------------------------------------------------------------- WARN rules

def test_r03_needs_bios_update_with_flashback_warns(good_build):
    b = replace(good_build, supported_cpu=BoardCpuSupport(1, 1, min_bios="F5"))    # ships F2
    res = results(b)
    assert res["R03"].status == WARN and "F5" in res["R03"].message and "F2" in res["R03"].message
    assert summary(list(res.values())) == WARN
    assert "Update BIOS to F5 or later via BIOS Flashback before installing the CPU" in checklist(b, check(b))


def test_r03_needs_bios_update_without_flashback_fails(good_build, board):
    b = replace(good_build, board=replace(board, bios_flashback=False),
                supported_cpu=BoardCpuSupport(1, 1, min_bios="F5"))
    assert "no flashback" in only_failure(b, "R03").message


def test_r03_shipped_bios_new_enough_passes(good_build):
    b = replace(good_build, supported_cpu=BoardCpuSupport(1, 1, min_bios="F1"))
    assert results(b)["R03"].status == PASS


def test_r03_uncomparable_bios_warns_for_manual_check(good_build):
    b = replace(good_build, supported_cpu=BoardCpuSupport(1, 1, min_bios="7E06v1A"))
    r = results(b)["R03"]
    assert r.status == WARN and "Verify BIOS version manually" in r.message


def test_r08_kit_not_on_qvl_warns(good_build):
    b = replace(good_build, qvl_kit_ids=set())
    res = results(b)
    assert res["R08"].status == WARN and "not on QVL" in res["R08"].message
    assert summary(list(res.values())) == WARN
    assert any("not on QVL" in s for s in checklist(b, check(b)))


def test_r09_overspeed_kit_warns(good_build, kit):
    b = replace(good_build, kit=replace(kit, speed=7200))       # CPU max 6400, board max 8000
    res = results(b)
    assert res["R09"].status == WARN and "6400" in res["R09"].message
    assert summary(list(res.values())) == WARN
    assert "Enable XMP/EXPO; expect RAM to run at 6400 MT/s" in checklist(b, check(b))


def test_r16_tight_psu_warns(good_build, psu):
    # 445 W draw, 445 * 1.3 = 578.5 -> 579 W recommended
    res = results(replace(good_build, psu=replace(psu, watts=550)))
    assert res["R16"].status == WARN and "579 W" in res["R16"].message
    assert summary(list(res.values())) == WARN


def test_r16_counts_no_gpu_as_zero(good_build, psu):
    # 125 + 0 + 100 = 225 W; 225 * 1.3 = 292.5 -> 293 W
    res = results(replace(good_build, gpu=None, psu=replace(psu, watts=293)))
    assert res["R16"].status == PASS


# ---------------------------------------------------------------- SKIP behavior

def test_no_gpu_with_igpu_skips_r11_and_passes_r12(good_build):
    res = results(replace(good_build, gpu=None))
    assert res["R11"].status == SKIP
    assert res["R12"].status == PASS
    assert summary(list(res.values())) == PASS


def test_aio_cooler_skips_r14(good_build, aio_cooler):
    res = results(replace(good_build, cooler=aio_cooler))
    assert res["R14"].status == SKIP
    assert res["R15"].status == PASS


def test_stock_cooler_skips_cooler_rules(good_build):
    res = results(replace(good_build, cooler=None))
    assert [res[r].status for r in ("R13", "R14", "R15")] == [SKIP, SKIP, SKIP]


def test_missing_data_warns_instead_of_crashing(good_build, board):
    res = results(replace(good_build, board=replace(board, mem_slots=None)))
    assert res["R06"].status == WARN and "verify manually" in res["R06"].message


def test_checklist_always_has_basics(good_build):
    steps = checklist(good_build, check(good_build))
    assert any("recommended slots for 2 modules" in s for s in steps)
    assert any("GPU power connectors" in s for s in steps)
    assert any("Verify POST" in s for s in steps)


# ---------------------------------------------------------------- BIOS comparison

@pytest.mark.parametrize("a, b, expected", [
    ("F2", "F5", -1), ("F10", "F5", 1), ("f5", "F5", 0),          # Gigabyte
    ("1.20", "1.3", 1), ("2.10", "2.20", -1),                      # dotted (MSI / ASRock)
    ("1203", "1601", -1), ("1601", "1601", 0),                     # ASUS
    ("F5", "1.20", None), ("1203", "1.20", None), ("7E06v1A", "7E06v1B", None), (None, "F5", None),
])
def test_compare_bios(a, b, expected):
    assert compare_bios(a, b) == expected


# ---------------------------------------------------------------- normalization

@pytest.mark.parametrize("raw", ["DDR5-6400", "6400MHz", "6400 MT/s", "6400", 6400])
def test_parse_speed(raw):
    assert parse_speed(raw) == 6400


@pytest.mark.parametrize("raw, expected", [
    ("LGA1851", "LGA1851"), ("lga 1851", "LGA1851"), ("LGA 1851", "LGA1851"), ("FCLGA1700", "LGA1700"),
    ("lga-1700", "LGA1700"), ("am5", "AM5"), (" AM4 ", "AM4"),
])
def test_normalize_socket(raw, expected):
    assert normalize_socket(raw) == expected


def test_normalize_socket_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_socket("Socket 7 maybe")


@pytest.mark.parametrize("raw, expected", [
    ("ATX", "ATX"), ("Micro-ATX", "mATX"), ("micro atx", "mATX"), ("mATX", "mATX"),
    ("Mini-ITX", "ITX"), ("ITX", "ITX"), ("E-ATX", "E-ATX"), ("EATX", "E-ATX"),
])
def test_normalize_form_factor(raw, expected):
    assert normalize_form_factor(raw) == expected


def test_other_normalizers():
    assert normalize_mem_type("ddr5 sdram") == "DDR5"
    assert normalize_psu_form_factor("SFX-L") == "SFX"
    assert parse_pg_array("{ATX,mATX,ITX}") == ["ATX", "mATX", "ITX"]


# ---------------------------------------------------------------- optional DB integration

@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="set TEST_DATABASE_URL to run")
def test_seed_and_check_against_database():
    """Drops and reloads every table in TEST_DATABASE_URL. Never point this at real data."""
    psycopg = pytest.importorskip("psycopg")
    from db.load_seed import load_seed
    from engine.repository import PartNotApproved, load_build, load_saved_build

    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        counts = load_seed(conn)
        assert counts["cpus"] > 0 and counts["motherboards"] > 0 and counts["builds"] > 0
        assert summary(check(load_saved_build(conn, 1))) == PASS
        # Sample CPU 6 is needs_review, so it must not be usable in a check.
        with pytest.raises(PartNotApproved):
            load_build(conn, cpu=6, board=3, kit=1, case=1, psu=1)
