import sys
sys.stdout.reconfigure(encoding='utf-8')

import ccxt
import time
import json
import os

EXCHANGE_ID    = "bybit"
QUOTE_CURRENCY = "USDT"
MIN_DAILY_VOL  = 1_000_000
TIMEFRAME      = "1h"
LIMIT          = 1000

exchange = getattr(ccxt, EXCHANGE_ID)({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

def get_sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

def calc_sma_series(closes, period):
    result = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(closes[i-period+1:i+1]) / period)
    return result

def is_c_candle_bear(candle):
    o, h, l, c = candle[1], candle[2], candle[3], candle[4]
    body = abs(c - o)
    wick = (h - l) - body
    is_green = c > o
    is_long_wick = wick > body if body > 0 else True
    return is_green or is_long_wick

def find_c_pattern(candles):
    for i in range(2, len(candles)):
        if (is_c_candle_bear(candles[i])
                and is_c_candle_bear(candles[i-1])
                and is_c_candle_bear(candles[i-2])):
            return i
    return None

def get_projected_daily_vol(ohlcv, price):
    recent = ohlcv[-24:] if len(ohlcv) >= 24 else ohlcv
    if not recent:
        return 0
    avg_hourly_vol = sum(c[5] for c in recent) / len(recent)
    return avg_hourly_vol * price * 24

def find_c1_c2_bear(ohlcv):
    n = len(ohlcv)
    if n < 210:
        return None

    closes = [c[4] for c in ohlcv]
    highs  = [c[2] for c in ohlcv]
    lows   = [c[3] for c in ohlcv]
    ma50_series  = calc_sma_series(closes, 50)
    ma150_series = calc_sma_series(closes, 150)
    ma200_series = calc_sma_series(closes, 200)

    reset_end_idx = None
    for i in range(201, n):
        ma50, ma150, ma200 = ma50_series[i], ma150_series[i], ma200_series[i]
        if not all([ma50, ma150, ma200]):
            continue
        if not (ma200 > ma150 > ma50):
            continue
        had_golden_cross = False
        for j in range(200, i):
            m150_j, m200_j = ma150_series[j], ma200_series[j]
            m150_jp, m200_jp = ma150_series[j-1], ma200_series[j-1]
            if not all([m150_j, m200_j, m150_jp, m200_jp]):
                continue
            if m150_jp <= m200_jp and m150_j > m200_j:
                had_golden_cross = True
                break
        if had_golden_cross:
            reset_end_idx = i
            break

    if reset_end_idx is None:
        return None

    search_candles = ohlcv[reset_end_idx:]
    if len(search_candles) < 10:
        return None

    lowest_idx_rel = 0
    lowest_price = lows[reset_end_idx]
    for i in range(len(search_candles)):
        idx = reset_end_idx + i
        if lows[idx] < lowest_price:
            lowest_price = lows[idx]
            lowest_idx_rel = i
    lowest_idx = reset_end_idx + lowest_idx_rel

    search_start = lowest_idx
    if search_start >= n - 5:
        return None

    c1_candles = ohlcv[search_start:]
    c_end_idx = find_c_pattern(c1_candles)
    if c_end_idx is None:
        return None

    c1_segment  = c1_candles[:c_end_idx + 1]
    c1_high     = max(c[2] for c in c1_segment)
    c1_high_idx = search_start + max(range(len(c1_segment)), key=lambda i: c1_segment[i][2])
    c1_low      = lowest_price
    c1_low_idx  = lowest_idx

    ma50_at_c1 = ma50_series[c1_high_idx]
    if not ma50_at_c1:
        return None
    touched_ma50 = c1_high >= ma50_at_c1

    if c1_low == 0:
        return None
    bounce_pct = (c1_high - c1_low) / c1_low
    if bounce_pct > 0.37:
        return None
    if not touched_ma50:
        return None

    c1_result = {"low": c1_low, "low_idx": c1_low_idx, "high": c1_high, "high_idx": c1_high_idx, "bounce": bounce_pct}

    c2_start_idx = c1_high_idx + 1
    if c2_start_idx >= n - 5:
        return None

    c2_candles = ohlcv[c2_start_idx:]
    c2_c_idx = find_c_pattern(c2_candles)
    if c2_c_idx is None:
        return None

    c2_segment = c2_candles[:c2_c_idx + 1]
    c2_high    = max(c[2] for c in c2_segment)
    c2_low     = min(c[3] for c in c2_segment)
    c2_high_idx = c2_start_idx + max(range(len(c2_segment)), key=lambda i: c2_segment[i][2])
    c2_low_idx  = c2_start_idx + min(range(len(c2_segment)), key=lambda i: c2_segment[i][3])

    if not (c1_result["low"] < c2_high < c1_result["high"]):
        return None

    if c2_low == 0:
        return None
    contraction = (c2_high - c2_low) / c2_low
    if contraction >= 0.10:
        return None

    c2_result = {"high": c2_high, "high_idx": c2_high_idx, "low": c2_low, "low_idx": c2_low_idx, "contraction": contraction}

    return {"reset_idx": reset_end_idx, "bottom_idx": lowest_idx, "c1": c1_result, "c2": c2_result}

def run():
    print(f"[SCAN] Bear C1/C2 scan | TF={TIMEFRAME} | limit={LIMIT}")
    markets = exchange.load_markets()
    symbols = [
        s for s, m in markets.items()
        if m.get('quote') == QUOTE_CURRENCY
        and m.get('swap', False)
        and m.get('linear', False)
        and m.get('active', False)
    ]
    print(f"   Scanning {len(symbols)} perpetual symbols...")

    found = []
    for symbol in symbols:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=LIMIT)
            time.sleep(0.1)
            if len(ohlcv) < 210:
                continue
            price = ohlcv[-1][4]
            vol = get_projected_daily_vol(ohlcv, price)
            if vol < MIN_DAILY_VOL:
                continue
            pattern = find_c1_c2_bear(ohlcv)
            if pattern:
                print(f"  [MATCH] {symbol}  bounce={pattern['c1']['bounce']*100:.1f}%  contraction={pattern['c2']['contraction']*100:.1f}%  vol=${vol:,.0f}")
                found.append({"symbol": symbol, "price": price, "vol": vol, "pattern": pattern})
        except Exception as e:
            print(f"  Error {symbol}: {e}")
        time.sleep(0.05)

    print(f"\n[DONE] {len(found)} symbols with valid bear C1/C2 pattern")
    out_path = os.path.join(os.path.dirname(__file__), 'c1c2_bear_matches.json')
    with open(out_path, 'w') as f:
        json.dump(found, f, indent=2, default=str)
    print(f"[SAVED] {out_path}")

if __name__ == '__main__':
    run()
