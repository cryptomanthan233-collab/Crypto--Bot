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
        self.wfile.write(b"24/7 Price Action Crypto Signal Bot is Running!")
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

def fetch_klines(symbol, interval, limit=220):
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

def add_momentum_indicators(df):
    df["EMA21"] = df["Close"].ewm(span=21, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["VOL_MA"] = df["Volume"].rolling(20).mean()

    # RSI (14)
    delta = df["Close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    df["RSI"] = 100 - (100 / (1 + (gain / loss)))

    # MACD (12, 26, 9)
    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_SIGNAL"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_HIST"] = df["MACD"] - df["MACD_SIGNAL"]

    # ADX (14)
    high, low, close = df["High"], df["Low"], df["Close"]
    plus_dm = high.diff().where((high.diff() > -low.diff()) & (high.diff() > 0), 0.0)
    minus_dm = (-low.diff()).where((-low.diff() > high.diff()) & (-low.diff() > 0), 0.0)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()
    plus_di = 100 * (plus_dm.rolling(14).mean() / df["ATR"])
    minus_di = 100 * (minus_dm.rolling(14).mean() / df["ATR"])
    dx = (np.abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    df["ADX"] = dx.rolling(14).mean()
    return df

def get_1h_trend(symbol):
    df_1h = fetch_klines(symbol, "1h", limit=220)
    if df_1h is None or len(df_1h) < 110:
        return "NEUTRAL"
    df_1h["EMA50"] = df_1h["Close"].ewm(span=50, adjust=False).mean()
    df_1h["EMA200"] = df_1h["Close"].ewm(span=200, adjust=False).mean()
    last = df_1h.iloc[-2]
    if float(last["Close"]) > float(last["EMA50"]) > float(last["EMA200"]):
        return "BULLISH"
    elif float(last["Close"]) < float(last["EMA50"]) < float(last["EMA200"]):
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
        "🎯 *Price Action Crypto Bot Upgraded!*\n"
        "• *Pairs:* BTC, ETH, SOL, XRP, PEPE\n"
        "• *Setups:* Support/Resistance Pullback + Volume Breakout\n"
        "• *Confirmations:* 1H Trend + RSI + MACD + ADX"
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

                htf_trend = get_1h_trend(symbol)
                if htf_trend == "NEUTRAL":
                    continue

                df = add_momentum_indicators(df)
                row = df.iloc[-2]
                prev_row = df.iloc[-3]
                sr_window = df.iloc[-22:-2]

                open_p, close_p = float(row["Open"]), float(row["Close"])
                low_p, high_p = float(row["Low"]), float(row["High"])
                ema21 = float(row["EMA21"])
                rsi, adx = float(row["RSI"]), float(row["ADX"])
                macd_hist, prev_hist = float(row["MACD_HIST"]), float(prev_row["MACD_HIST"])
                vol, vol_ma = float(row["Volume"]), float(row["VOL_MA"])
                atr = float(row["ATR"])

                if pd.isna(atr) or pd.isna(adx) or adx < 20:
                    continue

                key_resistance = float(sr_window["High"].max())
                key_support = float(sr_window["Low"].min())

                bull_momentum = (50 <= rsi <= 68) and (macd_hist > 0) and (macd_hist > prev_hist)
                bear_momentum = (32 <= rsi <= 50) and (macd_hist < 0) and (macd_hist < prev_hist)

                # Setup 1: Support & Pullback
                bull_pullback = (
                    (low_p <= ema21 or low_p <= key_support * 1.002)
                    and (close_p > ema21)
                    and (close_p > open_p)
                    and bull_momentum
                )
                bear_pullback = (
                    (high_p >= ema21 or high_p >= key_resistance * 0.998)
                    and (close_p < ema21)
                    and (close_p < open_p)
                    and bear_momentum
                )

                # Setup 2: Confirmed Breakout with Volume
                bull_breakout = (
                    (prev_row["Close"] <= key_resistance)
                    and (close_p > key_resistance)
                    and (close_p > open_p)
                    and (vol >= vol_ma * 1.25)
                    and bull_momentum
                )
                bear_breakout = (
                    (prev_row["Close"] >= key_support)
                    and (close_p < key_support)
                    and (close_p < open_p)
                    and (vol >= vol_ma * 1.25)
                    and bear_momentum
                )

                sl_dist = atr * 1.5
                tp_dist = sl_dist * 2.2
                risk_usd = ACCOUNT_BALANCE * (RISK_PERCENT / 100.0)
                pos_size_usd = round((risk_usd / (sl_dist / close_p)), 2)
                candle_label = candle_time.strftime("%H:%M UTC")

                if htf_trend == "BULLISH" and (bull_pullback or bull_breakout):
                    setup_name = "Confirmed Resistance Breakout" if bull_breakout else "Support & Pullback Bounce"
                    entry_str = format_price(symbol, close_p)
                    sl_str = format_price(symbol, close_p - sl_dist)
                    tp_str = format_price(symbol, close_p + tp_dist)
                    last_alert_candles[symbol] = candle_id

                    send_telegram(
                        f"🟢 *CRYPTO BUY SIGNAL: #{symbol}*\n"
                        f"• *Setup:* `{setup_name}`\n"
                        f"• *Momentum:* RSI `{rsi:.1f}` | ADX `{adx:.1f}` | MACD Bullish\n\n"
                        f"• *Entry:* `{entry_str}`\n"
                        f"• *Stop Loss (SL):* `{sl_str}`\n"
                        f"• *Take Profit (TP):* `{tp_str}` (1:2.2 RR)\n\n"
                        f"📊 *Risk (${ACCOUNT_BALANCE:.0f}):* Pos `${pos_size_usd}` | Max Risk `${risk_usd:.2f}`\n"
                        f"🕒 *Candle:* {candle_label}"
                    )

                elif htf_trend == "BEARISH" and (bear_pullback or bear_breakout):
                    setup_name = "Confirmed Support Breakdown" if bear_breakout else "Resistance & Pullback Rejection"
                    entry_str = format_price(symbol, close_p)
                    sl_str = format_price(symbol, close_p + sl_dist)
                    tp_str = format_price(symbol, close_p - tp_dist)
                    last_alert_candles[symbol] = candle_id

                    send_telegram(
                        f"🔴 *CRYPTO SELL SIGNAL: #{symbol}*\n"
                        f"• *Setup:* `{setup_name}`\n"
                        f"• *Momentum:* RSI `{rsi:.1f}` | ADX `{adx:.1f}` | MACD Bearish\n\n"
                        f"• *Entry:* `{entry_str}`\n"
                        f"• *Stop Loss (SL):* `{sl_str}`\n"
                        f"• *Take Profit (TP):* `{tp_str}` (1:2.2 RR)\n\n"
                        f"📊 *Risk (${ACCOUNT_BALANCE:.0f}):* Pos `${pos_size_usd}` | Max Risk `${risk_usd:.2f}`\n"
                        f"🕒 *Candle:* {candle_label}"
                    )

            except Exception as e:
                print(f"Error ({symbol}): {e}", flush=True)

            time.sleep(2)

        print(f"[{now_utc.strftime('%H:%M')} UTC] Crypto scan completed ({active_pairs}/5 pairs live).", flush=True)
        time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=start_web_server, daemon=True).start()
    run_bot()



