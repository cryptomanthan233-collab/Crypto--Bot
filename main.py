import os
import sys
import time
import threading
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import pandas as pd
import numpy as np

sys.stdout.reconfigure(line_buffering=True)

# --- APNI DETAILS YAHAN SET KAREIN ---
TELEGRAM_TOKEN = "8928396323:AAGjxXIYlHjGCS_MDmao2xmUUY59XJq6Xt0"
CHAT_ID = "1648384033"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "PEPEUSDT"]
ACCOUNT_BALANCE = 600.0   # ($) Equivalent capital for risk calculation
RISK_PERCENT = 1.5        # Max 1.5% risk per trade (~$9)
# ------------------------------------

last_alert_candles = {}

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"24/7 Crypto Signal Bot (BTC, ETH, SOL, XRP, PEPE) is Running!")
    def log_message(self, format, *args):
        pass

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), DummyHandler)
    server.serve_forever()

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print(f"Telegram Error: {e}", flush=True)

def fetch_klines(symbol, interval, limit=220):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        df = pd.DataFrame(data, columns=[
            "open_time", "Open", "High", "Low", "Close", "Volume",
            "close_time", "qav", "num_trades", "taker_base_vol", "taker_quote_vol", "ignore"
        ])
        for col in ["Open", "High", "Low", "Close"]:
            df[col] = df[col].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        return df
    except Exception as e:
        print(f"Fetch Error ({symbol} {interval}): {e}", flush=True)
        return None

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_1h_trend(symbol):
    df_1h = fetch_klines(symbol, "1h", limit=220)
    if df_1h is None or len(df_1h) < 205:
        return "NEUTRAL"

    df_1h["EMA50"] = df_1h["Close"].ewm(span=50, adjust=False).mean()
    df_1h["EMA200"] = df_1h["Close"].ewm(span=200, adjust=False).mean()
    last = df_1h.iloc[-2]

    close_1h = float(last["Close"])
    ema50_1h = float(last["EMA50"])
    ema200_1h = float(last["EMA200"])

    if close_1h > ema50_1h and ema50_1h > ema200_1h:
        return "BULLISH"
    elif close_1h < ema50_1h and ema50_1h < ema200_1h:
        return "BEARISH"
    return "NEUTRAL"

def format_price(symbol, price):
    if symbol == "PEPEUSDT":
        return f"{price:.8f}"
    elif symbol == "XRPUSDT":
        return f"{price:.4f}"
    return f"{price:.2f}"

def run_bot():
    send_telegram(
        "🚀 *24/7 Crypto Signal Bot Live!*\n"
        "• *Pairs:* BTC, ETH, SOL, XRP, PEPE\n"
        "• *Strategy:* 1H Trend (EMA 50/200) + 15m Pullback\n"
        f"• *Risk:* `{RISK_PERCENT}%` (`~${ACCOUNT_BALANCE * (RISK_PERCENT / 100):.1f}` per trade)"
    )

    while True:
        now_utc = datetime.now(timezone.utc)
        for symbol in SYMBOLS:
            try:
                df = fetch_klines(symbol, "15m", limit=100)
                if df is None or len(df) < 60:
                    continue

                candle_time = df["open_time"].iloc[-2]
                candle_id = f"{symbol}_{candle_time}"

                if last_alert_candles.get(symbol) == candle_id:
                    continue

                # 15m Indicators
                df["EMA9"] = df["Close"].ewm(span=9, adjust=False).mean()
                df["EMA21"] = df["Close"].ewm(span=21, adjust=False).mean()
                df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
                df["RSI"] = calculate_rsi(df["Close"], 14)

                high_low = df["High"] - df["Low"]
                high_close = np.abs(df["High"] - df["Close"].shift())
                low_close = np.abs(df["Low"] - df["Close"].shift())
                tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
                df["ATR"] = tr.rolling(14).mean()

                row = df.iloc[-2]
                ema9, ema21, ema50 = float(row["EMA9"]), float(row["EMA21"]), float(row["EMA50"])
                rsi = float(row["RSI"])
                atr = float(row["ATR"])
                close_p, low_p, high_p = float(row["Close"]), float(row["Low"]), float(row["High"])

                htf_trend = get_1h_trend(symbol)
                sl_dist = atr * 1.5
                tp_dist = sl_dist * 2.0
                risk_usd = ACCOUNT_BALANCE * (RISK_PERCENT / 100.0)
                pos_size_usd = round((risk_usd / (sl_dist / close_p)), 2)
                candle_label = candle_time.strftime("%H:%M UTC")

                # 1. BUY SIGNAL (Spot & Futures dono ke liye)
                if htf_trend == "BULLISH" and ema9 > ema21 > ema50 and low_p <= ema21 and close_p > ema21 and (52 <= rsi <= 65):
                    entry_str = format_price(symbol, close_p)
                    sl_str = format_price(symbol, close_p - sl_dist)
                    tp_str = format_price(symbol, close_p + tp_dist)
                    last_alert_candles[symbol] = candle_id

                    send_telegram(
                        f"🟢 *CRYPTO BUY SIGNAL: #{symbol}*\n"
                        f"_(1H Uptrend + 15m Pullback Confirmed)_\n\n"
                        f"• *Action:* BUY / LONG\n"
                        f"• *Entry:* `{entry_str}`\n"
                        f"• *Stop Loss (SL):* `{sl_str}`\n"
                        f"• *Take Profit (TP):* `{tp_str}` (1:2 RR)\n\n"
                        f"📊 *Risk Management (${ACCOUNT_BALANCE:.0f} Bal):*\n"
                        f"• *Suggested Position Value:* `${pos_size_usd}`\n"
                        f"• *Max Risk:* `${risk_usd:.2f}`\n\n"
                        f"🕒 *Candle:* {candle_label} | *RSI:* {rsi:.1f}"
                    )

                # 2. SELL / SHORT SIGNAL (Futures ke liye)
                elif htf_trend == "BEARISH" and ema9 < ema21 < ema50 and high_p >= ema21 and close_p < ema21 and (35 <= rsi <= 48):
                    entry_str = format_price(symbol, close_p)
                    sl_str = format_price(symbol, close_p + sl_dist)
                    tp_str = format_price(symbol, close_p - tp_dist)
                    last_alert_candles[symbol] = candle_id

                    send_telegram(
                        f"🔴 *CRYPTO SELL SIGNAL: #{symbol}*\n"
                        f"_(1H Downtrend + 15m Rejection Confirmed)_\n\n"
                        f"• *Action:* SELL / SHORT\n"
                        f"• *Entry:* `{entry_str}`\n"
                        f"• *Stop Loss (SL):* `{sl_str}`\n"
                        f"• *Take Profit (TP):* `{tp_str}` (1:2 RR)\n\n"
                        f"📊 *Risk Management (${ACCOUNT_BALANCE:.0f} Bal):*\n"
                        f"• *Suggested Position Value:* `${pos_size_usd}`\n"
                        f"• *Max Risk:* `${risk_usd:.2f}`\n\n"
                        f"🕒 *Candle:* {candle_label} | *RSI:* {rsi:.1f}"
                    )

            except Exception as e:
                print(f"Error ({symbol}): {e}", flush=True)

            time.sleep(2)

        print(f"[{now_utc.strftime('%H:%M')} UTC] Crypto scan completed for 5 pairs.", flush=True)
        time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    run_bot()
