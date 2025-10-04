import os
from flask import Flask, render_template, request, redirect, url_for, session
import pandas as pd
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from http.client import RemoteDisconnected

# ----------------------------------------------------------------------
# NEW IMPORTS FOR PLOTLY DASH
# ----------------------------------------------------------------------
import dash
from dash import dcc
from dash import html
import plotly.express as px
import plotly.graph_objects as go
from dash.dependencies import Input, Output
# ----------------------------------------------------------------------

# ==============================================================================
# --- 1. APP & DATA INITIALIZATION ---
# ==============================================================================
app = Flask(__name__)
app.secret_key = 'your_very_secret_key'

try:
    df_login = pd.read_csv('login.csv')
    df_clients = pd.read_csv('clients.csv')
except FileNotFoundError:
    print("FATAL ERROR: 'login.csv' or 'clients.csv' not found. Please ensure they are in the project folder.")
    exit()

# ==============================================================================
# --- 2. GOOGLE SHEETS API CONFIGURATION ---
# (Existing configuration logic remains here)
# ==============================================================================
gspread_client = None
worksheet = None
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    gspread_client = gspread.authorize(creds)
    spreadsheet = gspread_client.open("Space Assessor")
    worksheet = spreadsheet.worksheet("Adelaide_BoothA")
    print("Successfully connected to Google Sheets.")
except Exception as e:
    print(f"WARNING: Could not connect to Google Sheets. Live data will be unavailable. Details: {e}")

# ==============================================================================
# --- 3. HELPER FUNCTIONS ---
# (Existing helper functions remain here)
# ==============================================================================

def get_data_from_sheet():
    if not worksheet: return None
    try:
        data = worksheet.get_all_records()
        df = pd.DataFrame(data)
        if 'time' in df.columns:
            df['time'] = pd.to_datetime(df['time'], errors='coerce')
        return df.sort_values(by='time', ascending=True).reset_index(drop=True)
    except Exception as e:
        print(f"Error fetching data from Google Sheet: {e}")
        return None

def load_sensor_data(loc_name, booth_name):
    """
    Hybrid function to load data, handle missing columns, and enforce numeric types.
    """
    df = None
    required_cols = ['time', 'temp_c', 'humidity_pct', 'co2_ppm', 'pir_state']
    
    # Standardize booth name for comparison
    clean_booth_name = booth_name.replace(' ', '')
    
    if loc_name == 'Adelaide' and clean_booth_name == 'BoothA':
        df = get_data_from_sheet()
    else:
        filepath = os.path.join('data', f"{loc_name.replace(' ', '')}_{clean_booth_name}.csv")
        if os.path.exists(filepath):
            try:
                df = pd.read_csv(filepath)
            except Exception as e:
                print(f"Error reading {filepath}: {e}")
                return None
    
    if df is not None and not df.empty:
        # Step 1: Ensure all required columns exist, filling with None if missing
        for col in required_cols:
            if col not in df.columns:
                df[col] = None
        
        # --- THIS IS THE CRUCIAL FIX ---
        # Step 2: Convert all sensor reading columns to a numeric type.
        # 'errors='coerce'' will turn any non-numeric values (like empty strings) into NaN.
        numeric_cols = ['temp_c', 'humidity_pct', 'co2_ppm']
        for col in numeric_cols:
            # Check if column exists before trying to convert it
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        # --- END OF FIX ---
            
        # Step 3: Ensure the 'time' column is always a datetime object
        if 'time' in df.columns:
            df['time'] = pd.to_datetime(df['time'], errors='coerce')
        
        # Sort by time to ensure the last row is the latest reading
        return df.sort_values(by='time').reset_index(drop=True)
        
    return None


def get_locations(df_clients, client_name=None):
    if client_name:
        return df_clients[df_clients['client_name'] == client_name]['location'].unique().tolist()
    else:
        return df_clients['location'].unique().tolist()

# ==============================================================================
# --- 4. FLASK ROUTES ---
# (Login, logout, dashboard, location, booth routes remain unchanged)
# ==============================================================================

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = str(request.form['password'])
        user_data = df_login[(df_login['username'] == username) & (df_login['password'] == password)]
        if not user_data.empty:
            session['username'] = user_data.iloc[0]['username']
            session['role'] = user_data.iloc[0]['role']
            session['client_name'] = user_data.iloc[0]['client_name']
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error="Invalid credentials")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'username' not in session: return redirect(url_for('login'))
    
    client_name = session.get('client_name')
    locations = get_locations(df_clients, client_name if session.get('role') == 'client' else None)
    
    active_alerts = []
    system_status = []
    
    location_summaries = {}
    for loc in locations:
        alert_count = 0
        booths_in_loc = df_clients[df_clients['location'] == loc]['booth'].unique().tolist()
        for booth_name in booths_in_loc:
            df_booth = load_sensor_data(loc, booth_name)
            if df_booth is not None and not df_booth.empty:
                latest = df_booth.iloc[-1]
                co2_val = latest.get('co2_ppm')
                temp_val = latest.get('temp_c')
                if (co2_val is not None and co2_val > 1000) or (temp_val is not None and temp_val > 25):
                    alert_count += 1
        location_summaries[loc] = alert_count

         # 1. Logic for Active Alerts Log
    co2_val, temp_val = latest.get('co2_ppm'), latest.get('temp_c')
    if (co2_val is not None and co2_val > 1000):
         active_alerts.append(f"High CO₂ in {loc}, {booth_name}: {int(co2_val)} ppm")
    if (temp_val is not None and temp_val > 25):
         active_alerts.append(f"High Temp in {loc}, {booth_name}: {temp_val}°C")

    # 2. Logic for System Status Panel
    last_seen = latest.get('time')
    if last_seen:
        time_diff = datetime.now() - last_seen
        status = "Online"
        if time_diff > timedelta(hours=1):
            status = "Offline"
        system_status.append({'location': loc, 'booth': booth_name, 'last_seen': last_seen.strftime('%Y-%m-%d %H:%M'), 'status': status})
    else:
        system_status.append({'location': loc, 'booth': booth_name, 'last_seen': 'Never', 'status': 'Offline'})

        
    df_spotlight = load_sensor_data('Adelaide', 'Booth A')
    kpi_data = {}
    if df_spotlight is not None and not df_spotlight.empty:
        recent_data = df_spotlight.tail(24)
        kpi_data = {
            'temp_labels': recent_data['time'].dt.strftime('%H:%M').tolist(),
            'temp_values': recent_data['temp_c'].tolist(),
            'humidity_values': recent_data['humidity_pct'].tolist(),
            'occupancy_counts': recent_data['pir_state'].value_counts().to_dict()
        }
    return render_template('dashboard.html', locations=locations, location_summaries=location_summaries, kpi_data=kpi_data)

@app.route('/location/<loc_name>')
def location(loc_name):
    if 'username' not in session: return redirect(url_for('login'))
    client_name = session.get('client_name')
    user_locations = get_locations(df_clients, client_name if session.get('role') == 'client' else None)
    
    if session['role'] == 'client' and loc_name not in user_locations:
        return "Access Denied", 403
    
    booths = df_clients[df_clients['location'] == loc_name]['booth'].unique().tolist()
    return render_template('location.html', locations=user_locations, location_name=loc_name, booths=booths)

@app.route('/booth/<loc_name>/<booth_name>')
def booth(loc_name, booth_name):
    if 'username' not in session: return redirect(url_for('login'))
    
    df_booth_data = load_sensor_data(loc_name, booth_name)
    has_data = df_booth_data is not None and not df_booth_data.empty

    reading = {}
    historical_context = {}

    if has_data:
        reading = df_booth_data.iloc[-1].to_dict()
        
        # --- SAFE HISTORICAL CONTEXT CALCULATION ---
        # Filter for data older than 24 hours
        yesterday_data = df_booth_data[df_booth_data['time'] < (datetime.now() - timedelta(days=1))]
        
        # Only calculate changes if there is historical data to compare against
        if not yesterday_data.empty:
            if 'temp_c' in reading and reading['temp_c'] is not None:
                historical_context['temp_change'] = reading['temp_c'] - yesterday_data['temp_c'].mean()
            if 'humidity_pct' in reading and reading['humidity_pct'] is not None:
                historical_context['hum_change'] = reading['humidity_pct'] - yesterday_data['humidity_pct'].mean()

    client_name = session.get('client_name')
    if session['role'] == 'client':
        allowed_booths = df_clients[(df_clients['client_name'] == client_name) & (df_clients['location'] == loc_name)]
        if booth_name not in allowed_booths['booth'].tolist():
            return "Access Denied", 403
            
    df_booth_data = load_sensor_data(loc_name, booth_name)
    has_data = df_booth_data is not None and not df_booth_data.empty
    
    reading = df_booth_data.iloc[-1].to_dict() if has_data else {}
    
    booth_thresholds = {'temp_c': {'low': 18, 'high': 24}, 'humidity_pct': {'low': 40, 'high': 60}, 'co2_ppm': {'low': 0, 'high': 1000}, 'voc': {'low': 0, 'high': 100}}
    
    locations = get_locations(df_clients, client_name if session.get('role') == 'client' else None)
    
    return render_template('booth.html', reading=reading, historical_context=historical_context , locations=locations, loc_name=loc_name, booth_name=booth_name, thresholds=booth_thresholds, has_data=has_data)

# ==============================================================================
# --- 5. PLOTLY DASH INTEGRATION ---
# ==============================================================================

# Initialize Dash, telling it to use the existing Flask app ('app')
dash_app = dash.Dash(__name__, server=app, url_base_pathname='/dash/')

# A basic layout for the Dash app
dash_app.layout = html.Div([
    html.H1("Booth Analytics Dashboard (Dash)", style={'text-align': 'center'}),
    dcc.Dropdown(
        id='metric-dropdown',
        options=[
            {'label': 'Temperature (°C)', 'value': 'temp_c'},
            {'label': 'Humidity (%)', 'value': 'humidity_pct'},
            {'label': 'CO₂ (ppm)', 'value': 'co2_ppm'}
        ],
        value='temp_c',
        style={'width': '50%', 'margin': '10px auto'}
    ),
    dcc.Graph(id='live-update-graph')
])

# Define the callback to update the graph based on the dropdown selection
@dash_app.callback(
    Output('live-update-graph', 'figure'),
    [Input('metric-dropdown', 'value')]
)
def update_graph_live(selected_metric):
    # --- Data Retrieval Logic Reused ---
    # NOTE: For simplicity, this example hardcodes the booth data for the graph. 
    # In a real app, you would pass loc_name/booth_name as part of the URL/session.
    loc_name = 'Adelaide'
    booth_name = 'Booth A' 
    df = load_sensor_data(loc_name, booth_name)
    
    if df is None or df.empty:
        return go.Figure().set_layout(title="No Data Available")

    # Clean up the data for the selected metric
    plot_df = df.dropna(subset=['time', selected_metric]).tail(100) # Last 100 readings
    
    # Create the Plotly figure
    fig = px.line(
        plot_df, 
        x='time', 
        y=selected_metric,
        title=f'{selected_metric.replace("_", " ").title()} Trend in {loc_name} - {booth_name}',
        template='plotly_white'
    )
    
    fig.update_layout(
        xaxis_title="Time", 
        yaxis_title=selected_metric.upper(),
        margin=dict(l=20, r=20, t=40, b=20)
    )

    return fig

# ----------------------------------------------------------------------------------
# NEW FLASK ROUTE TO RENDER THE DASHBOARD
# ----------------------------------------------------------------------------------

@app.route('/analytics/<loc_name>/<booth_name>/plotly')
def dash_analytics(loc_name, booth_name):
    # This check is essential to keep the authentication logic
    if 'username' not in session: return redirect(url_for('login'))
    
    # NOTE: The actual Dash app is mounted at /dash/.
    # To fully integrate and pass booth_name dynamically, you would need
    # to modify the Dash layout and callback to read Flask session/URL data,
    # or you can simply link directly to the /dash/ route if the graph is generic,
    # or build the whole page layout in Dash instead of Flask's HTML.
    
    # For a simple solution, we redirect to the Dash app's base path for now.
    # In a fully integrated system, you would use an iframe or a complex Dash setup.
    return redirect('/dash/')

# --- Run the Application ---
if __name__ == '__main__':
    # Flask will now host the Flask routes AND the Dash app at /dash/
    app.run(debug=True)