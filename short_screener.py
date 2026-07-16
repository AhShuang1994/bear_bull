import sys, os
sys.stdout.reconfigure(encoding='utf-8')

import ccxt
import time
import json
from datetime import datetime

# ============ 配置 ============
EXCHANGE_ID    = "bybit"
QUOTE_CURRENCY = "USDT"
MIN_DAILY_VOL  = 1_000_000
TIMEFRAME      = "1h"
# ==============================

exchange = getattr(ccxt, EXCHANGE_ID)({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

def get_sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

def is_bear_aligned(ohlcv):
    """
    空头排列：MA200 > MA150 > MA50
    """
    closes = [c[4] for c in ohlcv]
    ma50   = get_sma(closes, 50)
    ma150  = get_sma(closes, 150)
    ma200  = get_sma(closes, 200)
    if ma50 and ma150 and ma200:
        aligned = ma200 > ma150 > ma50
    else:
        aligned = False
    return aligned, ma50, ma150, ma200

def get_projected_daily_vol(ohlcv_1h, price):
    recent = ohlcv_1h[-24:] if len(ohlcv_1h) >= 24 else ohlcv_1h
    if not recent:
        return 0
    avg_hourly_vol = sum(c[5] for c in recent) / len(recent)
    return avg_hourly_vol * price * 24

def is_ma150_falling(ohlcv):
    """
    确认下降趋势：现在的 MA150 < 10 根前的 MA150（1H 周期）
    """
    closes = [c[4] for c in ohlcv]
    if len(closes) < 160:
        return False
    ma150_now  = get_sma(closes[-150:], 150)
    ma150_prev = get_sma(closes[-160:-10], 150)
    if not ma150_now or not ma150_prev:
        return False
    return ma150_now < ma150_prev

def check_symbol(symbol):
    try:
        ohlcv_1h = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=210)
        if len(ohlcv_1h) < 1:
            return None

        price = ohlcv_1h[-1][4]

        projected_vol = get_projected_daily_vol(ohlcv_1h, price)
        if projected_vol < MIN_DAILY_VOL:
            return None

        # 下降趋势确认
        if not is_ma150_falling(ohlcv_1h):
            return None

        aligned, ma50, ma150, ma200 = is_bear_aligned(ohlcv_1h)
        if not aligned:
            return None

        return {
            "symbol":        symbol,
            "price":         price,
            "projected_vol": projected_vol,
            "ma50":          ma50,
            "ma150":         ma150,
            "ma200":         ma200,
        }

    except Exception as e:
        print(f"  Error {symbol}: {e}")
        return None

def run():
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    print(f"[SCREEN-SHORT] Screening at {now} | TF={TIMEFRAME} | Bear alignment: MA200>MA150>MA50")

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
            print(f"  [BEAR] {symbol} ALIGNED!")
        time.sleep(0.15)

    results.sort(key=lambda x: x['projected_vol'], reverse=True)
    top5 = results[:5]

    out_path = os.path.join(os.path.dirname(__file__), 'temp_results_short.json')
    with open(out_path, 'w') as f:
        json.dump(top5, f, indent=2, default=str)

    if results:
        print(f"\n[OK] Found {len(results)} bear-aligned coins total")
        print(f"[OK] Saved top {len(top5)} (by projected vol) to temp_results_short.json")
    else:
        print("[FAIL] No coins matched all conditions.")

if __name__ == '__main__':
    run()
