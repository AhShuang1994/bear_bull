import sys
sys.stdout.reconfigure(encoding='utf-8')

import ccxt
import time
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import os

EXCHANGE_ID  = "bybit"
SYMBOL       = sys.argv[1] if len(sys.argv) > 1 else "DOGE/USDT:USDT"
TIMEFRAME    = "1h"
LIMIT        = 2000  # 约83天的1H K线，Bybit单次上限1000根，分页2次拿够
# 只保留到这根K线为止（含），方便看图；非数字参数一律忽略（CMD把#注释当参数传进来过）
try:
    TRUNCATE_END = int(sys.argv[2]) if len(sys.argv) > 2 else None
except ValueError:
    TRUNCATE_END = None

exchange = getattr(ccxt, EXCHANGE_ID)({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

BASE_DIR = os.path.dirname(__file__)

BASE_FILL_COLOR = '#FFD700'   # Base 区域专属颜色（金色），和 C 段的五色循环区分
BASE_EDGE_COLOR = '#B8860B'

def fetch_ohlcv_paginated(symbol, timeframe, total):
    # Bybit kline 接口单次最多返回1000根，超过的部分要用 since 分页往后接
    tf_ms = exchange.parse_timeframe(timeframe) * 1000
    since = exchange.milliseconds() - total * tf_ms
    all_candles = []
    while len(all_candles) < total:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not batch:
            break
        all_candles += batch
        since = batch[-1][0] + tf_ms
        if len(batch) < 1000:
            break
    return all_candles[-total:]

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
    """
    空头版 C 条件：绿色蜡烛（收涨，反弹）或影线长过身体
    """
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

def find_leg_high(ohlcv, highs, lows, ma50_series, ma150_series, ma200_series, n, low_idx, low_price):
    """
    从给定低点开始找这段区间的最高点：区域终点 = 出现新低(跌破low_price) 或 均线排列翻转，
    两者谁先发生就在哪截止，没有"不能超过前高"这种天花板限制（那个规则由调用方处理）
    """
    if low_idx >= n - 1:
        return None

    region_end = n - 1
    for i in range(low_idx + 1, n):
        if lows[i] < low_price:
            region_end = i - 1
            break
        m150, m200 = ma150_series[i], ma200_series[i]
        # 排列翻转只看慢速对：MA150上穿MA200才算趋势翻转。
        # MA50与MA150的相互穿越不是结构事件，不截断反弹区域
        # （SIREN案例：07-06的合格C1曾被50x150穿越误杀，2026-07-10定稿）
        if all([m150, m200]) and not (m200 > m150):
            region_end = i - 1
            break

    if region_end <= low_idx:
        return None

    # 高点候选从低点【当根】算起（07-09 SIREN规则，2026-07-16确认原意）：
    # 接下来区域内的K线高点都没高过低点当根的高点时，取当根上影线——区域内没有更高的点。
    # 前提：C的成立条件（3连确认等）都要符合；确认组归本C所有，不再喂给下一个C
    region = ohlcv[low_idx:region_end + 1]
    high     = max(c[2] for c in region)
    high_idx = low_idx + max(range(len(region)), key=lambda i: region[i][2])
    return high, high_idx, region_end

def find_bases(ohlcv):
    """
    空头 VCP 滚动 Base 检测（v4 逻辑，2026-07-21 由 c1c2_v4.py 合并进主引擎）：
    - 统一确认：C1~C6 都用 find_c —— 从段起点逐根跟踪最低点(锚点)，从锚点数streak
      (锚点当根合格即算第一根)，连续3根确认；C1 额外保留 MA50 关(require_ma50)。
    - 段起点统一 = 上一个C的 box_end+1（不再区分高点+1/同根C例外/主动创新低）。
    - Reset / 跌破C1低点封口 / >6C标弱 / 已收盘纪律(ohlcv[:-1]) 同前。
    历史沿革 v2(C1拉平)→v3(移除主动创新)→v4(统一+boxEnd+1)，见 BEAR_C1C2_NOTES.md §6.7。
    """
    ohlcv = ohlcv[:-1]
    n = len(ohlcv)
    if n < 210:
        return None
    closes = [c[4] for c in ohlcv]; highs = [c[2] for c in ohlcv]; lows = [c[3] for c in ohlcv]
    ma50_series = calc_sma_series(closes, 50)
    ma150_series = calc_sma_series(closes, 150)
    ma200_series = calc_sma_series(closes, 200)
    reset_end_idx = None; last_cross_idx = None; cross_used = False
    for i in range(200, n):
        m150_p, m200_p = ma150_series[i-1], ma200_series[i-1]
        m150_i, m200_i = ma150_series[i], ma200_series[i]
        if all([m150_p, m200_p, m150_i, m200_i]) and m150_p <= m200_p and m150_i > m200_i:
            last_cross_idx = i; cross_used = False
        ma50 = ma50_series[i]
        if not all([ma50, m150_i, m200_i]):
            continue
        if last_cross_idx is not None and not cross_used and m200_i > m150_i > ma50:
            reset_end_idx = i; cross_used = True
    if reset_end_idx is None:
        return None

    def find_c(start, require_ma50):
        # 统一：从 start 逐根跟踪最低点(锚点)，从锚点数 streak(锚点当第一根)，连续3根确认。
        low_idx = None; low_under = False; failed = None; streak = 0; confirmed = False
        for i in range(start, n):
            if low_idx is None or lows[i] < lows[low_idx]:
                low_idx = i
                m = ma50_series[i]
                low_under = m is not None and ohlcv[i][4] < m
                streak = 1 if is_c_candle_bear(ohlcv[i]) else 0
                confirmed = False
                continue
            if is_c_candle_bear(ohlcv[i]):
                streak += 1
                if streak >= 3:
                    confirmed = True
            else:
                streak = 0
            ready = confirmed and (low_under if require_ma50 else True)
            if not ready or low_idx == failed:
                continue
            leg = find_leg_high(ohlcv, highs, lows, ma50_series, ma150_series, ma200_series, n,
                                low_idx, lows[low_idx])
            if leg is None:
                failed = low_idx; continue
            high, high_idx, region_end = leg
            if i > region_end:
                failed = low_idx; continue
            if require_ma50:
                touched = any(ma50_series[j] is not None and highs[j] >= ma50_series[j]
                              for j in range(low_idx, high_idx + 1))
                if not touched:
                    failed = low_idx; continue
            return {"low": lows[low_idx], "low_idx": low_idx, "high": high, "high_idx": high_idx,
                    "contraction": (high - lows[low_idx]) / lows[low_idx] if lows[low_idx] else 0,
                    "box_end_idx": max(high_idx, i), "confirm_at": i}
        return None

    bases = []; open_chain = None; search_start = reset_end_idx
    while search_start < n - 5:
        raw = find_c(search_start, True)
        if raw is None:
            break
        c1 = {"low": raw["low"], "low_idx": raw["low_idx"], "high": raw["high"],
              "high_idx": raw["high_idx"], "bounce": raw["contraction"], "box_end_idx": raw["box_end_idx"]}
        chain = [c1]; c1_low = c1['low']; outcome = None; guard = 0
        while True:
            guard += 1
            if guard > n:
                outcome = 'open'; break
            ref = chain[-1]
            seg_start = ref['box_end_idx'] + 1
            if seg_start >= n:
                outcome = 'open'; break
            breakout_idx = None
            for i in range(seg_start, n):
                if lows[i] < c1_low:
                    breakout_idx = i; break
            cand = find_c(seg_start, False)
            confirm_at = cand['confirm_at'] if cand else None
            if breakout_idx is not None and (confirm_at is None or breakout_idx <= confirm_at):
                outcome = ('sealed', breakout_idx); break
            if cand is None:
                outcome = 'open'; break
            cand_low = cand['low']; cand_low_idx = cand['low_idx']
            cand_high = cand['high']; cand_high_idx = cand['high_idx']; cand_end = cand['box_end_idx']
            cand_pct = cand['contraction']; ref_pct = ref.get('contraction', ref.get('bounce'))
            if cand_high >= ref['high'] or (ref_pct is not None and cand_pct >= ref_pct):
                ref['high'] = cand_high; ref['high_idx'] = cand_high_idx
                if 'bounce' in ref:
                    ref['bounce'] = (cand_high - ref['low']) / ref['low'] if ref['low'] else 0
                    ref['box_end_idx'] = max(ref.get('box_end_idx', 0), cand_end)
                else:
                    ref['low'] = cand_low; ref['low_idx'] = cand_low_idx
                    ref['contraction'] = cand_pct; ref['box_end_idx'] = cand_end
                    while len(chain) >= 3 and (chain[-1].get('contraction', 0)
                                               >= chain[-2].get('contraction', chain[-2].get('bounce', 0))):
                        del chain[-2]
                continue
            chain.append({"low": cand_low, "low_idx": cand_low_idx, "high": cand_high,
                          "high_idx": cand_high_idx, "box_end_idx": cand_end, "contraction": cand_pct})
        if outcome == 'open':
            open_chain = chain; break
        _, idx = outcome
        if len(chain) >= 2:
            bases.append({"num": len(bases) + 1, "legs": chain, "breakout_idx": idx, "weak": len(chain) > 6})
        search_start = idx
    if not bases and not open_chain:
        return None
    return {"reset_idx": reset_end_idx, "bases": bases, "open": open_chain}
# ============ 画图 ============

def plot_candlestick(ax, ohlcv, start_idx=0):
    width = 0.6
    for i, (timestamp, o, h, l, c, v) in enumerate(ohlcv):
        idx = start_idx + i
        color = 'g' if c >= o else 'r'
        ax.plot([idx, idx], [l, h], color=color, linewidth=1)
        height = abs(c - o)
        bottom = min(o, c)
        ax.bar(idx, height, width=width, bottom=bottom, color=color, edgecolor=color, linewidth=0.5)

def plot_ma_lines_from_series(ax, ma50_series, ma150_series, ma200_series, window_start, window_end):
    """
    用已经基于完整历史算好的 MA 序列画图，避免窗口太短时 MA150/MA200 算不出来
    """
    def plot_line(vals, color, label):
        segment = vals[window_start:window_end]
        valid = [(i, v) for i, v in enumerate(segment) if v is not None]
        if valid:
            idx, data = zip(*valid)
            ax.plot(idx, data, color=color, label=label, linewidth=2, alpha=0.8)

    plot_line(ma50_series, '#808080', 'MA50')
    plot_line(ma150_series, '#008B8B', 'MA150')
    plot_line(ma200_series, '#FF0000', 'MA200')

def draw_price_range(ax, x_start, x_end, low, high, color, label, dashed=False):
    """
    模仿 TradingView 的 Price Range 工具：实心半透明矩形，从 x_start 盖到 x_end，
    覆盖低点到高点的整个价格区间，右上角标价格差/百分比。dashed=未封口活结构
    """
    pct = (high - low) / low * 100

    left  = min(x_start, x_end)
    right = max(x_start, x_end)
    width = max(right - left, 0.6)

    ax.add_patch(mpatches.Rectangle(
        (left, low), width, high - low,
        facecolor=color, edgecolor=color,
        alpha=0.12 if dashed else 0.25,
        linewidth=1.5, linestyle='--' if dashed else '-', zorder=3
    ))

    # 紧凑标签：框顶居中一行小字，避免多个C的标签互相叠死
    ax.text(
        (left + right) / 2, high, f"{label} +{pct:.1f}%",
        fontsize=8, fontweight='bold', color=color,
        va='bottom', ha='center', zorder=4
    )

def draw_base_box(ax, x_start, x_end, low, high, num, weak=False):
    """
    Base 区域：固定金色的大框，纵向 = C1低点到C1高点，横向 = C1起点到突破K线，
    罩在所有 C 段的小框外面。weak = 超过6个C的弱Base
    """
    ax.add_patch(mpatches.Rectangle(
        (x_start, low), max(x_end - x_start, 0.6), high - low,
        facecolor=BASE_FILL_COLOR, edgecolor=BASE_EDGE_COLOR,
        alpha=0.15, linewidth=2.0, zorder=2
    ))
    ax.annotate(
        f"Base {num}" + (" (weak)" if weak else ""),
        xy=(x_start, low), xytext=(2, -14), textcoords='offset points',
        fontsize=12, fontweight='bold', color=BASE_EDGE_COLOR,
        va='top', ha='left'
    )

def main():
    print(f"[FETCH] {SYMBOL} {TIMEFRAME} x{LIMIT}")
    ohlcv = fetch_ohlcv_paginated(SYMBOL, TIMEFRAME, LIMIT)
    print(f"[FETCH] got {len(ohlcv)} candles")

    if TRUNCATE_END is not None:
        ohlcv = ohlcv[:TRUNCATE_END + 1]
        print(f"[TRUNCATE] Only keeping candles up to idx={TRUNCATE_END} ({len(ohlcv)} total)")

    result = find_bases(ohlcv)
    if not result:
        print("[FAIL] 没有找到符合条件的结构（Reset后没有碰到MA50的合格C1）")
        return

    bases = result['bases']
    open_chain = result['open']

    print(f"[OK] Reset idx={result['reset_idx']}")
    for b in bases:
        legs = b['legs']
        print(f"[BASE {b['num']}] C1低点={legs[0]['low']:.5f}(idx={legs[0]['low_idx']}) "
              f"高点={legs[0]['high']:.5f}(idx={legs[0]['high_idx']}) "
              f"共{len(legs)}个C，突破于idx={b['breakout_idx']}"
              + ("  [弱Base: 超过6个C]" if b.get('weak') else ""))
        for j, leg in enumerate(legs):
            pct = leg.get('contraction', leg.get('bounce')) * 100
            print(f"    C{j+1}: low={leg['low']:.5f}(idx={leg['low_idx']}) "
                  f"high={leg['high']:.5f}(idx={leg['high_idx']})  {pct:.1f}%")
    if open_chain:
        legs = open_chain
        print(f"[OPEN] 未封口结构：C1低点={legs[0]['low']:.5f}(idx={legs[0]['low_idx']}) "
              f"共{len(legs)}个C，还没跌破C1低点")
        for j, leg in enumerate(legs):
            pct = leg.get('contraction', leg.get('bounce')) * 100
            print(f"    C{j+1}: low={leg['low']:.5f}(idx={leg['low_idx']}) "
                  f"high={leg['high']:.5f}(idx={leg['high_idx']})  {pct:.1f}%")

    os.makedirs(os.path.join(BASE_DIR, 'charts'), exist_ok=True)
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    coin_name = SYMBOL.split('/')[0]
    price = ohlcv[-1][4]

    # 基于完整历史算一次 MA，画图时按窗口切片，放大图也能显示 MA150/MA200
    full_closes = [c[4] for c in ohlcv]
    ma50_series  = calc_sma_series(full_closes, 50)
    ma150_series = calc_sma_series(full_closes, 150)
    ma200_series = calc_sma_series(full_closes, 200)

    all_structs = [b['legs'] for b in bases] + ([open_chain] if open_chain else [])

    # 活结构失效点：C1低点之后慢速对第一次翻多 (MA150上穿MA200) 的位置
    flip_idx = None
    if open_chain:
        for i in range(open_chain[0]['low_idx'] + 1, len(ohlcv)):
            m150, m200 = ma150_series[i], ma200_series[i]
            if all([m150, m200]) and not (m200 > m150):
                flip_idx = i
                break
    if flip_idx is not None:
        print(f"[FLIP] 慢速对翻多于 idx={flip_idx}，活结构在此失效")

    def render(window_start, window_end, suffix, title_suffix):
        offset = window_start
        view = ohlcv[window_start:window_end]

        fig, ax = plt.subplots(figsize=(16, 8))
        plot_candlestick(ax, view, start_idx=0)
        plot_ma_lines_from_series(ax, ma50_series, ma150_series, ma200_series, window_start, window_end)

        # 锁定坐标轴范围：窗口外的结构框不能把轴撑大
        y_lo = min(c[3] for c in view)
        y_hi = max(c[2] for c in view)
        pad = (y_hi - y_lo) * 0.04
        ax.set_xlim(-2, len(view) + 1)
        ax.set_ylim(y_lo - pad * 1.5, y_hi + pad * 2)

        def in_window(a, b):
            return b >= window_start and a <= window_end

        reset_x = result['reset_idx'] - offset
        if 0 <= reset_x <= len(view):
            ax.axvline(reset_x, color='orange', linestyle=':', linewidth=1.2, alpha=0.8)
            ax.text(reset_x, ax.get_ylim()[1], 'Reset', color='orange', fontsize=9, ha='center', va='bottom')

        # 翻多点：洋红粗线，一眼看出活结构在哪里失效
        if flip_idx is not None:
            flip_x = flip_idx - offset
            if 0 <= flip_x <= len(view):
                ax.axvline(flip_x, color='#FF00FF', linewidth=2.5, alpha=0.85)
                ax.text(flip_x, ax.get_ylim()[1], 'FLIP', color='#FF00FF',
                        fontsize=13, fontweight='bold', ha='center', va='bottom')

        colors = ['#1f77b4', '#9467bd', '#d62728', '#ff7f0e', '#2ca02c']

        def draw_legs(legs, dashed=False):
            # 每个C的框：左边界=低点，右边界=确认完成点/高点取更远的，罩住整个范围；
            # 但不能和下一个C的框重叠——右边界最多画到下一个C的左边界前一根
            for idx, leg in enumerate(legs):
                color = colors[idx % len(colors)]
                right_abs = max(leg['high_idx'], leg.get('box_end_idx', leg['high_idx']))
                if idx + 1 < len(legs):
                    right_abs = min(right_abs, legs[idx + 1]['low_idx'] - 1)
                right_abs = max(right_abs, leg['high_idx'])
                if not in_window(leg['low_idx'], right_abs):
                    continue
                draw_price_range(ax, leg['low_idx'] - offset, right_abs - offset,
                                 leg['low'], leg['high'], color, f'C{idx + 1}', dashed=dashed)

        # 已完成（封口）的 Base：实线框 + 金色Base框
        for b in bases:
            c1 = b['legs'][0]
            if not in_window(c1['low_idx'], b['breakout_idx']):
                continue
            draw_base_box(ax, c1['low_idx'] - offset, b['breakout_idx'] - offset,
                          c1['low'], c1['high'], b['num'], weak=b.get('weak', False))
            draw_legs(b['legs'])

        # 未封口活结构：虚线框区分，C1低点画金色触发线（跌破=突破）
        if open_chain and in_window(open_chain[0]['low_idx'], len(ohlcv) - 1):
            draw_legs(open_chain, dashed=True)
            trig = open_chain[0]['low']
            ax.axhline(trig, color='#B8860B', linestyle='--', linewidth=1.2, alpha=0.8)
            ax.text(len(view), trig, f" C1 low {trig:.5f}",
                    color='#B8860B', fontsize=9, va='center', ha='left')

        n_base = len(bases)
        label = f"Bear VCP — {n_base} Base" + ("s" if n_base != 1 else "")
        if open_chain:
            label += f" + OPEN C{len(open_chain)}"
        ax.set_title(f"{SYMBOL} 1H SHORT — {label}{title_suffix} | Price: ${price:.5f}",
                     fontsize=14, fontweight='bold', pad=20)

        num_candles = len(view)
        step = max(1, num_candles // 15)
        ax.set_xticks(range(0, num_candles, step))
        ax.set_xticklabels([f"{i + offset}" for i in range(0, num_candles, step)], rotation=45)
        ax.set_xlabel('1H Candle Index', fontsize=11)
        ax.set_ylabel('Price (USDT)', fontsize=11)
        ax.legend(loc='upper right', fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.set_facecolor('#f8f9fa')

        filepath = os.path.join(BASE_DIR, 'charts', f'{coin_name}_BASES_BEAR_{suffix}_{timestamp}.png')
        plt.tight_layout()
        plt.savefig(filepath, dpi=110, bbox_inches='tight', facecolor='white')
        plt.close()
        print(f"[SAVED] {filepath}")

    # 整体视图：Reset 前 210 根到最新
    full_start = max(0, result['reset_idx'] - 210)
    render(full_start, len(ohlcv), 'FULL', '')

    # 放大视图：从第一个结构的C1低点前一点，一路画到最新蜡烛
    first_low_idx = all_structs[0][0]['low_idx']
    zoom_start = max(0, first_low_idx - 15)
    render(zoom_start, len(ohlcv), 'ZOOM', ' [Zoomed]')

    # 每个 Base 单独一张特写图，前后留一点上下文，结构看得清楚
    for b in bases:
        c1 = b['legs'][0]
        w_start = max(0, c1['low_idx'] - 20)
        w_end = min(len(ohlcv), b['breakout_idx'] + 40)
        render(w_start, w_end, f"BASE{b['num']}", f" [Base {b['num']}]")

if __name__ == '__main__':
    main()
