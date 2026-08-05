import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import json
import gspread
from google.oauth2.service_account import Credentials

try:
    import twstock
    HAS_TWSTOCK = True
except ImportError:
    HAS_TWSTOCK = False

# --- 頁面配置 ---
st.set_page_config(page_title="台股均線監控與選股儀表板", page_icon="📈", layout="wide")

# --- 設定常數 ---
ALL_MAS = ['5MA', '10MA', '20MA', '60MA', '120MA', '240MA']
MA_LABELS = {
    '5MA': '5日線', '10MA': '10日線', '20MA': '月線(20MA)',
    '60MA': '季線(60MA)', '120MA': '半年線(120MA)', '240MA': '年線(240MA)'
}

BUILTIN_STOCKS = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電",
    "2382": "廣達", "2881": "富邦金", "2882": "國泰金", "2412": "中華電",
    "2891": "中信金", "3711": "日月光投控", "0050": "元大台灣50", "0056": "元大高股息"
}

# --- 1. Google Sheets 連線初始化 ---
@st.cache_resource
def get_gspread_client():
    if "gcp_service_account" in st.secrets:
        try:
            creds_dict = json.loads(st.secrets["gcp_service_account"])
            scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
            credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
            return gspread.authorize(credentials)
        except Exception as e:
            st.error(f"GCP 認證失敗: {e}")
    return None

def get_worksheet():
    gc = get_gspread_client()
    if gc and "spreadsheet_key" in st.secrets:
        try:
            sh = gc.open_by_key(st.secrets["spreadsheet_key"])
            return sh.worksheet("Watchlist")
        except Exception as e:
            st.error(f"無法開啟 Watchlist 工作表: {e}")
    return None

# --- 2. 股票名稱查詢 ---
def get_stock_name(code):
    clean_code = str(code).replace(".TW", "").replace(".TWO", "").strip()
    if clean_code in BUILTIN_STOCKS:
        return BUILTIN_STOCKS[clean_code]
    if HAS_TWSTOCK:
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

# --- 3. 技術指標計算 ---
def calculate_indicators(df):
    for ma in [5, 10, 20, 60, 120, 240]:
        df[f'{ma}MA'] = df['Close'].rolling(ma).mean()
    
    df['Vol_5MA'] = df['Volume'].rolling(5).mean()

    # 計算 KD (9, 3, 3)
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
            data = yf.download(symbol, period="6m", interval="1d", progress=False)
            if data is None or data.empty: continue
            if isinstance(data.columns, pd.MultiIndex): 
                data.columns = data.columns.get_level_values(0)
            if 'Close' in data.columns: 
                data = data.dropna(subset=['Close'])
            if len(data) >= 20: 
                return calculate_indicators(data)
        except Exception:
            continue
    return None

# --- 4. 側邊欄切換主功能 ---
st.sidebar.title("🔍 選單與設定")
page = st.sidebar.radio("切換頁面", ["最愛清單監控 (原本功能)", "🚀 全台股短線轉強掃描"])

ws = get_worksheet()

# ==========================================
# 頁面 1：最愛清單監控 (完整還原原本的功能與視覺)
# ==========================================
if page == "最愛清單監控 (原本功能)":
    st.title("📌 我的最愛關注清單 - 均線監控")

    # 1. 讀取與設定全域警示門檻 (%)
    alert_threshold = 2.0
    if ws:
        try:
            val = ws.acell('D2').value
            if val: alert_threshold = float(str(val).replace("%", "").strip())
        except Exception:
            pass

    st.sidebar.subheader("⚙️ 警示門檻設定")
    new_threshold = st.sidebar.number_input(
        "均線接近門檻 (%)", min_value=0.5, max_value=10.0, value=alert_threshold, step=0.5,
        help="當股價與設定均線差距小於此 % 數時觸發警示"
    )

    if new_threshold != alert_threshold and ws:
        try:
            ws.update_acell('D1', 'AlertThreshold')
            ws.update_acell('D2', new_threshold)
            st.sidebar.success(f"已更新門檻為 {new_threshold}%")
        except Exception as e:
            st.sidebar.error(f"更新門檻失敗: {e}")

    # 2. 讀取 Google Sheets 的最愛清單 (Watchlist)
    watchlist = []
    ma_settings = {}
    if ws:
        records = ws.get_all_records()
        for row in records:
            stock = str(row.get('Stock', '')).split('.')[0].strip()
            if stock and stock.isalnum():
                watchlist.append(stock)
                mas_str = str(row.get('MAs', ''))
                mas_list = [m.strip() for m in mas_str.split(",") if m.strip() in ALL_MAS]
                ma_settings[stock] = mas_list if mas_list else ALL_MAS.copy()

    # 3. 渲染原本的監控表格
    if watchlist:
        st.write(f"目前監控中個股數量：**{len(watchlist)}** 檔 | 當前觸發門檻：**{new_threshold}%**")
        results = []

        with st.spinner("正在讀取最新數據與計算均線..."):
            for code in watchlist:
                df = load_stock_data(code)
                if df is not None and not df.empty:
                    last_row = df.iloc[-1]
                    price = float(last_row['Close'])
                    s_label = get_stock_label(code)
                    target_mas = ma_settings.get(code, ALL_MAS)

                    for ma_key in target_mas:
                        if ma_key in last_row and pd.notna(last_row[ma_key]):
                            ma_val = float(last_row[ma_key])
                            diff = ((price - ma_val) / ma_val) * 100
                            is_near = abs(diff) <= new_threshold
                            
                            results.append({
                                "股票": s_label,
                                "現價": round(price, 2),
                                "目標均線": MA_LABELS[ma_key],
                                "均線價格": round(ma_val, 2),
                                "差距 %": round(diff, 2),
                                "狀態": "🚨 靠近中" if is_near else "正常"
                            })

        if results:
            res_df = pd.DataFrame(results)
            
            # 使用醒目的顏色突顯接近門檻的個股
            def highlight_near(val):
                return 'background-color: #ff4b4b33; font-weight: bold;' if abs(val) <= new_threshold else ''

            st.dataframe(
                res_df.style.applymap(highlight_near, subset=['差距 %']),
                use_container_width=True
            )
    else:
        st.warning("⚠️ 無法取得 Google Sheets 最愛名單，請檢查 Google Sheets Watchlist 是否設定正確。")

# ==========================================
# 頁面 2：🚀 全台股短線轉強掃描 (不干擾原本功能)
# ==========================================
else:
    st.title("🚀 全台股短線轉強/均線上彎 掃描器")
    st.caption("自動篩選『成交量 > 2000張』且『均線扣低轉上彎 / KD低檔金叉』之轉強標的")

    col1, col2 = st.columns(2)
    with col1:
        min_vol = st.number_input("最低成交量門檻 (張)", min_value=500, value=2000, step=500)
    with col2:
        scan_scope = st.selectbox("掃描範圍", ["熱門大型股 (前100大)", "全台股上市上櫃"])

    if st.button("🔍 開始掃描轉強股票", type="primary"):
        stock_list = []
        if scan_scope == "熱門大型股 (前100大)" or not HAS_TWSTOCK:
            stock_list = list(BUILTIN_STOCKS.keys()) + [
                "2303", "2603", "2609", "2615", "3231", "2356", "6669", "3037",
                "2379", "3034", "2337", "2408", "2344", "2301", "2324", "2353"
            ]
        else:
            with st.spinner("載入全台股清單中..."):
                for code, info in twstock.codes.items():
                    if info.type == "股票" and len(code) == 4 and code.isdigit():
                        stock_list.append(code)

        st.info(f"正在掃描 {len(stock_list)} 支股票...")
        progress = st.progress(0)
        scan_results = []

        for idx, code in enumerate(stock_list):
            progress.progress((idx + 1) / len(stock_list))
            df = load_stock_data(code)

            if df is not None and not df.empty and len(df) >= 25:
                curr = df.iloc[-1]
                prev = df.iloc[-2]
                prev2 = df.iloc[-3]

                vol_lots = float(curr['Volume']) / 1000.0  # 轉為張數

                if vol_lots >= min_vol:
                    signals = []
                    price = float(curr['Close'])
                    vol_5ma = float(curr['Vol_5MA']) if pd.notna(curr['Vol_5MA']) else 0

                    # 1. 均線上彎
                    for ma_key, ma_name in [('5MA', '5日線'), ('10MA', '10日線'), ('20MA', '月線')]:
                        if pd.notna(curr[ma_key]) and pd.notna(prev[ma_key]) and pd.notna(prev2[ma_key]):
                            c_ma, p_ma, p2_ma = float(curr[ma_key]), float(prev[ma_key]), float(prev2[ma_key])
                            if p_ma <= p2_ma and c_ma > p_ma and price >= c_ma:
                                signals.append(f"📈 {ma_name}轉上彎")

                    # 2. KD金叉 (K<=65)
                    if pd.notna(curr['K']) and pd.notna(curr['D']) and pd.notna(prev['K']) and pd.notna(prev['D']):
                        c_k, c_d, p_k, p_d = float(curr['K']), float(curr['D']), float(prev['K']), float(prev['D'])
                        if p_k <= p_d and c_k > c_d and c_k <= 65:
                            signals.append(f"🔥 KD金叉(K:{c_k:.1f})")

                    # 3. 帶量
                    if vol_5ma > 0 and (float(curr['Volume']) >= vol_5ma * 1.4):
                        signals.append(f"⚡ 帶量({(float(curr['Volume'])/vol_5ma):.1f}倍)")

                    if signals:
                        scan_results.append({
                            "股票": get_stock_label(code),
                            "收盤價": price,
                            "成交量(張)": int(vol_lots),
                            "K值": round(float(curr['K']), 1),
                            "轉強訊號": " | ".join(signals)
                        })

        progress.empty()

        if scan_results:
            st.success(f"🎉 找到 {len(scan_results)} 支符合成交量與轉強條件的標的：")
            st.dataframe(pd.DataFrame(scan_results), use_container_width=True)
        else:
            st.warning("目前無符合條件的轉強股票。")