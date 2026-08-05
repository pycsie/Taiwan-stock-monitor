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
    """ 從 Google Sheets 撈取最新資料 """
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
    """ 將最新的股票與均線設定寫回 Google Sheets """
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

# --- 2. 狀態初始化保護機制 ---
if "watchlist" not in st.session_state or "ma_settings" not in st.session_state:
    db_watchlist, db_ma_settings = load_settings_from_gsheets()
    st.session_state.watchlist = db_watchlist
    st.session_state.ma_settings = db_ma_settings

default_token = st.secrets.get("LINE_CHANNEL_ACCESS_TOKEN", "") if "LINE_CHANNEL_ACCESS_TOKEN" in st.secrets else st.session_state.get("line_token", "")
default_user_id = st.secrets.get("LINE_USER_ID", "") if "LINE_USER_ID" in st.secrets else st.session_state.get("line_user_id", "")

# --- 3. 側邊欄設定 ---
st.sidebar.header("⚙️ 參數設定")

alert_threshold = st.sidebar.slider(
    "提醒觸發門檻（股價距離均線 %）", 
    min_value=0.5, max_value=5.0, value=1.5, step=0.1
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

# --- 擴充：計算技術指標與 5日均量 ---
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

# --- 5. 主介面 Tabs (新增第 4 個 Tab) ---
tab1, tab2, tab3, tab4 = st.tabs([
    "⭐ 我的最愛與自訂均線 (DB連動)", 
    "🔍 單一個股圖表細節", 
    "🚀 帶量紅K短線轉強掃描",
    "🧱 底部大均線尋寶器 (季/半年/年線)"
])

# ==========================================
# Tab 1: 我的最愛管理與對比警示 (完全保持原樣)
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
                    options=ALL_MAS,
                    default=current_selected,
                    format_func=lambda x: f"{x} ({MA_LABELS[x]})",
                    key=f"ms_{code}",
                    on_change=update_ma_setting,
                    args=(code,)
                )

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
# Tab 2: 單一個股圖表細節 (完全保持原樣)
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

# ==========================================
# Tab 3: 🚀 帶量紅K短線轉強掃描
# ==========================================
with tab3:
    st.subheader("🚀 全台股帶量紅 K 與均線上彎掃描器")
    st.caption("硬性條件：【成交量 > 2000張】＋【帶量紅K (量>5日均量1.3倍)】＋【5MA / 10MA / 20MA 至少有一條扣低轉上彎】")

    col_v, col_s = st.columns(2)
    with col_v:
        min_vol_lots = st.number_input("成交量防護門檻 (張)", min_value=500, value=2000, step=500, key="t3_vol")
    with col_s:
        scan_scope = st.selectbox("掃描標的範圍", ["熱門大型與權值股 (約 30 檔 - 快速)", "全台股上市上櫃 (約 1800 檔 - 需較長時間)"], key="t3_scope")

    if st.button("🔍 開始掃描強勢標的", type="primary", key="btn_t3"):
        target_codes = []
        if scan_scope.startswith("熱門大型"):
            target_codes = list(BUILTIN_STOCKS.keys()) + [
                "2303", "2603", "2609", "2615", "3231", "2356", "6669", "3037",
                "2379", "3034", "2337", "2408", "2344", "2301", "2324", "2353"
            ]
        else:
            with st.spinner("抓取全台股股票清單中..."):
                for code, info in twstock.codes.items():
                    if info.type == "股票" and len(code) == 4 and code.isdigit():
                        target_codes.append(code)

        st.info(f"正在掃描 {len(target_codes)} 檔股票數據...")
        p_bar = st.progress(0)
        scan_results = []

        for idx, code in enumerate(target_codes):
            p_bar.progress((idx + 1) / len(target_codes))
            df = load_stock_data(code)

            if df is not None and not df.empty and len(df) >= 25:
                curr = df.iloc[-1]
                prev = df.iloc[-2]
                prev2 = df.iloc[-3]

                open_p = float(curr['Open'])
                close_p = float(curr['Close'])
                vol_shares = float(curr['Volume'])
                vol_lots = vol_shares / 1000.0
                vol_5ma = float(curr['Vol_5MA']) if pd.notna(curr['Vol_5MA']) else 0

                if vol_lots >= min_vol_lots and close_p > open_p and (vol_5ma > 0 and vol_shares >= vol_5ma * 1.3):
                    up_mas = []
                    for ma_key, ma_name in [('5MA', '5日線'), ('10MA', '10日線'), ('20MA', '月線')]:
                        if pd.notna(curr[ma_key]) and pd.notna(prev[ma_key]) and pd.notna(prev2[ma_key]):
                            c_ma, p_ma, p2_ma = float(curr[ma_key]), float(prev[ma_key]), float(prev2[ma_key])
                            if p_ma <= p2_ma and c_ma > p_ma and close_p >= c_ma:
                                up_mas.append(ma_name)

                    if len(up_mas) > 0:
                        vol_mult = (vol_shares / vol_5ma) if vol_5ma > 0 else 0
                        scan_results.append({
                            "股票代號/名稱": get_stock_label(code),
                            "收盤價": f"{close_p:.2f}",
                            "漲跌K線": f"🔴 紅K (+{(close_p - open_p):.2f})",
                            "成交量 (張)": int(vol_lots),
                            "量增倍數": f"{vol_mult:.1f} 倍",
                            "轉上彎均線": "、".join(up_mas),
                            "KD (K值)": round(float(curr['K']), 1) if pd.notna(curr['K']) else "-"
                        })

        p_bar.empty()
        if scan_results:
            st.success(f"🎉 找到 {len(scan_results)} 檔短線轉強標的：")
            st.dataframe(pd.DataFrame(scan_results), use_container_width=True)
        else:
            st.warning("⚠️ 目前無符合條件的股票。")

# ==========================================
# Tab 4: 🧱 底部大均線尋寶器 (全新功能)
# ==========================================
with tab4:
    st.subheader("🧱 底部大均線（季線/半年線/年線）附近佈局器")
    st.caption("篩選當前股價貼近季線(60MA)、半年線(120MA) 或 年線(240MA) 的股票，適合大均線附近抄底與建倉。")

    col_target_ma, col_dist, col_v4 = st.columns(3)
    with col_target_ma:
        selected_bottom_mas = st.multiselect(
            "選擇要比對的底部大均線：",
            options=['60MA', '120MA', '240MA'],
            default=['60MA', '240MA'],
            format_func=lambda x: f"{x} ({MA_LABELS[x]})"
        )
    with col_dist:
        max_dist_pct = st.number_input("股價距離大均線範圍 (±%)", min_value=0.5, max_value=8.0, value=3.0, step=0.5)
    with col_v4:
        min_vol_bottom = st.number_input("成交量門檻 (張)", min_value=500, value=2000, step=500, key="t4_vol")

    only_red = st.checkbox("只顯示帶量紅 K（成交量 > 5日均量 1.2倍 且 當日收紅）", value=True)

    if st.button("🔍 掃描大均線底部個股", type="primary", key="btn_t4"):
        if not selected_bottom_mas:
            st.error("請至少選擇一條大均線進行比對！")
        else:
            target_codes = []
            with st.spinner("載入全台股清單中..."):
                for code, info in twstock.codes.items():
                    if info.type == "股票" and len(code) == 4 and code.isdigit():
                        target_codes.append(code)

            st.info(f"正在分析 {len(target_codes)} 檔股票之大均線距離...")
            p_bar4 = st.progress(0)
            bottom_results = []

            for idx, code in enumerate(target_codes):
                p_bar4.progress((idx + 1) / len(target_codes))
                df = load_stock_data(code)

                if df is not None and not df.empty and len(df) >= 240:
                    curr = df.iloc[-1]
                    price = float(curr['Close'])
                    open_p = float(curr['Open'])
                    vol_shares = float(curr['Volume'])
                    vol_lots = vol_shares / 1000.0
                    vol_5ma = float(curr['Vol_5MA']) if pd.notna(curr['Vol_5MA']) else 0

                    if vol_lots >= min_vol_bottom:
                        # 如果有勾選「只顯示帶量紅K」
                        if only_red:
                            if not (price > open_p and vol_shares >= vol_5ma * 1.2):
                                continue

                        near_info = []
                        min_abs_diff = 999.0

                        for ma_key in selected_bottom_mas:
                            if ma_key in curr and pd.notna(curr[ma_key]):
                                ma_val = float(curr[ma_key])
                                diff_pct = ((price - ma_val) / ma_val) * 100

                                if abs(diff_pct) <= max_dist_pct:
                                    pos_str = "線上方" if diff_pct >= 0 else "線下方"
                                    near_info.append(f"{MA_LABELS[ma_key]} (距 {diff_pct:+.1f}%, {pos_str})")
                                    if abs(diff_pct) < min_abs_diff:
                                        min_abs_diff = abs(diff_pct)

                        if near_info:
                            bottom_results.append({
                                "股票代號/名稱": get_stock_label(code),
                                "收盤價": f"{price:.2f}",
                                "成交量 (張)": int(vol_lots),
                                "貼近之大均線": " | ".join(near_info),
                                "KD (K值)": round(float(curr['K']), 1) if pd.notna(curr['K']) else "-",
                                "差距絕對值": min_abs_diff
                            })

            p_bar4.empty()

            if bottom_results:
                # 依距離大均線最近者排序
                res_df = pd.DataFrame(bottom_results).sort_values("差距絕對值").drop(columns=["差距絕對值"])
                st.success(f"🎉 找到 {len(res_df)} 檔貼近【{', '.join([MA_LABELS[m] for m in selected_bottom_mas])}】附近的標的：")
                st.dataframe(res_df, use_container_width=True)
            else:
                st.warning("⚠️ 目前無符合大均線範圍條件的標的，請嘗試放寬距離範圍 %。")