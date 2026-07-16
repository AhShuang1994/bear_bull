import sys, os
sys.stdout.reconfigure(encoding='utf-8')

# 直接 import 并调用 screen_all，不启动定时器
import ccxt
import requests
import time
import pytz
import json
from datetime import datetime

EXCHANGE_ID    = "bybit"
QUOTE_CURRENCY = "USDT"
MIN_DAILY_VOL  = 1_000_000
CHECK_15M = True
CHECK_30M = True
CHECK_1H  = True
CHECK_1D  = True

exchange = getattr(ccxt, EXCHANGE_ID)({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

def get_sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

def is_aligned(ohlcv):
    closes = [c[4] for c in ohlcv]
    ma50   = get_sma(closes, 50)
    ma150  = get_sma(closes, 150)
    ma200  = get_sma(closes, 200)
    if ma50 and ma150 and ma200:
        aligned = ma50 > ma150 > ma200
    else:
        aligned = False
    return aligned, ma50, ma150, ma200

def get_projected_daily_vol(ohlcv_1h, price):
    recent = ohlcv_1h[-24:] if len(ohlcv_1h) >= 24 else ohlcv_1h
    if not recent:
        return 0
    avg_hourly_vol = sum(c[5] for c in recent) / len(recent)
    return avg_hourly_vol * price * 24

def is_ma150_rising(ohlcv_1d):
    closes = [c[4] for c in ohlcv_1d]
    if len(closes) < 160:
        return False
    ma150_now  = get_sma(closes[-150:], 150)
    ma150_prev = get_sma(closes[-160:-10], 150)
    if not ma150_now or not ma150_prev:
        return False
    return ma150_now > ma150_prev

def check_symbol(symbol):
    try:
        ohlcv_1d_full = exchange.fetch_ohlcv(symbol, "1d", limit=500)
        actual_days   = len(ohlcv_1d_full)
        is_new_listing = actual_days < 200

        if actual_days < 1:
            return None

        price = ohlcv_1d_full[-1][4]

        ohlcv_1h = exchange.fetch_ohlcv(symbol, "1h", limit=210)
        time.sleep(0.1)

        projected_vol = get_projected_daily_vol(ohlcv_1h, price)
        if projected_vol < MIN_DAILY_VOL:
            return None

        tf_results = {}

        if CHECK_15M:
            ohlcv_15m = exchange.fetch_ohlcv(symbol, "15m", limit=210)
            aligned, ma50, ma150, ma200 = is_aligned(ohlcv_15m)
            tf_results["15m"] = {"aligned": aligned, "ma50": ma50, "ma150": ma150, "ma200": ma200}
            time.sleep(0.1)

        if CHECK_30M:
            ohlcv_30m = exchange.fetch_ohlcv(symbol, "30m", limit=210)
            aligned, ma50, ma150, ma200 = is_aligned(ohlcv_30m)
            tf_results["30m"] = {"aligned": aligned, "ma50": ma50, "ma150": ma150, "ma200": ma200}
            time.sleep(0.1)

        if CHECK_1H:
            aligned, ma50, ma150, ma200 = is_aligned(ohlcv_1h)
            tf_results["1h"] = {"aligned": aligned, "ma50": ma50, "ma150": ma150, "ma200": ma200}

        if CHECK_1D and not is_new_listing:
            ohlcv_1d = exchange.fetch_ohlcv(symbol, "1d", limit=210)
            time.sleep(0.1)

            if not is_ma150_rising(ohlcv_1d):
                return None

            aligned, ma50, ma150, ma200 = is_aligned(ohlcv_1d)
            tf_results["1d"] = {"aligned": aligned, "ma50": ma50, "ma150": ma150, "ma200": ma200}

        if not any(v["aligned"] for v in tf_results.values()):
            return None

        return {
            "symbol":        symbol,
            "price":         price,
            "is_new":        is_new_listing,
            "actual_days":   actual_days,
            "projected_vol": projected_vol,
            "tf":            tf_results,
        }

    except Exception as e:
        print(f"  Error {symbol}: {e}")
        return None

def run():
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    print(f"[SCREEN] Screening at {now}")

    try:
        markets = exchange.load_markets()
    except Exception as e:
        print(f"Failed to load markets: {e}")
        return

    symbols = [
        s for s, m in markets.items()
        if m.get('quote') == QUOTE_CURRENCY
        and m.get('swap', False)
        and m.get('linear', False)
        and m.get('active', False)
    ]

    print(f"   Scanning {len(symbols)} perpetual symbols...")
    results = []

    for symbol in symbols:
        print(f"  Checking {symbol}...", flush=True)
        result = check_symbol(symbol)
        if result:
            results.append(result)
            tag = "[NEW]" if result['is_new'] else "[OK]"
            print(f"  {tag} {symbol} ALIGNED!")
        time.sleep(0.15)

    results.sort(key=lambda x: x['projected_vol'], reverse=True)

    if results:
        old_listings = [r for r in results if not r['is_new']][:5]
        new_listings = [r for r in results if r['is_new']][:5]
        top_10 = old_listings + new_listings

        out_path = os.path.join(os.path.dirname(__file__), 'temp_results.json')
        with open(out_path, 'w') as f:
            json.dump(top_10, f, indent=2, default=str)

        print(f"\n[OK] Found {len(results)} aligned coins total")
        print(f"[OK] Saved top {len(top_10)} (Old:{len(old_listings)}, New:{len(new_listings)}) to temp_results.json")
    else:
        print("[FAIL] No coins matched all conditions.")

if __name__ == '__main__':
    run()
