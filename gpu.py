#!/usr/bin/env python3
# GPU — Quantitative Spot Signal Hunter
# Signal-only assistant for Binance Spot. This program NEVER places orders.

import os
import time
import json
import sqlite3
import logging
from datetime import datetime, timezone

import ccxt
import pandas as pd
import requests

APP_NAME = "GPU"
EXCHANGE_ID = "binance"
TIMEFRAMES = ["15m", "30m", "1h", "4h"]
SCAN_EVERY_SECONDS = 30 * 60
MIN_SCORE = 80
MIN_RR = 2.0
RISK_FRACTION = 0.01  # Illustrative risk sizing: 1% of user-defined account value
MAX_POSITION_FRACTION = 0.10
DB_FILE = "gpu_signals.sqlite3"

# Binance Spot USDT markets. The bot checks which pairs are currently available.
WATCHLIST = [
    "BTC/USDT", "SOL/USDT", "RENDER/USDT", "NEAR/USDT", "PROM/USDT",
    "LINK/USDT", "LTC/USDT", "ZEC/USDT", "DEXE/USDT", "HYPE/USDT",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | GPU | %(levelname)s | %(message)s",
)
log = logging.getLogger(APP_NAME)


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing {name}. Add it to your Codespaces Secrets and restart the Codespace."
        )
    return value


def telegram_send(token: str, chat_id: str, message: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    response = requests.post(
        url,
        json={"chat_id": chat_id, "text": message, "disable_web_page_preview": True},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError("Telegram API did not confirm message delivery.")


def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_signals (
                signal_key TEXT PRIMARY KEY,
                sent_at TEXT NOT NULL
            )
        """)
        conn.commit()


def already_sent(signal_key: str) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        row = conn.execute(
            "SELECT 1 FROM sent_signals WHERE signal_key = ?", (signal_key,)
        ).fetchone()
        return row is not None


def remember_signal(signal_key: str):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sent_signals(signal_key, sent_at) VALUES (?, ?)",
            (signal_key, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def fetch_frame(exchange, symbol: str, timeframe: str, limit: int = 220):
    rows = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    if not rows or len(rows) < 80:
        raise ValueError(f"Not enough candles for {symbol} {timeframe}")
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    # Exclude the currently forming candle to avoid premature signals.
    return df.iloc[:-1].copy().reset_index(drop=True)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    close = d["close"]
    d["ema20"] = close.ewm(span=20, adjust=False).mean()
    d["ema50"] = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    d["rsi"] = 100 - (100 / (1 + rs))
    d["rsi"] = d["rsi"].fillna(50)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    d["macd"] = ema12 - ema26
    d["macd_signal"] = d["macd"].ewm(span=9, adjust=False).mean()

    prev_close = close.shift(1)
    tr = pd.concat([
        d["high"] - d["low"],
        (d["high"] - prev_close).abs(),
        (d["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()
    d["vol_sma20"] = d["volume"].rolling(20).mean()
    d["bb_mid"] = close.rolling(20).mean()
    d["bb_std"] = close.rolling(20).std()
    d["bb_lower"] = d["bb_mid"] - 2 * d["bb_std"]
    d["bb_upper"] = d["bb_mid"] + 2 * d["bb_std"]
    return d.dropna().reset_index(drop=True)


def timeframe_score(df: pd.DataFrame) -> tuple[int, list[str]]:
    r = df.iloc[-1]
    prev = df.iloc[-2]
    score = 0
    reasons = []

    if r["close"] > r["ema20"]:
        score += 15
        reasons.append("السعر فوق EMA20")
    if r["ema20"] > r["ema50"]:
        score += 20
        reasons.append("EMA20 أعلى من EMA50")
    if r["macd"] > r["macd_signal"] and r["macd"] > prev["macd"]:
        score += 15
        reasons.append("زخم MACD إيجابي")
    if 45 <= r["rsi"] <= 68:
        score += 15
        reasons.append(f"RSI متوازن ({r['rsi']:.1f})")
    if r["volume"] > r["vol_sma20"] * 1.15:
        score += 15
        reasons.append("حجم التداول أعلى من متوسطه")
    if r["close"] > r["bb_mid"]:
        score += 10
        reasons.append("السعر أعلى متوسط Bollinger")
    if r["close"] > prev["high"]:
        score += 10
        reasons.append("إغلاق أعلى قمة الشمعة السابقة")
    return min(score, 100), reasons


def correlation_beta(exchange, symbol: str):
    if symbol == "BTC/USDT":
        return {"7d": (1.0, 1.0), "30d": (1.0, 1.0), "90d": (1.0, 1.0)}
    try:
        alt = fetch_frame(exchange, symbol, "4h", limit=600)
        btc = fetch_frame(exchange, "BTC/USDT", "4h", limit=600)
        a = alt[["timestamp", "close"]].copy().set_index("timestamp")["close"].pct_change()
        b = btc[["timestamp", "close"]].copy().set_index("timestamp")["close"].pct_change()
        joined = pd.concat([a.rename("alt"), b.rename("btc")], axis=1).dropna()
        result = {}
        for label, periods in [("7d", 42), ("30d", 180), ("90d", 540)]:
            sample = joined.tail(periods)
            if len(sample) < max(20, periods // 2):
                result[label] = (None, None)
                continue
            corr = sample["alt"].corr(sample["btc"])
            variance = sample["btc"].var()
            beta = sample["alt"].cov(sample["btc"]) / variance if variance and variance > 0 else None
            result[label] = (
                float(corr) if pd.notna(corr) else None,
                float(beta) if pd.notna(beta) else None,
            )
        return result
    except Exception as exc:
        log.warning("BTC relationship calculation failed for %s: %s", symbol, exc)
        return {"7d": (None, None), "30d": (None, None), "90d": (None, None)}


def build_signal(symbol: str, frames: dict, relationship: dict):
    # Directional context: 1h and 4h must both be bullish.
    score_15, reasons_15 = timeframe_score(frames["15m"])
    score_30, reasons_30 = timeframe_score(frames["30m"])
    score_1h, reasons_1h = timeframe_score(frames["1h"])
    score_4h, reasons_4h = timeframe_score(frames["4h"])

    if score_1h < 50 or score_4h < 50:
        return None

    # Higher timeframes have greater weight; lower timeframes time the setup.
    combined = round(
        score_15 * 0.20 + score_30 * 0.20 + score_1h * 0.25 + score_4h * 0.35
    )
    if combined < MIN_SCORE:
        return None

    d15 = frames["15m"]
    last = d15.iloc[-1]
    entry = float(last["close"])
    atr = float(last["atr"])
    recent_low = float(d15.tail(14)["low"].min())
    stop = min(recent_low, entry - 1.5 * atr)
    risk_per_unit = entry - stop
    if risk_per_unit <= 0 or atr <= 0:
        return None

    # Conservative illustrative targets; not guaranteed fills or outcomes.
    target1 = entry + 2.0 * risk_per_unit
    target2 = entry + 3.0 * risk_per_unit
    rr = (target1 - entry) / risk_per_unit
    if rr < MIN_RR:
        return None

    corr30, beta30 = relationship.get("30d", (None, None))
    return {
        "symbol": symbol,
        "score": combined,
        "entry": entry,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "rr": rr,
        "atr_pct": atr / entry * 100,
        "reasons": (reasons_1h[:2] + reasons_4h[:2] + reasons_30[:2] + reasons_15[:2]),
        "corr30": corr30,
        "beta30": beta30,
        "candle_time": int(last["timestamp"]),
    }


def fmt_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:.4f}"
    return f"{value:.8f}"


def signal_message(signal: dict) -> str:
    corr = "غير متاح" if signal["corr30"] is None else f"{signal['corr30']:.2f}"
    beta = "غير متاح" if signal["beta30"] is None else f"{signal['beta30']:.2f}"
    reasons = "\n".join(f"• {x}" for x in signal["reasons"][:6])
    return (
        "🦅 GPU | فرصة شراء محتملة — Binance Spot\n\n"
        f"الزوج: {signal['symbol']}\n"
        f"تقييم توافق المؤشرات: {signal['score']}/100\n"
        f"السعر المرجعي: {fmt_price(signal['entry'])}\n"
        f"منطقة الدخول المرجعية: قرب {fmt_price(signal['entry'])}\n"
        f"وقف الخسارة التحليلي: {fmt_price(signal['stop'])}\n"
        f"الهدف الأول: {fmt_price(signal['target1'])}\n"
        f"الهدف الثاني: {fmt_price(signal['target2'])}\n"
        f"العائد/المخاطرة إلى الهدف الأول: 1:{signal['rr']:.1f}\n"
        f"ATR: {signal['atr_pct']:.2f}%\n\n"
        "علاقة البيتكوين (4h، آخر 30 يوم):\n"
        f"Correlation: {corr} | Beta: {beta}\n\n"
        f"أسباب الإشارة:\n{reasons}\n\n"
        "إلغاء الفكرة: إغلاق شمعة 15m دون وقف الخسارة التحليلي.\n"
        "هذه إشارة تحليلية وليست ضمان ربح أو أمراً بالتنفيذ. تحقق من السعر والسيولة والرسوم بنفسك.\n"
        "GPU لا يشتري ولا يبيع ولا يرسل أوامر إلى Binance."
    )


def run_scan(exchange, token, chat_id):
    log.info("Starting GPU scan.")
    try:
        exchange.load_markets()
    except Exception as exc:
        log.error("Could not load Binance markets: %s", exc)
        return

    available = [s for s in WATCHLIST if s in exchange.markets and exchange.markets[s].get("spot")]
    missing = [s for s in WATCHLIST if s not in available]
    if missing:
        log.warning("Unavailable Spot pairs skipped: %s", ", ".join(missing))

    found = []
    for symbol in available:
        try:
            frames = {}
            for timeframe in TIMEFRAMES:
                raw = fetch_frame(exchange, symbol, timeframe)
                frames[timeframe] = add_indicators(raw)
            relationship = correlation_beta(exchange, symbol)
            signal = build_signal(symbol, frames, relationship)
            if signal:
                found.append(signal)
        except Exception as exc:
            log.warning("Skipping %s due to data/analysis issue: %s", symbol, exc)

    if not found:
        log.info("No qualifying signals this scan.")
        # Keep quiet on every scan to avoid Telegram spam; report status in console.
        return

    found.sort(key=lambda x: x["score"], reverse=True)
    for signal in found:
        key = f"{signal['symbol']}:{signal['candle_time']}:{signal['score']}"
        if already_sent(key):
            continue
        try:
            telegram_send(token, chat_id, signal_message(signal))
            remember_signal(key)
            log.info("Telegram signal sent for %s (score %s).", signal["symbol"], signal["score"])
        except Exception as exc:
            log.error("Telegram send failed for %s: %s", signal["symbol"], exc)


def main():
    token = env_required("TELEGRAM_BOT_TOKEN")
    chat_id = env_required("TELEGRAM_CHAT_ID")
    init_db()

    exchange = ccxt.binance({
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })

    telegram_send(
        token,
        chat_id,
        "🦅 GPU بدأ التشغيل.\n"
        "وضع إشارات فقط — Binance Spot.\n"
        "لا توجد صلاحية شراء أو بيع، ولا يتم إرسال أوامر تداول.\n"
        "الفحص كل 30 دقيقة على 15m و30m و1h و4h."
    )

    while True:
        started = time.time()
        run_scan(exchange, token, chat_id)
        elapsed = time.time() - started
        time.sleep(max(60, SCAN_EVERY_SECONDS - elapsed))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("GPU stopped by user.")
    except Exception as exc:
        log.exception("GPU stopped: %s", exc)
        raise
