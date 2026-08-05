import os
import json
import requests
import pandas as pd
import yfinance as yf
from google.oauth2.service_account import Credentials
import gspread

# 嘗試載入 twstock，若未安裝或失敗則不影響程式運作
try:
    import twstock
    HAS_TWSTOCK = True
except ImportError:
    HAS_TWSTOCK = False

# --- 1. 環境變數讀取 ---
LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
LINE_USER_ID = os.environ.get("LINE_USER_ID", "").strip()
GCP_SA_JSON = os.environ.get("GCP_SERVICE_ACCOUNT", "").strip()
SPREADSHEET_KEY = os.environ.get("SPREADSHEET_KEY", "").strip()

ALERT_THRESHOLD = 1.5  # 觸發門檻 (%)

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

# --- 2. 安全版股票名稱查詢 ---
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

# --- 3. 讀取 Google Sheets ---
def get_watchlist_from_gsheets():
    try:
        if not GCP_SA_JSON or not SPREADSHEET_KEY:
            print("❌ 錯誤：缺少 GCP_SERVICE_ACCOUNT 或 SPREADSHEET_KEY 環境變數！")
            return [], {}

        creds_dict = json.loads(GCP_SA_JSON)
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(credentials)
        
        sh = gc.open_by_key(SPREADSHEET_KEY)
        worksheet = sh.worksheet("Watchlist")
        records = worksheet.get_all_records()
        
        watchlist = []
        ma_settings = {}
        for row in records:
            stock = str(row.get('Stock', '')).split('.')[0].strip()
            if stock and stock.isalnum():
                watchlist.append(stock)
                mas_str = str(row.get('MAs', ''))
                mas_list = [m.strip() for m in mas_str.split(",") if m.strip() in ALL_MAS]
                ma_settings[stock] = mas_list if mas_list else ALL_MAS.copy()
        return watchlist, ma_settings
    except Exception as e:
        print(f"❌ 讀取 Google Sheets 失敗: {e}")
        return [], {}

# --- 4. 抓取股票資料 ---
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
            if data.empty or len(data) < 5:
                continue
                
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

# --- 5. 發送 LINE 訊息 ---
def send_line_message(token, user_id, text):
    if not token or not user_id:
        print("❌ 錯誤：缺少 LINE_CHANNEL_ACCESS_TOKEN 或 LINE_USER_ID！")
        return False
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    payload = {"to": user_id, "messages": [{"type": "text", "text": text}]}
    try:
        res = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        return res.status_code == 200
    except Exception as e:
        print(f"❌ LINE 訊息發送異常: {e}")
        return False

# --- 6. 主程式 ---
def main():
    try:
        watchlist, ma_settings = get_watchlist_from_gsheets()
        if not watchlist:
            print("⚠️ 關注清單為空或讀取失敗，程式結束。")
            return

        all_alerts = []
        print(f"正在比對 {len(watchlist)} 檔股票數據...")

        for code in watchlist:
            df = load_stock_data(code)
            if df is not None and not df.empty:
                last_row = df.iloc[-1]
                price = float(last_row['Close'])
                target_mas = ma_settings.get(code, ALL_MAS)
                stock_label = get_stock_label(code)

                for ma_key in target_mas:
                    if ma_key in last_row and pd.notna(last_row[ma_key]):
                        ma_val = float(last_row[ma_key])
                        diff = ((price - ma_val) / ma_val) * 100
                        if abs(diff) <= ALERT_THRESHOLD:
                            pos = "站上" if diff >= 0 else "跌破"
                            all_alerts.append(
                                f"• {stock_label} 現價 {price:.2f} 靠近 {MA_LABELS[ma_key]} ({ma_val:.2f})，差距 {abs(diff):.1f}% ({pos})"
                            )

        if all_alerts:
            msg = f"\n🚨【盤中定時均線警示】\n門檻：{ALERT_THRESHOLD}%\n" + "\n".join(all_alerts)
            success = send_line_message(LINE_TOKEN, LINE_USER_ID, msg)
            if success:
                print("✅ 警示訊息已成功發送至 LINE！")
            else:
                print("❌ LINE 訊息發送失敗！")
        else:
            print("💡 目前無股票符合均線警示門檻。")
    except Exception as main_e:
        print(f"❌ 主程式執行發生未預期例外: {main_e}")

if __name__ == "__main__":
    main()