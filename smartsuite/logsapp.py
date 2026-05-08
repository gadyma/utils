import streamlit as st
import pandas as pd
import json
import glob
import os
from datetime import datetime, time, timedelta

# --- CONFIGURATION ---
LOG_BASE_PATH = os.path.expanduser("~/Downloads/Smartsuite/Logs/siem-data")
ENRICHMENT_PATH = os.path.expanduser("~/Downloads/Smartsuite")

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
    div[data-testid="stExpander"] div[role="button"] p { font-weight: bold; color: #ff4b4b; }
    .stDataFrame { border: 1px solid #30363d; border-radius: 5px; }
    </style>
    """, unsafe_allow_html=True)

st.title("🛡️ SmartSuite SIEM Log Analytics")

@st.cache_data(ttl=300)
def load_enrichment_data():
    """Loads solution and table names from enrichment files."""
    solutions = {}
    tables = {}
    
    sol_file = os.path.join(ENRICHMENT_PATH, "solutions.json")
    tab_file = os.path.join(ENRICHMENT_PATH, "tables.json")
    
    if os.path.exists(sol_file):
        with open(sol_file, 'r', encoding='utf-8') as f:
            try: solutions = json.load(f)
            except: pass
            
    if os.path.exists(tab_file):
        with open(tab_file, 'r', encoding='utf-8') as f:
            try:
                table_list = json.load(f)
                for t in table_list:
                    tables[t['id']] = t.get('name', 'Unknown Table')
            except: pass
                
    return solutions, tables

@st.cache_data(ttl=300)
def load_and_process_logs(root_path):
    all_data = []
    search_pattern = os.path.join(root_path, "**/*.json")
    files = glob.glob(search_pattern, recursive=True)
    
    if not files:
        return pd.DataFrame()

    solutions_map, tables_map = load_enrichment_data()

    for f in files:
        with open(f, 'r', encoding='utf-8') as file:
            try:
                data = json.load(file)
                filename = os.path.basename(f)
                category = filename.split('_logs')[0] if '_logs' in filename else "other"
                
                for entry in data:
                    entry['log_category'] = category
                    s_id = entry.get('solution_id')
                    a_id = entry.get('application_id')
                    if s_id: entry['Solution_Name'] = solutions_map.get(s_id, f"ID: {s_id[:6]}")
                    if a_id: entry['Table_Name'] = tables_map.get(a_id, f"ID: {a_id[:6]}")
                    all_data.append(entry)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    
    df = pd.DataFrame(all_data)
    if df.empty: return df

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
    
    # Create Unified User ID column
    df['Unified_User'] = df.get('actor_email', df.get('userEmail', 'system/internal'))
    df['Unified_User'] = df['Unified_User'].fillna('system/internal').replace('', 'system/internal')
    
    df = df.reset_index(drop=True)
    return df

def parse_metadata(meta_str):
    """
    Advanced parsing for SmartSuite activity metadata.
    Handles extra_data resolution for User Fields and large lists.
    """
    try:
        if pd.isna(meta_str) or not isinstance(meta_str, str):
            return "N/A", "N/A", "N/A", "N/A"
        
        m = json.loads(meta_str)
        if 'commentId' in m:
            return "Comment", "N/A", m.get('preview', 'View raw for text'), "User Action"

        field_info = m.get('field', {})
        field_label = field_info.get('label', 'Unknown Field')
        choices = field_info.get('params', {}).get('choices', [])
        choice_lookup = {str(c['value']): c['label'] for c in choices if 'value' in c and 'label' in c}
        auto_label = m.get('automationData', {}).get('label', 'User Action')

        # Map to find readable names in extra_data
        extra = m.get('extra_data', {})
        res_curr = extra.get('current_value', []) if isinstance(extra.get('current_value'), list) else []
        res_prev = extra.get('previous_value', []) if isinstance(extra.get('previous_value'), list) else []
        
        name_map = {}
        for item in (res_curr + res_prev):
            if isinstance(item, dict):
                iid = str(item.get('id', ''))
                name = item.get('sys_root', item.get('sys_title', item.get('label', '')))
                if not name and 'full_name' in item:
                    fn = item['full_name']
                    name = fn.get('sys_root', f"{fn.get('first_name', '')} {fn.get('last_name', '')}".strip()) if isinstance(fn, dict) else str(fn)
                if iid and name: name_map[iid] = name

        def format_val(val):
            if val is None or val == "": return "Empty"
            if isinstance(val, list):
                if not val: return "Empty"
                if len(val) > 5:
                    resolved_subset = [format_val(item) for item in val[:3]]
                    return ", ".join(resolved_subset) + f" ... (+{len(val)-3} more)"
                return ", ".join([format_val(item) for item in val])
            if isinstance(val, dict):
                if 'sys_root' in val: return str(val['sys_root'])
                if 'sys_title' in val: return str(val['sys_title'])
                if 'date' in val: return str(val['date'])
                if 'value' in val: 
                    v = str(val['value'])
                    return choice_lookup.get(v, v)
                return "Object"
            val_str = str(val)
            return name_map.get(val_str, choice_lookup.get(val_str, val_str))

        prev = format_val(m.get('previousValue', 'None'))
        curr = format_val(m.get('currentValue', 'None'))
        
        return field_label, prev, curr, auto_label
    except:
        return "N/A", "N/A", "N/A", "N/A"

def get_event_summary(row):
    cat = row['log_category']
    table_context = f" [{row['Table_Name']}]" if 'Table_Name' in row else ""
    if cat == 'activity':
        meta_parsed = parse_metadata(row.get('metadata', ''))
        field = meta_parsed[0]
        action = row.get('activity_type', 'update').replace('record_', '').replace('_update', '')
        return f"{action.capitalize()}: {field}{table_context}"
    elif cat == 'api':
        return f"API Request: {row.get('apiEndpoint', 'request')}"
    elif 'usage' in cat:
        return f"Usage: {row.get('limitKind', 'event')}"
    return "System Event"

# --- APP LOGIC ---

if not os.path.exists(LOG_BASE_PATH):
    st.error(f"Directory not found: `{LOG_BASE_PATH}`")
else:
    with st.spinner("Aggregating SIEM data..."):
        df = load_and_process_logs(LOG_BASE_PATH)

    if df.empty:
        st.warning("No log files found in the specified directory.")
    else:
        # --- SIDEBAR FILTERS ---
        max_ts = df['timestamp'].max()
        min_ts = df['timestamp'].min()
        
        st.sidebar.header("⏱️ Time Controls")
        time_preset = st.sidebar.selectbox(
            "Quick Presets", 
            options=["All Time", "Last Hour", "Last Day", "Last Week", "Last Month", "Custom Range"], 
            index=4
        )

        preset_start = min_ts
        if time_preset == "Last Hour": preset_start = max_ts - timedelta(hours=1)
        elif time_preset == "Last Day": preset_start = max_ts - timedelta(days=1)
        elif time_preset == "Last Week": preset_start = max_ts - timedelta(weeks=1)
        elif time_preset == "Last Month": preset_start = max_ts - timedelta(days=30)
        
        if time_preset == "Custom Range":
            selected_date_range = st.sidebar.date_input("Date range", value=(min_ts.date(), max_ts.date()), min_value=min_ts.date(), max_value=max_ts.date())
            start_time, end_time = st.sidebar.slider("Time of day", value=(time(0, 0), time(23, 59)), format="HH:mm")
        else:
            st.sidebar.info(f"Filtered to logs after: {preset_start.strftime('%Y-%m-%d %H:%M')}")
            selected_date_range = (preset_start.date(), max_ts.date())
            start_time, end_time = time(0,0), time(23,59)

        st.sidebar.divider()
        st.sidebar.header("🔍 Key Field Filters")
        
        # New Field-level filters
        unique_solutions = sorted(df['Solution_Name'].dropna().unique())
        selected_solutions = st.sidebar.multiselect("Filter by Solution", options=unique_solutions, default=[])

        unique_users = sorted(df['Unified_User'].dropna().unique())
        selected_users = st.sidebar.multiselect("Filter by User", options=unique_users, default=[])

        # --- APPLY GLOBAL FILTERING ---
        mask = pd.Series(True, index=df.index)
        
        # Time Filter
        if time_preset != "Custom Range" and time_preset != "All Time":
            mask &= (df['timestamp'] >= preset_start)
        elif time_preset == "Custom Range" and isinstance(selected_date_range, tuple) and len(selected_date_range) == 2:
            mask &= (df['timestamp'].dt.date >= selected_date_range[0]) & (df['timestamp'].dt.date <= selected_date_range[1])
            mask &= (df['timestamp'].dt.time >= start_time) & (df['timestamp'].dt.time <= end_time)
        
        # Solution Filter
        if selected_solutions:
            mask &= df['Solution_Name'].isin(selected_solutions)
            
        # User Filter
        if selected_users:
            mask &= df['Unified_User'].isin(selected_users)
            
        filtered_df = df.loc[mask].copy()

        # KPIs
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Events Found", f"{len(filtered_df):,}")
        m2.metric("Active Users", filtered_df['Unified_User'].nunique())
        m3.metric("API Volume", f"{len(filtered_df[filtered_df['log_category']=='api']):,}")
        m4.metric("Log Types", filtered_df['log_category'].nunique())

        st.divider()

        # --- TABBED VIEW ---
        cat_map = {
            "master": "👁️ Master Unified Feed",
            "activity": "📝 Activity Audit",
            "api": "🌐 API Usage",
            "automation_usage": "🤖 Automations",
            "email_usage": "📧 Emails",
            "user_activity": "👤 User Sessions"
        }
        
        available_cats = sorted(filtered_df['log_category'].unique())
        tab_list = ["master"] + list(available_cats)
        tab_titles = [cat_map.get(t, t.capitalize()) for t in tab_list]
        tabs = st.tabs(tab_titles)

        # TAB 0: MASTER FEED
        with tabs[0]:
            st.subheader("Global Chronological Timeline")
            if not filtered_df.empty:
                comb_timeline = filtered_df.set_index('timestamp').resample('1h').size().reset_index(name='Count')
                st.area_chart(comb_timeline, x='timestamp', y='Count', use_container_width=True)
                
                st.subheader("Unified Master Feed")
                comb_display = filtered_df.copy()
                comb_display['Event Summary'] = comb_display.apply(get_event_summary, axis=1)
                
                cols = ['timestamp', 'log_category', 'Unified_User', 'Event Summary']
                st.dataframe(comb_display[cols].sort_values('timestamp', ascending=False), use_container_width=True, hide_index=True)
                
                with st.expander("🛠️ Debug: Full Master Raw Data Explorer (Unfiltered columns)"):
                    st.dataframe(filtered_df.sort_values('timestamp', ascending=False))
            else:
                st.info("No data found for this selection.")

        # CATEGORY SPECIFIC TABS
        for i, cat in enumerate(available_cats):
            with tabs[i+1]:
                cat_df = filtered_df[filtered_df['log_category'] == cat].copy()
                
                c1, c2 = st.columns([2, 1])
                with c1:
                    st.subheader(f"{cat_map.get(cat, cat)} Volume")
                    if not cat_df.empty: st.line_chart(cat_df.set_index('timestamp').resample('1h').size(), use_container_width=True)
                with c2:
                    st.subheader("Top Contributors")
                    if not cat_df.empty: st.bar_chart(cat_df['Unified_User'].value_counts().head(5), horizontal=True)

                st.divider()
                st.subheader(f"Relevant Data: {cat_map.get(cat, cat)}")
                
                if cat == "activity":
                    parsed_meta = cat_df['metadata'].apply(lambda x: pd.Series(parse_metadata(x)))
                    cat_df[['Field', 'From', 'To', 'Triggered By']] = parsed_meta
                    # Enhanced Column List for Activity Audit
                    cols = ['timestamp', 'actor_email', 'Solution_Name', 'Table_Name', 'record_id', 'Triggered By', 'Field', 'From', 'To']
                elif cat == "api":
                    cols = ['timestamp', 'Unified_User', 'apiEndpoint', 'source']
                elif cat == "automation_usage":
                    cols = ['timestamp', 'Unified_User', 'automationId']
                elif cat == "email_usage":
                    cols = ['timestamp', 'Unified_User', 'source', 'destinationCount']
                elif "api_usage" in cat:
                    cols = ['timestamp', 'limitKind', 'source', 'apiEndpoint']
                else:
                    cols = ['timestamp', 'Unified_User', 'activity_type']

                final_cols = [c for c in cols if c in cat_df.columns]
                st.dataframe(cat_df[final_cols].sort_values('timestamp', ascending=False), use_container_width=True, hide_index=True)
                
                with st.expander(f"🛠️ Underlying raw data for {cat.capitalize()} only"):
                    st.dataframe(cat_df)