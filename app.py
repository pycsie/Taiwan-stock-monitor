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
        st.error(f"⚠️ Google Sheets 連線或讀取失敗，請確認 Secrets 設定！錯誤細節: {e}")
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

# --- 2. 載入最新設定 ---
db_watchlist, db_ma_settings = load_settings_from_gsheets()
st.session_state.watchlist = db_watchlist
st.session_state.ma_settings = db_ma_settings

default_token = st.secrets.get("LINE_CHANNEL_ACCESS_TOKEN", "") if "LINE_CHANNEL_ACCESS_TOKEN" in st.secrets else st.session_state.get("line_token", "")
default_user_id = st.secrets.get("LINE_USER_ID", "") if "LINE_USER_ID" in st.secrets else st.session_state.get("line_user_id", "")

# --- 3. 側邊欄設定 ---
st.sidebar.header("⚙️ 參數設定")

alert_threshold = st.sidebar.slider(
    "提醒觸發門檻（股價距離均線 %）", 
    min_value=0.5, max_value=5.0, value=2.0, step=0.1
)

st.sidebar.markdown("---")
st.sidebar.header("💬 LINE API 密鑰設定")

line_token = st.sidebar.text_input("Channel Access Token", value=default_token, type="password", key="input_token")
line_user_id = st.sidebar.text_input("Your User ID", value=default_user_id, type="password", key="input_user_id")

st.session_state.line_token = line_token
st.session_state.line_user_id = line_user_id

st.sidebar.markdown("---")
if st.sidebar.button("🔄 手動同步 Google Sheets"):
    st.cache_data.clear()
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

# 🔥 核心修復：解決 NaN 與 MultiIndex 問題
@st.cache_data(ttl=300)
def load_stock_data(stock_id):
    clean_id = str(stock_id).replace(".TW", "").replace(".TWO", "").strip()
    
    # 依序嘗試上市 (.TW) 與 上櫃 (.TWO)
    for suffix in [".TW", ".TWO"]:
        symbol = f"{clean_id}{suffix}"
        try:
            data = yf.download(symbol, period="2y", interval="1d", progress=False)
            
            if data is None or data.empty:
                continue
                
            # 1. 處理 yfinance 的雙層欄位格式
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)
                
            # 2. 過濾未開盤或包含 NaN 的歷史資料列，確保 Close 是有效價格
            if 'Close' in data.columns:
                data = data.dropna(subset=['Close'])
                
            if data.empty or len(data) < 5:
                continue
                
            # 3. 計算 6 大均線
            data['5MA'] = data['Close'].rolling(5).mean()
            data['10MA'] = data['Close'].rolling(10).mean()
            data['20MA'] = data['Close'].rolling(20).mean()
            data['60MA'] = data['Close'].rolling(60).mean()
            data['120MA'] = data['Close'].rolling(120).mean()
            data['240MA'] = data['Close'].rolling(240).mean()
            
            return data
        except Exception:
            continue
            
    return None

# --- 5. 主介面 Tabs ---
tab1, tab2 = st.tabs(["⭐ 我的最愛與自訂均線 (DB連動)", "🔍 單一個股圖表細節"])

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
                    st.success(f"已新增 {clean_new} 並成功寫入 Google Sheets！")
                    st.rerun()

    st.markdown("---")
    st.subheader("⚙️ 獨立設定每檔股票要監控的均線")

    has_changed = False
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
                selected_mas = st.multiselect(
                    f"選擇 {stock_label} 要觸發通知的均線：",
                    options=ALL_MAS,
                    default=current_selected,
                    format_func=lambda x: f"{x} ({MA_LABELS[x]})",
                    key=f"ms_{code}"
                )
                if selected_mas != current_selected:
                    st.session_state.ma_settings[code] = selected_mas
                    has_changed = True

    if has_changed:
        save_settings_to_gsheets(st.session_state.watchlist, st.session_state.ma_settings)

    st.markdown("---")
    st.subheader("📊 清單即時均線警示比對")

    all_alerts = []
    summary_data = []

    if st.session_state.watchlist:
        with st.spinner("更新數據中 (預設 5 分鐘更新一次)..."):
            for code in st.session_state.watchlist:
                df_code = load_stock_data(code)
                stock_label = get_stock_label(code)
                
                if df_code is not None and not df_code.empty:
                    last_row = df_code.iloc[-1]
                    raw_price = last_row['Close']
                    
                    # 再次確認收盤價非 NaN 且可轉為 float
                    if pd.notna(raw_price):
                        price = float(raw_price)
                        target_mas = st.session_state.ma_settings.get(code, ALL_MAS)
                        triggered_info = []
                        
                        for ma_key in target_mas:
                            if ma_key in last_row and pd.notna(last_row[ma_key]):
                                ma_val = float(last_row[ma_key])
                                diff = ((price - ma_val) / ma_val) * 100
                                if abs(diff) <= alert_threshold:
                                    pos = "站上" if diff >= 0 else "跌破"
                                    triggered_info.append(f"{MA_LABELS[ma_key]}({abs(diff):.1f}%)")
                                    all_alerts.append(
                                        f"• **{stock_label}** 現價 {price:.2f} 靠近 **{MA_LABELS[ma_key]}** ({ma_val:.2f})，差距 {abs(diff):.1f}% ({pos})"
                                    )

                        summary_data.append({
                            "股票名稱 (代號)": stock_label,
                            "收盤價": f"{price:.2f}",
                            "監控中的均線": ", ".join([MA_LABELS.get(m, m) for m in target_mas]),
                            "符合警示的均線": ", ".join(triggered_info) if triggered_info else "無接近"
                        })
                    else:
                        summary_data.append({
                            "股票名稱 (代號)": stock_label,
                            "收盤價": "價格無效",
                            "監控中的均線": "-",
                            "符合警示的均線": "數據缺失"
                        })
                else:
                    summary_data.append({
                        "股票名稱 (代號)": stock_label,
                        "收盤價": "代號錯誤/無數據",
                        "監控中的均線": "-",
                        "符合警示的均線": "無法抓取"
                    })

        if summary_data:
            st.dataframe(pd.DataFrame(summary_data), use_container_width=True)

        st.markdown("---")
        st.write("### 🔔 LINE 手動發送與觸發通知區")
        
        col_btn1, col_btn2 = st.columns([1, 1])
        
        with col_btn1:
            if st.button("🧪 發送 LINE 測試訊息"):
                success, info = send_line_message(line_token, line_user_id, "🔔 這是一條來自【台股均線監測站】的連線測試訊息！")
                if success:
                    st.success(info)
                else:
                    st.error(info)

        if all_alerts:
            st.warning("⚠️ 目前滿足觸發門檻的個股：\n" + "\n".join(all_alerts))
            with col_btn2:
                if st.button("📲 發送選擇均線之 LINE 警示訊息", type="primary"):
                    msg = f"\n🚨【台股監測警示 - 均線通知】\n門檻設定：{alert_threshold}%\n" + "\n".join(all_alerts).replace("**", "")
                    success, info = send_line_message(line_token, line_user_id, msg)
                    if success:
                        st.success(info)
                    else:
                        st.error(info)
        else:
            st.info(f"💡 目前清單個股與其指定的監控均線差距均大於 {alert_threshold}%。")

# ==========================================
# Tab 2: 單一個股圖表細節
# ==========================================
with tab2:
    search_code = st.text_input("輸入台股代號查看技術線圖", value="2330").strip()
    if search_code:
        stock_label = get_stock_label(search_code)
        df_single = load_stock_data(search_code)
        
        if df_single is not None and not df_single.empty:
            st.subheader(f"📈 {stock_label} 技術線圖")
            plot_df = df_single.tail(120)
            fig = go.Figure()
            fig.add_trace(go.Candlestick(
                x=plot_df.index, open=plot_df['Open'], high=plot_df['High'],
                low=plot_df['Low'], close=plot_df['Close'], name='K線'
            ))
            colors = {'5MA': 'orange', '10MA': 'purple', '20MA': 'blue', '60MA': 'green', '120MA': 'brown', '240MA': 'red'}
            for ma_col, color in colors.items():
                if ma_col in plot_df.columns:
                    fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df[ma_col], mode='lines', name=MA_LABELS[ma_col], line=dict(color=color, width=1.5)))

            fig.update_layout(xaxis_rangeslider_visible=False, height=550, margin=dict(l=20, r=20, t=20, b=20), template="plotly_white")
            st.plotly_chart(fig, use_container_width=True)