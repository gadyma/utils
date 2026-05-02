# pip install streamlit pandas plotly
# python3 -m streamlit run app.py

import streamlit as st
import pandas as pd
import plotly.express as px
import os
import glob
import re
import ipaddress

# הגדרות עמוד למראה יוקרתי (חייב להיות הפקודה הראשונה של Streamlit)
st.set_page_config(
    page_title="Kinsta Log Analyzer",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- ניהול Session State לניווט וסינון ---
if 'drill_ip' not in st.session_state:
    st.session_state.drill_ip = ""

if 'navigation_menu_widget' not in st.session_state:
    st.session_state.navigation_menu_widget = "גישה (Access)"

# תפיסת בקשה לשינוי טאב *לפני* שהווידג'ט נטען
if 'next_tab' in st.session_state:
    st.session_state.navigation_menu_widget = st.session_state.next_tab
    del st.session_state.next_tab

# מעקב אחרי שורת ה-Drilldown האחרונה כדי למנוע לולאות
if 'last_selected_row' not in st.session_state:
    st.session_state.last_selected_row = None

# עיצוב CSS מותאם אישית למראה נקי ולתמיכה טובה יותר בכיווניות (RTL)
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; direction: rtl; }
    .stMetric {
        background-color: white;
        padding: 20px;
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
        border: 1px solid #eee;
        text-align: right;
    }
    h1, h2, h3 { color: #1e293b; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: right; }
    .stDataFrame { border-radius: 12px; overflow: hidden; }
    .stAlert { text-align: right; }
    /* סידור Sidebar לימין אם אפשר */
    [data-testid="stSidebar"] { text-align: right; }
    </style>
    """, unsafe_allow_html=True)

st.title("📊 לוח בקרה לניתוח לוגים - Kinsta")
st.markdown("---")

# --- 1. פונקציות טעינה ועיבוד נתונים ---

@st.cache_data
def load_access_logs(log_dir):
    """עיבוד לוגי גישה כולל חילוץ IP וסטטוס - עם טיפול בחריגות"""
    file_pattern = os.path.join(log_dir, '*access.log*')
    files = glob.glob(file_pattern)
    if not files: return pd.DataFrame()
    
    log_pattern = re.compile(
        r'\S+\s+(?P<ip>\S+)\s+\[(?P<time>[^\]]+)\] (?P<method>[A-Z]+) "(?P<url>[^"]+)" \S+ (?P<status>\d{3})'
    )
    
    parsed_data = []
    for file in files:
        try:
            with open(file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    match = log_pattern.search(line)
                    if match:
                        data = match.groupdict()
                        parts = line.strip().split()
                        try:
                            data['size'] = int(parts[-3]) if parts[-3].isdigit() else 0
                        except:
                            data['size'] = 0
                        parsed_data.append(data)
                    else:
                        simple_match = re.search(r'\[(?P<time>[^\]]+)\].*? (?P<status>\d{3})', line)
                        if simple_match:
                            data = simple_match.groupdict()
                            data['ip'] = 'Local' if '::1' in line else 'N/A'
                            data['method'], data['url'], data['size'] = 'HEAD', '/', 0
                            parsed_data.append(data)
        except Exception as e:
            st.warning(f"שגיאה בקריאת הקובץ {file}: {e}")
                    
    df = pd.DataFrame(parsed_data)
    if not df.empty:
        df['time'] = pd.to_datetime(df['time'], format='%d/%b/%Y:%H:%M:%S %z', errors='coerce')
        df = df.dropna(subset=['time'])
        # המרה בטוחה למספר כדי למנוע קריסה אם הסטטוס אינו מספר תקין
        df['status'] = pd.to_numeric(df['status'], errors='coerce').fillna(0).astype(int)
    return df

@st.cache_data
def load_error_logs(log_dir):
    file_pattern = os.path.join(log_dir, '*error.log*')
    files = glob.glob(file_pattern)
    if not files: return pd.DataFrame()
    
    log_pattern = re.compile(r'(?P<time>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[(?P<level>\w+)\]')
    
    parsed_data = []
    for file in files:
        try:
            with open(file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    match = log_pattern.search(line)
                    if match:
                        raw_msg = line.split(']', 2)[-1].strip()
                        cleaned_msg = re.sub(r'\d+#\d+:\s+\*\d+\s+', '', raw_msg)
                        cleaned_msg = cleaned_msg.split(', client:')[0]
                        data = match.groupdict()
                        data['message'] = cleaned_msg
                        parsed_data.append(data)
        except Exception as e:
             pass
                    
    df = pd.DataFrame(parsed_data)
    if not df.empty:
        df['time'] = pd.to_datetime(df['time'], format='%Y/%m/%d %H:%M:%S').dt.tz_localize('UTC')
    return df

@st.cache_data
def load_cache_logs(log_dir):
    file_pattern = os.path.join(log_dir, '*kinsta-cache-perf.log*')
    files = glob.glob(file_pattern)
    if not files: return pd.DataFrame()
    
    log_pattern = re.compile(r'\[(?P<time>[^\]]+)\].*?\s+(?P<cache_status>HIT|MISS|BYPASS|EXPIRED|STALE)')
    parsed_data = []
    for file in files:
        try:
            with open(file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    match = log_pattern.search(line)
                    if match:
                        parsed_data.append(match.groupdict())
        except Exception as e:
            pass
                    
    df = pd.DataFrame(parsed_data)
    if not df.empty:
        df['time'] = pd.to_datetime(df['time'], format='%d/%b/%Y:%H:%M:%S %z', errors='coerce')
        df = df.dropna(subset=['time'])
    return df

# --- 2. בדיקת נתיב וטעינת נתונים ---

LOG_DIR = os.path.expanduser('~/Downloads/KinstaLogs')

# וידוא שהתיקייה קיימת לפני תחילת העיבוד
if not os.path.exists(LOG_DIR):
    st.error(f"⚠️ התיקייה {LOG_DIR} לא נמצאה.")
    st.info("אנא צור את התיקייה, מקם בתוכה את קבצי הלוג של Kinsta, ורענן את הדף.")
    st.stop() # עוצר את המשך ריצת הסקריפט עד שהתיקייה תהיה קיימת

with st.spinner("🔄 מעבד נתונים..."):
    df_access = load_access_logs(LOG_DIR)
    df_error = load_error_logs(LOG_DIR)
    df_cache = load_cache_logs(LOG_DIR)

# בדיקה אם יש נתונים בכלל
if df_access.empty and df_error.empty and df_cache.empty:
    st.warning("לא נמצאו נתוני לוגים בתיקייה. אנא ודא שהקבצים בסיומת `.log` נכונה.")
    st.stop()

# --- 3. סינונים גלובליים ---

st.sidebar.header("🗓️ אפשרויות סינון")
active_dfs = [d for d in [df_access, df_error, df_cache] if not d.empty]

if active_dfs:
    all_times = pd.concat([d['time'] for d in active_dfs])
    min_date, max_date = all_times.min().date(), all_times.max().date()
    date_range = st.sidebar.date_input("בחר טווח תאריכים", value=(min_date, max_date))
    
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_dt = pd.to_datetime(date_range[0]).tz_localize('UTC')
        end_dt = pd.to_datetime(date_range[1]).tz_localize('UTC') + pd.Timedelta(days=1)
        if not df_access.empty: df_access = df_access[(df_access['time'] >= start_dt) & (df_access['time'] < end_dt)]
        if not df_error.empty: df_error = df_error[(df_error['time'] >= start_dt) & (df_error['time'] < end_dt)]
        if not df_cache.empty: df_cache = df_cache[(df_cache['time'] >= start_dt) & (df_cache['time'] < end_dt)]

# --- 4. ניווט ---

tabs_list = ["גישה (Access)", "שגיאות (Errors)", "מטמון (Cache)", "ניתוח IP (Analytics)"]

# יצירת ה-Segmented Control. השימוש ב-key מחבר אותו ישירות ל-session_state.
st.segmented_control(
    "בחר תצוגה", 
    tabs_list,
    key="navigation_menu_widget" 
)

current_tab = st.session_state.navigation_menu_widget
st.markdown("---")

# --- TAB 1: ACCESS ---
if current_tab == "גישה (Access)":
    if df_access.empty:
        st.info("אין נתוני גישה להצגה.")
    else:
        m1, m2, m3, m4 = st.columns(4)
        success_rate = (len(df_access[df_access['status'] < 400]) / len(df_access) * 100) if len(df_access) > 0 else 0
        errors_count = len(df_access[df_access['status'] >= 400])
        unique_ips = df_access['ip'].nunique()
        
        m1.metric("אחוז הצלחה", f"{success_rate:.1f}%")
        m2.metric("שגיאות (4xx/5xx)", f"{errors_count:,}")
        m3.metric("כתובות IP ייחודיות", f"{unique_ips:,}")
        m4.metric("סה\"כ בקשות", f"{len(df_access):,}")

        st.markdown("### 🔍 סינון וחיפוש")
        search_col1, search_col2, search_col3 = st.columns([1, 2, 1])
        with search_col1:
            status_options = ["הכל"] + sorted([str(s) for s in df_access['status'].unique() if s != 0])
            selected_status = st.selectbox("סטטוס", status_options, key="access_status_filter")
        with search_col2:
            st.text_input("חיפוש חופשי (IP, נתיב או מתודה)", 
                          placeholder="לדוגמה: 81.161...",
                          key="drill_ip")
        with search_col3:
            st.write("") ; st.write("")
            
            # פונקציית ה-Callback מנקה את ה-state *לפני* רינדור העמוד, ובכך פותרת את השגיאה
            def clear_search():
                st.session_state.drill_ip = ""
                
            st.button("🗑️ נקה סינון", key="clear_drill_btn", width='stretch', on_click=clear_search)

        filtered_df = df_access.copy()
        if selected_status != "הכל":
            filtered_df = filtered_df[filtered_df['status'] == int(selected_status)]
        if st.session_state.drill_ip:
            sq = st.session_state.drill_ip
            filtered_df = filtered_df[
                filtered_df['ip'].str.contains(sq, case=False, na=False) |
                filtered_df['url'].str.contains(sq, case=False, na=False) |
                filtered_df['method'].str.contains(sq, case=False, na=False)
            ]

        st.markdown("### 📜 פירוט בקשות")
        display_df = filtered_df.sort_values('time', ascending=False).head(1000).copy()
        display_df['זמן'] = display_df['time'].dt.strftime('%d/%b/%Y %H:%M:%S')

        def style_status_column(row):
            val = row['status']
            if 200 <= val < 300: color = 'background-color: #dcfce7; color: #166534;'
            elif 300 <= val < 400: color = 'background-color: #dbeafe; color: #1e40af;'
            elif 400 <= val < 500: color = 'background-color: #fef9c3; color: #854d0e;'
            else: color = 'background-color: #fee2e2; color: #991b1b;'
            return [color if col == 'status' else '' for col in row.index]

        st.dataframe(display_df[['זמן', 'ip', 'status', 'method', 'url']].style.apply(style_status_column, axis=1), hide_index=True, width='stretch')

# --- TAB 2: ERRORS ---
elif current_tab == "שגיאות (Errors)":
    if df_error.empty:
        st.success("לא נמצאו שגיאות! 🎉")
    else:
        st.subheader("ציר זמן של שגיאות")
        err_hourly = df_error.set_index('time').resample('h').size().reset_index(name='Errors')
        
        # פיצול הגרף כדי למנוע חיתוך שורות
        fig_errors = px.area(
            err_hourly, 
            x='time', 
            y='Errors', 
            color_discrete_sequence=['#ef4444'], 
            template="plotly_white"
        )
        st.plotly_chart(fig_errors, width='stretch')
        
        st.subheader("הודעות שגיאה נפוצות")
        st.dataframe(df_error['message'].value_counts().reset_index(name='count'), width='stretch')

# --- TAB 3: CACHE ---
elif current_tab == "מטמון (Cache)":
    if df_cache.empty:
        st.info("לוגי ביצועי מטמון לא נמצאו.")
    else:
        col_l, col_r = st.columns([1, 2])
        c_colors = {'HIT': '#10b981', 'MISS': '#f43f5e', 'BYPASS': '#6366f1', 'EXPIRED': '#f59e0b', 'STALE': '#8b5cf6'}
        with col_l:
            summary = df_cache['cache_status'].value_counts().reset_index()
            
            # פיצול הגרף כדי למנוע חיתוך שורות
            fig_pie = px.pie(
                summary, 
                values='count', 
                names='cache_status', 
                color='cache_status', 
                color_discrete_map=c_colors, 
                hole=0.4
            )
            st.plotly_chart(fig_pie, width='stretch')
            
            total_cache_requests = summary['count'].sum()
            hits = summary[summary['cache_status'] == 'HIT']['count'].sum() if 'HIT' in summary['cache_status'].values else 0
            hit_rate = (hits / total_cache_requests * 100) if total_cache_requests > 0 else 0
            st.metric("אחוז Hit כולל", f"{hit_rate:.1f}%")
            
        with col_r:
            cache_t = df_cache.groupby([pd.Grouper(key='time', freq='h'), 'cache_status']).size().reset_index(name='Count')
            
            # פיצול הגרף כדי למנוע חיתוך שורות
            fig_bar = px.bar(
                cache_t, 
                x='time', 
                y='Count', 
                color='cache_status', 
                color_discrete_map=c_colors, 
                barmode='stack', 
                template="plotly_white"
            )
            st.plotly_chart(fig_bar, width='stretch')

# --- TAB 4: IP ANALYTICS ---
elif current_tab == "ניתוח IP (Analytics)":
    if df_access.empty:
        st.info("אין נתוני גישה.")
    else:
        st.header("🔍 ניתוח כתובות IP מובילות")
        
        # --- CIDR SEARCH ---
        cidr_query = st.text_input("🛰️ חיפוש לפי טווח רשת (CIDR - למשל: 192.168.1.0/24)", key="cidr_input_analytics")
        
        filtered_ips_data = df_access.copy()
        if cidr_query:
            try:
                network = ipaddress.ip_network(cidr_query, strict=False)
                def is_in_network(ip_str):
                    try: return ipaddress.ip_address(ip_str) in network
                    except: return False
                filtered_ips_data = filtered_ips_data[filtered_ips_data['ip'].apply(is_in_network)]
            except ValueError:
                st.error("פורמט CIDR לא תקין.")

        # חישוב אגרגציות
        ip_analytics = filtered_ips_data.groupby('ip').agg({
            'time': ['count', 'min', 'max'],
            'url': 'nunique',
            'size': 'sum'
        })
        ip_analytics.columns = ['בקשות', 'נראה לראשונה', 'נראה לאחרונה', 'דפים שונים', 'נפח תעבורה (Bytes)']
        ip_analytics = ip_analytics.reset_index()
        
        # חישוב שגיאות
        error_ips = filtered_ips_data[filtered_ips_data['status'] >= 400].groupby('ip').size().reset_index(name='שגיאות')
        ip_analytics = ip_analytics.merge(error_ips, on='ip', how='left').fillna(0)
        
        # חישוב זמן פעילות וקצב
        ip_analytics['נפח (MB)'] = (ip_analytics['נפח תעבורה (Bytes)'] / (1024*1024)).round(2)
        ip_analytics['משך'] = ip_analytics['נראה לאחרונה'] - ip_analytics['נראה לראשונה']
        ip_analytics['זמן פעילות (דקות)'] = ip_analytics['משך'].dt.total_seconds() / 60
        
        def calc_rate(row):
            return row['בקשות'] / max(row['זמן פעילות (דקות)'], 0.5)
        ip_analytics['קצב (דקה)'] = ip_analytics.apply(calc_rate, axis=1).round(2)

        # טבלת סיכום
        st.subheader("📊 טבלת סיכום IPs")
        st.info("💡 הקלק על שורה בטבלה כדי לעבור אוטומטית לסינון ה-Access Logs עבור כתובת זו.")
        
        # מיון ברירת מחדל
        display_ips = ip_analytics.sort_values('בקשות', ascending=False).head(200).copy()

        selection = st.dataframe(
            display_ips[['ip', 'בקשות', 'שגיאות', 'נפח (MB)', 'דפים שונים', 'קצב (דקה)', 'זמן פעילות (דקות)', 'נראה לראשונה', 'נראה לאחרונה']],
            column_config={
                "ip": "כתובת IP",
                "בקשות": st.column_config.NumberColumn("בקשות 📥", format="%d"),
                "שגיאות": st.column_config.NumberColumn("שגיאות ❌", format="%d"),
                "קצב (דקה)": "קצב/דקה",
                "זמן פעילות (דקות)": st.column_config.NumberColumn("זמן פעילות (דקות) ⏱️", format="%.1f"),
                "נראה לראשונה": st.column_config.DatetimeColumn("התחלה", format="DD/MM/YY HH:mm"),
                "נראה לאחרונה": st.column_config.DatetimeColumn("סיום", format="DD/MM/YY HH:mm"),
            },
            hide_index=True,
            width='stretch',
            on_select="rerun",
            selection_mode="single-row",
            key="ip_analytics_table"
        )

        # לוגיקת המעבר האוטומטי (Drill-Down)
        # שימוש במעקב על הלחיצה האחרונה כדי למנוע את באג ה"קפיצה חזרה" המעיק
        current_selection = selection.selection.rows[0] if selection and selection.selection.rows else None
        
        if current_selection != st.session_state.last_selected_row:
            st.session_state.last_selected_row = current_selection
            # מוודאים שאכן בוצעה בחירה חדשה (ולא ביטול סימון)
            if current_selection is not None:
                selected_ip = display_ips.iloc[current_selection]['ip']
                st.session_state.drill_ip = selected_ip
                # שינוי נכון של הטאב באמצעות משתנה עזר במקום לעדכן את הווידג'ט ישירות
                st.session_state.next_tab = "גישה (Access)"
                st.rerun()

        st.markdown("---")
        
        # --- GRAPH SECTION ---
        st.subheader("📈 מגמות פעילות ובורר TOP IP")
        metric_options = {"בקשות": "בקשות", "שגיאות": "שגיאות", "נפח (MB)": "נפח (MB)", "דפים שונים": "דפים שונים"}
        
        g_col1, g_col2 = st.columns([1, 3])
        with g_col1:
            selected_metric_label = st.radio("הצג בגרף TOP 5 לפי:", list(metric_options.keys()), index=0, key="ip_metric_radio_analytics")
            selected_metric_col = metric_options[selected_metric_label]

        with g_col2:
            top_5_for_graph = ip_analytics.sort_values(selected_metric_col, ascending=False).head(5)['ip'].tolist()
            df_top_ips = df_access[df_access['ip'].isin(top_5_for_graph)]
            if not df_top_ips.empty:
                ip_time_series = df_top_ips.groupby([pd.Grouper(key='time', freq='h'), 'ip']).size().reset_index(name='Req')
                
                # פיצול הגרף כדי למנוע חיתוך שורות
                fig_line = px.line(
                    ip_time_series, 
                    x='time', 
                    y='Req', 
                    color='ip', 
                    markers=True, 
                    template="plotly_white"
                )
                st.plotly_chart(fig_line, width='stretch')