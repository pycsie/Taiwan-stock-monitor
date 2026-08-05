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
st.set_page_config(page_title="台股均線與短線轉強儀表板", page_icon="📈", layout="wide")

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

# --- 輔助函式：取得股票名稱 ---
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

# --- 計算技術指標 (均線、KD、成交量) ---
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

# --- 抓取個股 K 線資料 ---
@st.cache_data(ttl=600)
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

# --- 轉強訊號判斷邏輯 ---
def detect_turn_around_signals(df):
    if len(df) < 25:
        return []

    signals = []
    curr = df.iloc[-1]   # 今天 / 最新一筆
    prev = df.iloc[-2]   # 昨天
    prev2 = df.iloc[-3]  # 前天

    price = float(curr['Close'])
    vol = float(curr['Volume'])
    vol_5ma = float(curr['Vol_5MA']) if pd.notna(curr['Vol_5MA']) else 0

    # 1. 均線扣低轉上彎 (前天>=昨天, 今天>昨天, 且價格在均線上)
    ma_targets = [('5MA', '5日線'), ('10MA', '10日線'), ('20MA', '月線')]
    for ma_key, ma_name in ma_targets:
        if pd.notna(curr[ma_key]) and pd.notna(prev[ma_key]) and pd.notna(prev2[ma_key]):
            c_ma = float(curr[ma_key])
            p_ma = float(prev[ma_key])
            p2_ma = float(prev2[ma_key])

            if p_ma <= p2_ma and c_ma > p_ma and price >= c_ma:
                signals.append(f"📈 {ma_name}轉上彎")

    # 2. KD 低檔/中軸金叉 (K <= 65 避開過熱區)
    if pd.notna(curr['K']) and pd.notna(curr['D']) and pd.notna(prev['K']) and pd.notna(prev['D']):
        c_k, c_d = float(curr['K']), float(curr['D'])
        p_k, p_d = float(prev['K']), float(prev['D'])
        if p_k <= p_d and c_k > c_d and c_k <= 65:
            signals.append(f"🔥 KD黃金交叉(K:{c_k:.1f})")

    # 3. 帶量攻擊 (當天量 > 5日均量 1.4 倍)
    if vol_5ma > 0 and vol >= (vol_5ma * 1.4):
        signals.append(f"⚡ 帶量攻擊({(vol/vol_5ma):.1f}倍量)")

    return signals

# --- Google Sheets 連線 ---
def init_gspread():
    if "gcp_service_account" in st.secrets and "spreadsheet_key" in st.secrets:
        try:
            creds_dict = json.loads(st.secrets["gcp_service_account"])
            scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
            credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
            gc = gspread.authorize(credentials)
            sh = gc.open_by_key(st.secrets["spreadsheet_key"])
            return sh.worksheet("Watchlist")
        except Exception as e:
            st.sidebar.error(f"Google Sheets 連線失敗: {e}")
    return None

# --- 側邊欄與選單 ---
st.sidebar.title("📌 功能選單")
app_mode = st.sidebar.radio("選擇功能", ["個股均線監控", "🚀 全台股短線轉強掃描器"])

ws = init_gspread()

# ==========================================
# 模式一：個股均線監控 (原本的功能)
# ==========================================
if app_mode == "個股均線監控":
    st.title("📈 個股均線距離監控")

    # 讀取/更新門檻
    alert_threshold = 2.0
    if ws:
        try:
            val = ws.acell('D2').value
            if val: alert_threshold = float(str(val).replace("%", "").strip())
        except Exception:
            pass

    new_threshold = st.sidebar.number_input(
        "均線靠近門檻 (%)", min_value=0.5, max_value=10.0, value=alert_threshold, step=0.5
    )
    if new_threshold != alert_threshold and ws:
        try:
            ws.update_acell('D1', 'AlertThreshold')
            ws.update_acell('D2', new_threshold)
            st.sidebar.success(f"已更新門檻為 {new_threshold}%")
        except Exception as e:
            st.sidebar.error(f"更新失敗: {e}")

    # 從 Google Sheets 抓取 Watchlist
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

    if watchlist:
        results = []
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
                            "現價": price,
                            "目標均線": MA_LABELS[ma_key],
                            "均線價格": round(ma_val, 2),
                            "差距 %": round(diff, 2),
                            "狀態": "🚨 接近中" if is_near else "正常"
                        })

        if results:
            res_df = pd.DataFrame(results)
            st.dataframe(res_df.style.highlight_between(subset=["差距 %"], left=-new_threshold, right=new_threshold, color="#ff4b4b22"))
    else:
        st.info("目前 Google Sheets Watchlist 中無股票資料。")

# ==========================================
# 模式二：🚀 全台股短線轉強掃描器 (新功能)
# ==========================================
elif app_mode == "🚀 全台股短線轉強掃描器":
    st.title("🚀 全台股短線轉強/均線上彎 掃描器")
    st.caption("自動掃描台股成交量爆發與均線扣低轉上彎標的（自動過濾低流動性股票）")

    # 濾網條件設定
    col1, col2 = st.columns(2)
    with col1:
        min_vol = st.number_input("最低成交量門檻 (張)", min_value=500, value=2000, step=500, help="1張 = 1,000股")
    with col2:
        scan_scope = st.selectbox("掃描範圍", ["熱門大型股 (前100大)", "全台股上市上櫃 (需較長時間)"])

    if st.button("🔍 開始掃描轉強股票", type="primary"):
        # 1. 取得股票清單
        stock_list = []
        if scan_scope == "熱門大型股 (前100大)" or not HAS_TWSTOCK:
            # 預設熱門股清單
            stock_list = list(BUILTIN_STOCKS.keys()) + [
                "2303", "2603", "2609", "2615", "3231", "2356", "6669", "3037",
                "2379", "3034", "2337", "2408", "2344", "2301", "2324", "2353"
            ]
        else:
            # 使用 twstock 抓取全台股普通股
            with st.spinner("讀取全台股股票代碼中..."):
                for code, info in twstock.codes.items():
                    # 篩選上市與上櫃的普通股 (代碼4碼數字)
                    if info.type == "股票" and len(code) == 4 and code.isdigit():
                        stock_list.append(code)

        st.info(f"準備掃描 {len(stock_list)} 支股票，請稍候...")

        progress_bar = st.progress(0)
        scan_results = []

        # 2. 逐一掃描計算
        for idx, code in enumerate(stock_list):
            progress_bar.progress((idx + 1) / len(stock_list))
            df = load_stock_data(code)

            if df is not None and not df.empty and len(df) >= 20:
                curr = df.iloc[-1]
                vol_shares = float(curr['Volume'])
                vol_lots = vol_shares / 1000.0  # 轉換為「張數」

                # 門檻：成交量 >= 設定張數
                if vol_lots >= min_vol:
                    signals = detect_turn_around_signals(df)
                    if signals:
                        s_label = get_stock_label(code)
                        scan_results.append({
                            "股票": s_label,
                            "收盤價": float(curr['Close']),
                            "成交量 (張)": int(vol_lots),
                            "KD(K值)": round(float(curr['K']), 1),
                            "轉強訊號": " | ".join(signals)
                        })

        progress_bar.empty()

        # 3. 顯示結果表格
        if scan_results:
            result_df = pd.DataFrame(scan_results)
            st.success(f"🎉 掃描完成！共找到 {len(result_df)} 支符合條件的轉強股票：")
            st.dataframe(result_df, use_container_width=True)
        else:
            st.warning("沒有找到符合成交量與轉強條件的股票，嘗試降低成交量門檻再試一次。")