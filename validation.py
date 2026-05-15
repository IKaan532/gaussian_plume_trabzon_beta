
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from model import GaussianPlumeModel, SimulationGrid, TRABZON_LAT, TRABZON_LON

logger = logging.getLogger(__name__)

def r_squared(observed: np.ndarray, predicted: np.ndarray) -> float:
    obs_flat  = observed.ravel()
    pred_flat = predicted.ravel()
    ss_res = np.sum((obs_flat - pred_flat) ** 2)
    ss_tot = np.sum((obs_flat - np.mean(obs_flat)) ** 2)
    return float(1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0

def rmse(observed: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.sqrt(np.mean((observed.ravel() - predicted.ravel()) ** 2)))

def generate_synthetic_observations(
    model: GaussianPlumeModel,
    grid: SimulationGrid,
    source_lat: float,
    source_lon: float,
    noise_fraction: float = 0.10,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    rng     = np.random.default_rng(seed)
    C_model = model.point_source_concentration(grid, source_lat, source_lon)
    noise   = rng.normal(0.0, 1.0, C_model.shape) * noise_fraction * C_model
    C_obs   = np.maximum(C_model + noise, 0.0)
    return C_obs, C_model

@dataclass
class ValidationResult:
    test_name:   str
    passed:      bool
    r2:          Optional[float] = None
    rmse_value:  Optional[float] = None
    message:     str = ""
    details:     dict = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}

def test_wind_direction_vs_plume(
    grid: Optional[SimulationGrid] = None,
    source_lat: float = TRABZON_LAT,
    source_lon: float = TRABZON_LON,
) -> ValidationResult:
    if grid is None:
        grid = SimulationGrid(extent_m=3000.0, resolution_m=100.0)

    results_by_dir: dict[str, tuple[float, float]] = {}
    for wind_dir, label in [(270.0, "from_W"), (90.0, "from_E")]:
        model = GaussianPlumeModel("D", 3.0, wind_dir, stack_height=20.0)
        cq    = model.point_source_concentration(grid, source_lat, source_lon)
        idx   = np.unravel_index(np.argmax(cq), cq.shape)
        peak_east  = float(grid.lon_grid[idx])
        peak_north = float(grid.lat_grid[idx])
        results_by_dir[label] = (peak_north, peak_east)

    peak_lon_W = results_by_dir["from_W"][1]
    peak_lon_E = results_by_dir["from_E"][1]

    passed  = peak_lon_W > source_lon > peak_lon_E
    message = (
        f"Wind-from-W peak lon={peak_lon_W:.4f}° (expected > {source_lon:.4f}°), "
        f"wind-from-E peak lon={peak_lon_E:.4f}° (expected < {source_lon:.4f}°)."
    )
    logger.info("Test 1 [wind direction]: %s — %s", "PASS" if passed else "FAIL", message)
    return ValidationResult("wind_direction_vs_plume", passed, message=message, details=results_by_dir)

def test_wind_speed_vs_concentration(
    grid: Optional[SimulationGrid] = None,
    source_lat: float = TRABZON_LAT,
    source_lon: float = TRABZON_LON,
) -> ValidationResult:
    if grid is None:
        grid = SimulationGrid(extent_m=3000.0, resolution_m=100.0)

    peaks = {}
    for u in [1.5, 3.0, 6.0]:
        model     = GaussianPlumeModel("D", u, 270.0, stack_height=20.0)
        cq        = model.point_source_concentration(grid, source_lat, source_lon)
        peaks[u]  = float(cq.max())

    ratio_u1_u2 = peaks[1.5] / peaks[3.0] if peaks[3.0] > 0 else 0
    ratio_u2_u4 = peaks[3.0] / peaks[6.0] if peaks[6.0] > 0 else 0

    passed  = (1.3 <= ratio_u1_u2 <= 3.0) and (1.3 <= ratio_u2_u4 <= 3.0)
    message = (
        f"Peak C/Q: u=1.5->{peaks[1.5]:.3e}, u=3->{peaks[3.0]:.3e}, u=6->{peaks[6.0]:.3e} s/m^3. "
        f"Ratios: x2 speed -> x{1/ratio_u1_u2:.2f} (should ~ 0.5), x{1/ratio_u2_u4:.2f}."
    )
    logger.info("Test 2 [wind speed vs conc]: %s — %s", "PASS" if passed else "FAIL", message)
    return ValidationResult(
        "wind_speed_vs_concentration", passed, message=message,
        details={"peaks": peaks, "ratio_low_mid": ratio_u1_u2, "ratio_mid_high": ratio_u2_u4},
    )

def test_source_coordinate_shift(
    grid: Optional[SimulationGrid] = None,
) -> ValidationResult:
    if grid is None:
        grid = SimulationGrid(extent_m=4000.0, resolution_m=100.0)

    shift_m   = 500.0
    R         = 6_371_000.0
    dlon      = np.degrees(shift_m / (R * np.cos(np.radians(TRABZON_LAT))))

    lat_base  = TRABZON_LAT
    lon_base  = TRABZON_LON
    lon_shift = TRABZON_LON + dlon

    peaks = {}
    for label, slon in [("base", lon_base), ("shifted", lon_shift)]:
        model = GaussianPlumeModel("D", 3.0, 270.0, stack_height=20.0)
        cq    = model.point_source_concentration(grid, lat_base, slon)
        idx   = np.unravel_index(np.argmax(cq), cq.shape)
        peaks[label] = float(grid.lon_grid[idx])

    peak_shift_deg = peaks["shifted"] - peaks["base"]
    peak_shift_m   = np.radians(peak_shift_deg) * R * np.cos(np.radians(lat_base))
    rel_error      = abs(peak_shift_m - shift_m) / shift_m

    passed  = rel_error < 0.25
    message = (
        f"Source shifted {shift_m:.0f} m east; peak moved {peak_shift_m:.0f} m east "
        f"(relative error {rel_error*100:.1f}%)."
    )
    logger.info("Test 3 [source shift]: %s — %s", "PASS" if passed else "FAIL", message)
    return ValidationResult(
        "source_coordinate_shift", passed, message=message,
        details={"shift_m": shift_m, "peak_shift_m": peak_shift_m, "rel_error": rel_error},
    )

def validate_on_synthetic_dataset(
    stability_class: str = "D",
    wind_speed: float = 3.0,
    wind_direction: float = 270.0,
    stack_height: float = 30.0,
    noise_fraction: float = 0.10,
    grid: Optional[SimulationGrid] = None,
    source_lat: float = TRABZON_LAT,
    source_lon: float = TRABZON_LON,
) -> ValidationResult:
    if grid is None:
        grid = SimulationGrid(extent_m=3000.0, resolution_m=100.0)

    model   = GaussianPlumeModel(stability_class, wind_speed, wind_direction, stack_height)
    C_obs, C_pred = generate_synthetic_observations(
        model, grid, source_lat, source_lon, noise_fraction=noise_fraction
    )

    r2   = r_squared(C_obs, C_pred)
    rmse_val = rmse(C_obs, C_pred)

    passed  = r2 > 0.80
    message = (
        f"Synthetic dataset (noise={noise_fraction*100:.0f}%): "
        f"R^2={r2:.4f} ({'>=' if passed else '<'} 0.80), RMSE={rmse_val:.3e} s/m^3."
    )
    logger.info("Synthetic validation: %s — %s", "PASS" if passed else "FAIL", message)
    return ValidationResult(
        "synthetic_dataset_r2",
        passed,
        r2=r2,
        rmse_value=rmse_val,
        message=message,
        details={"stability_class": stability_class, "wind_speed": wind_speed, "noise": noise_fraction},
    )

def run_validation_suite(
    grid: Optional[SimulationGrid] = None,
    source_lat: float = TRABZON_LAT,
    source_lon: float = TRABZON_LON,
) -> list[ValidationResult]:
    suite = [
        test_wind_direction_vs_plume(grid, source_lat, source_lon),
        test_wind_speed_vs_concentration(grid, source_lat, source_lon),
        test_source_coordinate_shift(grid),
        validate_on_synthetic_dataset(grid=grid, source_lat=source_lat, source_lon=source_lon),
    ]

    n_pass = sum(r.passed for r in suite)
    logger.info("Validation suite: %d/%d tests passed.", n_pass, len(suite))
    return suite

def validation_summary(results: list[ValidationResult]) -> str:
    lines = ["=== Validation Summary ==="]
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        r2_str   = f"  R2={r.r2:.4f}"           if r.r2        is not None else ""
        rmse_str = f"  RMSE={r.rmse_value:.3e}"  if r.rmse_value is not None else ""
        lines.append(f"  [{status}] {r.test_name}{r2_str}{rmse_str}")
        lines.append(f"         {r.message}")
    return "\n".join(lines)
