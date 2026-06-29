# kinsight.py — Kinsta Log Analysis Dashboard
# Version 1.0.0
#
# Install:  pip install streamlit pandas plotly
# Run:      python3 -m streamlit run kinsight.py
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os
import sys
import glob
import re
import ipaddress
import time
import datetime
from concurrent.futures import ThreadPoolExecutor

def _make_executor(n_files):
    return ThreadPoolExecutor(max_workers=min(8, n_files))

TZ = 'Asia/Jerusalem'
_FILE_DATE_RE = re.compile(r'(\d{4}-\d{2}-\d{2})')

# Compiled once at import time — avoids re-compiling per file/line
# Kinsta nginx format:
#   <host> <ip> [<time>] <METHOD> "<url>" HTTP/x.x <status> "<referer>" "<ua>"
#   <upstream_ip> "<path>" <gzip_ratio> <request_time> <bytes_sent> ...
_ACCESS_RE = re.compile(
    r'\S+\s+(?P<ip>\S+)\s+\[(?P<time>[^\]]+)\]\s+'
    r'(?P<method>[A-Z]+)\s+"(?P<url>[^"]+)"\s+\S+\s+(?P<status>\d{3})\s+'
    r'"[^"]*"\s+"(?P<user_agent>[^"]*)"\s+'
    r'\S+\s+"[^"]*"\s+\S+\s+(?P<response_time>[\d.]+|-)\s+(?P<size>\d+)'
)
_ACCESS_SIMPLE_RE = re.compile(r'(?P<ip>\S+)\s+\S+\s+\[(?P<time>[^\]]+)\].*? (?P<status>\d{3})')
_ERROR_RE  = re.compile(r'(?P<time>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) \[(?P<level>\w+)\]')
_CACHE_RE  = re.compile(
    r'\[(?P<time>[^\]]+)\]\s+(?P<cache_status>HIT|MISS|BYPASS|EXPIRED|STALE)'
    r'\s+\S+\s+(?P<ip>\S+)\s+(?P<method>\S+)\s+"(?P<url>[^"]+)"'
    r'.*?(?P<response_time>\d+(?:\.\d+)?)\s*$'
)
# Normalises form-field probe URLs:
#   /path?form_fields[field_9ac1c94]True=Michael%20Carter
#   /path?form_fields[field_9ac1c94][]=Michael%20Carter
# → /path?form_fields[field_9ac1c94][*]=*
_FORM_FIELD_RE = re.compile(r'(form_fields\[[^\]]+\])[^\s&=]*(=[^&\s]*)?')
# Collapses numeric IDs (3+ digits) and UUIDs in URL path segments
_URL_ID_RE = re.compile(
    r'(?<=/)\d{3,}(?=[/?#]|$)'
    r'|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
)


def _normalize_url(url):
    return _FORM_FIELD_RE.sub(r'\1[*]=*', url)


def _normalize_path(url):
    """Strip query string, collapse numeric IDs/UUIDs — for endpoint grouping."""
    path = url.split('?')[0].split('#')[0]
    path = _FORM_FIELD_RE.sub(r'\1[*]=*', path)
    return _URL_ID_RE.sub('*', path)


def _parse_access_file(file):
    rows = []
    fallback = 0
    with open(file, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            # Some Kinsta log lines use doubled-quote fields (""value"") —
            # normalize to single-quotes before matching.
            parse_line = line.replace('""', '"') if '""' in line else line
            m = _ACCESS_RE.search(parse_line)
            if m:
                data = m.groupdict()
                try:
                    data['size'] = int(data['size'])
                except (TypeError, ValueError, KeyError):
                    data['size'] = 0
                rt = data.get('response_time', '-')
                data['response_time'] = float(rt) if rt and rt != '-' else 0.0
                data['raw_line'] = line.rstrip('\n')
                rows.append(data)
            else:
                sm = _ACCESS_SIMPLE_RE.search(parse_line)
                if sm:
                    data = sm.groupdict()
                    data.update(method='HEAD', url='/', size=0,
                                user_agent='', response_time=0.0,
                                raw_line=line.rstrip('\n'))
                    rows.append(data)
                    fallback += 1
    return rows, fallback


def _parse_error_file(file):
    rows = []
    with open(file, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            m = _ERROR_RE.search(line)
            if m:
                _tail = line.split(']', 2)[-1].strip()
                cleaned = re.sub(r'\d+#\d+:\s+\*\d+\s+', '', _tail).split(', client:')[0]
                data = m.groupdict()
                data['message'] = cleaned
                data['raw_line'] = line.rstrip('\n')
                rows.append(data)
    return rows


def _parse_cache_file(file):
    rows = []
    with open(file, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            m = _CACHE_RE.search(line)
            if m:
                data = m.groupdict()
                try:
                    data['response_time'] = float(data['response_time'])
                except (TypeError, ValueError):
                    data['response_time'] = 0.0
                data['raw_line'] = line.rstrip('\n')
                rows.append(data)
    return rows


st.set_page_config(
    page_title="Kinsta Log Analyzer",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Session State ---
for _k, _v in [
    ('drill_ip', ""),
    ('navigation_menu', "Access"),
    ('security_ip_drill', ""),
    ('preset_radio', "All data"),
    ('ip_exclude', "129.159.153.156"),  # own server — Wordfence loopback
]:
    if _k not in st.session_state:
        st.session_state[_k] = _v

if 'next_tab' in st.session_state:
    st.session_state.navigation_menu = st.session_state.next_tab
    del st.session_state.next_tab

# Apply chart-selection range BEFORE any widget renders so preset_radio and
# the cs_ date/time inputs pick up the new values without widget conflicts.
if '_pending_chart' in st.session_state:
    for _k, _v in st.session_state.pop('_pending_chart').items():
        st.session_state[_k] = _v

st.markdown("""
    <style>
    .stMetric {
        padding: 20px;
        border-radius: 12px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
    }
    .stDataFrame { border-radius: 12px; overflow: hidden; }
    h1, h2, h3 { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
    @media (prefers-color-scheme: light) {
        .main { background-color: #f8f9fa; }
        .stMetric { background-color: white; border: 1px solid #eee; }
        h1, h2, h3 { color: #1e293b; }
    }
    @media (prefers-color-scheme: dark) {
        .stMetric { background-color: #1e2130; border: 1px solid #2d3250; }
    }
    </style>
    """, unsafe_allow_html=True)

st.title("📊 Kinsta Log Dashboard")
st.markdown("---")

# Resolved in priority order: CLI arg → env var → script directory.
# CLI usage:  streamlit run kinsight.py -- /path/to/logs
# Env usage:  KINSIGHT_LOG_DIR=/path/to/logs streamlit run kinsight.py
_cli_arg = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else None
LOG_DIR  = _cli_arg or os.environ.get('KINSIGHT_LOG_DIR') or os.path.dirname(os.path.abspath(__file__))


def _collect_files(log_dir, rotated_glob, active_subdir, after_date=None, before_date=None):
    rotated = glob.glob(os.path.join(log_dir, '**', 'rotated', rotated_glob), recursive=True)
    active  = glob.glob(os.path.join(log_dir, '**', 'active', active_subdir, '*.log'), recursive=True)
    if after_date or before_date:
        def _in_range(path):
            m = _FILE_DATE_RE.search(os.path.basename(path))
            if not m:
                return True
            d = m.group(1)
            if after_date and d < after_date:
                return False
            if before_date and d > before_date:
                return False
            return True
        rotated = [f for f in rotated if _in_range(f)]
        active  = [f for f in active  if _in_range(f)]
    return rotated + active


def _parse_plotly_ts(val):
    if isinstance(val, (int, float)):
        return pd.Timestamp(val, unit='ms', tz='UTC')
    ts = pd.Timestamp(val)
    # Plotly returns naive strings representing the chart's display timezone (TZ),
    # not UTC — localize as TZ so tz_convert(TZ) in the caller gives correct times.
    return ts if ts.tzinfo is not None else ts.tz_localize(TZ)


def _extract_chart_range(event):
    try:
        # box gives exact drag coordinates regardless of y-extent or data density
        box = event.selection.box
        if box:
            x = box[0]['x']
            t0, t1 = _parse_plotly_ts(x[0]), _parse_plotly_ts(x[1])
            return min(t0, t1), max(t0, t1)
        # fallback: infer range from which data points landed in the box
        points = event.selection.points
        if not points:
            return None, None
        xs = [p['x'] for p in points if 'x' in p]
        if not xs:
            return None, None
        t0 = _parse_plotly_ts(min(xs))
        t1 = _parse_plotly_ts(max(xs))
        if t0 == t1:
            return None, None
        return t0, t1
    except Exception as e:
        st.session_state['_chart_sel_error'] = str(e)
        return None, None


_TIME_STEP = 300  # seconds — must match step= on the time_input widgets


def _snap_time(t):
    """Round a time down to the nearest _TIME_STEP boundary."""
    total = t.hour * 3600 + t.minute * 60 + t.second
    s = (total // _TIME_STEP) * _TIME_STEP % 86400
    return datetime.time(s // 3600, (s % 3600) // 60)


def _handle_chart_event(event):
    t0, t1 = _extract_chart_range(event)
    if t0 is None:
        return
    t0_il = t0.tz_convert(TZ)
    t1_il = t1.tz_convert(TZ)
    new_sd, new_st = t0_il.date(), _snap_time(t0_il.time())
    new_ed, new_et = t1_il.date(), _snap_time(t1_il.time())
    # Break infinite loop: skip if already in Custom range with identical bounds.
    if (st.session_state.get('preset_radio') == 'Custom range' and
            st.session_state.get('cs_date') == new_sd and
            st.session_state.get('cs_time') == new_st and
            st.session_state.get('ce_date') == new_ed and
            st.session_state.get('ce_time') == new_et):
        return
    st.session_state['_pending_chart'] = {
        'preset_radio': 'Custom range',
        'cs_date': new_sd, 'cs_time': new_st,
        'ce_date': new_ed, 'ce_time': new_et,
    }
    st.rerun()


def _selectable_chart(fig, key, xrange=None):
    if xrange:
        fig.update_xaxes(range=xrange)
    fig.update_yaxes(fixedrange=True)
    fig.update_layout(dragmode='select')
    # Ghost scatter so Plotly has selectable points for box-select events.
    if fig.data:
        fig.add_trace(go.Scatter(
            x=fig.data[0].x, y=fig.data[0].y,
            mode='markers',
            marker=dict(size=8, opacity=0, color='rgba(0,0,0,0)'),
            showlegend=False, hoverinfo='none', name='_sel',
        ))
    ev = st.plotly_chart(fig, on_select="rerun", selection_mode="box", key=key,
                         width='stretch')
    return ev


def _raw_log_expander(df, regex, key):
    """Collapsible raw-line viewer. `regex` is used to parse fields for the detail view."""
    with st.expander("🗒️ Raw log lines"):
        if df.empty or 'raw_line' not in df.columns:
            st.info("No raw lines available.")
            return
        _r_df = df[['raw_line']].reset_index(drop=True)
        _r_sel = st.dataframe(
            _r_df,
            column_config={'raw_line': st.column_config.TextColumn('Raw line', width='large')},
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            height=300,
            key=key,
        )
        if _r_sel and _r_sel.selection.rows:
            _line = _r_df.iloc[_r_sel.selection.rows[0]]['raw_line']
            st.code(_line, language="text")
            _m = regex.search(_line) if regex else None
            if _m:
                st.json(_m.groupdict())
            else:
                st.caption("(line did not match the parser regex)")


@st.cache_data
def load_access_logs(log_dir, after_date=None, before_date=None, file_count=0):
    files = _collect_files(log_dir, '*access.log*', 'access', after_date, before_date)
    if not files:
        return pd.DataFrame(), 0, 0
    rows, total_fallback = [], 0
    with _make_executor(len(files)) as ex:
        for batch, fb in ex.map(_parse_access_file, files):
            rows.extend(batch)
            total_fallback += fb
    df = pd.DataFrame(rows)
    if not df.empty:
        df['time'] = pd.to_datetime(df['time'], format='%d/%b/%Y:%H:%M:%S %z', errors='coerce')
        df = df.dropna(subset=['time'])
        df['status'] = df['status'].astype(int)
        df['response_time'] = pd.to_numeric(df['response_time'], errors='coerce').fillna(0.0)
    return df, len(files), total_fallback


@st.cache_data
def load_error_logs(log_dir, after_date=None, before_date=None, file_count=0):
    files = _collect_files(log_dir, '*error.log*', 'error', after_date, before_date)
    if not files:
        return pd.DataFrame(), 0
    with _make_executor(len(files)) as ex:
        rows = []
        for batch in ex.map(_parse_error_file, files):
            rows.extend(batch)
    df = pd.DataFrame(rows)
    if not df.empty:
        df['time'] = pd.to_datetime(df['time'], format='%Y/%m/%d %H:%M:%S').dt.tz_localize(TZ)
    return df, len(files)


@st.cache_data
def load_cache_logs(log_dir, after_date=None, before_date=None, file_count=0):
    files = _collect_files(log_dir, '*kinsta-cache-perf.log*', 'kinsta-cache-perf', after_date, before_date)
    if not files:
        return pd.DataFrame(), 0
    with _make_executor(len(files)) as ex:
        rows = []
        for batch in ex.map(_parse_cache_file, files):
            rows.extend(batch)
    df = pd.DataFrame(rows)
    if not df.empty:
        df['time'] = pd.to_datetime(df['time'], format='%d/%b/%Y:%H:%M:%S %z', errors='coerce')
        df = df.dropna(subset=['time'])
        df['response_time'] = pd.to_numeric(df['response_time'], errors='coerce').fillna(0.0)
    return df, len(files)


# --- Pre-compute file date boundaries from current preset ---
# Reading from session state before widgets are rendered lets us skip files
# outside the time window before a single byte is parsed.
_now_il = pd.Timestamp.now(tz=TZ)
_pk = st.session_state.get('preset_radio', 'All data')
if _pk == 'Last hour':
    _after, _before = (_now_il - pd.Timedelta(hours=2)).strftime('%Y-%m-%d'), _now_il.strftime('%Y-%m-%d')
elif _pk == 'Last 4 hours':
    _after, _before = (_now_il - pd.Timedelta(hours=5)).strftime('%Y-%m-%d'), _now_il.strftime('%Y-%m-%d')
elif _pk == 'Last 8 hours':
    _after, _before = (_now_il - pd.Timedelta(hours=9)).strftime('%Y-%m-%d'), _now_il.strftime('%Y-%m-%d')
elif _pk == 'Last 24 hours':
    _after, _before = (_now_il - pd.Timedelta(hours=25)).strftime('%Y-%m-%d'), _now_il.strftime('%Y-%m-%d')
elif _pk == 'Last 3 days':
    _after, _before = (_now_il - pd.Timedelta(days=4)).strftime('%Y-%m-%d'), _now_il.strftime('%Y-%m-%d')
elif _pk == 'Last 7 days':
    _after, _before = (_now_il - pd.Timedelta(days=8)).strftime('%Y-%m-%d'), _now_il.strftime('%Y-%m-%d')
elif _pk == 'Last month':
    _after, _before = (_now_il - pd.Timedelta(days=32)).strftime('%Y-%m-%d'), _now_il.strftime('%Y-%m-%d')
elif _pk == 'Custom range':
    _cs = st.session_state.get('cs_date')
    _ce = st.session_state.get('ce_date')
    if _cs and _ce:
        _after  = str(_cs - datetime.timedelta(days=1))
        _before = str(_ce + datetime.timedelta(days=1))
    else:
        _after = _before = None
else:  # All data
    _after = _before = None

# Count matching files (fast — just globs, no I/O) to key the cache;
# if a log rotation adds new files the count changes and the cache is invalidated.
_a_cnt = len(_collect_files(LOG_DIR, '*access.log*',          'access',            _after, _before))
_e_cnt = len(_collect_files(LOG_DIR, '*error.log*',           'error',             _after, _before))
_c_cnt = len(_collect_files(LOG_DIR, '*kinsta-cache-perf.log*', 'kinsta-cache-perf', _after, _before))

# --- Load data ---

with st.spinner("🔄 Loading data..."):
    df_access, access_file_count, access_fallback = load_access_logs(LOG_DIR, _after, _before, _a_cnt)
    df_error,  error_file_count                  = load_error_logs(LOG_DIR,  _after, _before, _e_cnt)
    df_cache,  cache_file_count                  = load_cache_logs(LOG_DIR,  _after, _before, _c_cnt)

# --- Sidebar filters ---

st.sidebar.header("🗓️ Filters")
st.sidebar.caption(f"📁 Log dir: `{LOG_DIR}`")
st.sidebar.caption(f"Files: {access_file_count} access · {error_file_count} errors · {cache_file_count} cache")
st.sidebar.caption(f"🕐 Timezone: {TZ}")
if '_chart_sel_error' in st.session_state:
    st.sidebar.warning(f"Chart selection parse error: {st.session_state.pop('_chart_sel_error')}")

_PRESETS = {
    "Last hour":     pd.Timedelta(hours=1),
    "Last 4 hours":  pd.Timedelta(hours=4),
    "Last 8 hours":  pd.Timedelta(hours=8),
    "Last 24 hours": pd.Timedelta(hours=24),
    "Last 3 days":   pd.Timedelta(days=3),
    "Last 7 days":   pd.Timedelta(days=7),
    "Last month":    pd.Timedelta(days=30),
    "Custom range":  "custom",
    "All data":      None,
}
preset_choice = st.sidebar.radio("Quick range", list(_PRESETS.keys()),
                                 key="preset_radio")

active_dfs = [d for d in [df_access, df_error, df_cache] if not d.empty]

start_dt = end_dt = None

if active_dfs:
    now_utc = pd.Timestamp.now(tz='UTC').floor('min')
    delta   = _PRESETS[preset_choice]

    if delta == "custom":
        all_times = pd.concat([d['time'] for d in active_dfs])
        _min_dt = all_times.min().tz_convert(TZ)
        _max_dt = all_times.max().tz_convert(TZ)
        # Set defaults only when the key is absent — don't override values written
        # by _pending_chart (chart selection) or the preset-sync block.
        for _k, _v in [
            ('cs_date', _min_dt.date()),
            ('cs_time', _snap_time(_min_dt.time())),
            ('ce_date', _max_dt.date()),
            ('ce_time', _snap_time(_max_dt.time())),
        ]:
            if _k not in st.session_state:
                st.session_state[_k] = _v
        st.sidebar.markdown("**From**")
        c_d1, c_t1 = st.sidebar.columns(2)
        custom_start_date = c_d1.date_input("Start date", label_visibility="collapsed",
                                            key="cs_date")
        custom_start_time = c_t1.time_input("Start time", label_visibility="collapsed",
                                            key="cs_time", step=_TIME_STEP)
        st.sidebar.markdown("**To**")
        c_d2, c_t2 = st.sidebar.columns(2)
        custom_end_date = c_d2.date_input("End date", label_visibility="collapsed",
                                          key="ce_date")
        custom_end_time = c_t2.time_input("End time", label_visibility="collapsed",
                                          key="ce_time", step=_TIME_STEP)
        start_dt = pd.Timestamp(datetime.datetime.combine(custom_start_date, custom_start_time)).tz_localize(TZ)
        end_dt   = pd.Timestamp(datetime.datetime.combine(custom_end_date,   custom_end_time)).tz_localize(TZ)
    elif delta is not None:
        start_dt = now_utc - delta
        end_dt   = now_utc + pd.Timedelta(minutes=1)
    else:
        all_times = pd.concat([d['time'] for d in active_dfs])
        min_date, max_date = all_times.min().date(), all_times.max().date()
        # Reset the picker whenever the earliest available date changes (e.g. new files rsynced).
        # Without an explicit key the widget's implicit session state sticks to whatever it was
        # first initialized with, ignoring the value= parameter on subsequent renders.
        if st.session_state.get('_ad_min') != min_date:
            st.session_state['_ad_min'] = min_date
            st.session_state['ad_range'] = (min_date, max_date)
        elif 'ad_range' not in st.session_state:
            st.session_state['ad_range'] = (min_date, max_date)
        date_range = st.sidebar.date_input("Select date range", key="ad_range")
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start_dt = pd.to_datetime(date_range[0]).tz_localize(TZ)
            end_dt   = (pd.to_datetime(date_range[1]).tz_localize(TZ) + pd.Timedelta(days=1))

# Mirror any non-custom preset into the custom range fields so switching to
# "Custom range" always starts from the last active window.
if preset_choice != "Custom range" and start_dt is not None:
    _s_il = start_dt.tz_convert(TZ)
    _e_il = end_dt.tz_convert(TZ)
    st.session_state['cs_date'] = _s_il.date()
    st.session_state['cs_time'] = _snap_time(_s_il.time())
    st.session_state['ce_date'] = _e_il.date()
    st.session_state['ce_time'] = _snap_time(_e_il.time())

if start_dt is not None and end_dt is not None:
    if not df_access.empty:
        df_access = df_access[(df_access['time'] >= start_dt) & (df_access['time'] < end_dt)]
    if not df_error.empty:
        df_error  = df_error[(df_error['time']   >= start_dt) & (df_error['time']   < end_dt)]
    if not df_cache.empty:
        df_cache  = df_cache[(df_cache['time']   >= start_dt) & (df_cache['time']   < end_dt)]

# Convert time to display timezone — charts and tables both pick this up automatically
if not df_access.empty:
    df_access = df_access.assign(time=df_access['time'].dt.tz_convert(TZ))
if not df_error.empty:
    df_error  = df_error.assign(time=df_error['time'].dt.tz_convert(TZ))
if not df_cache.empty:
    df_cache  = df_cache.assign(time=df_cache['time'].dt.tz_convert(TZ))

# Chart x-axis range — locked to 5-min boundary so reruns don't shift the axis and kill in-progress drags
if start_dt is not None and end_dt is not None:
    _chart_xrange = [start_dt.tz_convert(TZ).floor('5min'), end_dt.tz_convert(TZ).ceil('5min')]
    st.sidebar.caption(
        f"📅 {_chart_xrange[0].strftime('%d/%b %H:%M')} → {_chart_xrange[1].strftime('%d/%b %H:%M')}"
    )
else:
    _chart_xrange = None

st.sidebar.divider()

# --- IP Filter ---
st.sidebar.markdown("**🔍 IP Filter**")
_ip_inc_raw = st.sidebar.text_area(
    "Include only", key="ip_include", height=80,
    placeholder="1.2.3.4\n10.0.0.0/8\n(one per line or comma-separated)"
)
_ip_exc_raw = st.sidebar.text_area(
    "Exclude", key="ip_exclude", height=80,
    placeholder="1.2.3.4\n192.168.0.0/16"
)


def _parse_ip_list(raw):
    return [s.strip() for s in raw.replace(',', '\n').splitlines() if s.strip()]


def _ip_mask(series, entries):
    """Boolean mask — True where the IP matches any plain IP or CIDR in entries."""
    plain = {e for e in entries if '/' not in e}
    cidrs = []
    for e in entries:
        if '/' in e:
            try:
                cidrs.append(ipaddress.ip_network(e, strict=False))
            except ValueError:
                pass
    mask = series.isin(plain)
    if cidrs:
        def _in_cidr(ip_str):
            try:
                return any(ipaddress.ip_address(ip_str) in n for n in cidrs)
            except ValueError:
                return False
        mask = mask | series.apply(_in_cidr)
    return mask


_ip_include = _parse_ip_list(_ip_inc_raw)
_ip_exclude = _parse_ip_list(_ip_exc_raw)

for _df_var in ['df_access', 'df_cache']:
    _df = df_access if _df_var == 'df_access' else df_cache
    if not _df.empty and 'ip' in _df.columns:
        if _ip_include:
            _df = _df[_ip_mask(_df['ip'], _ip_include)]
        if _ip_exclude:
            _df = _df[~_ip_mask(_df['ip'], _ip_exclude)]
        if _df_var == 'df_access':
            df_access = _df
        else:
            df_cache = _df

if _ip_include:
    st.sidebar.caption(f"✅ Including {len(_ip_include)} entr{'y' if len(_ip_include)==1 else 'ies'}")
if _ip_exclude:
    st.sidebar.caption(f"🚫 Excluding {len(_ip_exclude)} entr{'y' if len(_ip_exclude)==1 else 'ies'}")

st.sidebar.divider()

# --- Auto-refresh ---
_REFRESH_OPTIONS = {"1 min": 60, "5 min": 300, "10 min": 600, "30 min": 1800}
auto_refresh = st.sidebar.checkbox("🔄 Auto-refresh", value=False)
refresh_seconds = 300
if auto_refresh:
    refresh_label   = st.sidebar.selectbox("Refresh interval", list(_REFRESH_OPTIONS.keys()), index=1)
    refresh_seconds = _REFRESH_OPTIONS[refresh_label]
    st.sidebar.caption(f"⏱️ Refreshing every {refresh_label}")

# --- Navigation ---

tabs_list = ["Access", "Errors", "Cache", "IP Analytics", "Security", "Insights"]
selected_nav = st.segmented_control(
    "View",
    tabs_list,
    default=st.session_state.navigation_menu,
    key="navigation_menu_widget"
)
st.session_state.navigation_menu = selected_nav

st.markdown("---")

# --- TAB 1: ACCESS ---
if st.session_state.navigation_menu == "Access":
    if df_access.empty:
        st.info("No access data to display.")
    else:
        m1, m2, m3, m4, m5 = st.columns(5)
        success_rate = (len(df_access[df_access['status'] < 400]) / len(df_access) * 100)
        errors_count = len(df_access[df_access['status'] >= 400])
        unique_ips   = df_access['ip'].nunique()
        avg_response = df_access[df_access['response_time'] > 0]['response_time'].mean()

        m1.metric("Success Rate",      f"{success_rate:.1f}%")
        m2.metric("Errors (4xx/5xx)",  f"{errors_count:,}")
        m3.metric("Unique IPs",        f"{unique_ips:,}")
        m4.metric("Total Requests",    f"{len(df_access):,}")
        m5.metric("Avg Response Time", f"{avg_response:.3f}s" if not pd.isna(avg_response) else "N/A")

        if access_fallback > 0:
            _fb_pct = access_fallback / max(len(df_access), 1) * 100
            _fb_msg = f"⚠️ {access_fallback:,} lines ({_fb_pct:.1f}%) used the fallback parser (method=HEAD, url=/) — these lines had a format the primary regex couldn't match (e.g. malformed User-Agent with unescaped quotes)."
            if _fb_pct > 2:
                st.warning(_fb_msg)
            else:
                st.caption(_fb_msg)

        st.markdown("### 📈 Request Timeline — drag to select range")
        _overlay_ip = st.text_input(
            "Overlay IP on timeline", key="timeline_overlay_ip",
            placeholder="e.g. 129.159.153.156  — highlights that IP's traffic as a red line"
        )

        access_hourly = df_access.set_index('time').resample('5min').size().reset_index(name='All Requests')
        fig_access = px.area(
            access_hourly, x='time', y='All Requests',
            color_discrete_sequence=['#6366f1'], template="plotly_white"
        )
        fig_access.update_traces(line_width=1.5)

        if _overlay_ip:
            _ov_ip = _overlay_ip.strip()
            _ov_df = df_access[df_access['ip'] == _ov_ip]
            if not _ov_df.empty:
                _ov_hourly = _ov_df.set_index('time').resample('5min').size().reset_index(name='Requests')
                fig_access.add_scatter(
                    x=_ov_hourly['time'], y=_ov_hourly['Requests'],
                    name=f"{_ov_ip}",
                    line=dict(color='#ef4444', width=2),
                    mode='lines+markers',
                    marker=dict(size=4),
                )
            else:
                st.caption(f"No activity for {_ov_ip} in the current time range.")

        ev_access = _selectable_chart(fig_access, key="chart_access_timeline", xrange=_chart_xrange)
        _handle_chart_event(ev_access)

        st.markdown("### 🔍 Filter & Search")
        search_col1, search_col2, search_col3 = st.columns([1, 2, 1])
        with search_col1:
            status_options = ["All"] + sorted([str(s) for s in df_access['status'].unique()])
            selected_status = st.selectbox("Status", status_options, key="access_status_filter")
        with search_col2:
            search_query = st.text_input(
                "Free search (IP, path, method or User Agent)",
                value=st.session_state.drill_ip,
                placeholder="e.g. 81.161...",
                key="access_search_box"
            )
        with search_col3:
            st.write(""); st.write("")
            if st.button("🗑️ Clear filter", key="clear_drill_btn"):
                st.session_state.drill_ip = ""
                st.rerun()

        _form_only = st.checkbox(
            "📋 Form attempts only (POST + form URL)",
            key="access_form_only",
        )

        _FORM_RE = re.compile(
            r'form_fields|wpcf7|contact.*form|fluent.*form|ninja.*form|gravityform|formidable',
            re.IGNORECASE,
        )

        filtered_df = df_access.copy()
        if selected_status != "All":
            filtered_df = filtered_df[filtered_df['status'] == int(selected_status)]
        if search_query:
            filtered_df = filtered_df[
                filtered_df['ip'].str.contains(search_query, case=False, na=False) |
                filtered_df['url'].str.contains(search_query, case=False, na=False) |
                filtered_df['method'].str.contains(search_query, case=False, na=False) |
                filtered_df['user_agent'].str.contains(search_query, case=False, na=False)
            ]
        if _form_only:
            filtered_df = filtered_df[
                (filtered_df['method'] == 'POST') &
                filtered_df['url'].str.contains(_FORM_RE.pattern, case=False, na=False, regex=True)
            ]

        st.markdown("### 📜 Request Details")
        display_df = filtered_df.sort_values('time', ascending=False).head(1000).copy()
        display_df['time_il'] = display_df['time'].dt.strftime('%d/%b/%Y %H:%M:%S')

        def style_status_column(row):
            val = row['status']
            if 200 <= val < 300:   color = 'background-color: #dcfce7; color: #166534;'
            elif 300 <= val < 400: color = 'background-color: #dbeafe; color: #1e40af;'
            elif 400 <= val < 500: color = 'background-color: #fef9c3; color: #854d0e;'
            else:                  color = 'background-color: #fee2e2; color: #991b1b;'
            return [color if col == 'status' else '' for col in row.index]

        show_cols = ['time_il', 'ip', 'status', 'method', 'url', 'response_time', 'size']
        st.dataframe(
            display_df[show_cols].style.apply(style_status_column, axis=1),
            hide_index=True
        )

        _dl1, _dl2 = st.columns(2)
        _dl1.download_button(
            "⬇️ Download structured (CSV)",
            data=display_df[show_cols].to_csv(index=False).encode('utf-8'),
            file_name="access_filtered.csv", mime="text/csv",
            key="dl_access_csv",
        )
        if 'raw_line' in display_df.columns:
            _dl2.download_button(
                "⬇️ Download raw lines (.log)",
                data='\n'.join(display_df['raw_line'].dropna()).encode('utf-8'),
                file_name="access_filtered.log", mime="text/plain",
                key="dl_access_raw",
            )

        _raw_log_expander(display_df, _ACCESS_RE, key="raw_access")

# --- TAB 2: ERRORS ---
elif st.session_state.navigation_menu == "Errors":
    if df_error.empty:
        st.success("No errors found! 🎉")
    else:
        st.subheader("Error Timeline — drag to select range")
        err_hourly = df_error.set_index('time').resample('h').size().reset_index(name='Errors')
        fig_err = px.area(err_hourly, x='time', y='Errors', color_discrete_sequence=['#ef4444'], template="plotly_white")
        ev_err = _selectable_chart(fig_err, key="chart_errors", xrange=_chart_xrange)
        _handle_chart_event(ev_err)

        st.subheader("Most Common Error Messages")
        _err_display = df_error.sort_values('time', ascending=False).copy()
        _err_display['time_il'] = _err_display['time'].dt.strftime('%d/%b/%Y %H:%M:%S')
        st.dataframe(_err_display['message'].value_counts().reset_index(name='count'))

        _edl1, _edl2 = st.columns(2)
        _edl1.download_button(
            "⬇️ Download structured (CSV)",
            data=_err_display[['time_il', 'level', 'message']].to_csv(index=False).encode('utf-8'),
            file_name="errors_filtered.csv", mime="text/csv",
            key="dl_errors_csv",
        )
        if 'raw_line' in _err_display.columns:
            _edl2.download_button(
                "⬇️ Download raw lines (.log)",
                data='\n'.join(_err_display['raw_line'].dropna()).encode('utf-8'),
                file_name="errors_filtered.log", mime="text/plain",
                key="dl_errors_raw",
            )

        _raw_log_expander(_err_display.head(500), _ERROR_RE, key="raw_errors")

# --- TAB 3: CACHE ---
elif st.session_state.navigation_menu == "Cache":
    if df_cache.empty:
        st.info("No cache performance logs found.")
    else:
        col_l, col_r = st.columns([1, 2])
        c_colors = {'HIT': '#10b981', 'MISS': '#f43f5e', 'BYPASS': '#6366f1', 'EXPIRED': '#f59e0b', 'STALE': '#8b5cf6'}
        with col_l:
            summary = df_cache['cache_status'].value_counts().reset_index()
            st.plotly_chart(px.pie(summary, values='count', names='cache_status', color='cache_status',
                                   color_discrete_map=c_colors, hole=0.4))
            hits = summary[summary['cache_status'] == 'HIT']['count'].sum()
            st.metric("Overall Hit Rate", f"{(hits / summary['count'].sum() * 100):.1f}%")
        with col_r:
            st.caption("Drag to select time range")
            cache_t = df_cache.groupby([pd.Grouper(key='time', freq='h'), 'cache_status']).size().reset_index(name='Count')
            fig_cache = px.bar(cache_t, x='time', y='Count', color='cache_status',
                               color_discrete_map=c_colors, barmode='stack', template="plotly_white")
            ev_cache = _selectable_chart(fig_cache, key="chart_cache_bar", xrange=_chart_xrange)
            _handle_chart_event(ev_cache)

        if 'response_time' in df_cache.columns:
            st.subheader("⏱️ Response Time by Cache Status")
            rt_df = df_cache[df_cache['response_time'] > 0]
            if not rt_df.empty:
                st.plotly_chart(
                    px.box(rt_df, x='cache_status', y='response_time', color='cache_status',
                           color_discrete_map=c_colors, template="plotly_white",
                           labels={'response_time': 'Seconds', 'cache_status': 'Status'})
                )

        _cache_display = df_cache.sort_values('time', ascending=False).copy()
        _cache_display['time_il'] = _cache_display['time'].dt.strftime('%d/%b/%Y %H:%M:%S')
        _cdl1, _cdl2 = st.columns(2)
        _cdl1.download_button(
            "⬇️ Download structured (CSV)",
            data=_cache_display[['time_il', 'ip', 'cache_status', 'method', 'url', 'response_time']].to_csv(index=False).encode('utf-8'),
            file_name="cache_filtered.csv", mime="text/csv",
            key="dl_cache_csv",
        )
        if 'raw_line' in _cache_display.columns:
            _cdl2.download_button(
                "⬇️ Download raw lines (.log)",
                data='\n'.join(_cache_display['raw_line'].dropna()).encode('utf-8'),
                file_name="cache_filtered.log", mime="text/plain",
                key="dl_cache_raw",
            )
        _raw_log_expander(_cache_display.head(500), _CACHE_RE, key="raw_cache")

# --- TAB 4: IP ANALYTICS ---
elif st.session_state.navigation_menu == "IP Analytics":
    if df_access.empty:
        st.info("No access data.")
    else:
        st.header("🔍 Top IP Analysis")

        cidr_query = st.text_input("🛰️ Filter by network range (CIDR, e.g. 192.168.1.0/24)", key="cidr_input_analytics")

        filtered_ips_data = df_access.copy()
        if cidr_query:
            try:
                network = ipaddress.ip_network(cidr_query, strict=False)
                def is_in_network(ip_str):
                    try:
                        return ipaddress.ip_address(ip_str) in network
                    except ValueError:
                        return False
                filtered_ips_data = filtered_ips_data[filtered_ips_data['ip'].apply(is_in_network)]
            except ValueError:
                st.error("Invalid CIDR format.")

        ip_analytics = filtered_ips_data.groupby('ip').agg({
            'time':          ['count', 'min', 'max'],
            'url':           'nunique',
            'size':          'sum',
            'response_time': 'mean',
        })
        ip_analytics.columns = ['requests', 'first_seen', 'last_seen', 'unique_urls', 'bytes', 'avg_response']
        ip_analytics = ip_analytics.reset_index()

        error_ips    = filtered_ips_data[filtered_ips_data['status'] >= 400].groupby('ip').size().reset_index(name='errors')
        ip_analytics = ip_analytics.merge(error_ips, on='ip', how='left').fillna(0)

        ip_analytics['mb']          = (ip_analytics['bytes'] / (1024 * 1024)).round(2)
        ip_analytics['active_min']  = (ip_analytics['last_seen'] - ip_analytics['first_seen']).dt.total_seconds() / 60
        ip_analytics['req_per_min'] = (ip_analytics['requests'] / ip_analytics['active_min'].clip(lower=0.5)).round(2)
        ip_analytics['avg_response'] = ip_analytics['avg_response'].round(3)

        st.subheader("📊 IP Summary Table")
        st.info("💡 Click a row to filter Access logs for that IP.")

        display_ips = ip_analytics.sort_values('requests', ascending=False).head(200).copy()

        selection = st.dataframe(
            display_ips[['ip', 'requests', 'errors', 'mb', 'unique_urls', 'req_per_min', 'avg_response', 'active_min', 'first_seen', 'last_seen']],
            column_config={
                "ip":          "IP Address",
                "requests":    st.column_config.NumberColumn("Requests 📥", format="%d"),
                "errors":      st.column_config.NumberColumn("Errors ❌", format="%d"),
                "mb":          st.column_config.NumberColumn("Volume (MB)", format="%.2f"),
                "unique_urls": st.column_config.NumberColumn("Unique URLs", format="%d"),
                "req_per_min": "Req/min",
                "avg_response": st.column_config.NumberColumn("Avg Response ⏱️", format="%.3fs"),
                "active_min":  st.column_config.NumberColumn("Active (min) ⏱️", format="%.1f"),
                "first_seen":  st.column_config.DatetimeColumn("First Seen", format="DD/MM HH:mm"),
                "last_seen":   st.column_config.DatetimeColumn("Last Seen",  format="DD/MM HH:mm"),
            },
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row"
        )

        if selection and selection.selection.rows:
            selected_ip = display_ips.iloc[selection.selection.rows[0]]['ip']
            st.session_state.drill_ip = selected_ip
            st.session_state.next_tab = "Access"
            st.rerun()

        st.markdown("---")

        st.subheader("📈 Activity Trends — drag to select range")
        # Metric drives both the TOP-5 ranking and the y-axis of the chart
        _TREND_METRICS = {
            "Requests":    ("requests",    "Requests"),
            "Errors":      ("errors",      "Errors"),
            "Volume (MB)": ("mb",          "Volume (MB)"),
            "Unique URLs": ("unique_urls", "Unique URLs"),
        }

        g_col1, g_col2 = st.columns([1, 3])
        with g_col1:
            _trend_label = st.radio("Show TOP 5 by:", list(_TREND_METRICS.keys()), key="ip_metric_radio_analytics")
        _sort_col, _y_label = _TREND_METRICS[_trend_label]

        _top5_ips = ip_analytics.sort_values(_sort_col, ascending=False).head(5)['ip'].tolist()
        _df_t5 = filtered_ips_data[filtered_ips_data['ip'].isin(_top5_ips)].copy()

        with g_col2:
            if _df_t5.empty:
                st.info("No data for the selected period.")
            else:
                # dt.floor is reliable with tz-aware columns; pd.Grouper(freq=) is not
                _df_t5['_hour'] = _df_t5['time'].dt.floor('h')

                if _trend_label == "Requests":
                    _ts = _df_t5.groupby(['_hour', 'ip']).size().reset_index(name=_y_label)
                elif _trend_label == "Errors":
                    _df_t5['_err'] = (_df_t5['status'] >= 400).astype(int)
                    _ts = _df_t5.groupby(['_hour', 'ip'])['_err'].sum().reset_index(name=_y_label)
                elif _trend_label == "Volume (MB)":
                    _df_t5['_mb'] = _df_t5['size'] / 1e6
                    _ts = _df_t5.groupby(['_hour', 'ip'])['_mb'].sum().reset_index(name=_y_label)
                else:  # Unique URLs
                    _ts = _df_t5.groupby(['_hour', 'ip'])['url'].nunique().reset_index(name=_y_label)

                _ts = _ts.rename(columns={'_hour': 'time'})
                fig_ip = px.line(_ts, x='time', y=_y_label, color='ip',
                                 markers=True, template="plotly_white")
                ev_ip = _selectable_chart(fig_ip, key="chart_ip_activity", xrange=_chart_xrange)
                _handle_chart_event(ev_ip)

# --- TAB 5: SECURITY ---
elif st.session_state.navigation_menu == "Security":
    if df_access.empty:
        st.info("No access data.")
    else:
        st.header("🔒 Security & Email Abuse Analysis")

        df_post  = df_access[df_access['method'] == 'POST'].copy()
        form_mask = df_post['url'].str.contains(
            r'form_fields|wpcf7|contact.*form|fluent.*form|ninja.*form|gravityform|formidable',
            case=False, na=False, regex=True)

        # --- Metrics ---
        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("Total POSTs",           f"{len(df_post):,}")
        mc2.metric("Unique IPs (POST)",     f"{df_post['ip'].nunique():,}")
        mc3.metric("Form/Email Attempts",   f"{form_mask.sum():,}")
        mc4.metric("Blocked POSTs (4xx/5xx)", f"{(df_post['status'] >= 400).sum():,}")

        # --- POST timeline ---
        st.subheader("📬 POST Request Timeline — drag to select range")
        if not df_post.empty:
            post_t = df_post.set_index('time').resample('15min').size().reset_index(name='POST Requests')
            fig_post = px.bar(post_t, x='time', y='POST Requests',
                              color_discrete_sequence=['#f43f5e'], template="plotly_white")
            ev_post = _selectable_chart(fig_post, key="chart_post_timeline", xrange=_chart_xrange)
            _handle_chart_event(ev_post)

        # --- Top attacking IPs ---
        st.subheader("🚨 Suspicious IPs — by POST volume")
        st.caption("💡 Click a row to drill down into that IP")
        if not df_post.empty:
            top_post = (
                df_post.groupby('ip')
                .agg(
                    requests=('time', 'count'),
                    post_errors=('status', lambda x: (x >= 400).sum()),
                    form_attempts=('url', lambda x: x.str.contains(
                        r'form_fields|wpcf7|contact.*form|fluent.*form', case=False, na=False, regex=True).sum()),
                    first_seen=('time', 'min'),
                    last_seen=('time', 'max'),
                )
                .reset_index()
                .sort_values('requests', ascending=False)
                .head(30)
            )
            top_post['duration_min'] = (
                (top_post['last_seen'] - top_post['first_seen']).dt.total_seconds() / 60
            ).round(1)
            top_post['req_per_min'] = (
                top_post['requests'] / top_post['duration_min'].clip(lower=0.5)
            ).round(1)
            # time is already in TZ — no conversion needed

            selection_sec = st.dataframe(
                top_post[['ip', 'requests', 'post_errors', 'form_attempts', 'req_per_min', 'duration_min', 'first_seen', 'last_seen']],
                column_config={
                    "ip":            "IP Address",
                    "requests":      st.column_config.NumberColumn("Requests", format="%d"),
                    "post_errors":   st.column_config.NumberColumn("POST Errors", format="%d"),
                    "form_attempts": st.column_config.NumberColumn("Form Attempts", format="%d"),
                    "req_per_min":   "Req/min",
                    "duration_min":  st.column_config.NumberColumn("Duration (min)", format="%.1f"),
                    "first_seen":    st.column_config.DatetimeColumn("First Seen", format="DD/MM HH:mm"),
                    "last_seen":     st.column_config.DatetimeColumn("Last Seen",  format="DD/MM HH:mm"),
                },
                hide_index=True,
                on_select="rerun", selection_mode="single-row"
            )
            if selection_sec and selection_sec.selection.rows:
                st.session_state.security_ip_drill = top_post.iloc[selection_sec.selection.rows[0]]['ip']
                st.rerun()

        st.markdown("---")

        # --- IP drill-down ---
        st.subheader("🔎 IP Drill-Down")
        drill_ip_sec = st.text_input(
            "IP address to analyse",
            value=st.session_state.security_ip_drill,
            placeholder="51.17.188.234",
            key="security_ip_drill",
        )

        if drill_ip_sec:
            df_ip = df_access[df_access['ip'] == drill_ip_sec].copy()
            if df_ip.empty:
                st.warning(f"No activity for {drill_ip_sec} in the selected time range.")
            else:
                d1, d2, d3, d4 = st.columns(4)
                d1.metric("Total Requests",    f"{len(df_ip):,}")
                d2.metric("POSTs",             f"{(df_ip['method'] == 'POST').sum():,}")
                d3.metric("200 OK",            f"{(df_ip['status'] == 200).sum():,}")
                d4.metric("Blocked (4xx/5xx)", f"{(df_ip['status'] >= 400).sum():,}")

                ip_t = df_ip.set_index('time').resample('5min').size().reset_index(name='Requests')
                st.plotly_chart(
                    px.bar(ip_t, x='time', y='Requests', template="plotly_white",
                           color_discrete_sequence=['#6366f1'],
                           title=f"Activity for {drill_ip_sec} — 5-min buckets")
                )

                st.subheader("📊 URL Patterns (grouped)")
                df_ip['url_pattern'] = df_ip['url'].apply(_normalize_url)
                url_groups = (
                    df_ip.groupby(['url_pattern', 'status'])
                    .agg(count=('time', 'count'))
                    .reset_index()
                    .sort_values('count', ascending=False)
                )
                st.dataframe(url_groups, hide_index=True)

                _raw_log_expander(df_ip.sort_values('time', ascending=False).head(500), _ACCESS_RE, key="raw_security_drill")

# --- TAB 6: INSIGHTS ---
elif st.session_state.navigation_menu == "Insights":
    if df_access.empty:
        st.info("No access data for the selected period.")
    else:
        df_ins = df_access.copy()
        df_ins['path'] = df_ins['url'].apply(_normalize_path)

        # ── Slow endpoints ───────────────────────────────────────────────────
        st.subheader("🐌 Slow Endpoints")
        st.caption("Sorted by P95. A high P95 with a low P50 usually means an unindexed query or an external API call with no timeout.")
        _rt = df_ins[df_ins['response_time'] > 0]
        if not _rt.empty:
            _slow = (
                _rt.groupby('path')['response_time']
                .agg(
                    requests='count',
                    p50=lambda x: x.quantile(0.50),
                    p95=lambda x: x.quantile(0.95),
                    p99=lambda x: x.quantile(0.99),
                    max='max',
                )
                .reset_index()
                .query('requests >= 5')
                .sort_values('p95', ascending=False)
                .head(50)
            )
            for _c in ['p50', 'p95', 'p99', 'max']:
                _slow[_c] = _slow[_c].round(3)
            _slow['requests'] = _slow['requests'].astype(int)
            st.dataframe(
                _slow,
                column_config={
                    'path':     'Endpoint',
                    'requests': st.column_config.NumberColumn('Requests', format='%d'),
                    'p50':      st.column_config.NumberColumn('P50 (s)',  format='%.3f'),
                    'p95':      st.column_config.NumberColumn('P95 (s)',  format='%.3f'),
                    'p99':      st.column_config.NumberColumn('P99 (s)',  format='%.3f'),
                    'max':      st.column_config.NumberColumn('Max (s)',  format='%.3f'),
                },
                hide_index=True, )

        st.markdown("---")

        # ── Error rate by endpoint ───────────────────────────────────────────
        st.subheader("❌ Error Rate by Endpoint")
        st.caption("Minimum 10 requests. Endpoints above 5% error rate almost always indicate a broken route or an unhandled exception.")
        _err_tbl = df_ins.groupby('path').agg(
            requests=('status', 'count'),
            errors=('status', lambda x: (x >= 400).sum()),
        ).reset_index()
        _err_tbl['error_rate'] = (_err_tbl['errors'] / _err_tbl['requests'] * 100).round(1)
        _err_tbl = _err_tbl[_err_tbl['requests'] >= 10].sort_values('error_rate', ascending=False).head(50)
        _top_status = (
            df_ins[df_ins['status'] >= 400]
            .groupby('path')['status']
            .agg(lambda x: int(x.mode().iloc[0]))
            .reset_index().rename(columns={'status': 'top_status'})
        )
        _err_tbl = _err_tbl.merge(_top_status, on='path', how='left')
        _err_tbl['requests'] = _err_tbl['requests'].astype(int)
        _err_tbl['errors']   = _err_tbl['errors'].astype(int)
        st.dataframe(
            _err_tbl,
            column_config={
                'path':       'Endpoint',
                'requests':   st.column_config.NumberColumn('Requests',    format='%d'),
                'errors':     st.column_config.NumberColumn('Errors',      format='%d'),
                'error_rate': st.column_config.NumberColumn('Error Rate %', format='%.1f'),
                'top_status': st.column_config.NumberColumn('Top Error',   format='%d'),
            },
            hide_index=True, )

        st.markdown("---")

        # ── 404 report ───────────────────────────────────────────────────────
        st.subheader("🔗 404 Not Found")
        _df404 = df_ins[df_ins['status'] == 404]
        if _df404.empty:
            st.success("No 404s in this period. 🎉")
        else:
            st.caption(f"{len(_df404):,} total 404 hits across {_df404['path'].nunique():,} distinct paths.")
            _r404 = (
                _df404.groupby('path')
                .agg(count=('time', 'count'), unique_ips=('ip', 'nunique'))
                .reset_index()
                .sort_values('count', ascending=False)
                .head(100)
            )
            _r404['count'] = _r404['count'].astype(int)
            st.dataframe(
                _r404,
                column_config={
                    'path':       'URL Path',
                    'count':      st.column_config.NumberColumn('Hits',       format='%d'),
                    'unique_ips': st.column_config.NumberColumn('Unique IPs', format='%d'),
                },
                hide_index=True, )

        # ── Cache efficiency ─────────────────────────────────────────────────
        if not df_cache.empty:
            st.markdown("---")
            st.subheader("📦 Cache Efficiency by Endpoint")
            st.caption("Endpoints with low HIT% — check for missing Cache-Control headers or Set-Cookie responses on cacheable pages.")
            _dc = df_cache.copy()
            _dc['path'] = _dc['url'].apply(_normalize_path)
            _cpivot = (
                _dc.groupby(['path', 'cache_status'])
                .size()
                .unstack(fill_value=0)
                .reset_index()
            )
            _cpivot['total'] = _cpivot.drop('path', axis=1).sum(axis=1)
            for _s in ['HIT', 'MISS', 'BYPASS', 'EXPIRED', 'STALE']:
                _cpivot[f'{_s}%'] = (
                    (_cpivot[_s] / _cpivot['total'] * 100).round(1)
                    if _s in _cpivot.columns else 0.0
                )
            _cpivot = _cpivot.sort_values('total', ascending=False).head(100)
            _show = ['path', 'total'] + [f'{_s}%' for _s in ['HIT', 'MISS', 'BYPASS', 'EXPIRED', 'STALE'] if f'{_s}%' in _cpivot.columns]
            st.dataframe(
                _cpivot[_show],
                column_config={
                    'path':      'Endpoint',
                    'total':     st.column_config.NumberColumn('Requests', format='%d'),
                    'HIT%':      st.column_config.NumberColumn('HIT %',     format='%.1f'),
                    'MISS%':     st.column_config.NumberColumn('MISS %',    format='%.1f'),
                    'BYPASS%':   st.column_config.NumberColumn('BYPASS %',  format='%.1f'),
                    'EXPIRED%':  st.column_config.NumberColumn('EXPIRED %', format='%.1f'),
                    'STALE%':    st.column_config.NumberColumn('STALE %',   format='%.1f'),
                },
                hide_index=True, )

        # ── Spike-correlated IPs ──────────────────────────────────────────────
        st.markdown("---")
        st.subheader("🎯 IPs Active During Traffic Spikes")
        st.caption(
            "Ranks IPs by how much of their traffic falls inside spike windows "
            "(5-min buckets with ≥ 300% above average traffic, i.e. 4× the mean). "
            "A high Spike% means the IP was specifically active when the site was under load."
        )
        _sp_buckets = df_access.set_index('time').resample('5min').size()
        _sp_mean = _sp_buckets.mean()
        _sp_thresh = _sp_mean * 4  # 300% above average (4× the mean)
        _spike_wins = set(_sp_buckets[_sp_buckets > _sp_thresh].index)

        if not _spike_wins:
            st.info("No significant traffic spikes detected in this time range.")
        else:
            df_ins2 = df_access.copy()
            df_ins2['_bucket'] = df_ins2['time'].dt.floor('5min')
            _in_spike = df_ins2['_bucket'].isin(_spike_wins)

            _sp_ip = (
                df_ins2[_in_spike].groupby('ip')
                .agg(spike_reqs=('time', 'count'), spike_windows=('_bucket', 'nunique'))
                .reset_index()
            )
            _total_ip = df_ins2.groupby('ip').size().reset_index(name='total_reqs')
            _sp_ip = _sp_ip.merge(_total_ip, on='ip')
            _sp_ip['spike_pct'] = (_sp_ip['spike_reqs'] / _sp_ip['total_reqs'] * 100).round(1)

            # Rate comparison: how many req/min during spikes vs outside spikes
            _spike_min     = len(_spike_wins) * 5          # each bucket = 5 min
            _total_min     = max((df_ins2['time'].max() - df_ins2['time'].min()).total_seconds() / 60, 1)
            _baseline_min  = max(_total_min - _spike_min, 1)
            _sp_ip['spike_rate']    = (_sp_ip['spike_reqs'] / _spike_min).round(2)
            _sp_ip['baseline_rate'] = ((_sp_ip['total_reqs'] - _sp_ip['spike_reqs']) / _baseline_min).round(2)
            # Ratio: how many times more active during spikes than at baseline
            _sp_ip['attack_ratio'] = (
                _sp_ip['spike_rate'] / _sp_ip['baseline_rate'].clip(lower=0.001)
            ).round(1)

            _sp_ip = _sp_ip[_sp_ip['total_reqs'] >= 5].sort_values('spike_reqs', ascending=False).head(30)

            st.caption(
                f"Spike threshold: **{_sp_thresh:.0f} req / 5 min** "
                f"(avg {_sp_mean:.0f} × 4 — 300% above normal) · "
                f"{len(_spike_wins)} spike window{'s' if len(_spike_wins) != 1 else ''} · "
                f"{_spike_min} min of spike time out of {_total_min:.0f} min total"
            )
            _sp_col = st.dataframe(
                _sp_ip[['ip', 'total_reqs', 'spike_reqs', 'spike_windows', 'spike_pct',
                         'spike_rate', 'baseline_rate', 'attack_ratio']],
                column_config={
                    'ip':            'IP Address',
                    'total_reqs':    st.column_config.NumberColumn('Total Reqs',       format='%d'),
                    'spike_reqs':    st.column_config.NumberColumn('Reqs in Spikes',   format='%d'),
                    'spike_windows': st.column_config.NumberColumn('Spike Windows',    format='%d'),
                    'spike_pct':     st.column_config.NumberColumn('Spike %',          format='%.1f'),
                    'spike_rate':    st.column_config.NumberColumn('Spike req/min',    format='%.2f'),
                    'baseline_rate': st.column_config.NumberColumn('Baseline req/min', format='%.2f'),
                    'attack_ratio':  st.column_config.NumberColumn('Attack Ratio ×',   format='%.1f'),
                },
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key="spike_ip_table",
            )
            if _sp_col and _sp_col.selection.rows:
                _picked = _sp_ip.iloc[_sp_col.selection.rows[0]]['ip']
                st.session_state['timeline_overlay_ip'] = _picked
                st.session_state['next_tab'] = 'Access'
                st.rerun()

# --- Auto-refresh ---
if auto_refresh:
    time.sleep(refresh_seconds)
    st.cache_data.clear()
    st.rerun()
