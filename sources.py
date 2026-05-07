"""
sources.py — Road Geometry & Emission Source Data

Fetches road geometries from the Overpass API (OpenStreetMap) for Trabzon
city centre and applies EMEP/EEA Tier 2 emission factors to build the
line-source segment list consumed by GaussianPlumeModel.line_source_concentration.
"""

import math
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

TRABZON_BBOX = (40.980, 39.700, 41.030, 39.755)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

_OVERPASS_QUERY = """
[out:json][timeout:30];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential)$"]
     ({south},{west},{north},{east});
);
out body;
>;
out skel qt;
"""

EMEP_EEA_NOX_G_PER_KM = {
    "motorway":    0.45,
    "trunk":       0.50,
    "primary":     0.55,
    "secondary":   0.60,
    "tertiary":    0.65,
    "residential": 0.70,
}

DEFAULT_TRAFFIC_FLOW_VEH_PER_HOUR = {
    "motorway":    2000,
    "trunk":       1500,
    "primary":     1000,
    "secondary":    700,
    "tertiary":     400,
    "residential":  150,
}

SEGMENT_LENGTH_M = 100.0

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points (metres)."""
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

def _interpolate_segment(
    lat1: float, lon1: float,
    lat2: float, lon2: float,
    step_m: float,
) -> list[tuple[float, float, float]]:
    """
    Discretise a line between two points into sub-segments of ~step_m.

    Returns list of (midpoint_lat, midpoint_lon, length_m) tuples.
    """
    total = haversine_m(lat1, lon1, lat2, lon2)
    if total < 1.0:
        return []
    n = max(1, int(round(total / step_m)))
    result = []
    for i in range(n):
        t0 = i / n
        t1 = (i + 1) / n
        tm = (t0 + t1) / 2.0
        mid_lat = lat1 + tm * (lat2 - lat1)
        mid_lon = lon1 + tm * (lon2 - lon1)
        seg_len = total / n
        result.append((mid_lat, mid_lon, seg_len))
    return result

def fetch_roads(
    bbox: tuple[float, float, float, float] = TRABZON_BBOX,
    timeout: int = 30,
) -> list[dict]:
    """
    Query Overpass API for road ways inside *bbox*.

    Returns a list of raw way dicts: {id, tags, nodes: [(lat, lon), ...]}.
    Falls back to an empty list with a warning on network/API errors.
    """
    south, west, north, east = bbox
    query = _OVERPASS_QUERY.format(south=south, west=west, north=north, east=east)

    try:
        resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Overpass API unavailable (%s). Using fallback road set.", exc)
        return _fallback_roads()

    nodes: dict[int, tuple[float, float]] = {}
    ways: list[dict] = []

    for elem in data.get("elements", []):
        if elem["type"] == "node":
            nodes[elem["id"]] = (elem["lat"], elem["lon"])
        elif elem["type"] == "way":
            ways.append(elem)

    result = []
    for way in ways:
        coords = [nodes[nid] for nid in way.get("nodes", []) if nid in nodes]
        if len(coords) < 2:
            continue
        result.append({
            "id":   way["id"],
            "tags": way.get("tags", {}),
            "nodes": coords,
        })

    logger.info("Fetched %d road ways from Overpass API.", len(result))
    return result if result else _fallback_roads()

def _fallback_roads() -> list[dict]:
    """Minimal hardcoded road skeleton for Trabzon city centre (offline fallback)."""
    return [
        {
            "id": -1,
            "tags": {"highway": "primary", "name": "Uzun Sokak"},
            "nodes": [(41.002, 39.718), (41.003, 39.726), (41.004, 39.734)],
        },
        {
            "id": -2,
            "tags": {"highway": "primary", "name": "Maraş Caddesi"},
            "nodes": [(40.998, 39.722), (41.003, 39.724)],
        },
        {
            "id": -3,
            "tags": {"highway": "secondary", "name": "Kahramanmaraş Cad."},
            "nodes": [(41.000, 39.716), (41.005, 39.718)],
        },
        {
            "id": -4,
            "tags": {"highway": "secondary", "name": "Cumhuriyet Caddesi"},
            "nodes": [(41.005, 39.723), (41.007, 39.730)],
        },
        {
            "id": -5,
            "tags": {"highway": "tertiary", "name": "Gazipaşa Bulvarı"},
            "nodes": [(41.001, 39.710), (41.002, 39.725), (41.003, 39.740)],
        },
    ]

def build_segments(
    roads: list[dict],
    traffic_multiplier: float = 1.0,
    step_m: float = SEGMENT_LENGTH_M,
) -> list[dict]:
    """
    Convert raw road ways into emission-weighted point segments.

    Each returned dict contains:
        lat             – midpoint latitude
        lon             – midpoint longitude
        length_m        – segment length (m)
        emission_factor – g/s/m  (NOx, EMEP/EEA Tier 2)
        road_class      – highway tag value
    """
    segments: list[dict] = []

    for road in roads:
        hw = road["tags"].get("highway", "residential")
        for key in EMEP_EEA_NOX_G_PER_KM:
            if hw.startswith(key):
                hw = key
                break
        else:
            hw = "residential"

        ef_g_per_km  = EMEP_EEA_NOX_G_PER_KM[hw]
        flow_veh_hr  = DEFAULT_TRAFFIC_FLOW_VEH_PER_HOUR[hw] * traffic_multiplier
        ef_g_per_s_m = ef_g_per_km * flow_veh_hr / 3_600_000.0

        node_coords = road["nodes"]
        for i in range(len(node_coords) - 1):
            lat1, lon1 = node_coords[i]
            lat2, lon2 = node_coords[i + 1]
            for mid_lat, mid_lon, seg_len in _interpolate_segment(lat1, lon1, lat2, lon2, step_m):
                segments.append({
                    "lat":             mid_lat,
                    "lon":             mid_lon,
                    "length_m":        seg_len,
                    "emission_factor": ef_g_per_s_m,
                    "road_class":      hw,
                })

    logger.info("Built %d road segments (step=%.0f m).", len(segments), step_m)
    return segments

def load_trabzon_segments(
    bbox: tuple[float, float, float, float] = TRABZON_BBOX,
    traffic_multiplier: float = 1.0,
    step_m: float = SEGMENT_LENGTH_M,
) -> list[dict]:
    """Fetch roads and build segments in one call. Safe to call on startup."""
    roads = fetch_roads(bbox)
    return build_segments(roads, traffic_multiplier=traffic_multiplier, step_m=step_m)
