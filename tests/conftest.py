"""In-memory fixtures for the rules engine tests. No database needed.

`good_build` passes every rule. Tests copy it with dataclasses.replace() and
change one value to trip exactly one rule.
"""
import pytest

from engine.models import (BoardCpuSupport, Build, Case, Cooler, Cpu, Gpu, MemoryKit, Motherboard,
                           Psu)


@pytest.fixture
def cpu():
    return Cpu(id=1, brand="Test", model="LGA1851 CPU", socket="LGA1851", tdp_w=125,
               has_igpu=True, mem_type="DDR5", max_mem_speed=6400)


@pytest.fixture
def board():
    return Motherboard(id=1, brand="Test", model="Z890 board", socket="LGA1851", chipset="Z890",
                       form_factor="ATX", mem_type="DDR5", mem_slots=4, max_mem_gb=256,
                       max_mem_speed=8000, shipped_bios="F2", bios_flashback=True)


@pytest.fixture
def kit():
    return MemoryKit(id=1, brand="Test", model="DDR5-6000 2x16", mem_type="DDR5", speed=6000,
                     modules=2, gb_per_module=16)


@pytest.fixture
def gpu():
    return Gpu(id=1, brand="Test", model="GPU 220W", length_mm=300, tbp_w=220,
               pcie_8pin_required=1, requires_12v_2x6=False)


@pytest.fixture
def case():
    return Case(id=1, brand="Test", model="ATX tower", supported_form_factors=["ATX", "mATX", "ITX"],
                max_gpu_mm=400, max_cooler_mm=170, psu_form_factor="ATX", max_radiator_mm=360)


@pytest.fixture
def psu():
    return Psu(id=1, brand="Test", model="850W", watts=850, form_factor="ATX", pcie_8pin=4, has_12v_2x6=True)


@pytest.fixture
def air_cooler():
    return Cooler(id=1, brand="Test", model="Tower 155", cooler_type="air", height_mm=155,
                  sockets=["LGA1851", "LGA1700", "AM5", "AM4"])


@pytest.fixture
def aio_cooler():
    return Cooler(id=2, brand="Test", model="AIO 360", cooler_type="aio", radiator_mm=360,
                  sockets=["LGA1851", "AM5"])


@pytest.fixture
def good_build(cpu, board, kit, gpu, case, psu, air_cooler):
    return Build(cpu=cpu, board=board, kit=kit, kit_qty=1, case=case, psu=psu, gpu=gpu,
                 cooler=air_cooler, supported_cpu=BoardCpuSupport(board_id=1, cpu_id=1, min_bios=None),
                 qvl_kit_ids={1})
