import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

# 頁面標題與配置
st.set_page_config(page_title="台股均線監測站", layout="wide")
st.title("📈 台股股價與多重均線監測站")

# 側邊欄輸入
st.sidebar.header("設定參數")
stock_id = st.sidebar.text_input("輸入台股代號（例: 2330, 0050）", value="2330").strip()
alert_threshold = st.sidebar.slider("提醒觸發門檻（股價距離均線 %）", min_value=0.5, max_value=5.0, value=2.0, step=0.1)

# 台股 yfinance 代號轉換
symbol = f"{stock_id}.TW" if not stock_id.endswith(".TW") and not stock_id.endswith(".TWO") else stock_id

@st.cache_data(ttl=300)  # 快取 5 分鐘，避免頻繁請求
def load_stock_data(ticker):
    try:
        # 抓取近 1.5 年歷史數據以精確計算 240 日年線
        data = yf.download(ticker, period="2y", interval="1d")
        if data.empty:
            # 嘗試上櫃股票代碼 (.TWO)
            alt_ticker = ticker.replace(".TW", ".TWO")
            data = yf.download(alt_ticker, period="2y", interval="1d")
        
        # 處理 MultiIndex 欄位 (yfinance 新版格式)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
            
        return data
    except Exception as e:
        return None

if stock_id:
    df = load_stock_data(symbol)
    
    if df is None or df.empty:
        st.error(f"無法取得股票代碼 {stock_id} 的資料，請確認代號是否正確（上櫃股票請確定代號無誤）。")
    else:
        # 1. 計算均線
        df['5MA'] = df['Close'].rolling(window=5).mean()      # 周線
        df['10MA'] = df['Close'].rolling(window=10).mean()    # 十日線
        df['20MA'] = df['Close'].rolling(window=20).mean()    # 月線
        df['60MA'] = df['Close'].rolling(window=60).mean()    # 季線
        df['120MA'] = df['Close'].rolling(window=120).mean()  # 半年線
        df['240MA'] = df['Close'].rolling(window=240).mean()  # 年線

        # 取得最新一筆數據
        latest = df.iloc[-1]
        latest_price = float(latest['Close'])
        latest_date = df.index[-1].strftime('%Y-%m-%d')

        st.subheader(f"📊 {symbol} 股票資訊（最後更新日期：{latest_date}）")
        
        # 2. 顯示現價指標
        col1, col2 = st.columns(2)
        col1.metric("當前收盤價", f"{latest_price:.2f} 元")
        
        # 3. 均線價格表與距離比較
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
                
                # 判斷是否靠近均線（絕對值差距小於設定的門檻 %）
                if abs(diff_pct) <= alert_threshold:
                    position = "上方" if diff_pct >= 0 else "下方"
                    alerts.append(f"⚠️ **{name}** ({ma_val:.2f}元)：現價 {latest_price:.2f} 元已接近該均線（相差 {abs(diff_pct):.2f}%，處於均線{position}）")
                status = f"{'站上' if diff_pct >= 0 else '跌破'} ({diff_pct:+.2f}%)"

            ma_summary.append({
                "均線名稱": name,
                "均線價格": f"{ma_val:.2f}" if pd.notna(ma_val) else "N/A",
                "距現價 (%)": f"{diff_pct:+.2f}%" if diff_pct is not None else "N/A",
                "狀態": status
            })

        # 顯示提醒訊息
        st.write("### 🔔 均線接近警示通知")
        if alerts:
            for alert in alerts:
                st.warning(alert)
        else:
            st.success(f"目前股價與所有均線相差均大於 {alert_threshold}%，無即時靠近警示。")

        # 顯示均線明細表格
        st.write("### 📋 各週期均線數據")
        st.dataframe(pd.DataFrame(ma_summary), use_container_width=True)

        # 4. K線圖與均線視覺化
        st.write("### 📉 股價與均線走勢圖 (近 120 個交易日)")
        plot_df = df.tail(120)

        fig = go.Figure()
        
        # K線圖
        fig.add_trace(go.Candlestick(
            x=plot_df.index,
            open=plot_df['Open'], high=plot_df['High'],
            low=plot_df['Low'], close=plot_df['Close'],
            name='K線'
        ))

        # 各均線繪製
        colors = {'5MA': 'orange', '10MA': 'purple', '20MA': 'blue', '60MA': 'green', '120MA': 'brown', '240MA': 'red'}
        for ma_col, color in colors.items():
            fig.add_trace(go.Scatter(
                x=plot_df.index, y=plot_df[ma_col],
                mode='lines', name=ma_col, line=dict(color=color, width=1.5)
            ))

        fig.update_layout(
            xaxis_rangeslider_visible=False,
            height=600,
            margin=dict(l=20, r=20, t=20, b=20),
            template="plotly_white"
        )
        st.plotly_chart(fig, use_container_width=True)