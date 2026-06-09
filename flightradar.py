import logging
import math
from concurrent.futures import ThreadPoolExecutor

import json

import folium
import folium.plugins
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from FlightRadar24 import FlightRadar24API
from streamlit_folium import st_folium
from streamlit_js_eval import get_geolocation

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

# --- CONFIGURATION ---
try:
    WEATHER_API_KEY = st.secrets.get("WEATHER_API_KEY", "")
except Exception:
    WEATHER_API_KEY = ""

SEARCH_RADIUS_M = 10_000

SQUAWK_ALERTS = {
    "7700": ("[EMERGENCY] General Emergency — Squawk 7700 active", "error"),
    "7600": ("[RADIO] Radio Failure — Squawk 7600 active", "warning"),
    "7500": ("[HIJACK] Hijack Alert — Squawk 7500 active", "error"),
}

st.set_page_config(page_title="SkyWatcher Pro", layout="wide", page_icon="radar")

st.markdown("""
    <style>
    /* ── override Streamlit CSS variables at root ── */
    :root, [data-testid="stAppViewContainer"] {
        --primary-color: #39ff14;
        --background-color: #000000;
        --secondary-background-color: #0a0a0a;
        --text-color: #e0e0e0;
    }
    /* ── force pure black on every surface ── */
    html, body,
    .stApp,
    .stApp > header,
    [data-testid="stAppViewContainer"],
    [data-testid="stHeader"],
    [data-testid="stToolbar"],
    [data-testid="stDecoration"],
    [data-testid="stBottom"],
    [data-testid="stAppViewBlockContainer"],
    section[data-testid="stSidebar"],
    section[data-testid="stSidebar"] > div,
    section[data-testid="stSidebar"] [data-testid="stSidebarContent"] {
        background-color: #000000 !important;
        background: #000000 !important;
    }
    /* ── ALL headings — neon green ── */
    h1, h2, h3, h4, h5, h6 {
        color: #39ff14 !important;
    }
    /* ── body text — white ── */
    p, span, label, li, div, td, th, a {
        color: #e0e0e0 !important;
    }
    /* ── metric values — neon green, larger ── */
    [data-testid="stMetricValue"] {
        font-size: 24px !important;
        color: #39ff14 !important;
    }
    [data-testid="stMetricValue"] * {
        color: #39ff14 !important;
    }
    [data-testid="stMetricDelta"], [data-testid="stMetricDelta"] * {
        color: #39ff14 !important;
    }
    /* ── tabs — neon green active ── */
    button[data-baseweb="tab"] { color: #666 !important; }
    button[data-baseweb="tab"][aria-selected="true"] {
        color: #39ff14 !important;
        border-bottom-color: #39ff14 !important;
    }
    /* ── alert boxes ── */
    .stAlert { background-color: #0a0a0a !important; }
    /* ── dividers ── */
    hr { border-color: #1a1a1a !important; }
    /* ── dataframe / table ── */
    .stDataFrame iframe {
        border: 1px solid #1a1a1a !important;
    }
    </style>
""", unsafe_allow_html=True)

# --- UTILITIES ---

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))

def project_position(lat: float, lon: float, heading_deg: float, speed_kts: float, minutes: float = 5):
    if heading_deg is None or speed_kts is None or speed_kts == 0:
        return None
    dist_km = speed_kts * 1.852 * (minutes / 60)
    R = 6371.0
    d = dist_km / R
    bearing = math.radians(heading_deg)
    lat1, lon1 = math.radians(lat), math.radians(lon)
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(bearing))
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), math.degrees(lon2)

def altitude_color(alt) -> str:
    if not alt:
        return "gray"
    if alt < 3000:
        return "green"
    if alt < 15000:
        return "orange"
    return "red"

def flight_phase(vs) -> str:
    if vs is None:
        return "LEVEL"
    if vs < -200:
        return "DESCENDING"
    if vs > 200:
        return "CLIMBING"
    return "LEVEL"

def calc_eta_to_airport(altitude_ft, vertical_speed_ft_per_min):
    if not altitude_ft or not vertical_speed_ft_per_min or vertical_speed_ft_per_min >= -100:
        return None
    mins = altitude_ft / abs(vertical_speed_ft_per_min)
    return mins if mins > 0 else None

# --- CACHED DETAIL FETCHING ---

_PS_HEADERS = {
    "User-Agent": "SkyWatcherPro/1.0 (+https://github.com/skywatcher)",
    "Accept": "application/json",
}

def get_aircraft_image(registration: str, image_cache: dict) -> str | None:
    """Return a thumbnail URL for an aircraft by registration via Planespotters (free, no auth).
    image_cache must be passed explicitly — never access st.session_state inside a thread."""
    if not registration:
        return None
    if registration in image_cache:
        return image_cache[registration]
    try:
        r = requests.get(
            f"https://api.planespotters.net/pub/photos/reg/{registration}",
            headers=_PS_HEADERS,
            timeout=10,
        )
        if r.status_code == 200:
            photos = r.json().get("photos", [])
            url = photos[0].get("thumbnail", {}).get("src") if photos else None
            image_cache[registration] = url
            return url
    except Exception as e:
        logging.warning(f"Planespotters fetch failed for {registration}: {e}")
    image_cache[registration] = None
    return None

def _flight_key(flight):
    return getattr(flight, "id", None) or getattr(flight, "callsign", None)

def get_cached_details(fr_api, flight, cache: dict, airline_lookup: dict, image_cache: dict):
    """Fetch + cache flight details.
    All three dicts are passed explicitly — never read st.session_state inside this
    function because it is called from ThreadPoolExecutor worker threads."""
    fid = _flight_key(flight)
    if fid in cache:
        try:
            flight.set_flight_details(cache[fid]["raw"])
        except Exception:
            pass
        return cache[fid]

    reg  = getattr(flight, "registration", None) or ""
    iata = getattr(flight, "airline_iata",  "") or ""
    icao = getattr(flight, "airline_icao",  "") or ""

    try:
        raw = fr_api.get_flight_details(flight)
        flight.set_flight_details(raw)
        # Try FR24 image first, fall back to Planespotters
        image_url = None
        try:
            thumbs = raw.get("aircraft", {}).get("images", {}).get("thumbnails", [])
            if thumbs:
                image_url = thumbs[0].get("src")
            if not image_url:
                large = raw.get("aircraft", {}).get("images", {}).get("large", [])
                if large:
                    image_url = large[0].get("src")
        except Exception:
            pass
        if not image_url:
            image_url = get_aircraft_image(reg, image_cache)
        entry = {
            "airline":   getattr(flight, "airline_name", None) or airline_lookup.get(iata) or airline_lookup.get(icao) or iata or "Private/Unknown",
            "flight_no": getattr(flight, "number", None) or getattr(flight, "callsign", None) or "N/A",
            "origin":    getattr(flight, "origin_airport_name", None) or getattr(flight, "origin_airport_iata", None) or "—",
            "dest":      getattr(flight, "destination_airport_name", None) or getattr(flight, "destination_airport_iata", None) or "—",
            "image_url": image_url,
            "raw":       raw,
        }
    except Exception as e:
        logging.warning(f"Detail fetch failed for {fid}: {e}")
        # FR24 detail endpoint blocked — use Planespotters for image, base attrs for text
        entry = {
            "airline":   airline_lookup.get(iata) or airline_lookup.get(icao) or iata or "—",
            "flight_no": getattr(flight, "number", None) or getattr(flight, "callsign", None) or "—",
            "origin":    getattr(flight, "origin_airport_iata", None) or "—",
            "dest":      getattr(flight, "destination_airport_iata", None) or "—",
            "image_url": get_aircraft_image(reg, image_cache),
            "raw":       {},
        }
    cache[fid] = entry
    return entry

def warm_cache_parallel(fr_api, flights):
    """Fetch details for uncached flights in parallel (5 workers).
    Extracts session_state dicts in the main thread, then passes them into workers
    as plain Python objects — session_state is not accessible in worker threads."""
    cache         = st.session_state.flight_details_cache
    airline_lookup = st.session_state.airline_lookup
    image_cache   = st.session_state.image_cache
    uncached = [f for f in flights if _flight_key(f) not in cache]
    if not uncached:
        return
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(
            lambda f: get_cached_details(fr_api, f, cache, airline_lookup, image_cache),
            uncached,
        ))

# --- WEATHER ---

@st.cache_data(ttl=60)
def get_weather_data(lat: float, lon: float):
    try:
        url = (
            f"https://api.openweathermap.org/data/2.5/weather"
            f"?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric"
        )
        data = requests.get(url, timeout=10).json()
        return data if data.get("cod") == 200 else None
    except Exception as e:
        logging.warning(f"Weather fetch failed: {e}")
        return None

def get_spotting_advice(w: dict) -> tuple:
    clouds = w.get("clouds", {}).get("all", 0)
    main_wx = w.get("weather", [{}])[0].get("main", "")
    if main_wx in ["Rain", "Thunderstorm", "Drizzle", "Snow"]:
        return "Poor", "Precipitation detected. Visibility is low.", "red"
    if clouds > 80:
        return "Marginal", "Heavy cloud cover. Planes mostly obscured.", "orange"
    if 20 <= clouds <= 80:
        return "Good", "Mixed clouds. Dynamic lighting — great for photography.", "blue"
    return "Excellent", "Clear skies. Perfect visibility for high-altitude spotting.", "green"

# --- SESSION STATE INIT ---

if "flight_details_cache" not in st.session_state:
    st.session_state.flight_details_cache = {}
if "airline_lookup" not in st.session_state:
    st.session_state.airline_lookup = {}      # populated once on first run
if "image_cache" not in st.session_state:
    st.session_state.image_cache = {}         # keyed by registration, never expires
if "heatmap_points" not in st.session_state:
    st.session_state.heatmap_points = []
if "live_count" not in st.session_state:
    st.session_state.live_count = 0
if "live_squawk_alerts" not in st.session_state:
    st.session_state.live_squawk_alerts = []
if "live_journey_rows" not in st.session_state:
    st.session_state.live_journey_rows = []
if "live_tech_rows" not in st.session_state:
    st.session_state.live_tech_rows = []
if "live_flight_positions" not in st.session_state:
    st.session_state.live_flight_positions = []
if "live_nearest_info" not in st.session_state:
    st.session_state.live_nearest_info = None

# --- APP ---

st.title("SkyWatcher Pro")
st.markdown("### Real-time Flight & Weather Intelligence")

if not WEATHER_API_KEY:
    st.error("Weather API key missing. Add `WEATHER_API_KEY` to `.streamlit/secrets.toml`.")

# Sidebar
st.sidebar.title("Controls")
refresh_choice = st.sidebar.selectbox(
    "Live Refresh Interval",
    ["5s", "10s", "30s", "60s", "Manual"],
    index=0,
)
_refresh_map = {"5s": 5, "10s": 10, "30s": 30, "60s": 60, "Manual": None}
refresh_seconds = _refresh_map[refresh_choice]

st.sidebar.divider()

st.sidebar.title("About")
st.sidebar.info("ADS-B transponder data + meteorological data for close-range spotting (~10 km).")
st.sidebar.markdown("""
**Data Stack:**
- Radar: FlightRadar24 API
- Weather: OpenWeather API
- UI: Streamlit & Folium
""")

# 1. Geolocation
loc = get_geolocation()

if not loc:
    st.info("Accessing GPS... Please allow location permissions in your browser.")
else:
    lat = loc["coords"]["latitude"]
    lon = loc["coords"]["longitude"]

    # 2. Weather (cached, doesn't change often)
    weather = get_weather_data(lat, lon)
    if weather:
        status, advice, _ = get_spotting_advice(weather)
        st.subheader(f"Conditions at {weather.get('name', 'Your Location')}")
        c1, c2, c3, c4 = st.columns(4)
        temp = weather.get("main", {}).get("temp", "N/A")
        cloud_pct = weather.get("clouds", {}).get("all", "N/A")
        vis_raw = weather.get("visibility")
        vis = f"{vis_raw / 1000:.1f} km" if vis_raw else "N/A"
        c1.metric("Temperature", f"{temp}°C" if temp != "N/A" else "N/A")
        c2.metric("Cloud Cover", f"{cloud_pct}%" if cloud_pct != "N/A" else "N/A")
        c3.metric("Visibility", vis)
        c4.metric("Spotter Index", status)
        st.info(f"**Spotter's Note:** {advice}")

    st.divider()

    # 3. Init FlightRadar API (cheap, reusable across reruns)
    fr_api = FlightRadar24API()

    # 4. Build airline lookup once per session (2 000+ airlines, no auth needed)
    if not st.session_state.airline_lookup:
        try:
            raw_airlines = fr_api.get_airlines()
            lk = {}
            for a in raw_airlines:
                if a.get("IATA"):
                    lk[a["IATA"]] = a["Name"]
                if a.get("ICAO"):
                    lk[a["ICAO"]] = a["Name"]
            st.session_state.airline_lookup = lk
        except Exception as e:
            logging.warning(f"Airline lookup fetch failed: {e}")

    # --- HELPERS ---

    def _fetch_and_process(lat: float, lon: float, fr_api) -> bool:
        """Fetch flights, warm detail cache in parallel, write all display data to session_state."""
        try:
            bounds = fr_api.get_bounds_by_point(lat, lon, SEARCH_RADIUS_M)
            flights = fr_api.get_flights(bounds=bounds)
        except Exception as e:
            logging.warning(f"FlightRadar24 fetch failed: {e}")
            st.error("Could not reach FlightRadar24. Retrying on next tick.")
            return False

        count = len(flights) if flights else 0
        st.session_state.live_count = count

        if not flights:
            st.session_state.live_squawk_alerts = []
            st.session_state.live_journey_rows = []
            st.session_state.live_tech_rows = []
            st.session_state.live_flight_positions = []
            st.session_state.live_nearest_info = None
            return True

        # Parallel warm-up for any new flight IDs (5 workers)
        warm_cache_parallel(fr_api, flights)

        # Extract dicts once in main thread for the loop below
        cache          = st.session_state.flight_details_cache
        airline_lookup = st.session_state.airline_lookup
        image_cache    = st.session_state.image_cache

        squawk_alerts, journey_rows, tech_rows, flight_positions = [], [], [], []
        nearest_km, nearest_info = float("inf"), None

        for f in flights:
            sq      = str(getattr(f, "squawk", "") or "")
            alt     = getattr(f, "altitude", None)
            vs      = getattr(f, "vertical_speed", None)
            spd     = getattr(f, "ground_speed", None)
            heading = getattr(f, "heading", None)
            f_lat   = getattr(f, "latitude", None)
            f_lon   = getattr(f, "longitude", None)

            d = get_cached_details(fr_api, f, cache, airline_lookup, image_cache)

            alt_str = f"{alt:,} ft" if alt else "N/A"
            phase   = flight_phase(vs)

            if sq in SQUAWK_ALERTS:
                msg, level = SQUAWK_ALERTS[sq]
                squawk_alerts.append((msg, level, getattr(f, "callsign", "UNKNOWN")))

            eta_str = "N/A"
            if vs is not None and vs < -100 and alt:
                eta_min = calc_eta_to_airport(alt, vs)
                if eta_min and eta_min > 0:
                    eta_str = f"~{int(eta_min)} min"

            journey_rows.append({
                "Preview": d["image_url"],
                "Airline": d["airline"],
                "Flight":  d["flight_no"],
                "From":    d["origin"],
                "To":      d["dest"],
            })
            tech_rows.append({
                "Flight":       d["flight_no"],
                "Alt (ft)":     alt_str,
                "Speed (kt)":   spd if spd else "N/A",
                "Heading":      f"{heading}°" if heading is not None else "N/A",
                "Phase":        phase,
                "Est. Descent": eta_str,
            })

            if f_lat and f_lon:
                dist = haversine_km(lat, lon, f_lat, f_lon)
                if dist < nearest_km:
                    nearest_km = dist
                    nearest_info = {
                        "airline": d["airline"],
                        "flight":  d["flight_no"],
                        "dist":    dist,
                        "alt":     alt_str,
                        "phase":   phase,
                    }
                st.session_state.heatmap_points.append([f_lat, f_lon])
                flight_positions.append({
                    "lat": f_lat, "lon": f_lon, "alt": alt, "spd": spd,
                    "heading": heading, "sq": sq,
                    "flight_no": d["flight_no"], "airline": d["airline"],
                })

        st.session_state.live_squawk_alerts   = squawk_alerts
        st.session_state.live_journey_rows    = journey_rows
        st.session_state.live_tech_rows       = tech_rows
        st.session_state.live_flight_positions = flight_positions
        st.session_state.live_nearest_info    = nearest_info
        return True

    def _render_live_map(lat: float, lon: float) -> None:
        """Leaflet map via st.components.v1.html.
        Init once (tiles, user pin, 10 km circle).  On every fragment tick only
        the plane markers slide to new positions via setLatLng — base map is
        never destroyed.  Overlays (heading lines, squawk rings) rebuild each
        tick (cheap polylines).  A re-center button lets the user snap back."""

        positions = st.session_state.live_flight_positions
        positions_json = json.dumps(positions)

        html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <link rel="stylesheet"
        href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    html, body {{ margin:0; padding:0; background:#0a0a0a; }}
    #map {{ height:600px; width:100%; }}
    .recenter-btn {{
      background:#000000; color:#39ff14; border:1px solid #39ff14;
      border-radius:4px; padding:5px 10px; font-size:12px;
      font-family:monospace; cursor:pointer; line-height:1;
    }}
    .recenter-btn:hover {{ background:#39ff14; color:#000000; }}
  </style>
</head>
<body>
<div id="map"></div>
<script>
(function() {{
  var userLat   = {lat};
  var userLon   = {lon};
  var radius    = {SEARCH_RADIUS_M};
  var positions = {positions_json};

  /* ── colour by altitude ── */
  function altColor(alt) {{
    if (!alt || alt === 0) return '#aaaaaa';
    if (alt < 3000)        return '#44ff88';
    if (alt < 15000)       return '#ffaa00';
    return '#ff4444';
  }}

  /* ── haversine forward projection ── */
  function projectPoint(lat, lon, hdg, distKm) {{
    var R = 6371;
    var d = distKm / R;
    var brng = hdg * Math.PI / 180;
    var lat1 = lat * Math.PI / 180;
    var lon1 = lon * Math.PI / 180;
    var lat2 = Math.asin(
      Math.sin(lat1)*Math.cos(d) +
      Math.cos(lat1)*Math.sin(d)*Math.cos(brng)
    );
    var lon2 = lon1 + Math.atan2(
      Math.sin(brng)*Math.sin(d)*Math.cos(lat1),
      Math.cos(d)-Math.sin(lat1)*Math.sin(lat2)
    );
    return [lat2 * 180 / Math.PI, lon2 * 180 / Math.PI];
  }}

  /* ── build plane divIcon ── */
  function makePlaneIcon(color, hdg) {{
    return L.divIcon({{
      html: '<div style="font-size:22px;line-height:1;'
          + 'color:' + color + ';'
          + 'transform:rotate(' + (hdg - 90) + 'deg);'
          + 'transition:transform 0.5s ease;'
          + 'filter:drop-shadow(0 0 3px ' + color + ')">✈</div>',
      iconSize: [22, 22], iconAnchor: [11, 11], className: ''
    }});
  }}

  /* ── init map once per page load ── */
  if (!window._skyMap) {{
    window._skyMap = L.map('map', {{
      center: [userLat, userLon],
      zoom: 13,
      zoomControl: true
    }});

    L.tileLayer(
      'https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png',
      {{ attribution: '&copy; CartoDB', maxZoom: 19 }}
    ).addTo(window._skyMap);

    /* user position pin */
    var youIcon = L.divIcon({{
      html: '<div style="width:14px;height:14px;border-radius:50%;'
          + 'background:#ff3333;border:2px solid #fff;'
          + 'box-shadow:0 0 6px #ff3333"></div>',
      iconSize: [14, 14], iconAnchor: [7, 7], className: ''
    }});
    L.marker([userLat, userLon], {{ icon: youIcon }})
     .bindTooltip('You are here', {{ permanent: false }})
     .addTo(window._skyMap);

    /* 10 km radius circle */
    window._skyCircle = L.circle([userLat, userLon], {{
      radius:      radius,
      color:       '#00d4ff',
      weight:      2,
      opacity:     0.8,
      fill:        true,
      fillColor:   '#00d4ff',
      fillOpacity: 0.04
    }}).addTo(window._skyMap);
    window._skyMap.fitBounds(window._skyCircle.getBounds(), {{ padding: [20, 20] }});

    /* re-center button (Leaflet custom control) */
    var RecenterCtrl = L.Control.extend({{
      options: {{ position: 'topright' }},
      onAdd: function() {{
        var btn = L.DomUtil.create('button', 'recenter-btn');
        btn.innerHTML = 'Re-center';
        btn.title = 'Fit 10 km radius';
        L.DomEvent.disableClickPropagation(btn);
        btn.addEventListener('click', function() {{
          window._userInteracted = false;
          window._skyMap.fitBounds(
            window._skyCircle.getBounds(), {{ padding: [20, 20] }}
          );
        }});
        return btn;
      }}
    }});
    new RecenterCtrl().addTo(window._skyMap);

    /* track whether user has manually panned/zoomed */
    window._userInteracted = false;
    window._skyMap.on('dragstart zoomstart', function() {{
      window._userInteracted = true;
    }});

    /* persistent plane markers dict + disposable overlay layer */
    window._planeMarkers = {{}};
    window._overlayLayer = L.layerGroup().addTo(window._skyMap);
  }}

  /* ── auto re-center if user hasn't interacted ── */
  if (!window._userInteracted) {{
    window._skyMap.fitBounds(
      window._skyCircle.getBounds(), {{ padding: [20, 20] }}
    );
  }}

  /* ── clear overlays (heading lines, squawk rings) — cheap to rebuild ── */
  window._overlayLayer.clearLayers();

  /* ── update plane markers (slide existing, add new, remove departed) ── */
  var activeIds = {{}};

  positions.forEach(function(fp) {{
    var color = altColor(fp.alt);
    var hdg   = fp.heading || 0;
    var id    = fp.flight_no;
    activeIds[id] = true;

    /* squawk alert ring (overlay — rebuilt each tick) */
    var sq = fp.sq || '';
    if (sq === '7700' || sq === '7500' || sq === '7600') {{
      L.circleMarker([fp.lat, fp.lon], {{
        radius: 24, color: '#ff0000',
        fill: true, fillOpacity: 0.18, weight: 2
      }}).addTo(window._overlayLayer);
    }}

    /* heading line — 20 km ray showing direction of travel (overlay) */
    if (fp.heading != null) {{
      var proj = projectPoint(fp.lat, fp.lon, fp.heading, 20);
      L.polyline([[fp.lat, fp.lon], proj], {{
        color: '#00d4ff', weight: 2, opacity: 0.6, dashArray: '6,5'
      }}).addTo(window._overlayLayer);
      L.circleMarker(proj, {{
        radius: 4, color: '#00d4ff', fill: true, fillOpacity: 0.5, weight: 1
      }}).bindTooltip('Heading: ' + fp.flight_no)
        .addTo(window._overlayLayer);
    }}

    /* plane marker — slide existing or create new */
    var altText = fp.alt ? fp.alt.toLocaleString() + ' ft' : 'N/A';
    var spdText = fp.spd ? fp.spd + ' kts' : 'N/A';
    var popupHtml =
      '<div style="font-family:monospace;min-width:170px;">'
      + '<div style="font-size:15px;font-weight:bold;margin-bottom:2px;">'
      + fp.flight_no + '</div>'
      + '<div style="font-size:12px;color:#666;margin-bottom:6px;">'
      + fp.airline + '</div>'
      + '<hr style="margin:4px 0;">'
      + '<div style="font-size:12px;">'
      + 'Alt: <b>' + altText + '</b><br>'
      + 'Speed: <b>' + spdText + '</b>'
      + '</div></div>';

    if (window._planeMarkers[id]) {{
      /* existing plane — slide to new position, update icon rotation */
      var m = window._planeMarkers[id];
      m.setLatLng([fp.lat, fp.lon]);
      m.setIcon(makePlaneIcon(color, hdg));
      m.setTooltipContent(fp.flight_no + ' — ' + fp.airline);
      m.setPopupContent(popupHtml);
    }} else {{
      /* new plane entering radius */
      var marker = L.marker([fp.lat, fp.lon], {{
        icon: makePlaneIcon(color, hdg)
      }})
      .bindTooltip(fp.flight_no + ' — ' + fp.airline, {{ sticky: false }})
      .bindPopup(popupHtml, {{ maxWidth: 220 }})
      .addTo(window._skyMap);
      window._planeMarkers[id] = marker;
    }}
  }});

  /* remove departed planes (no longer in positions data) */
  Object.keys(window._planeMarkers).forEach(function(id) {{
    if (!activeIds[id]) {{
      window._skyMap.removeLayer(window._planeMarkers[id]);
      delete window._planeMarkers[id];
    }}
  }});
}})();
</script>
</body>
</html>"""

        components.html(html, height=620)

    def _render_heatmap_tab(lat: float, lon: float) -> None:
        if st.session_state.heatmap_points:
            hmap = folium.Map(location=[lat, lon], zoom_start=13, tiles="CartoDB dark_matter")
            folium.Marker(
                [lat, lon],
                tooltip="You",
                icon=folium.Icon(color="red", icon="user", prefix="fa"),
            ).add_to(hmap)
            folium.plugins.HeatMap(
                st.session_state.heatmap_points, radius=50, blur=15, max_zoom=1,
            ).add_to(hmap)
            st_folium(hmap, width=1400, height=600, returned_objects=[], key="heatmap")
            st.caption(f"Heatmap: {len(st.session_state.heatmap_points)} aircraft positions recorded this session")
        else:
            st.info("Heatmap will populate after a few radar updates.")

    # --- LIVE MODE: four isolated fragments — each region updates independently, no full-page flicker ---

    _LEGEND = """
**Altitude Color Key:**
[LOW] `< 3,000 ft` — Final approach / departure
[MEDIUM] `3K – 15K ft` — Climbing or descending
[HIGH] `> 15,000 ft` — High cruise

**Heading:** Compass bearing in degrees (0° = North)
**Projected Path:** Dashed cyan line — projected position in 5 minutes
"""

    st.subheader("Live Radar (10 km Radius)")

    if refresh_seconds:
        @st.fragment(run_every=refresh_seconds)
        def _metrics_frag():
            ok = _fetch_and_process(lat, lon, fr_api)
            if not ok:
                return
            count = st.session_state.live_count
            status_label = "OK" if count <= 3 else ("BUSY" if count <= 8 else "CRITICAL")
            cache_size = len(st.session_state.flight_details_cache)
            s1, s2, s3 = st.columns(3)
            s1.metric("Aircraft in Radius", count, delta=status_label, delta_color="off")
            s2.metric("Detail Cache (flights)", cache_size)
            s3.metric("Refresh Interval", f"{refresh_seconds}s")
            if count == 0:
                st.warning("The sky is quiet. No flights detected within 10 km.")
                return
            for msg, level, cs in st.session_state.live_squawk_alerts:
                if level == "error":
                    st.error(f"{msg} — Aircraft: **{cs}**")
                else:
                    st.warning(f"{msg} — Aircraft: **{cs}**")
            nearest = st.session_state.live_nearest_info
            if nearest:
                st.info(
                    f"Closest Aircraft: {nearest['airline']} ({nearest['flight']}) — "
                    f"**{nearest['dist']:.2f} km** away · {nearest['alt']} · {nearest['phase']}"
                )

        @st.fragment(run_every=refresh_seconds)
        def _map_frag():
            _render_live_map(lat, lon)

        @st.fragment(run_every=refresh_seconds)
        def _heat_frag():
            _render_heatmap_tab(lat, lon)

        @st.fragment(run_every=refresh_seconds)
        def _tables_frag():
            journey_rows = st.session_state.live_journey_rows
            tech_rows    = st.session_state.live_tech_rows
            if not journey_rows:
                return
            st.subheader("Journey Details")
            st.dataframe(
                pd.DataFrame(journey_rows),
                column_config={"Preview": st.column_config.ImageColumn("Preview", width="small")},
                hide_index=True,
                use_container_width=True,
            )
            st.divider()
            st.subheader("Technical Intelligence")
            st.dataframe(pd.DataFrame(tech_rows), hide_index=True, use_container_width=True)
            st.markdown(_LEGEND)

        _metrics_frag()
        tab_live, tab_heat = st.tabs(["Live Radar", "Session Heatmap"])
        with tab_live:
            _map_frag()
        with tab_heat:
            _heat_frag()
        st.divider()
        _tables_frag()
        st.caption(f"Live mode — auto-refreshing every {refresh_seconds}s. Map & tables update in place.")

    else:
        _fetch_and_process(lat, lon, fr_api)
        count = st.session_state.live_count
        status_label = "OK" if count <= 3 else ("BUSY" if count <= 8 else "CRITICAL")
        cache_size = len(st.session_state.flight_details_cache)
        s1, s2, s3 = st.columns(3)
        s1.metric("Aircraft in Radius", count, delta=status_label, delta_color="off")
        s2.metric("Detail Cache (flights)", cache_size)
        s3.metric("Refresh Interval", "Manual")
        if count == 0:
            st.warning("The sky is quiet. No flights detected within 10 km.")
        else:
            for msg, level, cs in st.session_state.live_squawk_alerts:
                if level == "error":
                    st.error(f"{msg} — Aircraft: **{cs}**")
                else:
                    st.warning(f"{msg} — Aircraft: **{cs}**")
            nearest = st.session_state.live_nearest_info
            if nearest:
                st.info(
                    f"Closest Aircraft: {nearest['airline']} ({nearest['flight']}) — "
                    f"**{nearest['dist']:.2f} km** away · {nearest['alt']} · {nearest['phase']}"
                )
            tab_live, tab_heat = st.tabs(["Live Radar", "Session Heatmap"])
            with tab_live:
                _render_live_map(lat, lon)
            with tab_heat:
                _render_heatmap_tab(lat, lon)
            st.divider()
            st.subheader("Journey Details")
            st.dataframe(
                pd.DataFrame(st.session_state.live_journey_rows),
                column_config={"Preview": st.column_config.ImageColumn("Preview", width="small")},
                hide_index=True,
                use_container_width=True,
            )
            st.divider()
            st.subheader("Technical Intelligence")
            st.dataframe(pd.DataFrame(st.session_state.live_tech_rows), hide_index=True, use_container_width=True)
            st.markdown(_LEGEND)
        st.caption("Manual mode — reload the page to refresh.")
