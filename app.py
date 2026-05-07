"""
app.py — Streamlit Web Interface for Gaussian Plume Simulation

Çalıştır:
    streamlit run app.py

Canlı mod etkinleştirildiğinde uygulama her 2.5 dakikada bir
OWM API'den hava verisini çekip simülasyonu otomatik yeniler.

OWM_API_KEY ortam değişkeni veya .env dosyasında tanımlanmalıdır.
"""

from __future__ import annotations

import datetime
import logging
import traceback
from typing import Optional

import numpy as np
import streamlit as st
import streamlit.components.v1 as st_html
import pandas as pd
from streamlit_autorefresh import st_autorefresh

from model import (
    GaussianPlumeModel, SimulationGrid, STABILITY_DESCRIPTIONS,
    TRABZON_LAT, TRABZON_LON,
)
from scenarios import Scenario, ScenarioResult, run_scenario
from validation import run_validation_suite, validation_summary
from visualization import (
    plot_heatmap, plot_mapbox, plot_folium,
    figure_to_png_bytes, concentration_to_csv,
    HAS_FOLIUM,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REFRESH_INTERVAL_MS = 150_000

st.set_page_config(
    page_title  = "Gaussian Plume — Trabzon Canlı",
    page_icon   = "🏭",
    layout      = "wide",
    initial_sidebar_state = "expanded",
)

_DEFAULTS = {
    "result":                None,
    "val_results":           None,
    "weather":               None,
    "auto_refresh":          False,
    "last_refresh_count":    -1,
    "last_update_time":      None,
    "live_wind_speed":       3.0,
    "live_wind_dir":         270.0,
    "live_stability":        "D",
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

@st.cache_data(show_spinner="OpenStreetMap'dan yol geometrileri alınıyor…")
def _load_raw_roads() -> list[dict]:
    from sources import fetch_roads
    return fetch_roads()

def _fetch_weather_live() -> dict:
    """OWM API'den ÖNBELLEKSIZ anlık hava verisi çeker."""
    try:
        from api_module import fetch_current_weather
        return fetch_current_weather()
    except EnvironmentError as e:
        return {"_error": str(e)}
    except Exception as e:
        return {"_error": f"API hatası: {e}"}

@st.cache_data(show_spinner="Hava verisi alınıyor…")
def _fetch_weather_cached() -> dict:
    return _fetch_weather_live()

refresh_count: int = 0
if st.session_state.auto_refresh:
    refresh_count = st_autorefresh(
        interval = REFRESH_INTERVAL_MS,
        limit    = None,
        key      = "live_plume_autorefresh",
    )

new_refresh_cycle = (
    st.session_state.auto_refresh
    and refresh_count > 0
    and refresh_count != st.session_state.last_refresh_count
)

with st.sidebar:
    st.title("🏭 Simülasyon Kurulumu")
    st.markdown(
        "**Pilot bölge:** Trabzon il merkezi, Türkiye  \n"
        "`41.005 °N  39.726 °E`"
    )

    st.divider()
    live_on = st.toggle(
        "🔴 Canlı Mod  (2.5 dk'da bir otomatik güncelle)",
        value = st.session_state.auto_refresh,
        help  = "Etkinleştirildiğinde OWM API'den her 2.5 dakikada bir "
                "gerçek zamanlı hava verisi çekilerek simülasyon yenilenir.",
    )
    st.session_state.auto_refresh = live_on

    if live_on:
        now          = datetime.datetime.now()
        next_refresh = now + datetime.timedelta(seconds=150)

        if st.session_state.last_update_time:
            st.success(
                f"🟢 Canlı  |  Son: **{st.session_state.last_update_time}**",
                icon="🟢",
            )
        else:
            st.info("🟡 Canlı mod aktif — ilk güncelleme bekleniyor…")

        st.caption(f"⏱️ Sonraki güncelleme: **{next_refresh.strftime('%H:%M:%S')}**")

        elapsed_s = (now - (
            datetime.datetime.strptime(st.session_state.last_update_time, "%H:%M:%S")
            if st.session_state.last_update_time else now
        )).total_seconds()
        progress = min(elapsed_s / 150.0, 1.0)
        st.progress(progress, text=f"{int(elapsed_s)}s / 150s")
    else:
        st.caption("⚫ Manuel mod — butona basarak çalıştırın.")

    st.divider()

    source_type = st.selectbox(
        "Emisyon kaynağı",
        ["Nokta kaynak (endüstriyel baca)", "Çizgi kaynak (yol ağı)"],
    )
    is_point = source_type.startswith("Nokta")

    st.divider()

    if is_point:
        st.subheader("Nokta Kaynak Parametreleri")
        src_lat = st.number_input(
            "Kaynak enlemi (°N)", value=41.0020, format="%.5f", step=0.001
        )
        src_lon = st.number_input(
            "Kaynak boylamı (°E)", value=39.7200, format="%.5f", step=0.001
        )
        stack_h       = st.slider("Baca yüksekliği (m)", 5, 200, 50)
        emission_rate = st.number_input(
            "Emisyon debisi Q (g/s)", value=1.0, min_value=0.01, step=0.1
        )
        traffic_mult  = 1.0
    else:
        st.subheader("Çizgi Kaynak — Yol Ağı")
        st.info(
            "Yol geometrileri ilk çalıştırmada OpenStreetMap'ten otomatik alınır.",
            icon="🛣️",
        )
        stack_h       = st.slider("Salım yüksekliği (m)", 0, 10, 1)
        traffic_mult  = st.slider("Trafik çarpanı", 0.1, 3.0, 1.0, step=0.1)
        src_lat       = TRABZON_LAT
        src_lon       = TRABZON_LON
        emission_rate = 1.0

    st.divider()

    st.subheader("Meteorolojik Koşullar")

    if live_on:
        st.info("Canlı modda hava verisi OWM'den otomatik çekilir.", icon="🌤️")
        wind_speed      = st.session_state.live_wind_speed
        wind_dir        = st.session_state.live_wind_dir
        auto_sc         = st.session_state.live_stability
        met_mode        = "Gerçek zamanlı (OWM API)"
    else:
        met_mode = st.radio(
            "Veri kaynağı",
            ["Gerçek zamanlı (OWM API)", "Manuel giriş"],
        )
        if met_mode == "Gerçek zamanlı (OWM API)":
            if st.button("🔄 Hava verisini çek"):
                _fetch_weather_cached.clear()
                st.session_state.weather = _fetch_weather_cached()
            weather = st.session_state.weather
            if weather and "_error" not in weather:
                st.success(
                    f"Rüzgar: {weather['wind_speed']:.1f} m/s  "
                    f"{weather['wind_direction']:.0f}° yönünden  |  "
                    f"Sınıf: **{weather['stability_class']}**"
                )
                wind_speed = weather["wind_speed"]
                wind_dir   = weather["wind_direction"]
                auto_sc    = weather["stability_class"]
            elif weather and "_error" in weather:
                st.error(weather["_error"])
                wind_speed, wind_dir, auto_sc = 3.0, 270.0, "D"
            else:
                st.caption("'Hava verisini çek' butonuna tıklayın.")
                wind_speed, wind_dir, auto_sc = 3.0, 270.0, "D"
        else:
            wind_speed = st.slider("Rüzgar hızı (m/s)", 0.5, 15.0, 3.0, step=0.5)
            wind_dir   = st.slider(
                "Rüzgar yönü (° Kuzey'den saat yönünde)", 0, 359, 270
            )
            auto_sc = "D"

    stability_override = st.selectbox(
        "Kararlılık sınıfı (opsiyonel)",
        ["Otomatik"] + list(STABILITY_DESCRIPTIONS.keys()),
        format_func=lambda x: (
            x if x == "Otomatik"
            else f"{x} — {STABILITY_DESCRIPTIONS.get(x, '')}"
        ),
        disabled=live_on,
    )
    stability_class = (
        auto_sc if (stability_override == "Otomatik" or live_on)
        else stability_override
    )

    st.divider()
    run_btn = st.button(
        "▶  Simülasyonu Çalıştır",
        type             = "primary",
        use_container_width = True,
        disabled         = live_on,
    )

title_badge = "🔴 CANLI" if live_on else "⚫ Manuel"
st.title(f"🗺️ Gaussian Plume Dispersiyon Modeli — Trabzon, Türkiye  {title_badge}")
st.caption(
    "Pasquill-Gifford-Briggs dağılım katsayılı Gaussian plume denklemi  |  "
    "Harita: OpenStreetMap  |  Veri: OWM · OSM Overpass · EMEP/EEA"
)

if new_refresh_cycle:
    st.session_state.last_refresh_count = refresh_count

    with st.spinner("🔄 Canlı güncelleme: OWM'den hava verisi alınıyor…"):
        weather_live = _fetch_weather_live()

    if "_error" not in weather_live:
        st.session_state.weather           = weather_live
        st.session_state.live_wind_speed   = weather_live["wind_speed"]
        st.session_state.live_wind_dir     = weather_live["wind_direction"]
        st.session_state.live_stability    = weather_live["stability_class"]
        wind_speed      = weather_live["wind_speed"]
        wind_dir        = weather_live["wind_direction"]
        stability_class = weather_live["stability_class"]
        logger.info(
            "Canlı güncelleme #%d: %.1f m/s, %.0f°, sınıf %s",
            refresh_count, wind_speed, wind_dir, stability_class,
        )
    else:
        st.warning(
            f"⚠️ Hava verisi alınamadı ({weather_live['_error']}), "
            "önceki değerler kullanılıyor."
        )

    run_btn = True

if run_btn:
    if is_point:
        sc = Scenario(
            name            = "custom_point",
            source_type     = "point",
            stability_class = stability_class,
            wind_speed      = wind_speed,
            wind_direction  = float(wind_dir),
            stack_height    = float(stack_h),
            emission_rate   = float(emission_rate),
            description     = f"Nokta kaynak — Sınıf {stability_class}",
            source_lat      = float(src_lat),
            source_lon      = float(src_lon),
        )
        segments = None
    else:
        sc = Scenario(
            name            = "custom_line",
            source_type     = "line",
            stability_class = stability_class,
            wind_speed      = wind_speed,
            wind_direction  = float(wind_dir),
            stack_height    = float(stack_h),
            emission_rate   = 1.0,
            description     = f"Yol ağı çizgi kaynağı — Sınıf {stability_class}",
            source_lat      = TRABZON_LAT,
            source_lon      = TRABZON_LON,
        )
        with st.spinner("Yol segmentleri yükleniyor…"):
            from sources import build_segments as _build_segs
            roads    = _load_raw_roads()
            segments = _build_segs(roads, traffic_multiplier=traffic_mult)

    grid = SimulationGrid(extent_m=5000.0, resolution_m=100.0)

    spin_msg = "🔄 Canlı simülasyon yenileniyor…" if live_on else "Simülasyon hesaplanıyor…"
    with st.spinner(spin_msg):
        try:
            result = run_scenario(sc, grid=grid, segments=segments)
            st.session_state.result = result
        except Exception:
            st.error(f"Simülasyon başarısız:\n```\n{traceback.format_exc()}\n```")
            st.stop()

    with st.spinner("Doğrulama testleri çalışıyor…"):
        val_results = run_validation_suite(grid=grid)
        st.session_state.val_results = val_results

    st.session_state.last_update_time = datetime.datetime.now().strftime("%H:%M:%S")

    if not live_on:
        st.success("✅ Simülasyon tamamlandı!")

result: Optional[ScenarioResult] = st.session_state.result
val_results: Optional[list]      = st.session_state.val_results

if result is not None:
    sc = result.scenario

    if live_on and st.session_state.last_update_time:
        st.info(
            f"🟢 **Canlı** — Son güncelleme: **{st.session_state.last_update_time}**  |  "
            f"Rüzgar: **{sc.wind_speed:.1f} m/s**, **{sc.wind_direction:.0f}°**  |  "
            f"Kararlılık: **{sc.stability_class}** — {STABILITY_DESCRIPTIONS[sc.stability_class]}  |  "
            f"Tepe C/Q: **{result.peak_cq:.3e} s/m³**",
            icon="🟢",
        )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Tepe C/Q", f"{result.peak_cq:.3e} s/m³")
    col2.metric(
        "Kararlılık sınıfı",
        f"{sc.stability_class} — {STABILITY_DESCRIPTIONS[sc.stability_class]}",
    )
    col3.metric("Rüzgar", f"{sc.wind_speed:.1f} m/s  {sc.wind_direction:.0f}°")
    col4.metric(
        "Son güncelleme",
        st.session_state.last_update_time or "—",
        delta="canlı" if live_on else "manuel",
    )

    tab_osm, tab_folium, tab_metrics, tab_export = st.tabs([
        "🌍 OSM Haritası",
        "🗺️ Folium Haritası",
        "📊 Doğrulama",
        "📥 Dışa Aktar",
    ])

    with tab_osm:
        st.markdown(
            f"**{sc.description}**  |  "
            f"Rüzgar: {sc.wind_speed} m/s, {sc.wind_direction:.0f}° yönünden  |  "
            f"Baca/Salım yüksekliği: {sc.stack_height:.0f} m"
        )
        try:
            fig_mb = plot_mapbox(result, zoom=13)
            st.plotly_chart(fig_mb, use_container_width=True)
            st.caption(
                "© OpenStreetMap katkıcıları  |  "
                "Renk: log₁₀(C/Q) [s/m³]  |  "
                "Kaydırmak için sürükleyin · yakınlaştırmak için kaydırın."
            )
        except Exception as exc:
            st.error(f"Plotly harita hatası: {exc}")
            fig_mpl = plot_heatmap(result)
            st.pyplot(fig_mpl)
            import matplotlib.pyplot as _plt
            _plt.close(fig_mpl)

    with tab_folium:
        if not HAS_FOLIUM:
            st.warning("folium yüklü değil.  `pip install folium` komutunu çalıştırın.")
        else:
            st.markdown(
                "OpenStreetMap üzerinde etkileşimli ısı haritası.  "
                "Sağ üst köşeden uydu katmanına geçiş yapabilirsiniz."
            )
            try:
                m        = plot_folium(result)
                map_html = m._repr_html_()
                st_html.html(map_html, height=620, scrolling=False)
                st.caption(
                    "Tam ekran için sol üst köşedeki ikonu · "
                    "Katman geçişi için sağ üst köşedeki kontrolü kullanın."
                )
            except Exception as exc:
                st.error(f"Folium harita hatası: {exc}")

    with tab_metrics:
        if val_results:
            st.subheader("Fiziksel Tutarlılık Testleri")
            vcols = st.columns(len(val_results))
            for vcol, vr in zip(vcols, val_results):
                icon     = "✅" if vr.passed else "❌"
                r2_str   = f"\n\nR² = **{vr.r2:.4f}**"           if vr.r2 is not None else ""
                rmse_str = f"\n\nRMSE = **{vr.rmse_value:.3e}**"  if vr.rmse_value is not None else ""
                vcol.info(
                    f"{icon} **{vr.test_name.replace('_', ' ').title()}**"
                    f"{r2_str}{rmse_str}\n\n{vr.message}"
                )
            with st.expander("Ham doğrulama raporu"):
                st.code(validation_summary(val_results), language=None)
        else:
            st.info("Doğrulama metriklerini görmek için simülasyon çalıştırın.")

    with tab_export:
        st.subheader("Sonuçları İndir")
        dcol1, dcol2, dcol3 = st.columns(3)

        with dcol1:
            fig_png   = plot_heatmap(result)
            png_bytes = figure_to_png_bytes(fig_png)
            import matplotlib.pyplot as _plt2
            _plt2.close(fig_png)
            st.download_button(
                "⬇️ Isı haritası PNG",
                data      = png_bytes,
                file_name = f"{sc.name}_heatmap.png",
                mime      = "image/png",
                use_container_width=True,
            )

        with dcol2:
            df_csv    = concentration_to_csv(result)
            csv_bytes = df_csv.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Konsantrasyon CSV",
                data      = csv_bytes,
                file_name = f"{sc.name}_grid.csv",
                mime      = "text/csv",
                use_container_width=True,
            )

        with dcol3:
            if HAS_FOLIUM:
                try:
                    m_exp      = plot_folium(result)
                    html_bytes = m_exp._repr_html_().encode("utf-8")
                    st.download_button(
                        "⬇️ Etkileşimli Harita HTML",
                        data      = html_bytes,
                        file_name = f"{sc.name}_map.html",
                        mime      = "text/html",
                        use_container_width=True,
                    )
                except Exception:
                    st.caption("Folium dışa aktarımı mevcut değil.")

        st.divider()
        st.markdown("**Konsantrasyon ızgarası — istatistiksel özet**")
        st.dataframe(
            df_csv.describe().T.style.format("{:.4e}"),
            use_container_width=True,
        )

else:
    st.info(
        "👈 Sol panelden parametreleri ayarlayın ve "
        "**Simülasyonu Çalıştır** butonuna tıklayın  "
        "— ya da **Canlı Mod**'u etkinleştirin.",
        icon="ℹ️",
    )

    st.subheader("Çalışma Alanı — Trabzon, Türkiye")
    try:
        import plotly.graph_objects as go
        preview = go.Figure(
            go.Scattermapbox(
                lat          = [TRABZON_LAT],
                lon          = [TRABZON_LON],
                mode         = "markers+text",
                marker       = dict(size=16, color="crimson"),
                text         = ["Trabzon Merkez"],
                textposition = "top right",
                textfont     = dict(size=13, color="white"),
            )
        )
        preview.update_layout(
            mapbox  = dict(
                style  = "open-street-map",
                center = dict(lat=TRABZON_LAT, lon=TRABZON_LON),
                zoom   = 11,
            ),
            margin = dict(l=0, r=0, t=0, b=0),
            height = 450,
        )
        st.plotly_chart(preview, use_container_width=True)
        st.caption(
            "© OpenStreetMap katkıcıları  |  "
            "Simülasyon alanı: 10 km × 10 km, 100 m çözünürlük"
        )
    except Exception:
        st.map(pd.DataFrame({"lat": [TRABZON_LAT], "lon": [TRABZON_LON]}))

st.divider()
if live_on:
    st.caption(
        f"🔴 **CANLI MOD AKTİF** — Her **2.5 dakikada** bir otomatik güncelleniyor  |  "
        f"Gaussian Plume Modeli · Trabzon Pilot  |  "
        f"OWM · OSM Overpass · EMEP/EEA"
    )
else:
    st.caption(
        "Gaussian Plume Modeli · Trabzon Pilot  |  "
        "Veri: OpenWeatherMap · OSM Overpass · EMEP/EEA emisyon faktörleri"
    )
