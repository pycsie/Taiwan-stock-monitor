import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import requests
import json

# 頁面配置
st.set_page_config(page_title="台股均線監測與 LINE 警示站", layout="wide")
st.title("📈 台股股價與多重均線監測站 (含 LINE 通知)")

# 初始化 Session State (我的最愛清單)
if "watchlist" not in st.session_state:
    st.session_state.watchlist = ["2330", "0050", "2317"]

# --- 側邊欄設定 ---
st.sidebar.header("⚙️ 參數設定")

# 1. 均線提醒門檻
alert_threshold = st.sidebar.slider(
    "提醒觸發門檻（股價距離均線 %）", 
    min_value=0.5, max_value=5.0, value=2.0, step=0.1
)

# 2. LINE Messaging API 設定區
st.sidebar.markdown("---")
st.sidebar.header("💬 LINE Messaging API 設定")
st.sidebar.caption("因 LINE Notify 已終止服務，本站改用 LINE 官方帳號 API 發送推播。")
line_token = st.sidebar.text_input("Channel Access Token", type="password", help="請填入 LINE Developers 發行的 Channel Access Token")
line_user_id = st.sidebar.text_input("Your User ID", type="password", help="請填入您的 LINE User ID (可在 LINE Developers 基本頁面找到)")

# --- 函數定義 ---
def send_line_message(token, user_id, text):
    """ 使用 LINE Messaging API 發送 Push Message """
    if not token or not user_id:
        return False, "請先在側邊欄填寫 LINE Channel Access Token 與 User ID！"
    
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    payload = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }
    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        if res.status_code == 200:
            return True, "LINE 訊息發送成功！"
        else:
            return False, f"發送失敗 (代碼 {res.status_code}): {res.text}"
    except Exception as e:
        return False, f"發送發生異常: {str(e)}"

@st.cache_data(ttl=300)
def load_stock_data(stock_id):
    """ 抓取股票資料並計算均線 """
    symbol = f"{stock_id}.TW" if not stock_id.endswith(".TW") and not stock_id.endswith(".TWO") else stock_id
    try:
        data = yf.download(symbol, period="2y", interval="1d")
        if data.empty:
            alt_symbol = symbol.replace(".TW", ".TWO")
            data = yf.download(alt_symbol, period="2y", interval="1d")
        
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
            
        if data.empty:
            return None
            
        # 計算均線
        data['5MA'] = data['Close'].rolling(5).mean()
        data['10MA'] = data['Close'].rolling(10).mean()
        data['20MA'] = data['Close'].rolling(20).mean()
        data['60MA'] = data['Close'].rolling(60).mean()
        data['120MA'] = data['Close'].rolling(120).mean()
        data['240MA'] = data['Close'].rolling(240).mean()
        return data
    except Exception:
        return None

# --- 主分頁介面 ---
tab1, tab2 = st.tabs(["🔍 個股詳細分析", "⭐ 我的最愛與全清單監測"])

# ==========================================
# Tab 1: 個股詳細分析
# ==========================================
with tab1:
    col_input, col_add = st.columns([3, 1])
    with col_input:
        stock_id = st.text_input("輸入台股代號（例: 2330, 2454）", value="2330").strip()
    with col_add:
        st.write(" ")
        st.write(" ")
        if st.button("➕ 加到我的最愛"):
            if stock_id and stock_id not in st.session_state.watchlist:
                st.session_state.watchlist.append(stock_id)
                st.success(f"已將 {stock_id} 加入我的最愛！")

    if stock_id:
        df = load_stock_data(stock_id)
        if df is None or df.empty:
            st.error(f"無法取得 {stock_id} 資料，請檢查股票代號是否正確。")
        else:
            latest = df.iloc[-1]
            latest_price = float(latest['Close'])
            latest_date = df.index[-1].strftime('%Y-%m-%d')

            st.subheader(f"📊 {stock_id} 股票資訊（最後更新日期：{latest_date}）")
            st.metric("當前收盤價", f"{latest_price:.2f} 元")

            # 檢查均線警示
            ma_keys = {
                '5日線 (5MA)': latest['5MA'],
                '10日線 (10MA)': latest['10MA'],
                '月線 (20MA)': latest['20MA'],
                '季線 (60MA)': latest['60MA'],
                '半年線 (120MA)': latest['120MA'],
                '年線 (240MA)': latest['240MA']
            }

            ma_summary = []
            alerts = []

            for name, ma_val in ma_keys.items():
                if pd.isna(ma_val):
                    diff_pct = None
                    status = "資料不足"
                else:
                    ma_val = float(ma_val)
                    diff_pct = ((latest_price - ma_val) / ma_val) * 100
                    if abs(diff_pct) <= alert_threshold:
                        pos = "上方" if diff_pct >= 0 else "下方"
                        alerts.append(f"⚠️ {stock_id} 接近 {name} ({ma_val:.2f}元)：現價 {latest_price:.2f} 元 (相差 {abs(diff_pct):.2f}%，位處均線{pos})")
                    status = f"{'站上' if diff_pct >= 0 else '跌破'} ({diff_pct:+.2f}%)"

                ma_summary.append({
                    "均線名稱": name,
                    "均線價格": f"{ma_val:.2f}" if pd.notna(ma_val) else "N/A",
                    "距現價 (%)": f"{diff_pct:+.2f}%" if diff_pct is not None else "N/A",
                    "狀態": status
                })

            # 警示提示區
            st.write("### 🔔 均線接近警示通知")
            if alerts:
                for alert in alerts:
                    st.warning(alert)
                
                # 手動發送 LINE 按鈕
                if st.button(f"📲 發送 [{stock_id}] 警示訊息至 LINE"):
                    msg = f"\n📈 【台股均線警示 - {stock_id}】\n收盤價：{latest_price:.2f}\n" + "\n".join(alerts)
                    success, info = send_line_message(line_token, line_user_id, msg)
                    if success:
                        st.success(info)
                    else:
                        st.error(info)
            else:
                st.success(f"目前股價與所有均線相差均大於 {alert_threshold}%，無即時靠近警示。")

            st.write("### 📋 各週期均線數據")
            st.dataframe(pd.DataFrame(ma_summary), use_container_width=True)

            # K線走勢圖
            st.write("### 📉 股價與均線走勢圖 (近 120 個交易日)")
            plot_df = df.tail(120)
            fig = go.Figure()
            fig.add_trace(go.Candlestick(
                x=plot_df.index, open=plot_df['Open'], high=plot_df['High'],
                low=plot_df['Low'], close=plot_df['Close'], name='K線'
            ))
            colors = {'5MA': 'orange', '10MA': 'purple', '20MA': 'blue', '60MA': 'green', '120MA': 'brown', '240MA': 'red'}
            for ma_col, color in colors.items():
                fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df[ma_col], mode='lines', name=ma_col, line=dict(color=color, width=1.5)))

            fig.update_layout(xaxis_rangeslider_visible=False, height=500, margin=dict(l=20, r=20, t=20, b=20), template="plotly_white")
            st.plotly_chart(fig, use_container_width=True)

# ==========================================
# Tab 2: 我的最愛清單總覽與批次監測
# ==========================================
with tab2:
    st.subheader("⭐ 我的最愛清單")
    
    # 顯示目前關注股票與刪除按鈕
    if st.session_state.watchlist:
        cols = st.columns(len(st.session_state.watchlist))
        for idx, item in enumerate(st.session_state.watchlist):
            with cols[idx]:
                st.write(f"**{item}**")
                if st.button(f"🗑️ 移除", key=f"del_{item}"):
                    st.session_state.watchlist.remove(item)
                    st.rerun()
    else:
        st.info("目前我的最愛清單為空，請前往「個股詳細分析」分頁新增股票。")

    st.markdown("---")
    st.subheader("📊 關注清單均線狀態總覽")

    all_alerts = []
    summary_list = []

    if st.session_state.watchlist:
        with st.spinner("正在抓取清單中所有股票數據..."):
            for code in st.session_state.watchlist:
                df_code = load_stock_data(code)
                if df_code is not None and not df_code.empty:
                    last_row = df_code.iloc[-1]
                    price = float(last_row['Close'])
                    
                    triggered_mas = []
                    ma_map = {'5MA':'5日', '10MA':'10日', '20MA':'月線', '60MA':'季線', '120MA':'半年線', '240MA':'年線'}
                    
                    for ma_col, label in ma_map.items():
                        ma_p = last_row[ma_col]
                        if pd.notna(ma_p):
                            ma_p = float(ma_p)
                            diff = abs((price - ma_p) / ma_p) * 100
                            if diff <= alert_threshold:
                                triggered_mas.append(f"{label}(相差{diff:.1f}%)")
                                all_alerts.append(f"• {code} 現價 {price:.2f} 接近 {label} ({ma_p:.2f})，差距 {diff:.1f}%")

                    summary_list.append({
                        "股票代號": code,
                        "當前收盤價": f"{price:.2f}",
                        "靠近的均線": ", ".join(triggered_mas) if triggered_mas else "無接近均線",
                        "5MA": f"{last_row['5MA']:.2f}" if pd.notna(last_row['5MA']) else "-",
                        "20MA": f"{last_row['20MA']:.2f}" if pd.notna(last_row['20MA']) else "-",
                        "60MA": f"{last_row['60MA']:.2f}" if pd.notna(last_row['60MA']) else "-"
                    })

        st.dataframe(pd.DataFrame(summary_list), use_container_width=True)

        # 一鍵批次推播按鈕
        st.write("### 🔔 關注清單 LINE 批次通知")
        if all_alerts:
            st.warning("目前清單中有以下股票觸發均線接近條件：\n" + "\n".join(all_alerts))
            if st.button("📲 立即發送全清單警示至 LINE"):
                full_msg = f"\n🚨【台股全清單均線警示通知】\n提醒門檻：{alert_threshold}%\n" + "\n".join(all_alerts)
                success, info = send_line_message(line_token, line_user_id, full_msg)
                if success:
                    st.success(info)
                else:
                    st.error(info)
        else:
            st.success(f"目前關注清單中的股票均未觸發 {alert_threshold}% 均線接近警示。")