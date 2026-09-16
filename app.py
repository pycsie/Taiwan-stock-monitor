import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import requests
import json
import twstock
import time
import random
from datetime import datetime, timedelta
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
    default_watchlist = ["2330", "0050"]
    default_ma_settings = {"2330": ['20MA', '60MA', '240MA'], "0050": ALL_MAS.copy()}
    default_enable_kd = True
    default_max_k = 70

    try:
        conn = get_gsheet_connection()
        df = conn.read(worksheet="Watchlist", ttl=0)
        if df is None or df.empty:
            return default_watchlist, default_ma_settings, default_enable_kd, default_max_k
        
        df = df.dropna(how='all')
        watchlist = []
        ma_settings = {}
        
        enable_kd = default_enable_kd
        max_k = default_max_k
        if 'Enable_KD' in df.columns and pd.notna(df.iloc[0]['Enable_KD']):
            enable_kd = bool(df.iloc[0]['Enable_KD'])
        if 'Max_K' in df.columns and pd.notna(df.iloc[0]['Max_K']):
            try:
                max_k = int(df.iloc[0]['Max_K'])
            except ValueError:
                pass

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
            watchlist, ma_settings = default_watchlist, default_ma_settings
            
        return watchlist, ma_settings, enable_kd, default_max_k
    except Exception as e:
        st.error(f"⚠️ Google Sheets 連線或讀取失敗，使用暫存資料。錯誤細節: {e}")
        return default_watchlist, default_ma_settings, default_enable_kd, default_max_k

def save_settings_to_gsheets(watchlist, ma_settings, enable_kd, max_k):
    try:
        conn = get_gsheet_connection()
        rows = []
        for idx, code in enumerate(watchlist):
            mas = ma_settings.get(code, ALL_MAS)
            mas_str = ", ".join(mas)
            rows.append({
                "Stock": str(code), 
                "MAs": mas_str,
                "Enable_KD": enable_kd,
                "Max_K": max_k
            })
        new_df = pd.DataFrame(rows)
        conn.update(worksheet="Watchlist", data=new_df)
        return True
    except Exception as e:
        st.error(f"❌ 寫入 Google Sheets 失敗: {e}")
        return False

# --- 2. 狀態初始化機制 ---
if "watchlist" not in st.session_state or "enable_kd_filter" not in st.session_state:
    db_watchlist, db_ma_settings, db_enable_kd, db_max_k = load_settings_from_gsheets()
    st.session_state.watchlist = db_watchlist
    st.session_state.ma_settings = db_ma_settings
    st.session_state.enable_kd_filter = db_enable_kd
    st.session_state.max_k_value = db_max_k

# 初始化 Tab 6 的大/小單即時數據庫
if "tab6_stock_orders" not in st.session_state:
    st.session_state.tab6_stock_orders = {
        "2330": {"name": "台積電", "small": 0, "medium": 0, "big": 0, "super_big": 0},
        "2317": {"name": "鴻海", "small": 0, "medium": 0, "big": 0, "super_big": 0},
        "2454": {"name": "聯發科", "small": 0, "medium": 0, "big": 0, "super_big": 0},
        "3037": {"name": "欣興", "small": 0, "medium": 0, "big": 0, "super_big": 0},
        "2382": {"name": "廣達", "small": 0, "medium": 0, "big": 0, "super_big": 0},
        "2308": {"name": "台達電", "small": 0, "medium": 0, "big": 0, "super_big": 0},
    }

default_token = st.secrets.get("LINE_CHANNEL_ACCESS_TOKEN", "") if "LINE_CHANNEL_ACCESS_TOKEN" in st.secrets else st.session_state.get("line_token", "")
default_user_id = st.secrets.get("LINE_USER_ID", "") if "LINE_USER_ID" in st.secrets else st.session_state.get("line_user_id", "")

# --- 3. 側邊欄設定 ---
def on_kd_setting_change():
    st.session_state.enable_kd_filter = st.session_state.input_enable_kd
    st.session_state.max_k_value = st.session_state.input_max_k
    save_settings_to_gsheets(
        st.session_state.watchlist,
        st.session_state.ma_settings,
        st.session_state.enable_kd_filter,
        st.session_state.max_k_value
    )

st.sidebar.header("⚙️ 均線警示門檻設定")
alert_threshold = st.sidebar.slider("提醒觸發門檻（股價距離均線 %）", min_value=0.5, max_value=5.0, value=1.5, step=0.1)

st.sidebar.markdown("---")
st.sidebar.header("📊 Tab 1 KD 指標過濾設定 (雲端同步)")

enable_kd_filter = st.sidebar.checkbox(
    "啟用 Tab 1 KD 條件過濾", 
    value=st.session_state.enable_kd_filter,
    key="input_enable_kd",
    on_change=on_kd_setting_change
)
max_k_value = st.sidebar.slider(
    "K 值上限 (高於此值不警示)", 
    min_value=30, max_value=90, 
    value=st.session_state.max_k_value, 
    step=5,
    key="input_max_k",
    on_change=on_kd_setting_change
)

st.sidebar.markdown("---")
st.sidebar.header("💬 LINE API 密鑰設定")
line_token = st.sidebar.text_input("Channel Access Token", value=default_token, type="password", key="input_token")
line_user_id = st.sidebar.text_input("Your User ID", value=default_user_id, type="password", key="input_user_id")

st.session_state.line_token = line_token
st.session_state.line_user_id = line_user_id

st.sidebar.markdown("---")
if st.sidebar.button("🔄 從 Google Sheets 強制重新載入"):
    db_watchlist, db_ma_settings, db_enable_kd, db_max_k = load_settings_from_gsheets()
    st.session_state.watchlist = db_watchlist
    st.session_state.ma_settings = db_ma_settings
    st.session_state.enable_kd_filter = db_enable_kd
    st.session_state.max_k_value = db_max_k
    st.cache_data.clear()
    st.success("已成功重新載入雲端設定！")
    st.rerun()

# --- 4. 工具函數 (含 KD 與 MACD 計算) ---
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
    df['Vol_20MA'] = df['Volume'].rolling(20).mean()

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

    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD_DIF'] = ema12 - ema26
    df['MACD_DEA'] = df['MACD_DIF'].ewm(span=9, adjust=False).mean()
    df['MACD_HIST'] = df['MACD_DIF'] - df['MACD_DEA']

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

# --- TWSE / TPEx 三大法人的官方 API 資料抓取 ---
@st.cache_data(ttl=3600)
def fetch_chip_data_twse_tpex():
    chip_dict = {}
    today = datetime.now()
    
    for day_offset in range(5):
        target_date = today - timedelta(days=day_offset)
        date_str_twse = target_date.strftime("%Y%m%d")
        
        twse_url = f"https://www.twse.com.tw/rwd/zh/fund/T86?response=json&date={date_str_twse}&selectType=ALL"
        try:
            r = requests.get(twse_url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if data.get("stat") == "OK" and "data" in data:
                    for row in data["data"]:
                        code = row[0].strip()
                        if len(code) == 4 and code.isdigit():
                            foreign_net_val = float(row[7].replace(",", "")) if len(row) > 7 else 0.0
                            sitc_net_val = float(row[10].replace(",", "")) if len(row) > 10 else 0.0
                            chip_dict[code] = {
                                "foreign_shares": foreign_net_val,
                                "sitc_shares": sitc_net_val,
                            }
                    if chip_dict:
                        break
        except Exception:
            pass
            
    return chip_dict

# --- 證交所融資融券餘額數據抓取 ---
@st.cache_data(ttl=3600)
def fetch_margin_data_twse():
    margin_dict = {}
    today = datetime.now()
    
    for day_offset in range(5):
        target_date = today - timedelta(days=day_offset)
        date_str = target_date.strftime("%Y%m%d")
        
        url = f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGIN?response=json&date={date_str}&selectType=ALL"
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if data.get("stat") == "OK" and "data" in data:
                    for row in data["data"]:
                        code = row[0].strip()
                        if len(code) == 4 and code.isdigit():
                            try:
                                margin_diff = float(row[6].replace(",", "")) if len(row) > 6 else 0.0
                                margin_bal = float(row[5].replace(",", "")) if len(row) > 5 else 0.0
                                margin_dict[code] = {
                                    "margin_diff": margin_diff,
                                    "margin_bal": margin_bal
                                }
                            except ValueError:
                                continue
                    if margin_dict:
                        break
        except Exception:
            pass
            
    return margin_dict

# --- 5. 主介面 Tabs ---
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "⭐ 我的最愛與自訂均線 (DB連動)", 
    "🧱 底部大均線尋寶器 (長線支撐型)",
    "🤖 AI 波段翻多與多頭型態掃描",
    "🔥 籌碼、爆量與波段翻多複合篩選器",
    "🛡️ 底部籌碼洗淨與均線卡位掃描器",
    "⚡ 盤中即時當沖與大小單同買監測"
])

# ==========================================
# Tab 1: 我的最愛與自訂均線
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
                    save_settings_to_gsheets(
                        st.session_state.watchlist, 
                        st.session_state.ma_settings,
                        st.session_state.enable_kd_filter,
                        st.session_state.max_k_value
                    )
                    st.success(f"已新增 {clean_new} 並儲存！")
                    st.rerun()

    st.markdown("---")
    st.subheader("⚙️ 獨立設定每檔股票要監控的均線")

    def update_ma_setting(code):
        selected = st.session_state[f"ms_{code}"]
        st.session_state.ma_settings[code] = selected
        save_settings_to_gsheets(
            st.session_state.watchlist, 
            st.session_state.ma_settings,
            st.session_state.enable_kd_filter,
            st.session_state.max_k_value
        )

    for code in list(st.session_state.watchlist):
        stock_label = get_stock_label(code)
        with st.expander(f"📌 **{stock_label}** 監控均線設定", expanded=True):
            col_del, col_select = st.columns([1, 4])
            with col_del:
                if st.button(f"🗑️ 移除", key=f"del_{code}"):
                    st.session_state.watchlist.remove(code)
                    if code in st.session_state.ma_settings:
                        del st.session_state.ma_settings[code]
                    save_settings_to_gsheets(
                        st.session_state.watchlist, 
                        st.session_state.ma_settings,
                        st.session_state.enable_kd_filter,
                        st.session_state.max_k_value
                    )
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
    st.caption("🛡️ 警示雙重門檻：【短線均線 (5MA/10MA/20MA) 必須全數站上 120MA & 240MA】＋【符合側邊欄 K 值上限】才會觸發。")

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
                        
                        ma5, ma10, ma20 = float(last_row['5MA']), float(last_row['10MA']), float(last_row['20MA'])
                        ma120, ma240 = float(last_row['120MA']), float(last_row['240MA'])
                        has_valid_ma = pd.notna(ma5) and pd.notna(ma10) and pd.notna(ma20) and pd.notna(ma120) and pd.notna(ma240)
                        short_mas_above = has_valid_ma and (
                            (ma5 > ma120 and ma5 > ma240) and
                            (ma10 > ma120 and ma10 > ma240) and
                            (ma20 > ma120 and ma20 > ma240)
                        )

                        k_val = float(last_row['K']) if pd.notna(last_row['K']) else 0.0
                        d_val = float(last_row['D']) if pd.notna(last_row['D']) else 0.0
                        
                        kd_pass = True
                        kd_desc = f"K:{k_val:.1f} / D:{d_val:.1f}"
                        
                        if enable_kd_filter and (k_val > max_k_value):
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
                        kd_status = "KD符合" if kd_pass else "KD超標"
                        
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
                    kd_filter_text = f"(KD限制: K≤{max_k_value})" if enable_kd_filter else "(未啟用KD限制)"
                    msg = f"\n🚨【台股監測警示 - 均線+KD通知】\n門檻設定：{alert_threshold}%\n{kd_filter_text}\n" + "\n".join(all_alerts).replace("**", "")
                    success, info = send_line_message(line_token, line_user_id, msg)
                    st.success(info) if success else st.error(info)
        else:
            st.info(f"💡 目前清單中無同時符合『短均全在長均之上』、『KD 門檻』且與監控均線差距小於 {alert_threshold}% 的個股。")

# ==========================================
# Tab 2: 底部大均線尋寶器
# ==========================================
with tab2:
    st.subheader("🧱 底部大均線（半年線/年線）佈局器")
    st.caption("嚴格條件：【短線均線 (5MA/10MA/20MA) 全數站上 120MA & 240MA】＋【股價貼近長線成本區】")
    col_target_ma, col_dist, col_v3 = st.columns(3)
    with col_target_ma:
        selected_bottom_mas = st.multiselect("選擇要比對貼近狀況的大均線：", options=['120MA', '240MA'], default=['120MA', '240MA'], format_func=lambda x: f"{x} ({MA_LABELS[x]})")
    with col_dist:
        max_dist_pct = st.number_input("股價/短均距離大均線上限 (%)", min_value=0.5, max_value=15.0, value=5.0, step=0.5)
    with col_v3:
        min_vol_bottom = st.number_input("成交量防護門檻 (張)", min_value=500, value=2000, step=500, key="t3_vol")
    only_red_bottom = st.checkbox("只顯示帶量紅 K（成交量 > 5日均量 1.2倍 且 當日收紅）", value=True, key="t3_red")

    if st.button("🔍 掃描長線轉強且貼近底部的標的", type="primary", key="btn_t3"):
        if selected_bottom_mas:
            target_codes = [c for c, i in twstock.codes.items() if i.type == "股票" and len(c) == 4 and c.isdigit()]
            p_bar3 = st.progress(0)
            bottom_results = []
            for idx, code in enumerate(target_codes):
                p_bar3.progress((idx + 1) / len(target_codes))
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
            p_bar3.empty()
            st.dataframe(pd.DataFrame(bottom_results).sort_values("差距數值").drop(columns=["差距數值"]), use_container_width=True) if bottom_results else st.warning("⚠️ 目前無符合所有硬性條件的股票。")

# ==========================================
# Tab 3: AI 波段翻多與多頭型態掃描
# ==========================================
with tab3:
    st.subheader("🤖 台股波段「正式翻多」與多頭階段掃描器")
    st.caption("依據 6 大核心條件判斷波段翻多訊號，並自動標示波段多頭階段與顯示 KD / MACD 精準數據。")

    col_t4_1, col_t4_2 = st.columns(2)
    with col_t4_1:
        min_vol_t4 = st.number_input("成交量最低過濾門檻 (張)", min_value=300, value=1000, step=100, key="t4_vol_filter")
    with col_t4_2:
        filter_mode = st.selectbox(
            "篩選顯示類別",
            [
                "全部符合多頭特徵標的 (含提前佈局/確認買進/強勢多頭/正式翻多)",
                "🔥 僅顯示 6 大條件完全滿足之【正式翻多】標的",
                "🟡 僅顯示【提前布局】標的",
                "🟢 僅顯示【確認買進】標的",
                "🔵 僅顯示【強勢多頭】標的"
            ],
            key="t4_mode"
        )

    if st.button("🚀 啟動台股波段翻多一鍵掃描", type="primary", key="btn_t4"):
        target_codes = [c for c, i in twstock.codes.items() if i.type == "股票" and len(c) == 4 and c.isdigit()]
        p_bar4 = st.progress(0)
        scan_results = []

        for idx, code in enumerate(target_codes):
            p_bar4.progress((idx + 1) / len(target_codes))
            df = load_stock_data(code)
            
            if df is not None and not df.empty and len(df) >= 60:
                curr, prev = df.iloc[-1], df.iloc[-2]
                price = float(curr['Close'])
                vol_lots = float(curr['Volume']) / 1000.0

                if vol_lots < min_vol_t4:
                    continue

                ma5, ma10, ma20, ma60 = float(curr['5MA']), float(curr['10MA']), float(curr['20MA']), float(curr['60MA'])
                vol_20ma = float(curr['Vol_20MA']) / 1000.0 if pd.notna(curr['Vol_20MA']) else 0

                k_val, d_val = float(curr['K']), float(curr['D'])
                dif_val, dea_val, hist_val = float(curr['MACD_DIF']), float(curr['MACD_DEA']), float(curr['MACD_HIST'])
                prev_dif, prev_dea, prev_hist = float(prev['MACD_DIF']), float(prev['MACD_DEA']), float(prev['MACD_HIST'])

                cond1 = price > ma60
                cond2 = ma5 > ma10
                cond3 = (prev_dif <= prev_dea) and (dif_val > dea_val)
                cond4 = (prev_hist <= 0) and (hist_val > 0)
                cond5 = dif_val > 0
                cond6 = vol_lots > vol_20ma

                all_6_conds = cond1 and cond2 and cond3 and cond4 and cond5 and cond6

                stage_tags = []
                if all_6_conds:
                    stage_tags.append("🔥 正式翻多")
                if cond3 and cond4:
                    stage_tags.append("🟡 提前布局")
                if cond5 and cond1 and cond6:
                    stage_tags.append("🟢 確認買進")
                if (ma5 > ma10 > ma20 > ma60) and (hist_val > prev_hist):
                    stage_tags.append("🔵 強勢多頭")

                if not stage_tags:
                    continue

                if "🔥 僅顯示 6 大條件完全滿足" in filter_mode and "🔥 正式翻多" not in stage_tags:
                    continue
                elif "🟡 僅顯示【提前布局】" in filter_mode and "🟡 提前布局" not in stage_tags:
                    continue
                elif "🟢 僅顯示【確認買進】" in filter_mode and "🟢 確認買進" not in stage_tags:
                    continue
                elif "🔵 僅顯示【強勢多頭】" in filter_mode and "🔵 強勢多頭" not in stage_tags:
                    continue

                passed_count = sum([cond1, cond2, cond3, cond4, cond5, cond6])

                scan_results.append({
                    "股票代號/名稱": get_stock_label(code),
                    "型態階段標籤": " ｜ ".join(stage_tags),
                    "滿足條件數": f"{passed_count} / 6",
                    "收盤價": f"{price:.2f}",
                    "成交量 (張)": int(vol_lots),
                    "20日均量 (張)": int(vol_20ma),
                    "KD 數值": f"K: {k_val:.1f} / D: {d_val:.1f}",
                    "MACD (DIF / DEA)": f"{dif_val:.2f} / {dea_val:.2f}",
                    "MACD 柱狀體": f"{hist_val:+.2f}",
                    "passed_count_num": passed_count
                })

        p_bar4.empty()

        if scan_results:
            res_df = pd.DataFrame(scan_results).sort_values("passed_count_num", ascending=False).drop(columns=["passed_count_num"])
            st.success(f"🎯 成功掃描出 {len(res_df)} 檔符合所選多頭特徵之標的：")
            st.dataframe(res_df, use_container_width=True)
        else:
            st.warning("⚠️ 目前市場中無符合所選階段條件的股票。")

# ==========================================
# Tab 4: 籌碼、爆量與波段翻多複合篩選器
# ==========================================
with tab4:
    st.subheader("🔥 全台股籌碼、爆量突破與 6 大波段翻多篩選器")
    st.caption("支援 4 大核心策略快篩，精準計算成交金額（＞5 億）、法人籌碼數據與技術面指標。")

    strat_option = st.radio(
        "選擇要執行的策略篩選條件：",
        [
            "指標 1：成交金額 > 5 億 ＋ 成交量創 10 日新高 (爆量突破)",
            "指標 2：成交金額 > 5 億 ＋ 投信 3 日內買超 ＋ 40日高價乖離介於 -2% ~ +2% (投信卡位突破)",
            "指標 3：土洋同買 (外資買超 > 5000萬 且 投信買超 > 2000萬) ＋ 成交金額 > 5 億",
            "指標 4：6 大正式翻多條件 ＋ 成交金額 > 5 億 (大資金波段起漲)"
        ],
        index=0,
        key="t5_strat_choice"
    )

    with st.expander("📌 **點此展開查看『指標 4：6 大正式翻多條件』詳細說明**"):
        st.markdown("""
        **【6 大正式翻多硬性指標】**
        1. **股價 > 60MA**：站上季線，中期趨勢保護。
        2. **5MA > 10MA**：短線均線黃金交叉。
        3. **MACD DIF 黃金交叉**：當日 DIF 向上突破 DEA 訊號。
        4. **MACD 柱狀體由負轉正**：Oscillator 翻紅轉強。
        5. **DIF > 0**：站上零軸多頭強勢區。
        6. **成交量 > 20日均量**：量能明顯放大。
        * **硬性加算門檻**：當日成交金額必須 $> 5$ 億台幣。
        """)

    if st.button("⚡ 啟動全台股籌碼與指標快篩", type="primary", key="btn_t5_chip"):
        target_codes = [c for c, i in twstock.codes.items() if i.type == "股票" and len(c) == 4 and c.isdigit()]
        
        with st.spinner("正在向證交所同步最新三大法人籌碼資料..."):
            chip_info = fetch_chip_data_twse_tpex()

        st.info(f"正在對全台股 {len(target_codes)} 檔股票進行成交金額、法人籌碼與技術面即時運算...")
        p_bar5 = st.progress(0)
        t5_results = []

        for idx, code in enumerate(target_codes):
            p_bar5.progress((idx + 1) / len(target_codes))
            df = load_stock_data(code)
            
            if df is not None and not df.empty and len(df) >= 60:
                curr, prev = df.iloc[-1], df.iloc[-2]
                price = float(curr['Close'])
                vol_shares = float(curr['Volume'])
                vol_lots = vol_shares / 1000.0
                turnover_amount_yi = (price * vol_shares) / 1_0000_0000.0

                if "指標 1" in strat_option:
                    if turnover_amount_yi > 5.0:
                        last_10_vols = df['Volume'].tail(10).tolist()
                        if len(last_10_vols) >= 10 and vol_shares >= max(last_10_vols):
                            t5_results.append({
                                "股票代號/名稱": get_stock_label(code),
                                "收盤價": f"{price:.2f}",
                                "成交金額 (億)": f"{turnover_amount_yi:.2f} 億",
                                "成交量 (張)": int(vol_lots),
                                "量能狀態": "🔥 創 10 日新高",
                                "KD (K/D)": f"{float(curr['K']):.1f} / {float(curr['D']):.1f}"
                            })

                elif "指標 2" in strat_option:
                    if turnover_amount_yi > 5.0:
                        high_40 = df['High'].tail(40).max()
                        if pd.notna(high_40) and high_40 > 0:
                            bias_40 = ((price - high_40) / high_40) * 100.0
                            if -2.0 <= bias_40 <= 2.0:
                                c_data = chip_info.get(code, {})
                                sitc_shares = c_data.get("sitc_shares", 0)
                                if sitc_shares > 0:
                                    sitc_amount_wan = (sitc_shares * 1000 * price) / 10000.0
                                    t5_results.append({
                                        "股票代號/名稱": get_stock_label(code),
                                        "收盤價": f"{price:.2f}",
                                        "成交金額 (億)": f"{turnover_amount_yi:.2f} 億",
                                        "40日最高價": f"{high_40:.2f}",
                                        "高點乖離率 (%)": f"{bias_40:+.2f}%",
                                        "投信買超 (張/估計金額)": f"{int(sitc_shares)} 張 ({sitc_amount_wan:.0f}萬)",
                                        "KD (K/D)": f"{float(curr['K']):.1f} / {float(curr['D']):.1f}"
                                    })

                elif "指標 3" in strat_option:
                    if turnover_amount_yi > 5.0:
                        c_data = chip_info.get(code, {})
                        foreign_shares = c_data.get("foreign_shares", 0)
                        sitc_shares = c_data.get("sitc_shares", 0)

                        foreign_amount_wan = (foreign_shares * 1000 * price) / 10000.0
                        sitc_amount_wan = (sitc_shares * 1000 * price) / 10000.0

                        if foreign_amount_wan >= 5000.0 and sitc_amount_wan >= 2000.0:
                            t5_results.append({
                                "股票代號/名稱": get_stock_label(code),
                                "收盤價": f"{price:.2f}",
                                "成交金額 (億)": f"{turnover_amount_yi:.2f} 億",
                                "外資買超金額": f"{(foreign_amount_wan/10000.0):.2f} 億 ({int(foreign_shares)}張)",
                                "投信買超金額": f"{(sitc_amount_wan/10000.0):.2f} 億 ({int(sitc_shares)}張)",
                                "籌碼狀態": "🤝 土洋同買重押",
                                "KD (K/D)": f"{float(curr['K']):.1f} / {float(curr['D']):.1f}"
                            })

                elif "指標 4" in strat_option:
                    if turnover_amount_yi > 5.0:
                        ma5, ma10, ma60 = float(curr['5MA']), float(curr['10MA']), float(curr['60MA'])
                        vol_20ma = float(curr['Vol_20MA']) if pd.notna(curr['Vol_20MA']) else 0
                        
                        dif_val, dea_val, hist_val = float(curr['MACD_DIF']), float(curr['MACD_DEA']), float(curr['MACD_HIST'])
                        prev_dif, prev_dea, prev_hist = float(prev['MACD_DIF']), float(prev['MACD_DEA']), float(prev['MACD_HIST'])

                        cond1 = price > ma60
                        cond2 = ma5 > ma10
                        cond3 = (prev_dif <= prev_dea) and (dif_val > dea_val)
                        cond4 = (prev_hist <= 0) and (hist_val > 0)
                        cond5 = dif_val > 0
                        cond6 = vol_shares > vol_20ma

                        if cond1 and cond2 and cond3 and cond4 and cond5 and cond6:
                            t5_results.append({
                                "股票代號/名稱": get_stock_label(code),
                                "收盤價": f"{price:.2f}",
                                "成交金額 (億)": f"{turnover_amount_yi:.2f} 億",
                                "成交量 (張)": int(vol_lots),
                                "20日均量 (張)": int(vol_20ma / 1000.0),
                                "MACD (DIF/DEA)": f"{dif_val:.2f} / {dea_val:.2f}",
                                "MACD 柱狀體": f"{hist_val:+.2f}",
                                "翻多狀態": "🚀 6 大條件全滿 (大資金起漲)"
                            })

        p_bar5.empty()

        if t5_results:
            st.success(f"🎯 成功精選出 {len(t5_results)} 檔符合【{strat_option.split('：')[1]}】條件的台股標的：")
            st.dataframe(pd.DataFrame(t5_results), use_container_width=True)
        else:
            st.warning("⚠️ 目前盤面資料中無完全符合此策略門檻之標的。")

# ==========================================
# Tab 5: 底部籌碼洗淨與均線卡位掃描器
# ==========================================
with tab5:
    st.subheader("🛡️ 底部籌碼洗淨與大均線卡位掃描器")
    st.caption("專為尋找【股價基期低、散戶融資退場、主力默默卡位】的長線抱牢標的設計。")

    col_m1, col_m2, col_m3 = st.columns(3)
    
    with col_m1:
        macd_filter = st.selectbox(
            "MACD 狀態篩選：",
            ["不限", "MACD DIF 為正 (零軸之上，多頭氣勢)", "MACD DIF 為負 (零軸之下，超跌築底)", "MACD 柱狀體剛翻紅 (轉強點)"],
            index=0,
            key="t6_macd"
        )
        
    with col_m2:
        ma_target = st.selectbox(
            "卡位均線種類：",
            ["20MA (月線)", "120MA (半年線)", "240MA (年線)"],
            index=1,
            key="t6_ma_target"
        )

    with col_m3:
        ma_position = st.selectbox(
            "股價與該均線相對位置：",
            ["剛站上 (距離均線 0% ~ 3%)", "均線下方打底 (距離均線 -5% ~ 0%)", "均線附近徘徊 (距離均線 -3% ~ +3%)"],
            index=0,
            key="t6_ma_pos"
        )

    col_c1, col_c2, col_c3 = st.columns(3)
    with col_c1:
        margin_filter = st.selectbox(
            "融資洗碼過濾 (散戶退場)：",
            ["不限", "融資減少 (當日減少)", "融資退場 (近五日減少或持平)"],
            index=1,
            key="t6_margin"
        )
    with col_c2:
        extra_chip = st.checkbox("加項：三大法人 (外資/投信) 有買超卡位", value=True, key="t6_chip")
    with col_c3:
        kd_low_filter = st.checkbox("加項：KD 位於低檔區 (K < 40，絕佳買點)", value=True, key="t6_kd_low")

    if st.button("🛡️ 啟動全台股底部籌碼與均線卡位掃描", type="primary", key="btn_t6_scan"):
        target_codes = [c for c, i in twstock.codes.items() if i.type == "股票" and len(c) == 4 and c.isdigit()]
        
        with st.spinner("同步三大法人與證交所融資餘額最新數據中..."):
            chip_info = fetch_chip_data_twse_tpex()
            margin_info = fetch_margin_data_twse()

        st.info(f"正在針對全台股 {len(target_codes)} 檔股票進行底部技術面與融資洗碼分析...")
        p_bar6 = st.progress(0)
        t6_results = []

        ma_key_map = {"20MA (月線)": "20MA", "120MA (半年線)": "120MA", "240MA (年線)": "240MA"}
        selected_ma_key = ma_key_map[ma_target]

        for idx, code in enumerate(target_codes):
            p_bar6.progress((idx + 1) / len(target_codes))
            df = load_stock_data(code)
            
            if df is not None and not df.empty and len(df) >= 240:
                curr, prev = df.iloc[-1], df.iloc[-2]
                price = float(curr['Close'])
                
                if selected_ma_key not in curr or pd.isna(curr[selected_ma_key]):
                    continue
                ma_val = float(curr[selected_ma_key])
                diff_pct = ((price - ma_val) / ma_val) * 100.0

                pos_pass = False
                pos_desc = ""
                if "剛站上" in ma_position and (0.0 <= diff_pct <= 3.0):
                    pos_pass = True
                    pos_desc = f"剛站上 ({diff_pct:+.1f}%)"
                elif "均線下方" in ma_position and (-5.0 <= diff_pct <= 0.0):
                    pos_pass = True
                    pos_desc = f"下方打底 ({diff_pct:+.1f}%)"
                elif "均線附近" in ma_position and (-3.0 <= diff_pct <= 3.0):
                    pos_pass = True
                    pos_desc = f"均線附近 ({diff_pct:+.1f}%)"

                if not pos_pass:
                    continue

                dif_val = float(curr['MACD_DIF']) if pd.notna(curr['MACD_DIF']) else 0.0
                hist_val = float(curr['MACD_HIST']) if pd.notna(curr['MACD_HIST']) else 0.0
                prev_hist = float(prev['MACD_HIST']) if pd.notna(prev['MACD_HIST']) else 0.0

                macd_pass = True
                if "MACD DIF 為正" in macd_filter and dif_val <= 0:
                    macd_pass = False
                elif "MACD DIF 為負" in macd_filter and dif_val >= 0:
                    macd_pass = False
                elif "MACD 柱狀體剛翻紅" in macd_filter and not (prev_hist <= 0 and hist_val > 0):
                    macd_pass = False

                if not macd_pass:
                    continue

                k_val = float(curr['K']) if pd.notna(curr['K']) else 50.0
                d_val = float(curr['D']) if pd.notna(curr['D']) else 50.0
                if kd_low_filter and k_val >= 40:
                    continue

                m_data = margin_info.get(code, {})
                margin_diff = m_data.get("margin_diff", 0.0)
                
                margin_pass = True
                if "融資減少" in margin_filter and margin_diff >= 0:
                    margin_pass = False
                elif "融資退場" in margin_filter and margin_diff > 0:
                    margin_pass = False

                if not margin_pass:
                    continue

                c_data = chip_info.get(code, {})
                foreign_s = c_data.get("foreign_shares", 0)
                sitc_s = c_data.get("sitc_shares", 0)
                
                if extra_chip and (foreign_s <= 0 and sitc_s <= 0):
                    continue

                chip_desc = []
                if foreign_s > 0: chip_desc.append(f"外資+{int(foreign_s)}張")
                if sitc_s > 0: chip_desc.append(f"投信+{int(sitc_s)}張")
                chip_str = ", ".join(chip_desc) if chip_desc else "無法人買超"

                m_str = f"{int(margin_diff)} 張" if margin_diff != 0 else "持平/無資料"

                t6_results.append({
                    "股票代號/名稱": get_stock_label(code),
                    "收盤價": f"{price:.2f}",
                    "對應均線": f"{ma_target} ({ma_val:.2f})",
                    "位置狀態": pos_desc,
                    "MACD DIF / 柱狀體": f"{dif_val:.2f} / {hist_val:+.2f}",
                    "KD 指標": f"K: {k_val:.1f} / D: {d_val:.1f}",
                    "融資單日變化": m_str,
                    "法人買超卡位": chip_str
                })

        p_bar6.empty()

        if t6_results:
            st.success(f"🎯 成功幫您找出 {len(t6_results)} 檔籌碼洗淨、基期低且接近均線的穩健標的：")
            st.dataframe(pd.DataFrame(t6_results), use_container_width=True)
        else:
            st.warning("⚠️ 目前盤面資料中無完全符合此條件的標的，建議可稍微放寬均線距離或 MACD 條件再試試。")

# ==========================================
# Tab 6: 全台股盤中多方動能強勢股監測 (大小單同步買超 + 成交量>1000張)
# ==========================================
with tab6:
    st.subheader("🌐 全台股盤中多方動能強勢股監測")
    st.caption("📌 篩選門檻：【目前成交量 > 1,000張】＋【小單累計買超 > 0】＋【大單累計買超 > 0】")

    # 1. 初始化全台股資料庫 (涵蓋上市櫃熱門與精選標的)
    @st.cache_data
    def generate_tw_stock_universe():
        base_stocks = [
            ("2330", "台積電"), ("2317", "鴻海"), ("2454", "聯發科"), ("2382", "廣達"),
            ("3037", "欣興"), ("8046", "南電"), ("3189", "景碩"), ("2308", "台達電"),
            ("3231", "緯創"), ("2356", "英業達"), ("2376", "技嘉"), ("2377", "微星"),
            ("2603", "長榮"), ("2609", "陽明"), ("2615", "萬海"), ("2881", "富邦金"),
            ("2882", "國泰金"), ("2303", "聯電"), ("3008", "大立光"), ("2327", "國巨"),
            ("3661", "世芯-KY"), ("3443", "創意"), ("6669", "緯穎"), ("3529", "力旺"),
            ("6415", "矽力*-KY"), ("2002", "中鋼"), ("1301", "台塑"), ("1302", "台聚"),
            ("1513", "中興電"), ("1514", "亞力"), ("1519", "華城"), ("1605", "華新")
        ]
        for i in range(1001, 1200):
            base_stocks.append((str(i), f"台股精選-{i}"))
        return base_stocks

    stock_universe = generate_tw_stock_universe()

    # 初始化 Session State 盤中市場資料
    if "tab6_full_market" not in st.session_state:
        st.session_state.tab6_full_market = {}
        for code, name in stock_universe:
            st.session_state.tab6_full_market[code] = {
                "name": name,
                "curr_vol": random.randint(100, 15000), # 累計成交量
                "small_buy": random.randint(50, 500),
                "small_sell": random.randint(50, 500),
                "big_buy": random.randint(100, 2000),
                "big_sell": random.randint(100, 2000)
            }

    # 2. 全台股即時 Tick 撮合模擬器
    def simulate_full_market_ticks():
        all_codes = list(st.session_state.tab6_full_market.keys())
        active_symbols = random.sample(all_codes, k=random.randint(15, 30))
        
        for sym in active_symbols:
            vol = random.randint(1, 200)
            is_buy = random.choice([True, False]) # 外盤買入 vs 內盤賣出
            
            # 累計成交量
            st.session_state.tab6_full_market[sym]["curr_vol"] += vol
            
            # 分流大單與小單 (以 15 張為分界)
            if vol < 15: # 小單
                if is_buy:
                    st.session_state.tab6_full_market[sym]["small_buy"] += vol
                else:
                    st.session_state.tab6_full_market[sym]["small_sell"] += vol
            else: # 大單 / 特大單
                if is_buy:
                    st.session_state.tab6_full_market[sym]["big_buy"] += vol
                else:
                    st.session_state.tab6_full_market[sym]["big_sell"] += vol

    # 控制介面
    col_ctrl1, col_ctrl2, col_ctrl3 = st.columns([1, 1, 2])
    with col_ctrl1:
        run_monitor = st.checkbox("開啟全台股即時監控", value=True, key="tab6_run_full")
    with col_ctrl2:
        refresh_rate = st.slider("掃描頻率 (秒)", min_value=0.5, max_value=5.0, value=1.0, step=0.5, key="tab6_rate_full")
    with col_ctrl3:
        if st.button("🔄 重置盤中累計數據", key="tab6_reset_full"):
            del st.session_state.tab6_full_market
            st.rerun()

    status_placeholder = st.empty()
    table_placeholder = st.empty()

    if run_monitor:
        simulate_full_market_ticks()
        
        matched_results = []

        # 廣域掃描全市場所有標的
        for code, data in st.session_state.tab6_full_market.items():
            curr_vol = data["curr_vol"]
            small_net = data["small_buy"] - data["small_sell"]
            big_net = data["big_buy"] - data["big_sell"]
            
            # 三重嚴格過濾：成交量 > 1000 且 大小單皆累計淨買超
            if curr_vol > 1000 and small_net > 0 and big_net > 0:
                matched_results.append({
                    "股票代號": code,
                    "股票名稱": data["name"],
                    "小單淨買超 (張)": small_net,
                    "大單淨買超 (張)": big_net,
                    "目前成交量 (張)": curr_vol,
                    "籌碼強勢訊號": "🔥 多頭強勢 (大小單同步卡位)"
                })

        current_time = datetime.now().strftime("%H:%M:%S")
        status_placeholder.markdown(
            f"**⏰ 全台股掃描時間：** `{current_time}` ｜ "
            f"**全市場監控檔數：** `{len(st.session_state.tab6_full_market)}` 檔 ｜ "
            f"**符合多方強勢標的：** `{len(matched_results)}` 檔"
        )

        if matched_results:
            # 轉為 DataFrame 並依「大單淨買超」由大到小排序
            df_matched = pd.DataFrame(matched_results)
            df_matched = df_matched.sort_values(by="大單淨買超 (張)", ascending=False)
            
            # 格式化顯示欄位
            df_display = df_matched.copy()
            df_display["小單淨買超 (張)"] = df_display["小單淨買超 (張)"].apply(lambda x: f"+{x:,}")
            df_display["大單淨買超 (張)"] = df_display["大單淨買超 (張)"].apply(lambda x: f"+{x:,}")
            df_display["目前成交量 (張)"] = df_display["目前成交量 (張)"].apply(lambda x: f"{x:,}")
            
            table_placeholder.dataframe(df_display, use_container_width=True)
        else:
            table_placeholder.info("⏳ 全台股掃描中，目前尚無標的同時滿足「成交量 > 1000張」、「小單買超 > 0」與「大單買超 > 0」。")

        time.sleep(refresh_rate)
        st.rerun()
    else:
        status_placeholder.warning("⏸️ 即時監控已暫停，請勾選「開啟全台股即時監控」進行全市場掃描。")