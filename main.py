"""
main.py — CLI Entry Point

Runs all four operational scenarios under different meteorological conditions,
generates heatmap PNGs and HTML maps, an animated GIF, and summary.csv.

Usage:
    python main.py [--no-api] [--outdir ./output]

Flags:
    --no-api   Skip OWM API call; use default meteorological values instead.
    --outdir   Directory for output files (created if absent). Default: ./output
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Gaussian Plume Dispersion Model — Trabzon CLI runner"
    )
    p.add_argument("--no-api",  action="store_true", help="Skip OWM API; use default weather")
    p.add_argument("--outdir",  default="output",    help="Output directory (default: ./output)")
    return p.parse_args()

def main() -> None:
    args   = parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    logger.info("Output directory: %s", outdir.resolve())

    from api_module import fetch_current_weather, default_weather
    if args.no_api:
        weather = default_weather()
        logger.info("Using default weather (--no-api flag).")
    else:
        try:
            weather = fetch_current_weather()
            logger.info(
                "Live weather — wind %.1f m/s from %.0f°, class %s",
                weather["wind_speed"], weather["wind_direction"], weather["stability_class"],
            )
        except EnvironmentError as e:
            logger.warning("%s\nFalling back to default weather.", e)
            weather = default_weather()

    from sources import load_trabzon_segments
    logger.info("Fetching Trabzon road network…")
    segments = load_trabzon_segments()
    logger.info("%d road segments loaded.", len(segments))

    from model import SimulationGrid
    grid = SimulationGrid(extent_m=5000.0, resolution_m=100.0)

    from scenarios import BUILTIN_SCENARIOS, run_scenario
    from visualization import plot_heatmap, plot_mapbox, plot_folium, save_summary_csv

    results = []
    for sc in BUILTIN_SCENARIOS:
        logger.info("Running scenario: %s", sc.name)
        try:
            result = run_scenario(
                sc,
                grid     = grid,
                segments = segments if sc.source_type == "line" else None,
            )
            results.append(result)

            png_path = outdir / f"{sc.name}_heatmap.png"
            plot_heatmap(result, output_path=png_path)

            try:
                plotly_fig = plot_mapbox(result, output_path=outdir / f"{sc.name}_interactive.html")
            except ImportError:
                logger.warning("plotly not installed — skipping HTML for %s", sc.name)

            try:
                plot_folium(result, output_path=outdir / f"{sc.name}_map.html")
            except ImportError:
                logger.warning("folium not installed — skipping folium map for %s", sc.name)

        except Exception as exc:
            logger.error("Scenario '%s' failed: %s", sc.name, exc, exc_info=True)

    from validation import run_validation_suite, validation_summary
    logger.info("Running validation suite…")
    val_results = run_validation_suite(grid=grid)
    print(validation_summary(val_results))

    summary_path = outdir / "summary.csv"
    save_summary_csv(results, val_results, output_path=summary_path)
    logger.info("Summary CSV saved → %s", summary_path)

    from visualization import create_animation
    from model import TRABZON_LAT, TRABZON_LON
    gif_path = outdir / "plume_animation.gif"
    logger.info("Generating animated GIF…")
    create_animation(
        stability_class = "D",
        wind_speed      = 3.0,
        source_lat      = TRABZON_LAT,
        source_lon      = TRABZON_LON,
        stack_height    = 50.0,
        grid            = grid,
        output_path     = gif_path,
        fps             = 4,
        n_frames        = 12,
    )

    print("\n" + "=" * 70)
    print("SCENARIO RESULTS")
    print("=" * 70)
    print(f"{'Scenario':<22} {'Source':<8} {'Stability':<12} {'Wind':>10}  {'Peak C/Q (s/m³)':>18}")
    print("-" * 70)
    for r in results:
        sc = r.scenario
        print(
            f"{sc.name:<22} {sc.source_type:<8} {sc.stability_class} "
            f"({sc.stability_class}){'':<6} "
            f"{sc.wind_speed:>5.1f} m/s   {r.peak_cq:>18.4e}"
        )
    print("=" * 70)

    val_pass = sum(v.passed for v in val_results)
    r2_val   = next((v.r2 for v in val_results if v.r2 is not None), None)
    print(f"\nValidation: {val_pass}/{len(val_results)} tests passed"
          + (f", R² = {r2_val:.4f}" if r2_val is not None else ""))
    print(f"Outputs saved to: {outdir.resolve()}\n")

if __name__ == "__main__":
    main()
