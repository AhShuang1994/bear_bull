import ccxt
import json
import os
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import time

# ============ 配置 ============
EXCHANGE_ID = "bybit"
QUOTE_CURRENCY = "USDT"

exchange = getattr(ccxt, EXCHANGE_ID)({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

# 确保输出文件夹存在
os.makedirs('tradingMAScheduler/charts', exist_ok=True)

def get_sma(values, period):
    """计算简单移动平均线"""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

def plot_candlestick(ax, ohlcv, start_idx=0):
    """绘制K线"""
    width = 0.6
    width2 = 0.1

    for i, (timestamp, o, h, l, c, v) in enumerate(ohlcv):
        idx = start_idx + i
        color = 'g' if c >= o else 'r'

        # 绘制高低线 (wick)
        ax.plot([idx, idx], [l, h], color=color, linewidth=1)

        # 绘制开收线 (body)
        height = abs(c - o)
        bottom = min(o, c)
        ax.bar(idx, height, width=width, bottom=bottom, color=color, edgecolor=color, linewidth=0.5)

def plot_ma_lines(ax, ohlcv, start_idx=0):
    """计算并绘制 MA 线"""
    closes = [c[4] for c in ohlcv]

    # 计算 MA
    ma50_vals = []
    ma150_vals = []
    ma200_vals = []

    for i in range(len(closes)):
        ma50 = get_sma(closes[:i+1], 50)
        ma150 = get_sma(closes[:i+1], 150)
        ma200 = get_sma(closes[:i+1], 200)
        ma50_vals.append(ma50)
        ma150_vals.append(ma150)
        ma200_vals.append(ma200)

    # 绘制 MA 线
    indices = np.arange(len(closes)) + start_idx

    # MA50 (灰色)
    valid_ma50 = [(i, v) for i, v in enumerate(ma50_vals) if v is not None]
    if valid_ma50:
        ma50_idx, ma50_data = zip(*valid_ma50)
        ax.plot([i + start_idx for i in ma50_idx], ma50_data, color='#808080', label='MA50', linewidth=2, alpha=0.8)

    # MA150 (青色)
    valid_ma150 = [(i, v) for i, v in enumerate(ma150_vals) if v is not None]
    if valid_ma150:
        ma150_idx, ma150_data = zip(*valid_ma150)
        ax.plot([i + start_idx for i in ma150_idx], ma150_data, color='#00FFFF', label='MA150', linewidth=2, alpha=0.8)

    # MA200 (红色)
    valid_ma200 = [(i, v) for i, v in enumerate(ma200_vals) if v is not None]
    if valid_ma200:
        ma200_idx, ma200_data = zip(*valid_ma200)
        ax.plot([i + start_idx for i in ma200_idx], ma200_data, color='#FF0000', label='MA200', linewidth=2, alpha=0.8)

    return ma50_vals, ma150_vals, ma200_vals

def plot_coin_chart(symbol, price, is_new, projected_vol):
    """为单个币种生成 1H K线 + MA 线图表"""
    try:
        print(f"  [FETCH] Fetching data for {symbol}...")

        # 拉取 1H OHLCV 数据
        ohlcv = exchange.fetch_ohlcv(symbol, '1h', limit=210)
        time.sleep(0.1)

        if not ohlcv or len(ohlcv) < 50:
            print(f"    [WARN] Insufficient data for {symbol}")
            return False

        # 创建图表
        fig, ax = plt.subplots(figsize=(14, 7))

        # 绘制 K线
        plot_candlestick(ax, ohlcv, start_idx=0)

        # 绘制并获取最后的 MA 值用于标题
        ma50_vals, ma150_vals, ma200_vals = plot_ma_lines(ax, ohlcv, start_idx=0)

        # 获取最新的 MA 值
        latest_ma50 = ma50_vals[-1] if ma50_vals[-1] is not None else 0
        latest_ma150 = ma150_vals[-1] if ma150_vals[-1] is not None else 0
        latest_ma200 = ma200_vals[-1] if ma200_vals[-1] is not None else 0

        # 设置标题和标签
        tag = "[NEW]" if is_new else "[OLD]"
        title = f"{symbol} 1H | Price: ${price:.4f} {tag} | Vol: ${projected_vol:,.0f}"
        ax.set_title(title, fontsize=14, fontweight='bold', pad=20)

        # 设置 X 轴标签
        num_candles = len(ohlcv)
        step = max(1, num_candles // 10)
        ax.set_xticks(range(0, num_candles, step))
        ax.set_xticklabels([f"{i}" for i in range(0, num_candles, step)], rotation=45)
        ax.set_xlabel('1H Candle Index', fontsize=11)
        ax.set_ylabel('Price (USDT)', fontsize=11)

        # 添加图例
        ax.legend(loc='upper left', fontsize=11)
        ax.grid(True, alpha=0.3)
        ax.set_facecolor('#f8f9fa')

        # 保存图表
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        coin_name = symbol.split('/')[0]
        tag = 'NEW' if is_new else 'OLD'
        filepath = f'tradingMAScheduler/charts/{coin_name}_{tag}_{timestamp}.png'

        plt.tight_layout()
        plt.savefig(filepath, dpi=100, bbox_inches='tight', facecolor='white')
        plt.close()

        print(f"    [SAVED] {filepath}")
        return True

    except Exception as e:
        print(f"    [ERROR] Error plotting {symbol}: {e}")
        return False

def main():
    """主函数：读取 temp_results.json 并生成所有图表"""

    # 读取 temp_results.json
    try:
        with open('tradingMAScheduler/temp_results.json', 'r') as f:
            results = json.load(f)
    except FileNotFoundError:
        print("❌ temp_results.json not found. Run main.py first to generate results.")
        return
    except Exception as e:
        print(f"❌ Error reading temp_results.json: {e}")
        return

    if not results:
        print("❌ No results found in temp_results.json")
        return

    print(f"\n[PLOT] Generating charts for {len(results)} coins...\n")

    success_count = 0
    for i, r in enumerate(results, 1):
        symbol = r['symbol']
        price = r['price']
        is_new = r['is_new']
        projected_vol = r['projected_vol']

        print(f"[{i}/{len(results)}] {symbol}")

        if plot_coin_chart(symbol, price, is_new, projected_vol):
            success_count += 1

        time.sleep(0.2)

    print(f"\n[DONE] Done! Generated {success_count}/{len(results)} charts.")
    print(f"[FILE] Charts saved to: tradingMAScheduler/charts/")

if __name__ == '__main__':
    main()
