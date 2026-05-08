import streamlit as st
import pandas as pd
import json
import glob
import os
from datetime import datetime, time, timedelta

# --- CONFIGURATION ---
LOG_BASE_PATH = os.path.expanduser("~/Downloads/Smartsuite/Logs/siem-data")

st.set_page_config(
    page_title="SmartSuite SIEM Analytics", 
    page_icon="🛡️", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for a more "Security Ops" feel
st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .stMetric { background-color: #161b22; padding: 15px; border-radius: 10px; border: 1px solid #30363d; }
    </style>
    """, unsafe_allow_html=True)

st.title("🛡️ SmartSuite SIEM Log Analytics")

@st.cache_data(ttl=300)
def load_and_process_logs(root_path):
    all_data = []
    
    # Pattern to match all json files in the Hive structure
    search_pattern = os.path.join(root_path, "**/*.json")
    files = glob.glob(search_pattern, recursive=True)
    
    if not files:
        return pd.DataFrame()

    for f in files:
        with open(f, 'r') as file:
            try:
                data = json.load(file)
                filename = os.path.basename(f)
                category = filename.split('_logs')[0] if '_logs' in filename else "other"
                
                for entry in data:
                    entry['log_category'] = category
                    all_data.append(entry)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    
    df = pd.DataFrame(all_data)
    
    if df.empty:
        return df

    # --- ROBUST DATE PARSING ---
    time_col = 'timestamp'
    if 'activity_at' in df.columns and 'usedAt' in df.columns:
        df[time_col] = df['activity_at'].fillna(df['usedAt'])
    elif 'activity_at' in df.columns:
        df[time_col] = df['activity_at']
    else:
        df[time_col] = df.get('usedAt', None)

    if time_col in df.columns:
        df[time_col] = pd.to_datetime(df[time_col], format='ISO8601', utc=True)
        df = df.dropna(subset=[time_col])
    
    # Reset index to ensure a clean RangeIndex (prevents IndexingError)
    df = df.reset_index(drop=True)
        
    return df

def parse_metadata(meta_str):
    try:
        if pd.isna(meta_str) or not isinstance(meta_str, str):
            return "N/A", "N/A", "N/A"
        m = json.loads(meta_str)
        field = m.get('field', {}).get('label', 'Unknown Field')
        
        def extract_val(v):
            if isinstance(v, dict):
                return v.get('value', v.get('date', str(v)))
            return str(v)

        prev = extract_val(m.get('previousValue', 'None'))
        curr = extract_val(m.get('currentValue', 'None'))
        return field, prev, curr
    except:
        return "Parse Error", "N/A", "N/A"

# --- APP LOGIC ---

if not os.path.exists(LOG_BASE_PATH):
    st.error(f"Directory not found: `{LOG_BASE_PATH}`")
    st.info("Check your `LOG_BASE_PATH` in the code or ensure the folders exist.")
else:
    with st.spinner("Aggregating SIEM data..."):
        df = load_and_process_logs(LOG_BASE_PATH)

    if df.empty:
        st.warning("No log files found in the specified directory.")
    else:
        # --- SIDEBAR FILTERS ---
        st.sidebar.header("Global Filters")
        
        # Reference point for relative time
        max_ts = df['timestamp'].max()
        min_ts = df['timestamp'].min()
        
        # 1. Preset Relative Time Filter
        st.sidebar.subheader("Quick Presets")
        time_preset = st.sidebar.selectbox(
            "Select relative to latest log",
            options=["All Time", "Last Hour", "Last Day", "Last Week", "Last Month", "Custom Range"],
            index=4 # Default to Last Month
        )

        # Logic for presets
        preset_start = min_ts
        if time_preset == "Last Hour":
            preset_start = max_ts - timedelta(hours=1)
        elif time_preset == "Last Day":
            preset_start = max_ts - timedelta(days=1)
        elif time_preset == "Last Week":
            preset_start = max_ts - timedelta(weeks=1)
        elif time_preset == "Last Month":
            preset_start = max_ts - timedelta(days=30)
        
        # 2. Custom Date and Time Selection
        st.sidebar.divider()
        st.sidebar.subheader("Detailed Range")
        
        # Determine dynamic defaults based on preset
        sel_start = preset_start.date() if time_preset != "All Time" else min_ts.date()
        sel_end = max_ts.date()

        # Fix: Ensure selected_date_range is always used carefully for filtering
        selected_date_range = st.sidebar.date_input(
            "Date range",
            value=(sel_start, sel_end),
            min_value=min_ts.date(),
            max_value=max_ts.date()
        )

        start_time, end_time = st.sidebar.slider(
            "Time of day",
            value=(time(0, 0), time(23, 59)),
            format="HH:mm"
        )

        # --- APPLY FILTERING ---
        # Initialize mask with True values matching df index
        mask = pd.Series(True, index=df.index)
        
        if time_preset != "Custom Range" and time_preset != "All Time":
            mask &= (df['timestamp'] >= preset_start)
        elif time_preset == "Custom Range" and isinstance(selected_date_range, tuple) and len(selected_date_range) == 2:
            d_start, d_end = selected_date_range
            mask &= (df['timestamp'].dt.date >= d_start) & (df['timestamp'].dt.date <= d_end)
            mask &= (df['timestamp'].dt.time >= start_time) & (df['timestamp'].dt.time <= end_time)
            
        filtered_df = df.loc[mask].copy()

        # KPIs Row
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Events", f"{len(filtered_df):,}")
        
        user_col = 'actor_email' if 'actor_email' in filtered_df.columns else 'userEmail'
        unique_users = filtered_df[user_col].nunique() if user_col in filtered_df.columns else 0
        
        m2.metric("Active Users", unique_users)
        m3.metric("API Calls", f"{len(filtered_df[filtered_df['log_category']=='api']):,}")
        m4.metric("Active Period", f"{(max_ts - min_ts).days}d")

        st.divider()

        # --- TABBED VIEW BY LOG TYPE ---
        # Get available categories in current filter
        available_cats = sorted(filtered_df['log_category'].unique())
        
        # Create human-readable tab names
        cat_map = {
            "activity": "📝 Activity Audit",
            "api": "🌐 API Usage",
            "automation_usage": "🤖 Automations",
            "email_usage": "📧 Emails",
            "user_activity": "👤 User Sessions",
            "other": "📂 Other"
        }
        
        tab_titles = [cat_map.get(cat, cat.capitalize()) for cat in available_cats]
        
        if available_cats:
            tabs = st.tabs(tab_titles)
            
            for i, cat in enumerate(available_cats):
                with tabs[i]:
                    cat_df = filtered_df[filtered_df['log_category'] == cat].copy()
                    
                    # 1. Summary Visuals for this tab
                    sub_col1, sub_col2 = st.columns([2, 1])
                    
                    with sub_col1:
                        st.subheader(f"{cat_map.get(cat, cat)} Timeline")
                        # Resample using lowercase 'h' for modern Pandas
                        timeline = cat_df.set_index('timestamp').resample('1h').size().reset_index(name='Count')
                        st.area_chart(timeline, x='timestamp', y='Count', use_container_width=True)
                    
                    with sub_col2:
                        st.subheader("Top Contributors")
                        u_col = 'actor_email' if 'actor_email' in cat_df.columns else 'userEmail'
                        if u_col in cat_df.columns and not cat_df[u_col].dropna().empty:
                            st.bar_chart(cat_df[u_col].value_counts().head(5), horizontal=True)

                    # 2. Category-Specific Deep Dive Tables
                    st.divider()
                    if cat == "activity":
                        st.subheader("Field Changes Detail")
                        cat_df[['Field', 'From', 'To']] = cat_df['metadata'].apply(lambda x: pd.Series(parse_metadata(x)))
                        display_cols = ['timestamp', 'actor_email', 'activity_type', 'Field', 'From', 'To']
                        st.dataframe(cat_df[display_cols].sort_values('timestamp', ascending=False), use_container_width=True, hide_index=True)
                    
                    elif cat == "api":
                        st.subheader("Endpoint Traffic")
                        if 'apiEndpoint' in cat_df.columns:
                            st.dataframe(cat_df[['timestamp', 'userEmail', 'apiEndpoint', 'source']].sort_values('timestamp', ascending=False), use_container_width=True, hide_index=True)
                    
                    elif "usage" in cat:
                        st.subheader("Usage Events")
                        cols = ['timestamp', 'limitKind', 'source']
                        available_cols = [c for c in cols if c in cat_df.columns]
                        st.dataframe(cat_df[available_cols].sort_values('timestamp', ascending=False), use_container_width=True, hide_index=True)
                    
                    else:
                        st.subheader("Log Explorer")
                        st.dataframe(cat_df.sort_values('timestamp', ascending=False), use_container_width=True)

        else:
            st.info("No data categories found for the selected range. Check your filters in the sidebar.")

        # Full Data Explorer
        with st.expander("Full Raw Dataframe Explorer"):
            st.write(filtered_df)