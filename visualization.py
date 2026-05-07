"""
visualization.py — Heatmaps, Interactive Maps & Animations

Provides:
  - plot_heatmap()      → matplotlib figure (PNG export, no map background)
  - plot_mapbox()       → Plotly Densitymapbox on real OpenStreetMap tiles (Trabzon)
  - plot_folium()       → Folium HeatMap on OpenStreetMap with Trabzon basemap
  - create_animation()  → rotating-wind GIF (matplotlib FuncAnimation)
  - concentration_to_csv() / save_summary_csv()
"""

from __future__ import annotations

import io
import base64
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.animation import FuncAnimation, PillowWriter
import pandas as pd

try:
    import folium
    from folium.plugins import HeatMap, MiniMap, Fullscreen
    HAS_FOLIUM = True
except ImportError:
    HAS_FOLIUM = False

try:
    import plotly.graph_objects as go
    import plotly.express as px
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

from model import GaussianPlumeModel, SimulationGrid, TRABZON_LAT, TRABZON_LON
from scenarios import ScenarioResult

logger = logging.getLogger(__name__)

_CMAP = "YlOrRd"

def _log_norm(data: np.ndarray) -> mcolors.LogNorm:
    nonzero = data[data > 0]
    if nonzero.size == 0:
        return mcolors.LogNorm(vmin=1e-12, vmax=1e-10)
    vmax = float(nonzero.max())
    vmin = max(float(np.quantile(nonzero, 0.80)), vmax * 1e-4)
    return mcolors.LogNorm(vmin=vmin, vmax=vmax)

def plot_heatmap(
    result: ScenarioResult,
    output_path: Optional[str | Path] = None,
    figsize: tuple[float, float] = (9, 7),
) -> plt.Figure:
    """Static matplotlib figure — concentration field in geographic coords."""
    grid = result.grid
    cq   = result.concentration
    sc   = result.scenario

    fig, ax = plt.subplots(figsize=figsize)
    norm = _log_norm(cq)
    pcm  = ax.pcolormesh(grid.lon_grid, grid.lat_grid, cq,
                          norm=norm, cmap=_CMAP, shading="auto")
    fig.colorbar(pcm, ax=ax, pad=0.01, label="C/Q  (s/m3)")

    if result.segments:
        rlats = [s["lat"] for s in result.segments]
        rlons = [s["lon"] for s in result.segments]
        ax.scatter(rlons, rlats, s=1, c="grey", alpha=0.35, label="road")

    if sc.source_type == "point":
        ax.plot(sc.source_lon, sc.source_lat, "b^", ms=10, label="source")
    ax.plot(result.peak_lon, result.peak_lat, "w*", ms=12,
            label=f"peak {result.peak_cq:.2e}")

    ax.set_xlabel("Longitude (E)")
    ax.set_ylabel("Latitude (N)")
    ax.set_title(
        f"Gaussian Plume — {sc.description}\n"
        f"Class {sc.stability_class}  |  u = {sc.wind_speed} m/s  "
        f"|  dir = {sc.wind_direction:.0f} deg"
    )
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        logger.info("PNG saved -> %s", output_path)
    return fig

def figure_to_png_bytes(fig: plt.Figure) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    buf.seek(0)
    return buf.read()

def plot_mapbox(
    result: ScenarioResult,
    output_path: Optional[str | Path] = None,
    zoom: int = 13,
) -> "go.Figure":
    """
    Concentration field as Densitymapbox on OpenStreetMap tiles.

    No Mapbox token required (open-street-map style).
    Visible in Streamlit via st.plotly_chart().
    """
    if not HAS_PLOTLY:
        raise ImportError("plotly not installed.  pip install plotly")

    grid  = result.grid
    cq    = result.concentration
    sc    = result.scenario

    lat_flat = grid.lat_grid.ravel()
    lon_flat = grid.lon_grid.ravel()
    cq_flat  = cq.ravel()

    mask     = cq_flat > 0
    cq_log   = np.where(mask, np.log10(np.where(mask, cq_flat, 1e-30)), np.nan)
    cq_log_v = cq_log[mask]

    fig = go.Figure()

    radius = 12 if sc.source_type == "line" else 18

    fig.add_trace(
        go.Densitymapbox(
            lat        = lat_flat[mask],
            lon        = lon_flat[mask],
            z          = cq_log_v,
            radius     = radius,
            colorscale = "YlOrRd",
            opacity    = 0.72,
            zmin       = float(np.nanmin(cq_log_v)),
            zmax       = float(np.nanmax(cq_log_v)),
            colorbar   = dict(
                title      = dict(text="log₁₀(C/Q)  [s/m³]", side="right"),
                tickformat = ".1f",
                x          = 1.0,
            ),
            hovertemplate = (
                "Lat: %{lat:.5f}<br>Lon: %{lon:.5f}<br>"
                "log₁₀(C/Q): %{z:.2f}<extra></extra>"
            ),
            name="Concentration",
        )
    )

    if sc.source_type == "point":
        fig.add_trace(
            go.Scattermapbox(
                lat        = [sc.source_lat],
                lon        = [sc.source_lon],
                mode       = "markers+text",
                marker     = dict(size=14, color="royalblue", symbol="circle"),
                text       = [f"Stack  H={sc.stack_height:.0f} m"],
                textfont   = dict(color="white", size=11),
                textposition = "top right",
                name       = "Emission source",
                hovertemplate = (
                    f"<b>Emission source</b><br>"
                    f"Lat: {sc.source_lat:.5f}<br>Lon: {sc.source_lon:.5f}<br>"
                    f"Stack height: {sc.stack_height:.0f} m<extra></extra>"
                ),
            )
        )

    if sc.source_type == "line" and result.segments:
        road_colors_map = {
            "motorway": "#e74c3c", "trunk": "#e67e22", "primary": "#f1c40f",
            "secondary": "#2ecc71", "tertiary": "#3498db", "residential": "#bdc3c7",
        }
        by_class: dict[str, list] = {}
        for seg in result.segments:
            cls = seg.get("road_class", "residential")
            by_class.setdefault(cls, []).append(seg)

        for cls, segs in by_class.items():
            lats_line: list = []
            lons_line: list = []
            for seg in segs:
                lats_line += [seg["lat"], seg["lat"], None]
                lons_line += [seg["lon"], seg["lon"], None]
            fig.add_trace(
                go.Scattermapbox(
                    lat        = lats_line,
                    lon        = lons_line,
                    mode       = "lines",
                    line       = dict(width=2, color=road_colors_map.get(cls, "#888")),
                    name       = f"Road: {cls}",
                    hoverinfo  = "skip",
                    opacity    = 0.75,
                )
            )

    fig.add_trace(
        go.Scattermapbox(
            lat        = [result.peak_lat],
            lon        = [result.peak_lon],
            mode       = "markers+text",
            marker     = dict(size=16, color="crimson", symbol="star"),
            text       = [f"Peak C/Q = {result.peak_cq:.2e} s/m³"],
            textfont   = dict(color="white", size=10),
            textposition = "bottom right",
            name       = "Peak concentration",
            hovertemplate = (
                f"<b>Peak concentration</b><br>"
                f"C/Q = {result.peak_cq:.3e} s/m³<br>"
                f"Lat: {result.peak_lat:.5f}<br>Lon: {result.peak_lon:.5f}<extra></extra>"
            ),
        )
    )

    arrow_lat = TRABZON_LAT - 0.020
    arrow_lon = TRABZON_LON - 0.025
    wind_rad  = np.radians(sc.wind_direction)
    arrow_txt = (
        f"Wind: {sc.wind_speed:.1f} m/s  "
        f"from {sc.wind_direction:.0f} deg  "
        f"| Stability: {sc.stability_class}"
    )

    fig.update_layout(
        mapbox = dict(
            style  = "open-street-map",
            center = dict(lat=TRABZON_LAT, lon=TRABZON_LON),
            zoom   = zoom,
        ),
        title = dict(
            text     = (
                f"<b>Gaussian Plume Dispersion — Trabzon, Turkey</b><br>"
                f"<sup>{sc.description}  |  Stability class {sc.stability_class}  "
                f"|  Wind {sc.wind_speed} m/s from {sc.wind_direction:.0f} deg</sup>"
            ),
            x        = 0.5,
            xanchor  = "center",
            font     = dict(size=15),
        ),
        legend = dict(
            bgcolor      = "rgba(255,255,255,0.85)",
            bordercolor  = "grey",
            borderwidth  = 1,
            x            = 0.01,
            y            = 0.99,
            xanchor      = "left",
            yanchor      = "top",
        ),
        margin = dict(l=0, r=0, t=80, b=0),
        height = 640,
    )

    if output_path:
        fig.write_html(str(output_path))
        logger.info("Interactive HTML saved -> %s", output_path)
    return fig

def plot_folium(
    result: ScenarioResult,
    output_path: Optional[str | Path] = None,
    max_zoom: int = 16,
) -> "folium.Map":
    """
    Folium map with HeatMap plugin overlay.

    Shows Trabzon city on OpenStreetMap tiles with the concentration field.
    """
    if not HAS_FOLIUM:
        raise ImportError("folium not installed.  pip install folium")

    grid  = result.grid
    cq    = result.concentration
    sc    = result.scenario

    m = folium.Map(
        location    = [TRABZON_LAT, TRABZON_LON],
        zoom_start  = 13,
        max_zoom    = max_zoom,
        tiles       = "OpenStreetMap",
        control_scale = True,
    )

    folium.TileLayer(
        tiles     = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr      = "Esri World Imagery",
        name      = "Satellite",
        overlay   = False,
        control   = True,
    ).add_to(m)

    cq_flat  = cq.ravel()
    lat_flat = grid.lat_grid.ravel()
    lon_flat = grid.lon_grid.ravel()

    cq_max = cq_flat.max()
    if cq_max > 0:
        cq_norm = cq_flat / cq_max
    else:
        cq_norm = cq_flat

    threshold = 0.005
    heat_data = [
        [float(la), float(lo), float(c)]
        for la, lo, c in zip(lat_flat, lon_flat, cq_norm)
        if c >= threshold
    ]

    HeatMap(
        heat_data,
        name       = "Concentration plume",
        min_opacity = 0.25,
        max_opacity = 0.80,
        radius     = 18,
        blur       = 15,
        max_zoom   = max_zoom,
        gradient   = {
            "0.00": "#ffffb2",
            "0.25": "#fecc5c",
            "0.50": "#fd8d3c",
            "0.75": "#f03b20",
            "1.00": "#bd0026",
        },
    ).add_to(m)

    if sc.source_type == "point":
        folium.Marker(
            location = [sc.source_lat, sc.source_lon],
            tooltip  = f"<b>Emission source</b><br>Stack H = {sc.stack_height:.0f} m",
            icon     = folium.Icon(color="blue", icon="industry", prefix="fa"),
        ).add_to(m)
    elif sc.source_type == "line" and result.segments:
        roads_by_class: dict[str, list] = {}
        for seg in result.segments:
            cls = seg.get("road_class", "residential")
            roads_by_class.setdefault(cls, []).append(seg)

        road_colors = {
            "motorway": "#e74c3c", "trunk": "#e67e22", "primary": "#f39c12",
            "secondary": "#27ae60", "tertiary": "#2980b9", "residential": "#95a5a6",
        }
        for cls, segs in roads_by_class.items():
            lats = [s["lat"] for s in segs]
            lons = [s["lon"] for s in segs]
            color = road_colors.get(cls, "#888888")
            for la, lo in zip(lats[::2], lons[::2]):
                folium.CircleMarker(
                    [la, lo], radius=2, color=color,
                    fill=True, fill_color=color, fill_opacity=0.6,
                    tooltip=cls,
                ).add_to(m)

    folium.Marker(
        location = [result.peak_lat, result.peak_lon],
        tooltip  = (
            f"<b>Peak C/Q</b><br>"
            f"{result.peak_cq:.3e} s/m³<br>"
            f"({result.peak_lat:.4f} N, {result.peak_lon:.4f} E)"
        ),
        icon = folium.Icon(color="red", icon="star", prefix="fa"),
    ).add_to(m)

    info_html = f"""
    <div style="
        position:fixed; top:70px; right:10px; z-index:9999;
        background:rgba(255,255,255,0.92); padding:10px 14px;
        border-radius:8px; border:1px solid #ccc;
        font-family:sans-serif; font-size:12px; max-width:220px;
        box-shadow:2px 2px 6px rgba(0,0,0,0.2);">
      <b style="font-size:13px;">Gaussian Plume — Trabzon</b><br>
      <hr style="margin:4px 0">
      <b>Scenario:</b> {sc.name}<br>
      <b>Source:</b> {sc.source_type}<br>
      <b>Stability:</b> Class {sc.stability_class}<br>
      <b>Wind:</b> {sc.wind_speed:.1f} m/s from {sc.wind_direction:.0f}&deg;<br>
      <b>Stack H:</b> {sc.stack_height:.0f} m<br>
      <b>Peak C/Q:</b> {result.peak_cq:.2e} s/m&sup3;<br>
      <hr style="margin:4px 0">
      <span style="color:#888">Data: OSM + EMEP/EEA + OWM</span>
    </div>
    """
    m.get_root().html.add_child(folium.Element(info_html))

    Fullscreen(position="topleft").add_to(m)
    MiniMap(
        tile_layer  = "OpenStreetMap",
        position    = "bottomright",
        width       = 140,
        height      = 100,
        zoom_level_offset = -5,
    ).add_to(m)
    folium.LayerControl(position="topright", collapsed=False).add_to(m)

    if output_path:
        m.save(str(output_path))
        logger.info("Folium map saved -> %s", output_path)
    return m

def create_animation(
    stability_class: str = "D",
    wind_speed: float = 3.0,
    source_lat: float = TRABZON_LAT,
    source_lon: float = TRABZON_LON,
    stack_height: float = 30.0,
    grid: Optional[SimulationGrid] = None,
    output_path: str | Path = "animation.gif",
    fps: int = 4,
    n_frames: int = 12,
) -> Path:
    """Animate plume over a 360 deg wind-direction sweep and save as GIF."""
    if grid is None:
        grid = SimulationGrid(extent_m=4000.0, resolution_m=100.0)

    wind_dirs   = np.linspace(0, 360, n_frames, endpoint=False)
    frames_data = []
    global_max  = 0.0

    for wd in wind_dirs:
        model = GaussianPlumeModel(stability_class, wind_speed, wd, stack_height)
        cq    = model.point_source_concentration(grid, source_lat, source_lon)
        frames_data.append(cq)
        global_max = max(global_max, float(cq.max()))

    fig, ax = plt.subplots(figsize=(7, 6))
    norm = mcolors.LogNorm(vmin=max(global_max * 1e-4, 1e-15), vmax=global_max)
    pcm  = ax.pcolormesh(grid.lon_grid, grid.lat_grid, frames_data[0],
                          norm=norm, cmap=_CMAP, shading="auto")
    fig.colorbar(pcm, ax=ax, label="C/Q  (s/m3)")
    ax.plot([source_lon], [source_lat], "b^", ms=9)
    title_txt = ax.set_title("")
    ax.set_xlabel("Longitude (E)")
    ax.set_ylabel("Latitude (N)")
    fig.tight_layout()

    def _update(frame: int):
        cq = frames_data[frame]
        wd = wind_dirs[frame]
        pcm.set_array(cq.ravel())
        title_txt.set_text(
            f"Gaussian Plume — Class {stability_class} — "
            f"Wind from {wd:.0f} deg at {wind_speed} m/s"
        )
        return pcm, title_txt

    anim  = FuncAnimation(fig, _update, frames=n_frames, blit=True, repeat=True)
    writer = PillowWriter(fps=fps)
    out   = Path(output_path)
    anim.save(str(out), writer=writer)
    plt.close(fig)
    logger.info("GIF saved -> %s", out)
    return out

def plot_mapbox_combined(
    result_point: ScenarioResult,
    result_line:  ScenarioResult,
    output_path: Optional[str | Path] = None,
    zoom: int = 13,
) -> "go.Figure":
    """
    Hem nokta hem çizgi kaynağı tek haritada gösterir.
    Her kaynak kendi Densitymapbox katmanına sahiptir.
    """
    if not HAS_PLOTLY:
        raise ImportError("plotly not installed.")

    fig = go.Figure()

    for result, label, colorscale, opacity in [
        (result_point, "Nokta Kaynak", "Blues",  0.60),
        (result_line,  "Çizgi Kaynak", "YlOrRd", 0.60),
    ]:
        grid     = result.grid
        cq       = result.concentration
        lat_flat = grid.lat_grid.ravel()
        lon_flat = grid.lon_grid.ravel()
        cq_flat  = cq.ravel()
        mask     = cq_flat > 0
        cq_log   = np.where(mask, np.log10(np.where(mask, cq_flat, 1e-30)), np.nan)
        cq_log_v = cq_log[mask]

        fig.add_trace(
            go.Densitymapbox(
                lat        = lat_flat[mask],
                lon        = lon_flat[mask],
                z          = cq_log_v,
                radius     = 14,
                colorscale = colorscale,
                opacity    = opacity,
                zmin       = float(np.nanmin(cq_log_v)),
                zmax       = float(np.nanmax(cq_log_v)),
                name       = label,
                hovertemplate = (
                    f"<b>{label}</b><br>"
                    "Lat: %{lat:.5f}<br>Lon: %{lon:.5f}<br>"
                    "log₁₀(C/Q): %{z:.2f}<extra></extra>"
                ),
            )
        )

    sc_p = result_point.scenario
    sc_l = result_line.scenario

    fig.add_trace(
        go.Scattermapbox(
            lat  = [sc_p.source_lat],
            lon  = [sc_p.source_lon],
            mode = "markers+text",
            marker = dict(size=14, color="royalblue"),
            text = [f"Baca H={sc_p.stack_height:.0f}m"],
            textfont = dict(color="white", size=11),
            textposition = "top right",
            name = "Nokta Kaynak (baca)",
        )
    )

    road_colors_map = {
        "motorway": "#e74c3c", "trunk": "#e67e22", "primary": "#f1c40f",
        "secondary": "#2ecc71", "tertiary": "#3498db", "residential": "#bdc3c7",
    }
    if result_line.segments:
        by_class: dict[str, list] = {}
        for seg in result_line.segments:
            cls = seg.get("road_class", "residential")
            by_class.setdefault(cls, []).append(seg)
        for cls, segs in by_class.items():
            lats_l = [s["lat"] for s in segs]
            lons_l = [s["lon"] for s in segs]
            fig.add_trace(
                go.Scattermapbox(
                    lat  = lats_l,
                    lon  = lons_l,
                    mode = "markers",
                    marker = dict(size=2, color=road_colors_map.get(cls, "#888"), opacity=0.5),
                    name = f"Yol: {cls}",
                    hoverinfo = "skip",
                )
            )

    fig.add_trace(
        go.Scattermapbox(
            lat  = [result_point.peak_lat],
            lon  = [result_point.peak_lon],
            mode = "markers+text",
            marker = dict(size=14, color="blue", symbol="star"),
            text = [f"Tepe(P)={result_point.peak_cq:.2e}"],
            textfont = dict(color="white", size=9),
            textposition = "bottom right",
            name = "Tepe — Nokta",
        )
    )
    fig.add_trace(
        go.Scattermapbox(
            lat  = [result_line.peak_lat],
            lon  = [result_line.peak_lon],
            mode = "markers+text",
            marker = dict(size=14, color="crimson", symbol="star"),
            text = [f"Tepe(Ç)={result_line.peak_cq:.2e}"],
            textfont = dict(color="white", size=9),
            textposition = "bottom right",
            name = "Tepe — Çizgi",
        )
    )

    fig.update_layout(
        mapbox = dict(
            style  = "open-street-map",
            center = dict(lat=TRABZON_LAT, lon=TRABZON_LON),
            zoom   = zoom,
        ),
        title = dict(
            text = (
                "<b>Gaussian Plume — Nokta + Çizgi Kaynak — Trabzon</b><br>"
                f"<sup>Nokta: Sınıf {sc_p.stability_class} | {sc_p.wind_speed} m/s {sc_p.wind_direction:.0f}°  ·  "
                f"Çizgi: Sınıf {sc_l.stability_class} | {sc_l.wind_speed} m/s {sc_l.wind_direction:.0f}°</sup>"
            ),
            x = 0.5, xanchor = "center", font = dict(size=14),
        ),
        legend = dict(
            bgcolor="rgba(255,255,255,0.88)", bordercolor="grey",
            borderwidth=1, x=0.01, y=0.99, xanchor="left", yanchor="top",
        ),
        margin = dict(l=0, r=0, t=80, b=0),
        height = 660,
    )

    if output_path:
        fig.write_html(str(output_path))
    return fig


def concentration_to_csv(result: ScenarioResult) -> pd.DataFrame:
    """Flatten concentration grid to tidy DataFrame."""
    grid = result.grid
    return pd.DataFrame({
        "latitude":    grid.lat_grid.ravel(),
        "longitude":   grid.lon_grid.ravel(),
        "cq_s_per_m3": result.concentration.ravel(),
        "scenario":    result.scenario.name,
        "stability":   result.scenario.stability_class,
        "wind_speed":  result.scenario.wind_speed,
        "wind_dir":    result.scenario.wind_direction,
    })

def save_summary_csv(
    results: list[ScenarioResult],
    val_results: list,
    output_path: str | Path = "summary.csv",
) -> pd.DataFrame:
    """Per-scenario summary with R², RMSE, peak C/Q and conditions."""
    syn_val = next(
        (v for v in val_results if getattr(v, "r2", None) is not None), None
    )
    rows = []
    for r in results:
        sc = r.scenario
        rows.append({
            "scenario":     sc.name,
            "source_type":  sc.source_type,
            "stability":    sc.stability_class,
            "wind_speed":   sc.wind_speed,
            "wind_dir":     sc.wind_direction,
            "stack_height": sc.stack_height,
            "peak_cq":      r.peak_cq,
            "peak_lat":     r.peak_lat,
            "peak_lon":     r.peak_lon,
            "r2":           syn_val.r2        if syn_val else None,
            "rmse":         syn_val.rmse_value if syn_val else None,
        })
    df = pd.DataFrame(rows)
    df.to_csv(str(output_path), index=False)
    logger.info("Summary CSV saved -> %s", output_path)
    return df
