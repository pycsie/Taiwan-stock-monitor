import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import requests
import json
import twstock

# 頁面配置
st.set_page_config(page_title="台股均線精準監測站", layout="wide")
st.title("📈 台股股價與客製化均線監測站 (LINE 推播)")

# --- 1. Session State 初始化 ---
if "watchlist" not in st.session_state:
    st.session_state.watchlist = ["2330", "0050"]

ALL_MAS = ['5MA', '10MA', '20MA', '60MA', '120MA', '240MA']
if "ma_settings" not in st.session_state:
    st.session_state.ma_settings = {
        "2330": ['20MA', '60MA', '240MA'],
        "0050": ['5MA', '10MA', '20MA', '60MA', '120MA', '240MA']
    }

# Secrets / Session 讀取 Token
default_token = st.secrets.get("LINE_CHANNEL_ACCESS_TOKEN", "") if "LINE_CHANNEL_ACCESS_TOKEN" in st.secrets else st.session_state.get("line_token", "")
default_user_id = st.secrets.get("LINE_USER_ID", "") if "LINE_USER_ID" in st.secrets else st.session_state.get("line_user_id", "")

# --- 2. 側邊欄設定 ---
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

# --- 3. 核心與工具函數 ---
@st.cache_data(ttl=86400) # 名稱快取 24 小時
def get_stock_name(code):
    """ 取得台股中文名稱 """
    clean_code = code.replace(".TW", "").replace(".TWO", "").strip()
    # 優先從 twstock 查詢
    if clean_code in twstock.codes:
        return twstock.codes[clean_code].name
    
    # 備用方案: 從 yfinance info 嘗試搜尋
    try:
        symbol = f"{clean_code}.TW"
        ticker = yf.Ticker(symbol)
        info = ticker.info
        name = info.get("shortName") or info.get("longName")
        if name:
            return name
    except Exception:
        pass
        
    return clean_code  # 若都找不到則回傳原代號

def get_stock_label(code):
    """ 格式化顯示：名稱 (代號) """
    name = get_stock_name(code)
    clean_code = code.replace(".TW", "").replace(".TWO", "").strip()
    if name != clean_code:
        return f"{name} ({clean_code})"
    return clean_code

def send_line_message(token, user_id, text):
    """ 使用 LINE Messaging API 發送 Push Message """
    if not token or not user_id:
        return False, "請填寫完整的 LINE Token 與 User ID！"
    
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token.strip()}"
    }
    payload = {
        "to": user_id.strip(),
        "messages": [{"type": "text", "text": text}]
    }
    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        if res.status_code == 200:
            return True, "LINE 訊息發送成功！"
        else:
            return False, f"發送失敗 (代碼 {res.status_code}): {res.text}"
    except Exception as e:
        return False, f"發送異常: {str(e)}"

@st.cache_data(ttl=300)
def load_stock_data(stock_id):
    """ 抓取歷史資料並計算均線 """
    clean_id = stock_id.replace(".TW", "").replace(".TWO", "").strip()
    symbol = f"{clean_id}.TW"
    try:
        data = yf.download(symbol, period="2y", interval="1d")
        if data.empty:
            alt_symbol = f"{clean_id}.TWO"
            data = yf.download(alt_symbol, period="2y", interval="1d")
        
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
            
        if data.empty:
            return None
            
        data['5MA'] = data['Close'].rolling(5).mean()
        data['10MA'] = data['Close'].rolling(10).mean()
        data['20MA'] = data['Close'].rolling(20).mean()
        data['60MA'] = data['Close'].rolling(60).mean()
        data['120MA'] = data['Close'].rolling(120).mean()
        data['240MA'] = data['Close'].rolling(240).mean()
        return data
    except Exception:
        return None

MA_LABELS = {
    '5MA': '5日線', '10MA': '10日線', '20MA': '月線(20MA)',
    '60MA': '季線(60MA)', '120MA': '半年線(120MA)', '240MA': '年線(240MA)'
}

# --- 4. 主介面 Tabs ---
tab1, tab2 = st.tabs(["⭐ 我的最愛與自訂均線通知", "🔍 單一個股圖表細節"])

# ==========================================
# Tab 1: 我的最愛管理與獨立均線設定
# ==========================================
with tab1:
    st.subheader("➕ 新增與管理關注個股")
    
    col_in, col_btn = st.columns([3, 1])
    with col_in:
        new_stock = st.text_input("輸入要加入我的最愛的股票代號（如：2317 或 0050）", key="new_stock_input").strip()
    with col_btn:
        st.write(" ")
        st.write(" ")
        if st.button("加到關注清單"):
            if new_stock:
                clean_new = new_stock.replace(".TW", "").replace(".TWO", "").strip()
                if clean_new not in st.session_state.watchlist:
                    st.session_state.watchlist.append(clean_new)
                    st.session_state.ma_settings[clean_new] = ALL_MAS.copy()
                    st.rerun()

    st.markdown("---")
    st.subheader("⚙️ 獨立設定每檔股票要監控的均線")

    # 動態設定每檔股票監控的均線
    for code in st.session_state.watchlist:
        stock_label = get_stock_label(code)
        with st.expander(f"📌 **{stock_label}** 監控均線設定", expanded=True):
            col_del, col_select = st.columns([1, 4])
            with col_del:
                if st.button(f"🗑️ 移除", key=f"del_{code}"):
                    st.session_state.watchlist.remove(code)
                    if code in st.session_state.ma_settings:
                        del st.session_state.ma_settings[code]
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
                st.session_state.ma_settings[code] = selected_mas

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
                    price = float(last_row['Close'])
                    target_mas = st.session_state.ma_settings.get(code, ALL_MAS)
                    
                    triggered_info = []
                    
                    for ma_key in target_mas:
                        ma_val = last_row[ma_key]
                        if pd.notna(ma_val):
                            ma_val = float(ma_val)
                            diff = ((price - ma_val) / ma_val) * 100
                            if abs(diff) <= alert_threshold:
                                pos = "站上" if diff >= 0 else "跌破"
                                triggered_info.append(f"{MA_LABELS[ma_key]}({abs(diff):.1f}%)")
                                # LINE 推播與網頁警示顯示「股票名稱 (代號)」
                                all_alerts.append(
                                    f"• **{stock_label}** 現價 {price:.2f} 靠近 **{MA_LABELS[ma_key]}** ({ma_val:.2f})，差距 {abs(diff):.1f}% ({pos})"
                                )

                    summary_data.append({
                        "股票名稱 (代號)": stock_label,
                        "收盤價": f"{price:.2f}",
                        "監控中的均線": ", ".join([MA_LABELS[m] for m in target_mas]),
                        "符合警示的均線": ", ".join(triggered_info) if triggered_info else "無接近"
                    })

        st.dataframe(pd.DataFrame(summary_data), use_container_width=True)

        st.write("### 🔔 觸發通知區")
        if all_alerts:
            st.warning("⚠️ 目前滿足觸發門檻的個股：\n" + "\n".join(all_alerts))
            if st.button("📲 發送選擇均線之 LINE 警示訊息"):
                msg = f"\n🚨【台股監測警示 - 均線通知】\n門檻設定：{alert_threshold}%\n" + "\n".join(all_alerts).replace("**", "")
                success, info = send_line_message(line_token, line_user_id, msg)
                if success:
                    st.success(info)
                else:
                    st.error(info)
        else:
            st.success(f"目前清單中的個股與其指定的監控均線差距均大於 {alert_threshold}%。")

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
                fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df[ma_col], mode='lines', name=MA_LABELS[ma_col], line=dict(color=color, width=1.5)))

            fig.update_layout(xaxis_rangeslider_visible=False, height=550, margin=dict(l=20, r=20, t=20, b=20), template="plotly_white")
            st.plotly_chart(fig, use_container_width=True)