import ccxt
import json
import os
from datetime import datetime
import matplotlib.pyplot as plt
import numpy as np
import time

# ============ 配置 ============
EXCHANGE_ID = "bybit"
TIMEFRAME   = "1h"
# ==============================

exchange = getattr(ccxt, EXCHANGE_ID)({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

BASE_DIR = os.path.dirname(__file__)
os.makedirs(os.path.join(BASE_DIR, 'charts'), exist_ok=True)

def get_sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

def plot_candlestick(ax, ohlcv, start_idx=0):
    width = 0.6
    for i, (timestamp, o, h, l, c, v) in enumerate(ohlcv):
        idx = start_idx + i
        color = 'g' if c >= o else 'r'
        ax.plot([idx, idx], [l, h], color=color, linewidth=1)
        height = abs(c - o)
        bottom = min(o, c)
        ax.bar(idx, height, width=width, bottom=bottom, color=color, edgecolor=color, linewidth=0.5)

def plot_ma_lines(ax, ohlcv, start_idx=0):
    closes = [c[4] for c in ohlcv]

    ma50_vals, ma150_vals, ma200_vals = [], [], []
    for i in range(len(closes)):
        ma50_vals.append(get_sma(closes[:i+1], 50))
        ma150_vals.append(get_sma(closes[:i+1], 150))
        ma200_vals.append(get_sma(closes[:i+1], 200))

    def plot_line(vals, color, label):
        valid = [(i, v) for i, v in enumerate(vals) if v is not None]
        if valid:
            idx, data = zip(*valid)
            ax.plot([i + start_idx for i in idx], data, color=color, label=label, linewidth=2, alpha=0.8)

    plot_line(ma50_vals, '#808080', 'MA50')
    plot_line(ma150_vals, '#00FFFF', 'MA150')
    plot_line(ma200_vals, '#FF0000', 'MA200')
    return ma50_vals, ma150_vals, ma200_vals

def plot_coin_chart(symbol, price, projected_vol):
    try:
        print(f"  [FETCH] Fetching data for {symbol}...")
        ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=210)
        time.sleep(0.1)

        if not ohlcv or len(ohlcv) < 50:
            print(f"    [WARN] Insufficient data for {symbol}")
            return False

        fig, ax = plt.subplots(figsize=(14, 7))
        plot_candlestick(ax, ohlcv, start_idx=0)
        plot_ma_lines(ax, ohlcv, start_idx=0)

        title = f"{symbol} 1H SHORT | Price: ${price:.4f} | Vol: ${projected_vol:,.0f} | Bear: MA200>MA150>MA50"
        ax.set_title(title, fontsize=13, fontweight='bold', pad=20)

        num_candles = len(ohlcv)
        step = max(1, num_candles // 10)
        ax.set_xticks(range(0, num_candles, step))
        ax.set_xticklabels([f"{i}" for i in range(0, num_candles, step)], rotation=45)
        ax.set_xlabel('1H Candle Index', fontsize=11)
        ax.set_ylabel('Price (USDT)', fontsize=11)
        ax.legend(loc='upper right', fontsize=11)
        ax.grid(True, alpha=0.3)
        ax.set_facecolor('#f8f9fa')

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        coin_name = symbol.split('/')[0]
        filepath = os.path.join(BASE_DIR, 'charts', f'{coin_name}_SHORT_{timestamp}.png')

        plt.tight_layout()
        plt.savefig(filepath, dpi=100, bbox_inches='tight', facecolor='white')
        plt.close()

        print(f"    [SAVED] {filepath}")
        return True

    except Exception as e:
        print(f"    [ERROR] Error plotting {symbol}: {e}")
        return False

def main():
    results_path = os.path.join(BASE_DIR, 'temp_results_short.json')
    try:
        with open(results_path, 'r') as f:
            results = json.load(f)
    except FileNotFoundError:
        print("[ERROR] temp_results_short.json not found. Run short_screener.py first.")
        return
    except Exception as e:
        print(f"[ERROR] Error reading temp_results_short.json: {e}")
        return

    if not results:
        print("[ERROR] No results found in temp_results_short.json")
        return

    print(f"\n[PLOT] Generating charts for {len(results)} coins...\n")

    success_count = 0
    for i, r in enumerate(results, 1):
        print(f"[{i}/{len(results)}] {r['symbol']}")
        if plot_coin_chart(r['symbol'], r['price'], r['projected_vol']):
            success_count += 1
        time.sleep(0.2)

    print(f"\n[DONE] Generated {success_count}/{len(results)} charts.")
    print(f"[FILE] Charts saved to: {os.path.join(BASE_DIR, 'charts')}")

if __name__ == '__main__':
    main()
