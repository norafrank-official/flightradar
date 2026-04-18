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

# --- CSS FOR STYLING (CORRECTED) ---
st.markdown("""
    <style>
    .main {
        background-color: #0e1117;
    }
    div[data-testid="stMetricValue"] {
        font-size: 24px;
        color: #00d4ff;
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
    """Calculates if plane spotting is ideal based on atmospheric conditions."""
    clouds = weather_json.get("clouds", {}).get("all", 0)
    main_weather = weather_json.get("weather", [{}])[0].get("main", "")
    
    if main_weather in ["Rain", "Thunderstorm", "Drizzle", "Snow"]:
        return "❌ Poor", "Precipitation detected. Visibility is low and gear might get wet.", "red"
    elif clouds > 80:
        return "⚠️ Marginal", "Heavy cloud cover. Planes will be mostly obscured by the ceiling.", "orange"
    elif 20 <= clouds <= 80:
        return "✅ Good", "Mixed clouds. Good for photography with dynamic lighting.", "blue"
    else:
        return "🌟 Excellent", "Clear skies! Perfect visibility for high-altitude spotting.", "green"

# --- MAIN APP LOGIC ---

st.title("✈️ SkyWatcher Pro")
st.markdown("### Real-time Flight & Weather Intelligence")

# 1. Get User Location via Browser
loc = get_geolocation()

if not loc:
    st.info("🛰️ Accessing GPS... Please allow location permissions in your browser.")
else:
    lat = loc['coords']['latitude']
    lon = loc['coords']['longitude']
    
    # 2. Weather & Spotting Index Section
    weather = get_weather_data(lat, lon)
    if weather:
        status, advice, color = get_spotting_advice(weather)
        
        with st.container():
            st.subheader(f"📍 Conditions at {weather.get('name', 'Your Location')}")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Temperature", f"{weather['main']['temp']}°C")
            m2.metric("Cloud Cover", f"{weather['clouds']['all']}%")
            m3.metric("Visibility", f"{weather['visibility']/1000} km")
            m4.metric("Spotter Index", status)
            st.info(f"**Spotter's Note:** {advice}")

    st.divider()

    # 3. Flight Radar Section
    st.subheader("📡 Live Radar (10km Radius)")
    
    fr_api = FlightRadar24API()
    # Scans a 10km radius (10,000 meters)
    bounds = fr_api.get_bounds_by_point(lat, lon, 10000) 
    flights = fr_api.get_flights(bounds=bounds)

    if flights:
        col_map, col_list = st.columns([2, 1])
        
        # Setup Map - Using 'CartoDB dark_matter' for a "Command Center" look
        m = folium.Map(location=[lat, lon], zoom_start=13, tiles='CartoDB dark_matter')
        folium.Marker([lat, lon], tooltip="You are here", icon=folium.Icon(color='red', icon='user', prefix='fa')).add_to(m)

        flight_details_list = []

        for f in flights:
            # Fetching deep details for Airline/Route names (as per your friend's suggestion)
            try:
                details = fr_api.get_flight_details(f)
                f.set_flight_details(details)
                
                airline = f.airline_name if f.airline_name else "Private/Unknown"
                flight_no = f.number if f.number else f.callsign
                origin = f.origin_airport_name if f.origin_airport_name else "Unknown"
                destination = f.destination_airport_name if f.destination_airport_name else "Unknown"
            except:
                airline, flight_no, origin, destination = "N/A", f.callsign, "N/A", "N/A"

            # Add Plane to Map
            folium.Marker(
                [f.latitude, f.longitude],
                popup=f"<b>{airline}</b><br>Flight: {flight_no}<br>To: {destination}",
                tooltip=f"{airline} - {flight_no}",
                icon=folium.Icon(color='lightblue', icon='plane', prefix='fa')
            ).add_to(m)
            
            # Add to Data Table
            flight_details_list.append({
                "Airline": airline,
                "Flight": flight_no,
                "From": origin,
                "To": destination,
                "Altitude": f"{f.altitude:,} ft"
            })

        with col_map:
            st_folium(m, width=800, height=500, returned_objects=[], key="flight_map")
        
        with col_list:
            st.write("**Aircraft Details**")
            df = pd.DataFrame(flight_details_list)
            st.dataframe(df, hide_index=True, use_container_width=True)
            
    else:
        st.warning("The sky is quiet! No flights detected within 10km.")

# Sidebar Info
st.sidebar.title("App Intelligence")
st.sidebar.info(
    "This app combines ADS-B transponder data with local meteorological data to provide a comprehensive spotting dashboard."
)
st.sidebar.markdown("""
**Data Stack:**
- **Radar:** FlightRadar24 API
- **Weather:** OpenWeather API
- **UI:** Streamlit & Folium
""")
