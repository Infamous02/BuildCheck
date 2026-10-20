"""Automated tests for the BuildCheck rules engine. Run from the repo root with: py -m pytest -v"""
import pytest

from engine.buildcheck import FAIL, PASS, WARN, check_build, load_parts, make_build


@pytest.fixture(scope="module")
def parts():
    return load_parts()


def statuses(parts, **ids):
    overall, results = check_build(make_build(parts, **ids), parts)
    return overall, {r.rule: r.status for r in results}


def test_known_good_intel_build(parts):
    overall, s = statuses(parts, cpu="ultra7-265k", board="gb-z890-eagle",
                          ram="corsair-6000", gpu="rtx5070", psu="rm850e")
    assert overall == PASS
    assert all(v == PASS for v in s.values())


def test_socket_mismatch_fails(parts):
    overall, s = statuses(parts, cpu="r7-9800x3d", board="gb-z890-eagle",
                          ram="corsair-6000", gpu="rtx5070", psu="rm850e")
    assert overall == FAIL
    assert s["CPU socket"] == FAIL
    assert s["Chipset support"] == FAIL


def test_ddr4_kit_on_ddr5_board_fails(parts):
    overall, s = statuses(parts, cpu="ultra5-245k", board="gb-z890-eagle",
                          ram="kingston-ddr4", gpu="rtx5070", psu="rm750e")
    assert overall == FAIL
    assert s["Board memory type"] == FAIL
    assert s["CPU memory support"] == FAIL


def test_ddr4_build_on_lga1700_passes(parts):
    overall, s = statuses(parts, cpu="i7-14700k", board="msi-b760m-ddr4",
                          ram="kingston-ddr4", gpu="rtx5070", psu="rm750e")
    assert s["Board memory type"] == PASS
    assert s["CPU memory support"] == PASS
    assert overall in (PASS, WARN)


def test_ryzen_9000_on_b650_needs_bios_warning(parts):
    overall, s = statuses(parts, cpu="r7-9800x3d", board="asus-tuf-b650",
                          ram="corsair-6000", gpu="rtx5070", psu="rm750e")
    assert overall == WARN
    assert s["BIOS revision"] == WARN


def test_ram_over_board_speed_and_off_qvl_warns(parts):
    overall, s = statuses(parts, cpu="r5-7600x", board="asus-tuf-b650",
                          ram="gskill-8000", gpu="rtx5070", psu="rm750e")
    assert overall == WARN
    assert s["RAM speed"] == WARN
    assert s["Memory QVL"] == WARN


def test_undersized_psu_fails(parts):
    overall, s = statuses(parts, cpu="ultra7-265k", board="gb-z890-eagle",
                          ram="corsair-6000", gpu="rtx5090", psu="cx550")
    assert overall == FAIL
    assert s["PSU capacity"] == FAIL
    assert s["GPU PSU recommendation"] == WARN


def test_psu_without_headroom_warns(parts):
    # 9800X3D (162W) + RTX 5090 (575W) + 100W base = 837W; 850W < 1004W target
    overall, s = statuses(parts, cpu="r7-9800x3d", board="msi-b650-tomahawk",
                          ram="corsair-6000", gpu="rtx5090", psu="rm850e")
    assert s["PSU capacity"] == WARN
    assert s["GPU PSU recommendation"] == WARN
