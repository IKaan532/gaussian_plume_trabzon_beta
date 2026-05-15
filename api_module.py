
import os
import datetime
import requests
from typing import Optional

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass

from model import pasquill_stability_class, TRABZON_LAT, TRABZON_LON

_OWM_BASE = "https://api.openweathermap.org/data/2.5"

def load_api_key() -> str:
    key = os.environ.get("OWM_API_KEY", "").strip()
    if not key:
        raise EnvironmentError(
            "OWM_API_KEY environment variable not set.\n"
            "Set it with:\n"
            "  export OWM_API_KEY=your_key_here\n"
            "Or create a .env file containing:\n"
            "  OWM_API_KEY=your_key_here"
        )
    return key

def fetch_current_weather(
    lat: float = TRABZON_LAT,
    lon: float = TRABZON_LON,
    timeout: int = 10,
) -> dict:
    api_key = load_api_key()
    resp = requests.get(
        f"{_OWM_BASE}/weather",
        params={"lat": lat, "lon": lon, "appid": api_key, "units": "metric"},
        timeout=timeout,
    )
    resp.raise_for_status()
    return parse_weather_data(resp.json())

def fetch_forecast(
    lat: float = TRABZON_LAT,
    lon: float = TRABZON_LON,
    hours: int = 24,
    timeout: int = 10,
) -> list[dict]:
    api_key = load_api_key()
    resp = requests.get(
        f"{_OWM_BASE}/forecast",
        params={"lat": lat, "lon": lon, "appid": api_key, "units": "metric", "cnt": hours // 3},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return [parse_weather_data(item, is_forecast=True) for item in data.get("list", [])]

def parse_weather_data(raw: dict, is_forecast: bool = False) -> dict:
    wind      = raw.get("wind", {})
    main      = raw.get("main", {})
    clouds    = raw.get("clouds", {})
    weather   = raw.get("weather", [{}])[0]
    sys_info  = raw.get("sys", {})

    wind_speed = float(wind.get("speed", 3.0))
    wind_dir   = float(wind.get("deg",   270.0))
    temp       = float(main.get("temp",  15.0))
    cloud_frac = float(clouds.get("all", 50)) / 100.0
    humidity   = float(main.get("humidity", 60))
    pressure   = float(main.get("pressure", 1013))

    dt = raw.get("dt", None)
    sunrise = sys_info.get("sunrise")
    sunset  = sys_info.get("sunset")
    if dt and sunrise and sunset:
        is_daytime = sunrise <= dt <= sunset
    else:
        hour = datetime.datetime.utcnow().hour
        is_daytime = 6 <= hour < 20

    sc = pasquill_stability_class(
        wind_speed=wind_speed,
        temperature=temp,
        cloud_cover=cloud_frac,
        is_daytime=is_daytime,
    )

    ts = datetime.datetime.utcfromtimestamp(raw["dt"]) if "dt" in raw else datetime.datetime.utcnow()

    return {
        "wind_speed":      wind_speed,
        "wind_direction":  wind_dir,
        "temperature":     temp,
        "cloud_cover":     cloud_frac,
        "humidity":        humidity,
        "pressure":        pressure,
        "is_daytime":      is_daytime,
        "stability_class": sc,
        "description":     weather.get("description", ""),
        "timestamp":       ts,
    }

def default_weather(stability_class: Optional[str] = None) -> dict:
    sc = stability_class or "D"
    return {
        "wind_speed":      3.0,
        "wind_direction":  270.0,
        "temperature":     15.0,
        "cloud_cover":     0.5,
        "humidity":        70,
        "pressure":        1013,
        "is_daytime":      True,
        "stability_class": sc,
        "description":     "default (no API call)",
        "timestamp":       datetime.datetime.utcnow(),
    }
