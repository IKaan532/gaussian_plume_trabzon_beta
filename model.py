"""
model.py — Gaussian Plume Dispersion Model

Implements Gaussian plume equation with Pasquill-Gifford-Briggs dispersion
coefficients for point and line sources. Output is dimensionless C/Q (s/m³).
"""

import numpy as np
from dataclasses import dataclass
from typing import Literal, Optional, Tuple

StabilityClass = Literal['A', 'B', 'C', 'D', 'E', 'F']

STABILITY_DESCRIPTIONS = {
    'A': 'Very Unstable',
    'B': 'Unstable',
    'C': 'Slightly Unstable',
    'D': 'Neutral',
    'E': 'Slightly Stable',
    'F': 'Stable',
}

_SY_COEFFS: dict[str, float] = {
    'A': 0.22, 'B': 0.16, 'C': 0.11,
    'D': 0.08, 'E': 0.06, 'F': 0.04,
}

TRABZON_LAT = 41.0050
TRABZON_LON = 39.7262

def geo_to_local(
    lat: np.ndarray, lon: np.ndarray,
    ref_lat: float, ref_lon: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """Geographic coords → local Cartesian (east_m, north_m)."""
    R = 6_371_000.0
    north = R * np.radians(lat - ref_lat)
    east  = R * np.radians(lon - ref_lon) * np.cos(np.radians(ref_lat))
    return east, north

def rotate_to_plume(
    east: np.ndarray, north: np.ndarray,
    wind_dir_deg: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Rotate (east, north) → (x_downwind, y_crosswind).

    wind_dir_deg: meteorological convention — direction the wind blows FROM,
                  measured clockwise from north (0° = wind from north → blows south).
    """
    phi = np.radians(270.0 - wind_dir_deg)
    x_down  =  east * np.cos(phi) + north * np.sin(phi)
    y_cross = -east * np.sin(phi) + north * np.cos(phi)
    return x_down, y_cross

def sigma_y(x_m: np.ndarray, sc: StabilityClass) -> np.ndarray:
    """Horizontal dispersion coefficient σy (m). x_m in metres."""
    x_km = np.maximum(x_m, 1.0) / 1000.0
    return _SY_COEFFS[sc] * x_km * (1.0 + 0.0001 * x_km) ** (-0.5) * 1000.0

def sigma_z(x_m: np.ndarray, sc: StabilityClass) -> np.ndarray:
    """
    Vertical dispersion coefficient σz (m). x_m in metres.
    Capped at 500 m (practical atmospheric mixing height limit).
    """
    x_km = np.maximum(x_m, 1.0) / 1000.0
    if   sc == 'A': sz = 0.200 * x_km * 1000.0
    elif sc == 'B': sz = 0.120 * x_km * 1000.0
    elif sc == 'C': sz = 0.080 * x_km * (1.0 + 0.0002 * x_km) ** (-0.5) * 1000.0
    elif sc == 'D': sz = 0.060 * x_km * (1.0 + 0.0015 * x_km) ** (-0.5) * 1000.0
    elif sc == 'E': sz = 0.030 * x_km * (1.0 + 0.0003 * x_km) ** (-1.0) * 1000.0
    elif sc == 'F': sz = 0.016 * x_km * (1.0 + 0.0003 * x_km) ** (-1.0) * 1000.0
    else: raise ValueError(f"Unknown stability class: {sc}")
    return np.minimum(sz, 500.0)

@dataclass
class SimulationGrid:
    """Rectangular grid centred on a geographic location."""
    center_lat: float = TRABZON_LAT
    center_lon: float = TRABZON_LON
    extent_m: float = 5000.0
    resolution_m: float = 100.0

    def __post_init__(self) -> None:
        n = int(2 * self.extent_m / self.resolution_m) + 1
        edge = np.linspace(-self.extent_m, self.extent_m, n)
        self.east_grid, self.north_grid = np.meshgrid(edge, edge)

        R = 6_371_000.0
        lat_rad = np.radians(self.center_lat)
        self.lat_grid = self.center_lat + np.degrees(self.north_grid / R)
        self.lon_grid = self.center_lon + np.degrees(
            self.east_grid / (R * np.cos(lat_rad))
        )
        self.shape = self.east_grid.shape

class GaussianPlumeModel:
    """
    Steady-state Gaussian plume model.

    All concentration output is C/Q (s/m³).  Multiply by emission rate Q (g/s)
    to obtain absolute concentration (g/m³).
    """

    def __init__(
        self,
        stability_class: StabilityClass,
        wind_speed: float,
        wind_direction: float,
        stack_height: float = 0.0,
    ) -> None:
        self.stability_class = stability_class
        self.wind_speed      = max(wind_speed, 0.5)
        self.wind_direction  = float(wind_direction)
        self.stack_height    = float(stack_height)

    def _kernel(
        self,
        x_down: np.ndarray,
        y_cross: np.ndarray,
        z_r: float = 0.0,
    ) -> np.ndarray:
        """Evaluate C/Q (s/m³) at plume-coordinate positions."""
        H  = self.stack_height
        u  = self.wind_speed
        xp = np.maximum(x_down, 1.0)

        sy = sigma_y(xp, self.stability_class)
        sz = sigma_z(xp, self.stability_class)

        cq = (
            1.0 / (2.0 * np.pi * u * sy * sz)
            * np.exp(-y_cross**2 / (2.0 * sy**2))
            * (
                np.exp(-(z_r - H)**2 / (2.0 * sz**2))
                + np.exp(-(z_r + H)**2 / (2.0 * sz**2))
            )
        )
        return np.where(x_down <= 0.0, 0.0, cq)

    def point_source_concentration(
        self,
        grid: SimulationGrid,
        source_lat: float,
        source_lon: float,
        z_receptor: float = 0.0,
    ) -> np.ndarray:
        """C/Q field (s/m³) for a single point source."""
        east, north = geo_to_local(grid.lat_grid, grid.lon_grid, source_lat, source_lon)
        xd, yc = rotate_to_plume(east, north, self.wind_direction)
        return self._kernel(xd, yc, z_receptor)

    def line_source_concentration(
        self,
        grid: SimulationGrid,
        segments: list[dict],
        z_receptor: float = 0.0,
    ) -> np.ndarray:
        """
        C/Q_total field (s/m³) for a discretised road network.

        Each segment dict must contain:
            lat, lon        – midpoint coordinates
            length_m        – segment length (m)
            emission_factor – g s⁻¹ m⁻¹ (per metre of road)
        """
        total_cq = np.zeros(grid.shape)
        total_q  = 0.0

        for seg in segments:
            east, north = geo_to_local(
                grid.lat_grid, grid.lon_grid, seg['lat'], seg['lon']
            )
            xd, yc = rotate_to_plume(east, north, self.wind_direction)
            q_seg   = float(seg['emission_factor']) * float(seg['length_m'])
            total_q += q_seg
            total_cq += q_seg * self._kernel(xd, yc, z_receptor)

        return total_cq / total_q if total_q > 0 else total_cq

    def sigma_profiles(self, distances_m: np.ndarray) -> dict:
        """Return σy / σz profiles along the centreline for diagnostics."""
        return {
            'distance_m': distances_m,
            'sigma_y_m':  sigma_y(distances_m, self.stability_class),
            'sigma_z_m':  sigma_z(distances_m, self.stability_class),
        }

def pasquill_stability_class(
    wind_speed: float,
    temperature: float = 15.0,
    cloud_cover: float = 0.5,
    solar_radiation: Optional[float] = None,
    is_daytime: bool = True,
) -> StabilityClass:
    """
    Determine Pasquill-Gifford stability class.

    Uses the Turner (1970) look-up table approach.

    Parameters
    ----------
    wind_speed      : m/s
    temperature     : °C (used for insolation estimate when solar_radiation is None)
    cloud_cover     : 0–1 fractional cloud cover
    solar_radiation : W/m² direct solar irradiance (optional)
    is_daytime      : True during daylight hours
    """
    if is_daytime:
        if solar_radiation is not None:
            ins = 'strong' if solar_radiation > 600 else ('moderate' if solar_radiation > 300 else 'slight')
        elif cloud_cover < 0.25 and temperature > 20:
            ins = 'strong'
        elif cloud_cover < 0.60:
            ins = 'moderate'
        else:
            ins = 'slight'

        table = {
            'strong':   ['A', 'A', 'B', 'C', 'C'],
            'moderate': ['A', 'B', 'B', 'C', 'D'],
            'slight':   ['B', 'C', 'C', 'D', 'D'],
        }
        thresholds = [2, 3, 5, 6]
        idx = sum(wind_speed >= t for t in thresholds)
        return table[ins][idx]
    else:
        if wind_speed < 2:   return 'F'
        if wind_speed < 3:   return 'F' if cloud_cover < 0.5 else 'E'
        if wind_speed < 5:   return 'E' if cloud_cover < 0.5 else 'D'
        return 'D'
