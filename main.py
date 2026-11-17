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
        self.wfile.write(b"24/7 Institutional Sniper Crypto Bot is Running!")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
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

def fetch_klines(symbol, interval, limit=250):
    """US-Server Friendly Public Data Endpoints (Koi API Key nahi chahiye)"""
    endpoints = [
        f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}",
        f"https://api.binance.us/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    ]
    for url in endpoints:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and len(data) > 0:
                    df = pd.DataFrame(data, columns=[
                        "open_time", "Open", "High", "Low", "Close", "Volume",
                        "close_time", "qav", "num_trades", "taker_base_vol", "taker_quote_vol", "ignore"
                    ])
                    for col in ["Open", "High", "Low", "Close", "Volume"]:
                        df[col] = df[col].astype(float)
                    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
                    df.set_index("open_time", inplace=True)
                    return df
        except Exception as e:
            print(f"Fetch Error ({symbol} {interval}): {e}", flush=True)
    return None

def calculate_adx(df, period=14):
    """Market sideways hai ya trending, yeh check karta hai (ADX > 22 = Strong Trend)"""
    high, low, close = df["High"], df["Low"], df["Close"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(period).mean()
    plus_di = 100 * (plus_dm.rolling(period).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(period).mean() / atr)
    dx = (np.abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    return dx.rolling(period).mean()

def get_dual_htf_bias(symbol):
    """4H + 1H Dual Timeframe Alignment (Dono same direction mein hone chahiye)"""
    df_1h = fetch_klines(symbol, "1h", limit=250)
    if df_1h is None or len(df_1h) < 210:
        return "NEUTRAL"

    df_1h["EMA50"] = df_1h["Close"].ewm(span=50, adjust=False).mean()
    df_1h["EMA200"] = df_1h["Close"].ewm(span=200, adjust=False).mean()
    last_1h = df_1h.iloc[-2]

    c_1h = float(last_1h["Close"])
    e50_1h = float(last_1h["EMA50"])
    e200_1h = float(last_1h["EMA200"])

    bias_1h = "NEUTRAL"
    if c_1h > e50_1h and e50_1h > e200_1h:
        bias_1h = "BULLISH"
    elif c_1h < e50_1h and e50_1h < e200_1h:
        bias_1h = "BEARISH"

    if bias_1h == "NEUTRAL":
        return "NEUTRAL"

    # 4H Structure Check
    df_4h = df_1h.resample("4h").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last"
    }).dropna()

    if len(df_4h) < 55:
        return "NEUTRAL"

    df_4h["EMA20"] = df_4h["Close"].ewm(span=20, adjust=False).mean()
    df_4h["EMA50"] = df_4h["Close"].ewm(span=50, adjust=False).mean()
    last_4h = df_4h.iloc[-2]

    c_4h = float(last_4h["Close"])
    e20_4h = float(last_4h["EMA20"])
    e50_4h = float(last_4h["EMA50"])

    if bias_1h == "BULLISH" and c_4h > e20_4h and e20_4h > e50_4h:
        return "BULLISH"
    elif bias_1h == "BEARISH" and c_4h < e20_4h and e20_4h < e50_4h:
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
        "🏛️ *Institutional Sniper Crypto Bot Live!*\n"
        "• *Pairs:* BTC, ETH, SOL, XRP, PEPE\n"
        "• *Filters:* 4H+1H Structure + Liquidity Sweep + Volume Spike + ADX\n"
        "• *Mode:* High-Accuracy Sniper Only (1:2.5 RR)\n"
        f"• *Risk:* `{RISK_PERCENT}%` (`~${ACCOUNT_BALANCE * (RISK_PERCENT / 100):.1f}` per trade)"
    )

    while True:
        now_utc = datetime.now(timezone.utc)
        active_pairs = 0

        for symbol in SYMBOLS:
            try:
                df = fetch_klines(symbol, "15m", limit=120)
                if df is None or len(df) < 60:
                    continue

                active_pairs += 1
                candle_time = df.index[-2]
                candle_id = f"{symbol}_{candle_time}"

                if last_alert_candles.get(symbol) == candle_id:
                    continue

                # Indicators
                df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
                df["VOL_MA"] = df["Volume"].rolling(20).mean()
                df["ADX"] = calculate_adx(df, 14)

                high_low = df["High"] - df["Low"]
                high_close = np.abs(df["High"] - df["Close"].shift())
                low_close = np.abs(df["Low"] - df["Close"].shift())
                tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
                df["ATR"] = tr.rolling(14).mean()

                row = df.iloc[-2]
                prev_window = df.iloc[-18:-2]  # Pichhli 16 candles ka swing zone

                open_p, close_p = float(row["Open"]), float(row["Close"])
                low_p, high_p = float(row["Low"]), float(row["High"])
                atr = float(row["ATR"])
                adx = float(row["ADX"])
                vol, vol_ma = float(row["Volume"]), float(row["VOL_MA"])
                ema50 = float(row["EMA50"])

                candle_range = high_p - low_p
                if pd.isna(atr) or pd.isna(adx) or candle_range <= 0:
                    continue

                # Filter 1: Sideways market block (ADX > 22 zaroori)
                if adx < 22:
                    continue

                # Filter 2: Institutional Volume Spike (1.3x average volume)
                if vol < (vol_ma * 1.3):
                    continue

                # Filter 3: 4H + 1H Dual Institutional Bias
                htf_bias = get_dual_htf_bias(symbol)
                if htf_bias == "NEUTRAL":
                    continue

                swing_low = float(prev_window["Low"].min())
                swing_high = float(prev_window["High"].max())
                risk_usd = ACCOUNT_BALANCE * (RISK_PERCENT / 100.0)
                candle_label = candle_time.strftime("%H:%M UTC")

                # 1. SNIPER BUY: 4H+1H Bullish + Swing Low Liquidity Sweep + Strong Rejection Wick
                swept_low = low_p < swing_low
                reclaimed_low = (close_p > swing_low) and (close_p > open_p)
                strong_bull_wick = (close_p - low_p) >= (candle_range * 0.60)
                near_value = close_p >= (ema50 - atr * 1.2)

                if htf_bias == "BULLISH" and swept_low and reclaimed_low and strong_bull_wick and near_value:
                    sweep_depth = close_p - low_p
                    sl_dist = max(sweep_depth + (atr * 0.4), atr * 1.4)
                    tp_dist = sl_dist * 2.5
                    pos_size_usd = round((risk_usd / (sl_dist / close_p)), 2)

                    entry_str = format_price(symbol, close_p)
                    sl_str = format_price(symbol, close_p - sl_dist)
                    tp_str = format_price(symbol, close_p + tp_dist)
                    last_alert_candles[symbol] = candle_id

                    send_telegram(
                        f"🟢 *SNIPER CRYPTO BUY: #{symbol}*\n"
                        f"_(4H+1H Bullish + Liquidity Sweep + Vol Spike)_\n\n"
                        f"• *Action:* BUY / LONG\n"
                        f"• *Entry:* `{entry_str}`\n"
                        f"• *Stop Loss (SL):* `{sl_str}`\n"
                        f"• *Take Profit (TP):* `{tp_str}` (1:2.5 RR)\n\n"
                        f"📊 *Risk Management (${ACCOUNT_BALANCE:.0f} Bal):*\n"
                        f"• *Suggested Position Value:* `${pos_size_usd}`\n"
                        f"• *Max Risk:* `${risk_usd:.2f}`\n\n"
                        f"🕒 *Candle:* {candle_label} | *ADX:* `{adx:.1f}`"
                    )

                # 2. SNIPER SELL: 4H+1H Bearish + Swing High Liquidity Sweep + Strong Rejection Wick
                elif htf_bias == "BEARISH":
                    swept_high = high_p > swing_high
                    reclaimed_high = (close_p < swing_high) and (close_p < open_p)
                    strong_bear_wick = (high_p - close_p) >= (candle_range * 0.60)
                    near_value = close_p <= (ema50 + atr * 1.2)

                    if swept_high and reclaimed_high and strong_bear_wick and near_value:
                        sweep_depth = high_p - close_p
                        sl_dist = max(sweep_depth + (atr * 0.4), atr * 1.4)
                        tp_dist = sl_dist * 2.5
                        pos_size_usd = round((risk_usd / (sl_dist / close_p)), 2)

                        entry_str = format_price(symbol, close_p)
                        sl_str = format_price(symbol, close_p + sl_dist)
                        tp_str = format_price(symbol, close_p - tp_dist)
                        last_alert_candles[symbol] = candle_id

                        send_telegram(
                            f"🔴 *SNIPER CRYPTO SELL: #{symbol}*\n"
                            f"_(4H+1H Bearish + Liquidity Sweep + Vol Spike)_\n\n"
                            f"• *Action:* SELL / SHORT\n"
                            f"• *Entry:* `{entry_str}`\n"
                            f"• *Stop Loss (SL):* `{sl_str}`\n"
                            f"• *Take Profit (TP):* `{tp_str}` (1:2.5 RR)\n\n"
                            f"📊 *Risk Management (${ACCOUNT_BALANCE:.0f} Bal):*\n"
                            f"• *Suggested Position Value:* `${pos_size_usd}`\n"
                            f"• *Max Risk:* `${risk_usd:.2f}`\n\n"
                            f"🕒 *Candle:* {candle_label} | *ADX:* `{adx:.1f}`"
                        )

            except Exception as e:
                print(f"Error ({symbol}): {e}", flush=True)

            time.sleep(2)

        print(f"[{now_utc.strftime('%H:%M')} UTC] Sniper scan completed ({active_pairs}/5 pairs live).", flush=True)
        time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    run_bot()


