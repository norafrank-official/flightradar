import streamlit as st
import requests
import pandas as pd
import folium
from FlightRadar24 import FlightRadar24API
from streamlit_js_eval import get_geolocation
from streamlit_folium import st_folium

# --- CONFIGURATION ---
# Replace with your key from https://openweathermap.org/
# For deployment, use: WEATHER_API_KEY = st.secrets["WEATHER_API_KEY"]
WEATHER_API_KEY = "ca09bf0a26e46d745eeff8da704aa2e2" 

st.set_page_config(page_title="SkyWatcher Pro", layout="wide", page_icon="✈️")

# --- CSS FOR STYLING ---
st.markdown("""
    <style>
    .main {
        background-color: #0e1117;
    }
    div[data-testid="stMetricValue"] {
        font-size: 22px;
        color: #00d4ff;
    }
    h3 {
        padding-top: 1rem;
    }
    </style>
    """, unsafe_allow_html=True)

# --- FUNCTIONS ---

def get_weather_data(lat, lon):
    """Fetches real-time weather and cloud cover data."""
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={WEATHER_API_KEY}&units=metric"
        response = requests.get(url).json()
        return response if response.get("cod") == 200 else None
    except:
        return None

def get_spotting_advice(weather_json):
    """Calculates if plane spotting is ideal."""
    clouds = weather_json.get("clouds", {}).get("all", 0)
    main_weather = weather_json.get("weather", [{}])[0].get("main", "")
    
    if main_weather in ["Rain", "Thunderstorm", "Drizzle", "Snow"]:
        return "❌ Poor", "Rain/Storm detected. Visibility is low.", "red"
    elif clouds > 80:
        return "⚠️ Marginal", "Heavy clouds. Planes might be hidden.", "orange"
    elif 20 <= clouds <= 80:
        return "✅ Good", "Mixed clouds. Good for dramatic spotting.", "blue"
    else:
        return "🌟 Excellent", "Clear skies! Perfect visibility.", "green"

# --- MAIN APP LOGIC ---

st.title("✈️ SkyWatcher Pro")

# 1. Get User Location via Browser
loc = get_geolocation()

if not loc:
    st.info("🛰️ Accessing GPS... Please allow location permissions in your browser.")
else:
    lat = loc['coords']['latitude']
    lon = loc['coords']['longitude']
    
    # 2. WEATHER & SPOTTING SECTION
    weather = get_weather_data(lat, lon)
    if weather:
        status, advice, color = get_spotting_advice(weather)
        
        st.subheader(f"📍 Conditions at {weather.get('name', 'Current Location')}")
        w1, w2, w3, w4 = st.columns(4)
        w1.metric("Temp", f"{weather['main']['temp']}°C")
        w2.metric("Clouds", f"{weather['clouds']['all']}%")
        w3.metric("Visibility", f"{weather['visibility']/1000} km")
        w4.metric("Spotter Index", status)
        st.info(f"**Pro Tip:** {advice}")
    else:
        st.error("Weather data unavailable. Please check your API Key.")

    st.divider()

    # 3. FLIGHT RADAR SECTION
    st.subheader("📡 Live Radar (10km Radius)")
    
    fr_api = FlightRadar24API()
    bounds = fr_api.get_bounds_by_point(lat, lon, 10000) 
    flights = fr_api.get_flights(bounds=bounds)

    if flights:
        # Map Display
        m = folium.Map(location=[lat, lon], zoom_start=13, tiles='CartoDB dark_matter')
        folium.Marker([lat, lon], tooltip="You", icon=folium.Icon(color='red', icon='user', prefix='fa')).add_to(m)

        journey_data = []
        tech_data = []

        for f in flights:
            try:
                details = fr_api.get_flight_details(f)
                f.set_flight_details(details)
                
                airline = f.airline_name if f.airline_name else "Private"
                flight_no = f.number if f.number else f.callsign
                origin = f.origin_airport_name if f.origin_airport_name else "Unknown"
                dest = f.destination_airport_name if f.destination_airport_name else "Unknown"
            except:
                airline, flight_no, origin, dest = "N/A", f.callsign, "N/A", "N/A"

            # Populate Table 1: Journey
            journey_data.append({
                "Airline": airline,
                "Flight": flight_no,
                "From": origin,
                "To": dest
            })
            
            # Populate Table 2: Technical
            tech_data.append({
                "Flight": flight_no,
                "Alt (ft)": f"{f.altitude:,}",
                "Speed (kt)": f.ground_speed,
                "Heading": f"{f.heading}°"
            })

            # Add Marker
            folium.Marker(
                [f.latitude, f.longitude],
                popup=f"<b>{airline}</b><br>{flight_no}",
                icon=folium.Icon(color='lightblue', icon='plane', prefix='fa')
            ).add_to(m)

        # UI Layout
        st_folium(m, width=1200, height=450, returned_objects=[], key="main_map")
        
        st.write("### ✈️ Journey Details")
        st.dataframe(pd.DataFrame(journey_data), hide_index=True, use_container_width=True)
        
        st.write("### 📊 Technical Intelligence")
        st.dataframe(pd.DataFrame(tech_data), hide_index=True, use_container_width=True)
            
    else:
        st.warning("No flights detected within 10km of your current GPS position.")

st.sidebar.title("SkyWatcher Pro")
st.sidebar.markdown("""
- **Radar:** FlightRadar24
- **Weather:** OpenWeatherMap
- **UI:** Streamlit
""")
