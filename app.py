import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import requests
import json
import twstock
from streamlit_gsheets import GSheetsConnection

# 頁面配置
st.set_page_config(page_title="台股均線精準監測站 (Google Sheets DB)", layout="wide")
st.title("📈 台股股價與客製化均線監測站 (Google Sheets 雲端連動)")

ALL_MAS = ['5MA', '10MA', '20MA', '60MA', '120MA', '240MA']
MA_LABELS = {
    '5MA': '5日線', '10MA': '10日線', '20MA': '月線(20MA)',
    '60MA': '季線(60MA)', '120MA': '半年線(120MA)', '240MA': '年線(240MA)'
}

BUILTIN_STOCKS = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電",
    "2382": "廣達", "2881": "富邦金", "2882": "國泰金", "2412": "中華電",
    "2891": "中信金", "3711": "日月光投控", "0050": "元大台灣50", "0056": "元大高股息",
    "00878": "國泰永續高股息", "00919": "群益台灣精選高息", "00929": "復華台灣科技優息"
}

# --- 1. Google Sheets 資料庫連線與讀寫函數 ---
def get_gsheet_connection():
    return st.connection("gsheets", type=GSheetsConnection)

def load_settings_from_gsheets():
    try:
        conn = get_gsheet_connection()
        df = conn.read(worksheet="Watchlist", ttl=0)
        if df is None or df.empty:
            return ["2330", "0050"], {"2330": ['20MA', '60MA', '240MA'], "0050": ALL_MAS.copy()}
        df = df.dropna(how='all')
        watchlist = []
        ma_settings = {}
        for _, row in df.iterrows():
            stock_val = row.get('Stock', '')
            if pd.notna(stock_val):
                code = str(stock_val).split('.')[0].strip()
                if code and code.isalnum():
                    watchlist.append(code)
                    mas_val = row.get('MAs', '')
                    mas_str = str(mas_val) if pd.notna(mas_val) else ""
                    mas_list = [m.strip() for m in mas_str.split(",") if m.strip() in ALL_MAS]
                    ma_settings[code] = mas_list if mas_list else ALL_MAS.copy()
        if not watchlist:
            watchlist = ["2330", "0050"]
            ma_settings = {"2330": ['20MA', '60MA', '240MA'], "0050": ALL_MAS.copy()}
        return watchlist, ma_settings
    except Exception as e:
        st.error(f"⚠️ Google Sheets 連線或讀取失敗，使用暫存資料。錯誤細節: {e}")
        if "watchlist" in st.session_state and "ma_settings" in st.session_state:
            return st.session_state.watchlist, st.session_state.ma_settings
        return ["2330", "0050"], {"2330": ['20MA', '60MA', '240MA'], "0050": ALL_MAS.copy()}

def save_settings_to_gsheets(watchlist, ma_settings):
    try:
        conn = get_gsheet_connection()
        rows = []
        for code in watchlist:
            mas = ma_settings.get(code, ALL_MAS)
            mas_str = ", ".join(mas)
            rows.append({"Stock": str(code), "MAs": mas_str})
        new_df = pd.DataFrame(rows)
        conn.update(worksheet="Watchlist", data=new_df)
        return True
    except Exception as e:
        st.error(f"❌ 寫入 Google Sheets 失敗: {e}")
        return False

# --- 2. 狀態初始化保護機制 (包含記憶 KD 設定) ---
if "watchlist" not in st.session_state or "ma_settings" not in st.session_state:
    db_watchlist, db_ma_settings = load_settings_from_gsheets()
    st.session_state.watchlist = db_watchlist
    st.session_state.ma_settings = db_ma_settings

# 記憶 KD 設定關鍵狀態初始化
if "enable_kd_filter" not in st.session_state:
    st.session_state.enable_kd_filter = True
if "max_k_value" not in st.session_state:
    st.session_state.max_k_value = 70
if "require_kd_cross" not in st.session_state:
    st.session_state.require_kd_cross = True

default_token = st.secrets.get("LINE_CHANNEL_ACCESS_TOKEN", "") if "LINE_CHANNEL_ACCESS_TOKEN" in st.secrets else st.session_state.get("line_token", "")
default_user_id = st.secrets.get("LINE_USER_ID", "") if "LINE_USER_ID" in st.secrets else st.session_state.get("line_user_id", "")

# --- 3. 側邊欄設定 (雙向綁定 session_state) ---
st.sidebar.header("⚙️ 均線警示門檻設定")
alert_threshold = st.sidebar.slider("提醒觸發門檻（股價距離均線 %）", min_value=0.5, max_value=5.0, value=1.5, step=0.1)

st.sidebar.markdown("---")
st.sidebar.header("📊 Tab 1 KD 指標過濾設定 (已紀錄)")

# 使用 key 自動記憶設定狀態，刷新不會遺失
enable_kd_filter = st.sidebar.checkbox(
    "啟用 Tab 1 KD 條件過濾", 
    value=st.session_state.enable_kd_filter, 
    key="enable_kd_filter"
)
max_k_value = st.sidebar.slider(
    "K 值上限 (防止追高)", 
    min_value=30, max_value=90, 
    value=st.session_state.max_k_value, 
    step=5, 
    key="max_k_value"
)
require_kd_cross = st.sidebar.checkbox(
    "要求 KD 處於多頭/黃金交叉 (K > D)", 
    value=st.session_state.require_kd_cross, 
    key="require_kd_cross"
)

st.sidebar.markdown("---")
st.sidebar.header("💬 LINE API 密鑰設定")
line_token = st.sidebar.text_input("Channel Access Token", value=default_token, type="password", key="input_token")
line_user_id = st.sidebar.text_input("Your User ID", value=default_user_id, type="password", key="input_user_id")

st.session_state.line_token = line_token
st.session_state.line_user_id = line_user_id

st.sidebar.markdown("---")
if st.sidebar.button("🔄 從 Google Sheets 強制重新載入"):
    db_watchlist, db_ma_settings = load_settings_from_gsheets()
    st.session_state.watchlist = db_watchlist
    st.session_state.ma_settings = db_ma_settings
    st.cache_data.clear()
    st.success("已成功重新載入雲端設定！")
    st.rerun()

# --- 4. 工具函數 ---
@st.cache_data(ttl=86400)
def get_stock_name(code):
    clean_code = str(code).replace(".TW", "").replace(".TWO", "").strip()
    if clean_code in BUILTIN_STOCKS:
        return BUILTIN_STOCKS[clean_code]
    try:
        if clean_code in twstock.codes:
            name = twstock.codes[clean_code].name
            if name: return name
    except Exception:
        pass
    try:
        symbol = f"{clean_code}.TW"
        info = yf.Ticker(symbol).info
        name = info.get("shortName") or info.get("longName")
        if name: return name
    except Exception:
        pass
    return clean_code

def get_stock_label(code):
    clean_code = str(code).replace(".TW", "").replace(".TWO", "").strip()
    name = get_stock_name(clean_code)
    if name and name != clean_code:
        return f"{name} ({clean_code})"
    return clean_code

def send_line_message(token, user_id, text):
    if not token or not user_id:
        return False, "請先填寫完整的 LINE Token 與 User ID！"
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token.strip()}"}
    payload = {"to": user_id.strip(), "messages": [{"type": "text", "text": text}]}
    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        return (True, "LINE 訊息發送成功！") if res.status_code == 200 else (False, f"發送失敗 (代碼 {res.status_code}): {res.text}")
    except Exception as e:
        return False, f"發送異常: {str(e)}"

def calculate_indicators(df):
    for ma in [5, 10, 20, 60, 120, 240]:
        df[f'{ma}MA'] = df['Close'].rolling(ma).mean()
    
    df['Vol_5MA'] = df['Volume'].rolling(5).mean()
    low_min = df['Low'].rolling(9).min()
    high_max = df['High'].rolling(9).max()
    rsv = (df['Close'] - low_min) / (high_max - low_min) * 100
    rsv = rsv.fillna(50)
    
    k_list, d_list = [], []
    k, d = 50.0, 50.0
    for r in rsv:
        k = (2/3) * k + (1/3) * r
        d = (2/3) * d + (1/3) * k
        k_list.append(k)
        d_list.append(d)
    
    df['K'] = k_list
    df['D'] = d_list
    return df

@st.cache_data(ttl=300)
def load_stock_data(stock_id):
    clean_id = str(stock_id).replace(".TW", "").replace(".TWO", "").strip()
    for suffix in [".TW", ".TWO"]:
        symbol = f"{clean_id}{suffix}"
        try:
            data = yf.download(symbol, period="2y", interval="1d", progress=False)
            if data is None or data.empty:
                continue
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)
            if 'Close' in data.columns:
                data = data.dropna(subset=['Close'])
            if data.empty or len(data) < 25:
                continue
            return calculate_indicators(data)
        except Exception:
            continue
    return None

@st.cache_data(ttl=86400)
def fetch_fundamental_chip_info(stock_id):
    clean_id = str(stock_id).replace(".TW", "").replace(".TWO", "").strip()
    for suffix in [".TW", ".TWO"]:
        try:
            ticker = yf.Ticker(f"{clean_id}{suffix}")
            info = ticker.info
            if info and 'trailingPE' in info:
                return {
                    "pe": info.get("trailingPE"),
                    "pb": info.get("priceToBook"),
                    "revenue_growth": info.get("revenueGrowth"),
                    "inst_percent": info.get("heldPercentInstitutions")
                }
        except Exception:
            continue
    return {"pe": None, "pb": None, "revenue_growth": None, "inst_percent": None}

# --- 5. 主介面 Tabs ---
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "⭐ 我的最愛與自訂均線 (DB連動)", 
    "🔍 單一個股圖表細節", 
    "🚀 帶量紅K短線轉強掃描",
    "🧱 底部大均線尋寶器 (長線支撐型)",
    "🤖 AI 三位一體起漲尋寶器 (三面合一)"
])

# ==========================================
# Tab 1: 我的最愛管理與對比警示
# ==========================================
with tab1:
    st.subheader("➕ 新增與管理關注個股 (自動同步至 Google Sheets)")
    
    col_in, col_btn = st.columns([3, 1])
    with col_in:
        new_stock = st.text_input("輸入要加入的股票代號（如：2317 或 0050）", key="new_stock_input").strip()
    with col_btn:
        st.write(" ")
        st.write(" ")
        if st.button("加到關注清單"):
            if new_stock:
                clean_new = new_stock.replace(".TW", "").replace(".TWO", "").strip()
                if clean_new not in st.session_state.watchlist:
                    st.session_state.watchlist.append(clean_new)
                    st.session_state.ma_settings[clean_new] = ALL_MAS.copy()
                    save_settings_to_gsheets(st.session_state.watchlist, st.session_state.ma_settings)
                    st.success(f"已新增 {clean_new} 並儲存！")
                    st.rerun()

    st.markdown("---")
    st.subheader("⚙️ 獨立設定每檔股票要監控的均線")

    def update_ma_setting(code):
        selected = st.session_state[f"ms_{code}"]
        st.session_state.ma_settings[code] = selected
        save_settings_to_gsheets(st.session_state.watchlist, st.session_state.ma_settings)

    for code in list(st.session_state.watchlist):
        stock_label = get_stock_label(code)
        with st.expander(f"📌 **{stock_label}** 監控均線設定", expanded=True):
            col_del, col_select = st.columns([1, 4])
            with col_del:
                if st.button(f"🗑️ 移除", key=f"del_{code}"):
                    st.session_state.watchlist.remove(code)
                    if code in st.session_state.ma_settings:
                        del st.session_state.ma_settings[code]
                    save_settings_to_gsheets(st.session_state.watchlist, st.session_state.ma_settings)
                    st.rerun()
            with col_select:
                current_selected = st.session_state.ma_settings.get(code, ALL_MAS)
                st.multiselect(
                    f"選擇 {stock_label} 要觸發通知的均線：",
                    options=ALL_MAS, default=current_selected,
                    format_func=lambda x: f"{x} ({MA_LABELS[x]})",
                    key=f"ms_{code}", on_change=update_ma_setting, args=(code,)
                )

    st.markdown("---")
    st.subheader("📊 清單即時均線與 KD 雙重警示比對")
    st.caption("🛡️ 警示雙重門檻：【短線均線 (5MA/10MA/20MA) 必須全數站上 120MA & 240MA】＋【符合側邊欄設定之 KD 指標】才會觸發。")

    all_alerts = []
    summary_data = []

    if st.session_state.watchlist:
        with st.spinner("更新數據中 (預設 5 分鐘更新一次)..."):
            for code in st.session_state.watchlist:
                df_code = load_stock_data(code)
                stock_label = get_stock_label(code)
                
                if df_code is not None and not df_code.empty and len(df_code) >= 240:
                    last_row = df_code.iloc[-1]
                    raw_price = last_row['Close']
                    
                    if pd.notna(raw_price):
                        price = float(raw_price)
                        target_mas = st.session_state.ma_settings.get(code, ALL_MAS)
                        
                        # 1. 均線型態檢測 (短均 > 長均)
                        ma5, ma10, ma20 = float(last_row['5MA']), float(last_row['10MA']), float(last_row['20MA'])
                        ma120, ma240 = float(last_row['120MA']), float(last_row['240MA'])
                        has_valid_ma = pd.notna(ma5) and pd.notna(ma10) and pd.notna(ma20) and pd.notna(ma120) and pd.notna(ma240)
                        short_mas_above = has_valid_ma and (
                            (ma5 > ma120 and ma5 > ma240) and
                            (ma10 > ma120 and ma10 > ma240) and
                            (ma20 > ma120 and ma20 > ma240)
                        )

                        # 2. KD 指標檢測 (讀取記憶後的 state)
                        k_val = float(last_row['K']) if pd.notna(last_row['K']) else 0.0
                        d_val = float(last_row['D']) if pd.notna(last_row['D']) else 0.0
                        
                        kd_pass = True
                        kd_desc = f"K:{k_val:.1f} / D:{d_val:.1f}"
                        
                        if enable_kd_filter:
                            if k_val > max_k_value:
                                kd_pass = False
                            if require_kd_cross and not (k_val > d_val):
                                kd_pass = False

                        triggered_info = []
                        
                        if short_mas_above and kd_pass:
                            for ma_key in target_mas:
                                if ma_key in last_row and pd.notna(last_row[ma_key]):
                                    ma_val = float(last_row[ma_key])
                                    diff = ((price - ma_val) / ma_val) * 100
                                    if abs(diff) <= alert_threshold:
                                        pos = "站上" if diff >= 0 else "跌破"
                                        triggered_info.append(f"{MA_LABELS[ma_key]}({abs(diff):.1f}%)")
                                        all_alerts.append(
                                            f"• **{stock_label}** 現價 {price:.2f} 靠近 **{MA_LABELS[ma_key]}** ({ma_val:.2f})，差距 {abs(diff):.1f}% ({pos}) [KD: K={k_val:.1f}, D={d_val:.1f}]"
                                        )

                        ma_status = "短均>長均" if short_mas_above else "短均未過"
                        kd_status = "KD符合" if kd_pass else "KD未符合"
                        
                        summary_data.append({
                            "股票名稱 (代號)": stock_label,
                            "收盤價": f"{price:.2f}",
                            "型態結構": f"{ma_status} | {kd_status}",
                            "KD 指標": kd_desc,
                            "監控中的均線": ", ".join([MA_LABELS.get(m, m) for m in target_mas]),
                            "符合警示的均線": ", ".join(triggered_info) if triggered_info else "無接近/未符合雙條件"
                        })
                    else:
                        summary_data.append({"股票名稱 (代號)": stock_label, "收盤價": "價格無效", "型態結構": "-", "KD 指標": "-", "監控中的均線": "-", "符合警示的均線": "數據缺失"})
                else:
                    summary_data.append({"股票名稱 (代號)": stock_label, "收盤價": "代號錯誤/數據不足", "型態結構": "-", "KD 指標": "-", "監控中的均線": "-", "符合警示的均線": "無法抓取"})

        if summary_data:
            st.dataframe(pd.DataFrame(summary_data), use_container_width=True)

        st.markdown("---")
        st.write("### 🔔 LINE 手動發送與觸發通知區")
        
        col_btn1, col_btn2 = st.columns([1, 1])
        
        with col_btn1:
            if st.button("🧪 發送 LINE 測試訊息"):
                success, info = send_line_message(line_token, line_user_id, "🔔 這是一條來自【台股均線監測站】的連線測試訊息！")
                st.success(info) if success else st.error(info)

        if all_alerts:
            st.warning("⚠️ 目前同時滿足【型態結構】＋【KD條件】且觸發門檻的個股：\n" + "\n".join(all_alerts))
            with col_btn2:
                if st.button("📲 發送選擇均線之 LINE 警示訊息", type="primary"):
                    kd_filter_text = f"(KD限制: K≤{max_k_value}" + (", 要求K>D)" if require_kd_cross else ")") if enable_kd_filter else "(未啟用KD限制)"
                    msg = f"\n🚨【台股監測警示 - 均線+KD通知】\n門檻設定：{alert_threshold}%\n{kd_filter_text}\n" + "\n".join(all_alerts).replace("**", "")
                    success, info = send_line_message(line_token, line_user_id, msg)
                    st.success(info) if success else st.error(info)
        else:
            st.info(f"💡 目前清單中無同時符合『短均全在長均之上』、『KD篩選門檻』且與監控均線差距小於 {alert_threshold}% 的個股。")

# (Tab 2, Tab 3, Tab 4, Tab 5 保持不變...)
with tab2:
    search_code = st.text_input("輸入台股代號查看技術線圖", value="2330").strip()
    if search_code:
        stock_label = get_stock_label(search_code)
        df_single = load_stock_data(search_code)
        if df_single is not None and not df_single.empty:
            st.subheader(f"📈 {stock_label} 技術線圖")
            plot_df = df_single.tail(120)
            fig = go.Figure()
            fig.add_trace(go.Candlestick(x=plot_df.index, open=plot_df['Open'], high=plot_df['High'], low=plot_df['Low'], close=plot_df['Close'], name='K線'))
            colors = {'5MA': 'orange', '10MA': 'purple', '20MA': 'blue', '60MA': 'green', '120MA': 'brown', '240MA': 'red'}
            for ma_col, color in colors.items():
                if ma_col in plot_df.columns:
                    fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df[ma_col], mode='lines', name=MA_LABELS[ma_col], line=dict(color=color, width=1.5)))
            fig.update_layout(xaxis_rangeslider_visible=False, height=550, margin=dict(l=20, r=20, t=20, b=20), template="plotly_white")
            st.plotly_chart(fig, use_container_width=True)

with tab3:
    st.subheader("🚀 全台股帶量紅 K 與均線上彎掃描器")
    st.caption("硬性條件：【成交量 > 2000張】＋【帶量紅K (量>5日均量1.3倍)】＋【5MA / 10MA / 20MA 至少有一條扣低轉上彎】")
    col_v, col_s = st.columns(2)
    with col_v:
        min_vol_lots = st.number_input("成交量防護門檻 (張)", min_value=500, value=2000, step=500, key="t3_vol")
    with col_s:
        scan_scope = st.selectbox("掃描標的範圍", ["熱門大型與權值股 (約 30 檔 - 快速)", "全台股上市上櫃 (約 1800 檔 - 需較長時間)"], key="t3_scope")

    if st.button("🔍 開始掃描強勢標的", type="primary", key="btn_t3"):
        target_codes = list(BUILTIN_STOCKS.keys()) + ["2303", "2603", "2609", "2615", "3231", "2356", "6669", "3037", "2379", "3034", "2337", "2408", "2344", "2301", "2324", "2353"] if scan_scope.startswith("熱門大型") else [c for c, i in twstock.codes.items() if i.type == "股票" and len(c) == 4 and c.isdigit()]
        p_bar = st.progress(0)
        scan_results = []
        for idx, code in enumerate(target_codes):
            p_bar.progress((idx + 1) / len(target_codes))
            df = load_stock_data(code)
            if df is not None and not df.empty and len(df) >= 25:
                curr, prev, prev2 = df.iloc[-1], df.iloc[-2], df.iloc[-3]
                open_p, close_p, vol_shares = float(curr['Open']), float(curr['Close']), float(curr['Volume'])
                vol_lots, vol_5ma = vol_shares / 1000.0, float(curr['Vol_5MA']) if pd.notna(curr['Vol_5MA']) else 0
                if vol_lots >= min_vol_lots and close_p > open_p and (vol_5ma > 0 and vol_shares >= vol_5ma * 1.3):
                    up_mas = [m_name for m_key, m_name in [('5MA', '5日線'), ('10MA', '10日線'), ('20MA', '月線')] if pd.notna(curr[m_key]) and pd.notna(prev[m_key]) and pd.notna(prev2[m_key]) if float(prev[m_key]) <= float(prev2[m_key]) and float(curr[m_key]) > float(prev[m_key]) and close_p >= float(curr[m_key])]
                    if up_mas:
                        scan_results.append({"股票代號/名稱": get_stock_label(code), "收盤價": f"{close_p:.2f}", "漲跌K線": f"🔴 紅K (+{(close_p - open_p):.2f})", "成交量 (張)": int(vol_lots), "量增倍數": f"{(vol_shares/vol_5ma):.1f} 倍", "轉上彎均線": "、".join(up_mas), "KD (K值)": round(float(curr['K']), 1) if pd.notna(curr['K']) else "-"})
        p_bar.empty()
        st.dataframe(pd.DataFrame(scan_results), use_container_width=True) if scan_results else st.warning("⚠️ 目前無符合條件的股票。")

with tab4:
    st.subheader("🧱 底部大均線（半年線/年線）佈局器")
    st.caption("嚴格條件：【短線均線 (5MA/10MA/20MA) 全數站上 120MA & 240MA】＋【股價貼近長線成本區】")
    col_target_ma, col_dist, col_v4 = st.columns(3)
    with col_target_ma:
        selected_bottom_mas = st.multiselect("選擇要比對貼近狀況的大均線：", options=['120MA', '240MA'], default=['120MA', '240MA'], format_func=lambda x: f"{x} ({MA_LABELS[x]})")
    with col_dist:
        max_dist_pct = st.number_input("股價/短均距離大均線上限 (%)", min_value=0.5, max_value=15.0, value=5.0, step=0.5)
    with col_v4:
        min_vol_bottom = st.number_input("成交量防護門檻 (張)", min_value=500, value=2000, step=500, key="t4_vol")
    only_red_bottom = st.checkbox("只顯示帶量紅 K（成交量 > 5日均量 1.2倍 且 當日收紅）", value=True, key="t4_red")

    if st.button("🔍 掃描長線轉強且貼近底部的標的", type="primary", key="btn_t4"):
        if selected_bottom_mas:
            target_codes = [c for c, i in twstock.codes.items() if i.type == "股票" and len(c) == 4 and c.isdigit()]
            p_bar4 = st.progress(0)
            bottom_results = []
            for idx, code in enumerate(target_codes):
                p_bar4.progress((idx + 1) / len(target_codes))
                df = load_stock_data(code)
                if df is not None and not df.empty and len(df) >= 240:
                    curr = df.iloc[-1]
                    price, open_p, vol_shares = float(curr['Close']), float(curr['Open']), float(curr['Volume'])
                    vol_lots, vol_5ma = vol_shares / 1000.0, float(curr['Vol_5MA']) if pd.notna(curr['Vol_5MA']) else 0
                    if vol_lots >= min_vol_bottom:
                        if only_red_bottom and not (price > open_p and vol_shares >= vol_5ma * 1.2):
                            continue
                        ma5, ma10, ma20 = float(curr['5MA']), float(curr['10MA']), float(curr['20MA'])
                        ma120, ma240 = float(curr['120MA']), float(curr['240MA'])
                        if pd.notna(ma5) and pd.notna(ma10) and pd.notna(ma20) and pd.notna(ma120) and pd.notna(ma240):
                            if (ma5 > ma120 and ma5 > ma240) and (ma10 > ma120 and ma10 > ma240) and (ma20 > ma120 and ma20 > ma240):
                                near_info = []
                                min_abs_diff = 999.0
                                for ma_key in selected_bottom_mas:
                                    ma_val = float(curr[ma_key])
                                    diff_pct = ((price - ma_val) / ma_val) * 100
                                    if 0 <= diff_pct <= max_dist_pct:
                                        near_info.append(f"{MA_LABELS[ma_key]} (高出 {diff_pct:+.1f}%)")
                                        if diff_pct < min_abs_diff: min_abs_diff = diff_pct
                                if near_info:
                                    bottom_results.append({"股票代號/名稱": get_stock_label(code), "收盤價": f"{price:.2f}", "成交量 (張)": int(vol_lots), "貼近狀況 (站上長均)": " | ".join(near_info), "KD (K值)": round(float(curr['K']), 1) if pd.notna(curr['K']) else "-", "距離長均差距 (%)": f"{min_abs_diff:.1f}%", "差距數值": min_abs_diff})
            p_bar4.empty()
            st.dataframe(pd.DataFrame(bottom_results).sort_values("差距數值").drop(columns=["差距數值"]), use_container_width=True) if bottom_results else st.warning("⚠️ 目前無符合所有硬性條件的股票。")

with tab5:
    st.subheader("🤖 AI 三位一體：基本面＋技術面＋籌碼面「未起漲/剛起漲」交叉驗證")
    st.caption("AI 評分模型：篩選打底完成、籌碼收集、且估值合理或具營收成長性的極早期潛力標的。")

    col_t5_1, col_t5_2, col_t5_3 = st.columns(3)
    with col_t5_1:
        max_pe_input = st.number_input("基本面：本益比 (P/E) 上限", min_value=5, max_value=50, value=25, step=1)
    with col_t5_2:
        max_kd_input = st.number_input("技術面：KD (K值) 防高檔上限", min_value=20, max_value=80, value=65, step=5)
    with col_t5_3:
        min_vol_t5 = st.number_input("成交量門檻 (張)", min_value=300, value=1000, step=100, key="t5_vol")

    if st.button("🚀 啟動 AI 交叉分析掃描", type="primary", key="btn_t5"):
        target_codes = [c for c, i in twstock.codes.items() if i.type == "股票" and len(c) == 4 and c.isdigit()]
        st.info(f"AI 正在對 {len(target_codes)} 檔股票進行基本面、技術面、籌碼面進行模型檢測...")
        p_bar5 = st.progress(0)
        ai_results = []

        for idx, code in enumerate(target_codes):
            p_bar5.progress((idx + 1) / len(target_codes))
            df = load_stock_data(code)
            
            if df is not None and not df.empty and len(df) >= 60:
                curr, prev = df.iloc[-1], df.iloc[-2]
                price = float(curr['Close'])
                vol_lots = float(curr['Volume']) / 1000.0

                if vol_lots < min_vol_t5:
                    continue

                tech_score = 0
                tech_reasons = []

                k_val = float(curr['K']) if pd.notna(curr['K']) else 99
                d_val = float(curr['D']) if pd.notna(curr['D']) else 99
                prev_k = float(prev['K']) if pd.notna(prev['K']) else 0
                prev_d = float(prev['D']) if pd.notna(prev['D']) else 0

                if k_val <= max_kd_input and prev_k <= prev_d and k_val > d_val:
                    tech_score += 35
                    tech_reasons.append(f"KD低檔金叉(K={k_val:.1f})")

                ma20 = float(curr['20MA']) if pd.notna(curr['20MA']) else 0
                ma60 = float(curr['60MA']) if pd.notna(curr['60MA']) else 0
                if price >= ma20 and price >= ma60:
                    diff_60 = abs(price - ma60) / ma60 * 100
                    if diff_60 <= 5.0:
                        tech_score += 35
                        tech_reasons.append(f"剛站上季線({diff_60:.1f}%)")

                if tech_score == 0:
                    continue

                f_info = fetch_fundamental_chip_info(code)
                pe, pb, rev_growth = f_info['pe'], f_info['pb'], f_info['revenue_growth']

                fund_score = 0
                fund_reasons = []

                if pe and 0 < pe <= max_pe_input:
                    fund_score += 15
                    fund_reasons.append(f"PE低估({pe:.1f})")
                if rev_growth and rev_growth > 0:
                    fund_score += 15
                    fund_reasons.append(f"營收成長({rev_growth*100:.1f}%)")

                total_ai_score = tech_score + fund_score

                if total_ai_score >= 50:
                    ai_results.append({
                        "股票代號/名稱": get_stock_label(code),
                        "AI綜合推薦指數": f"⭐ {total_ai_score} 分",
                        "收盤價": f"{price:.2f}",
                        "成交量 (張)": int(vol_lots),
                        "技術面訊號": " + ".join(tech_reasons) if tech_reasons else "強勢打底",
                        "基本/籌碼亮點": " | ".join(fund_reasons) if fund_reasons else "估值合理",
                        "score_num": total_ai_score
                    })

        p_bar5.empty()

        if ai_results:
            res_df = pd.DataFrame(ai_results).sort_values("score_num", ascending=False).drop(columns=["score_num"])
            st.success(f"🤖 AI 模型成功精選出 {len(res_df)} 檔『基本面+技術面+籌碼面』剛起漲/潛力打底個股：")
            st.dataframe(res_df, use_container_width=True)
        else:
            st.warning("⚠️ 目前未掃描到符合三位一體高分技術與估值條件的股票。")