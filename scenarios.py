
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np

from model import GaussianPlumeModel, SimulationGrid, TRABZON_LAT, TRABZON_LON

logger = logging.getLogger(__name__)

SourceType = Literal["point", "line"]

@dataclass
class Scenario:
    name:            str
    source_type:     SourceType
    stability_class: str
    wind_speed:      float
    wind_direction:  float
    stack_height:    float
    emission_rate:   float
    description:     str = ""
    source_lat:      float = TRABZON_LAT
    source_lon:      float = TRABZON_LON

BUILTIN_SCENARIOS: list[Scenario] = [
    Scenario(
        name            = "point_unstable",
        source_type     = "point",
        stability_class = "A",
        wind_speed      = 2.0,
        wind_direction  = 270.0,
        stack_height    = 50.0,
        emission_rate   = 1.0,
        description     = "Point source — very unstable (class A, wind from W at 2 m/s)",
        source_lat      = 41.002,
        source_lon      = 39.720,
    ),
    Scenario(
        name            = "point_stable",
        source_type     = "point",
        stability_class = "F",
        wind_speed      = 1.5,
        wind_direction  = 270.0,
        stack_height    = 50.0,
        emission_rate   = 1.0,
        description     = "Point source — stable (class F, wind from W at 1.5 m/s)",
        source_lat      = 41.002,
        source_lon      = 39.720,
    ),
    Scenario(
        name            = "line_unstable",
        source_type     = "line",
        stability_class = "A",
        wind_speed      = 2.0,
        wind_direction  = 0.0,
        stack_height    = 0.5,
        emission_rate   = 1.0,
        description     = "Road network — very unstable (class A, wind from N at 2 m/s)",
    ),
    Scenario(
        name            = "line_stable",
        source_type     = "line",
        stability_class = "F",
        wind_speed      = 1.5,
        wind_direction  = 0.0,
        stack_height    = 0.5,
        emission_rate   = 1.0,
        description     = "Road network — stable (class F, wind from N at 1.5 m/s)",
    ),
]

SCENARIO_INDEX: dict[str, Scenario] = {s.name: s for s in BUILTIN_SCENARIOS}

@dataclass
class ScenarioResult:
    scenario:       Scenario
    grid:           SimulationGrid
    concentration:  np.ndarray
    peak_cq:        float
    peak_lat:       float
    peak_lon:       float
    model:          GaussianPlumeModel
    segments:       Optional[list[dict]] = field(default=None, repr=False)

def run_scenario(
    scenario: Scenario,
    grid: Optional[SimulationGrid] = None,
    segments: Optional[list[dict]] = None,
    z_receptor: float = 0.0,
) -> ScenarioResult:
    if grid is None:
        grid = SimulationGrid()

    model = GaussianPlumeModel(
        stability_class = scenario.stability_class,
        wind_speed      = scenario.wind_speed,
        wind_direction  = scenario.wind_direction,
        stack_height    = scenario.stack_height,
    )

    if scenario.source_type == "point":
        cq = model.point_source_concentration(
            grid, scenario.source_lat, scenario.source_lon, z_receptor
        )
    elif scenario.source_type == "line":
        if not segments:
            raise ValueError(
                f"Scenario '{scenario.name}' is a line source but no segments were provided."
            )
        cq = model.line_source_concentration(grid, segments, z_receptor)
    else:
        raise ValueError(f"Unknown source type: {scenario.source_type}")

    flat_idx = int(np.argmax(cq))
    idx2d    = np.unravel_index(flat_idx, cq.shape)
    peak_lat = float(grid.lat_grid[idx2d])
    peak_lon = float(grid.lon_grid[idx2d])
    peak_cq  = float(cq[idx2d])

    logger.info(
        "Scenario '%s' done. Peak C/Q = %.3e s/m³ at (%.4f°N, %.4f°E).",
        scenario.name, peak_cq, peak_lat, peak_lon,
    )

    return ScenarioResult(
        scenario      = scenario,
        grid          = grid,
        concentration = cq,
        peak_cq       = peak_cq,
        peak_lat      = peak_lat,
        peak_lon      = peak_lon,
        model         = model,
        segments      = segments,
    )

def run_all_scenarios(
    grid: Optional[SimulationGrid] = None,
    segments: Optional[list[dict]] = None,
    scenarios: Optional[list[Scenario]] = None,
) -> list[ScenarioResult]:
    sc_list = scenarios or BUILTIN_SCENARIOS
    results = []
    for sc in sc_list:
        try:
            r = run_scenario(
                sc,
                grid     = grid,
                segments = segments if sc.source_type == "line" else None,
            )
            results.append(r)
        except Exception as exc:
            logger.error("Scenario '%s' failed: %s", sc.name, exc)
    return results

def make_point_scenario(
    name:            str,
    stability_class: str,
    wind_speed:      float,
    wind_direction:  float,
    source_lat:      float,
    source_lon:      float,
    stack_height:    float = 50.0,
    emission_rate:   float = 1.0,
    description:     str   = "",
) -> Scenario:
    return Scenario(
        name            = name,
        source_type     = "point",
        stability_class = stability_class,
        wind_speed      = wind_speed,
        wind_direction  = wind_direction,
        stack_height    = stack_height,
        emission_rate   = emission_rate,
        description     = description or f"Point source, class {stability_class}",
        source_lat      = source_lat,
        source_lon      = source_lon,
    )

def make_line_scenario(
    name:            str,
    stability_class: str,
    wind_speed:      float,
    wind_direction:  float,
    stack_height:    float = 0.5,
    description:     str   = "",
) -> Scenario:
    return Scenario(
        name            = name,
        source_type     = "line",
        stability_class = stability_class,
        wind_speed      = wind_speed,
        wind_direction  = wind_direction,
        stack_height    = stack_height,
        emission_rate   = 1.0,
        description     = description or f"Line source, class {stability_class}",
    )
