import sys
sys.stdout.reconfigure(encoding='utf-8')

import ccxt
import time
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import os

# ============================================================================
# 多头版 VCP Base 检测 —— plot_c1c2_bear_doge.py 的完整镜像（2026-07-09）
# 上涨途中"回调一浪比一浪浅"，升破 C1 高点 = 突破完成封 Base。
# 所有规则与空头版逐条对应：低换高、跌破换升破、阳换阴、金叉换死叉、MA排列倒序。
# 规则本体见 BEAR_C1C2_NOTES.md 第4节（镜像阅读）。
# ============================================================================

EXCHANGE_ID  = "bybit"
SYMBOL       = sys.argv[1] if len(sys.argv) > 1 else "BTC/USDT:USDT"
TIMEFRAME    = "1h"
LIMIT        = 2000  # 约83天的1H K线，Bybit单次上限1000根，分页2次拿够
# 非数字参数一律忽略（CMD把#注释当参数传进来过）
try:
    TRUNCATE_END = int(sys.argv[2]) if len(sys.argv) > 2 else None
except ValueError:
    TRUNCATE_END = None

exchange = getattr(ccxt, EXCHANGE_ID)({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

BASE_DIR = os.path.dirname(__file__)

BASE_FILL_COLOR = '#FFD700'
BASE_EDGE_COLOR = '#B8860B'

def fetch_ohlcv_paginated(symbol, timeframe, total):
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

def is_c_candle_bull(candle):
    """
    多头版 C 条件：阴线（收跌，回调）或影线长过身体（长影线算法与空头版完全一致）
    """
    o, h, l, c = candle[1], candle[2], candle[3], candle[4]
    body = abs(c - o)
    wick = (h - l) - body
    is_red = c < o
    is_long_wick = wick > body if body > 0 else True
    return is_red or is_long_wick

def find_c_pattern_bull(candles):
    for i in range(2, len(candles)):
        if (is_c_candle_bull(candles[i])
                and is_c_candle_bull(candles[i-1])
                and is_c_candle_bull(candles[i-2])):
            return i
    return None

def find_leg_low(ohlcv, highs, lows, ma50_series, ma150_series, ma200_series, n, high_idx, high_price):
    """
    从给定高点开始找这段回调的最低点：区域终点 = 出现新高(升破high_price) 或 均线排列翻空，
    两者谁先发生就在哪截止。低点候选从高点当根算起（自己的下影线也算）。
    """
    if high_idx >= n - 1:
        return None

    region_end = n - 1
    for i in range(high_idx + 1, n):
        if highs[i] > high_price:
            region_end = i - 1
            break
        m150, m200 = ma150_series[i], ma200_series[i]
        # 排列翻转只看慢速对：MA150下穿MA200才算趋势翻转（镜像2026-07-10定稿）。
        # MA50与MA150的相互穿越不是结构事件，不截断回调区域
        if all([m150, m200]) and not (m150 > m200):
            region_end = i - 1
            break

    if region_end <= high_idx:
        return None

    # 低点候选从高点【当根】算起（镜像07-09 SIREN规则，2026-07-16确认原意）：
    # 接下来区域内的K线低点都没低过高点当根的低点时，取当根下影线——区域内没有更低的点。
    # 前提：C的成立条件（3连确认等）都要符合；确认组归本C所有，不再喂给下一个C
    region = ohlcv[high_idx:region_end + 1]
    low     = min(c[3] for c in region)
    low_idx = high_idx + min(range(len(region)), key=lambda i: region[i][3])
    return low, low_idx, region_end

def find_bases_bull(ohlcv):
    """
    多头 VCP 滚动 Base 检测（空头版 find_bases 的镜像）：
    - Reset = 最近一次"MA150死叉MA200后重新回到多头排列 (MA50>MA150>MA200)"
    - C1 资格：高点之后的回调必须 碰到MA50（多头排列中MA50是最上面的支撑线）
      且出现连续3根阴线/长影线确认（顺序不限）；高点出现新高就后移、条件重数
    - C = 高点 -> 高点之后的回调低点；高点必须出现在"连续3根确认"开始之前
    - 取代：候选低点 <= 参考低点（回调更深，前一个C没走完）或 % >= 参考% -> 原地取代；级联取代
    - 突破：任何一根K线的 high 升破 C1 高点 = 突破完成封 Base（至少要有C2）
    - 超过6个C作废；封口后从突破K线继续找下一组 -> Base 2, 3...
    """
    n = len(ohlcv)
    if n < 210:
        return None

    closes = [c[4] for c in ohlcv]
    highs  = [c[2] for c in ohlcv]
    lows   = [c[3] for c in ohlcv]

    ma50_series  = calc_sma_series(closes, 50)
    ma150_series = calc_sma_series(closes, 150)
    ma200_series = calc_sma_series(closes, 200)

    # Reset 取【最近一次】"死叉后重新回到多头排列"
    reset_end_idx = None
    last_cross_idx = None
    cross_used = False
    for i in range(200, n):
        m150_p, m200_p = ma150_series[i-1], ma200_series[i-1]
        m150_i, m200_i = ma150_series[i], ma200_series[i]
        if all([m150_p, m200_p, m150_i, m200_i]) and m150_p >= m200_p and m150_i < m200_i:
            last_cross_idx = i
            cross_used = False
        ma50 = ma50_series[i]
        if not all([ma50, m150_i, m200_i]):
            continue
        if last_cross_idx is not None and not cross_used and ma50 > m150_i > m200_i:
            reset_end_idx = i
            cross_used = True

    if reset_end_idx is None:
        return None

    def find_c1(start):
        high_idx = None
        high_over_ma50 = False
        failed_high = None   # 已判定"回调段碰不到MA50"的高点，不再重复检查
        streak = 0
        confirmed = False
        for i in range(start, n):
            if high_idx is None or highs[i] > highs[high_idx]:
                high_idx = i   # 新高出现，高点后移，两个条件全部重新数
                # C1高点必须在MA50上方：价格从均线上面回落碰它才叫"碰到MA50"
                # （镜像2026-07-10定稿：价格压在MA50下方时touch条件形同虚设）
                m50_at_high = ma50_series[i]
                high_over_ma50 = m50_at_high is not None and ohlcv[i][4] > m50_at_high
                streak = 0
                confirmed = False
                continue
            if is_c_candle_bull(ohlcv[i]):
                streak += 1
                if streak >= 3:
                    confirmed = True
            else:
                streak = 0
            if not (confirmed and high_over_ma50) or high_idx == failed_high:
                continue
            leg = find_leg_low(ohlcv, highs, lows, ma50_series, ma150_series, ma200_series, n,
                               high_idx, highs[high_idx])
            if leg is None:
                failed_high = high_idx
                continue
            low, low_idx, region_end = leg
            if i > region_end:
                failed_high = high_idx
                continue
            # 触碰MA50必须发生在C1【自己的回调段】内（高点~低点之间）——镜像HIGH案例
            touched = any(
                ma50_series[j] is not None and lows[j] <= ma50_series[j]
                for j in range(high_idx, low_idx + 1))
            if not touched:
                failed_high = high_idx
                continue
            return {
                "high": highs[high_idx], "high_idx": high_idx,
                "low": low, "low_idx": low_idx,
                "pullback": (highs[high_idx] - low) / highs[high_idx] if highs[high_idx] else 0,
                "region_end": region_end,
                "box_end_idx": max(low_idx, i),
            }
        return None

    bases = []
    open_chain = None
    search_start = reset_end_idx

    while search_start < n - 5:
        c1 = find_c1(search_start)
        if c1 is None:
            break
        chain = [c1]
        c1_high = c1['high']
        outcome = None  # ('sealed', idx) / 'open'
        # 触发搜索游标：C1的确认组3连已被C1消耗，C2从C1的框结束之后找新的3连
        scan_from = c1['box_end_idx'] + 1

        while True:
            ref = chain[-1]
            # 段的起点固定在前一个C的低点+1；scan_from 只推进确认触发的搜索位置
            start_idx = ref['low_idx'] + 1
            trigger_from = max(start_idx, scan_from)
            if trigger_from >= n:
                outcome = 'open'
                break

            # 突破检查：start_idx 起第一根 high 升破 C1 高点的K线
            breakout_idx = None
            for i in range(start_idx, n):
                if highs[i] > c1_high:
                    breakout_idx = i
                    break

            c_idx = find_c_pattern_bull(ohlcv[trigger_from:])
            confirm_idx = trigger_from + c_idx if c_idx is not None else None

            if breakout_idx is not None and (confirm_idx is None or breakout_idx <= confirm_idx):
                outcome = ('sealed', breakout_idx)
                break
            if confirm_idx is None:
                outcome = 'open'
                break

            segment = ohlcv[start_idx:confirm_idx + 1]
            cand_high_rel = max(range(len(segment)), key=lambda i: segment[i][2])
            cand_high     = segment[cand_high_rel][2]
            cand_high_idx = start_idx + cand_high_rel

            # 高点不能落在3连的第二/三根（确认蜡烛上影线冒充高点——镜像H假C3案例）；
            # 可以是3连的【第一根】（冲出新高的长上影反转蜡烛自己开启3连——镜像SIREN Base3 1928案例）
            if cand_high_idx > confirm_idx - 2:
                scan_from = confirm_idx + 1
                continue
            # 候选高点那根必须【主动创新高】（high高于它前一根的high）：新回调的开始一定是冲出
            # 新高的那根（镜像SLX 885案例）；没创新高的"段内最高"只是段边界上的巧合，
            # 不开启C（镜像SIREN C7案例，2026-07-16定稿）
            if cand_high_idx > 0 and cand_high <= highs[cand_high_idx - 1]:
                scan_from = confirm_idx + 1
                continue

            leg = find_leg_low(ohlcv, highs, lows, ma50_series, ma150_series, ma200_series, n,
                               cand_high_idx, cand_high)
            if leg is None:
                # 高点之后没有真实的回调（下一根就升破高点了）——不构成C，确认作废跳过
                scan_from = confirm_idx + 1
                continue
            cand_low, cand_low_idx, _ = leg
            cand_end_idx = max(confirm_idx, cand_low_idx)

            cand_pct = (cand_high - cand_low) / cand_high if cand_high else 0
            ref_pct = ref.get('contraction', ref.get('pullback'))

            # 跌破参考低点（回调更深，前一个C没走完）、或 % 没收缩 -> 取代前一个C
            if cand_low <= ref['low'] or (ref_pct is not None and cand_pct >= ref_pct):
                ref['low'] = cand_low
                ref['low_idx'] = cand_low_idx
                if 'pullback' in ref:
                    ref['pullback'] = (ref['high'] - cand_low) / ref['high'] if ref['high'] else 0
                    ref['confirm_idx'] = cand_end_idx
                    ref['box_end_idx'] = max(ref.get('box_end_idx', 0), cand_end_idx)
                else:
                    ref['high'] = cand_high
                    ref['high_idx'] = cand_high_idx
                    ref['contraction'] = cand_pct
                    ref['box_end_idx'] = cand_end_idx
                    # 级联取代：被撑大的C如果%反超了它前一个C，就把前一个吞掉（C1不被吞）
                    while len(chain) >= 3 and (chain[-1].get('contraction', 0)
                                               >= chain[-2].get('contraction', chain[-2].get('pullback', 0))):
                        del chain[-2]
                scan_from = confirm_idx + 1  # 这组3连已被取代动作消耗，下一个触发要找新的3连
                continue

            chain.append({
                "high": cand_high, "high_idx": cand_high_idx,
                "search_start_idx": start_idx,
                "low": cand_low, "low_idx": cand_low_idx,
                "box_end_idx": cand_end_idx,
                "contraction": cand_pct,
            })
            scan_from = confirm_idx + 1  # 一组3连确认只能用一次：被本C消耗后，下一个C从确认点之后找新3连

            # 超过6个C不作废：结构继续跟踪到突破封口照样成Base，只是标记"强度不强"（2026-07-16定稿）

        if outcome == 'open':
            open_chain = chain
            break

        _, idx = outcome
        if len(chain) >= 2:
            bases.append({"num": len(bases) + 1, "legs": chain, "breakout_idx": idx,
                          "weak": len(chain) > 6})  # >6个C：Base成立但强度不强
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
    pct = (high - low) / high * 100 if high else 0

    left  = min(x_start, x_end)
    right = max(x_start, x_end)
    width = max(right - left, 0.6)

    ax.add_patch(mpatches.Rectangle(
        (left, low), width, high - low,
        facecolor=color, edgecolor=color,
        alpha=0.12 if dashed else 0.25,
        linewidth=1.5, linestyle='--' if dashed else '-', zorder=3
    ))

    ax.text(
        (left + right) / 2, high, f"{label} -{pct:.1f}%",
        fontsize=8, fontweight='bold', color=color,
        va='bottom', ha='center', zorder=4
    )

def draw_base_box(ax, x_start, x_end, low, high, num, weak=False):
    ax.add_patch(mpatches.Rectangle(
        (x_start, low), max(x_end - x_start, 0.6), high - low,
        facecolor=BASE_FILL_COLOR, edgecolor=BASE_EDGE_COLOR,
        alpha=0.15, linewidth=2.0, zorder=2
    ))
    ax.annotate(
        f"Base {num}" + (" (weak)" if weak else ""),
        xy=(x_start, high), xytext=(2, 6), textcoords='offset points',
        fontsize=12, fontweight='bold', color=BASE_EDGE_COLOR,
        va='bottom', ha='left'
    )

def main():
    print(f"[FETCH] {SYMBOL} {TIMEFRAME} x{LIMIT}")
    ohlcv = fetch_ohlcv_paginated(SYMBOL, TIMEFRAME, LIMIT)
    print(f"[FETCH] got {len(ohlcv)} candles")

    if TRUNCATE_END is not None:
        ohlcv = ohlcv[:TRUNCATE_END + 1]
        print(f"[TRUNCATE] Only keeping candles up to idx={TRUNCATE_END} ({len(ohlcv)} total)")

    result = find_bases_bull(ohlcv)
    if not result:
        print("[FAIL] 没有找到符合条件的结构（Reset后没有碰到MA50的合格C1）")
        return

    bases = result['bases']
    open_chain = result['open']

    print(f"[OK] Reset idx={result['reset_idx']}")
    for b in bases:
        legs = b['legs']
        print(f"[BASE {b['num']}] C1高点={legs[0]['high']:.5f}(idx={legs[0]['high_idx']}) "
              f"低点={legs[0]['low']:.5f}(idx={legs[0]['low_idx']}) "
              f"共{len(legs)}个C，突破于idx={b['breakout_idx']}"
              + ("  [弱Base: 超过6个C]" if b.get('weak') else ""))
        for j, leg in enumerate(legs):
            pct = leg.get('contraction', leg.get('pullback')) * 100
            print(f"    C{j+1}: high={leg['high']:.5f}(idx={leg['high_idx']}) "
                  f"low={leg['low']:.5f}(idx={leg['low_idx']})  -{pct:.1f}%")
    if open_chain:
        legs = open_chain
        print(f"[OPEN] 未封口结构：C1高点={legs[0]['high']:.5f}(idx={legs[0]['high_idx']}) "
              f"共{len(legs)}个C，还没升破C1高点")
        for j, leg in enumerate(legs):
            pct = leg.get('contraction', leg.get('pullback')) * 100
            print(f"    C{j+1}: high={leg['high']:.5f}(idx={leg['high_idx']}) "
                  f"low={leg['low']:.5f}(idx={leg['low_idx']})  -{pct:.1f}%")

    os.makedirs(os.path.join(BASE_DIR, 'charts'), exist_ok=True)
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    coin_name = SYMBOL.split('/')[0]
    price = ohlcv[-1][4]

    full_closes = [c[4] for c in ohlcv]
    ma50_series  = calc_sma_series(full_closes, 50)
    ma150_series = calc_sma_series(full_closes, 150)
    ma200_series = calc_sma_series(full_closes, 200)

    all_structs = [b['legs'] for b in bases] + ([open_chain] if open_chain else [])

    # 活结构失效点：C1高点之后慢速对第一次翻空 (MA150下穿MA200) 的位置
    flip_idx = None
    if open_chain:
        for i in range(open_chain[0]['high_idx'] + 1, len(ohlcv)):
            m150, m200 = ma150_series[i], ma200_series[i]
            if all([m150, m200]) and not (m150 > m200):
                flip_idx = i
                break
    if flip_idx is not None:
        print(f"[FLIP] 慢速对翻空于 idx={flip_idx}，活结构在此失效")

    def render(window_start, window_end, suffix, title_suffix):
        offset = window_start
        view = ohlcv[window_start:window_end]

        fig, ax = plt.subplots(figsize=(16, 8))
        plot_candlestick(ax, view, start_idx=0)
        plot_ma_lines_from_series(ax, ma50_series, ma150_series, ma200_series, window_start, window_end)

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

        # 翻空点：洋红粗线，一眼看出活结构在哪里失效
        if flip_idx is not None:
            flip_x = flip_idx - offset
            if 0 <= flip_x <= len(view):
                ax.axvline(flip_x, color='#FF00FF', linewidth=2.5, alpha=0.85)
                ax.text(flip_x, ax.get_ylim()[1], 'FLIP', color='#FF00FF',
                        fontsize=13, fontweight='bold', ha='center', va='bottom')

        colors = ['#1f77b4', '#9467bd', '#d62728', '#ff7f0e', '#2ca02c']

        def draw_legs(legs, dashed=False):
            # 每个C的框：左边界=高点，右边界=确认完成点/低点取更远的；
            # 但不能和下一个C的框重叠——右边界最多画到下一个C的左边界前一根
            for idx, leg in enumerate(legs):
                color = colors[idx % len(colors)]
                right_abs = max(leg['low_idx'], leg.get('box_end_idx', leg['low_idx']))
                if idx + 1 < len(legs):
                    right_abs = min(right_abs, legs[idx + 1]['high_idx'] - 1)
                right_abs = max(right_abs, leg['low_idx'])
                if not in_window(leg['high_idx'], right_abs):
                    continue
                draw_price_range(ax, leg['high_idx'] - offset, right_abs - offset,
                                 leg['low'], leg['high'], color, f'C{idx + 1}', dashed=dashed)

        # 已完成（封口）的 Base：实线框 + 金色Base框
        for b in bases:
            c1 = b['legs'][0]
            if not in_window(c1['high_idx'], b['breakout_idx']):
                continue
            draw_base_box(ax, c1['high_idx'] - offset, b['breakout_idx'] - offset,
                          c1['low'], c1['high'], b['num'], weak=b.get('weak', False))
            draw_legs(b['legs'])

        # 未封口活结构：虚线框区分，C1高点画金色触发线（升破=突破）
        if open_chain and in_window(open_chain[0]['high_idx'], len(ohlcv) - 1):
            draw_legs(open_chain, dashed=True)
            trig = open_chain[0]['high']
            ax.axhline(trig, color='#B8860B', linestyle='--', linewidth=1.2, alpha=0.8)
            ax.text(len(view), trig, f" C1 high {trig:.5f}",
                    color='#B8860B', fontsize=9, va='center', ha='left')

        n_base = len(bases)
        label = f"Bull VCP — {n_base} Base" + ("s" if n_base != 1 else "")
        if open_chain:
            label += f" + OPEN C{len(open_chain)}"
        ax.set_title(f"{SYMBOL} 1H LONG — {label}{title_suffix} | Price: ${price:.5f}",
                     fontsize=14, fontweight='bold', pad=20)

        num_candles = len(view)
        step = max(1, num_candles // 15)
        ax.set_xticks(range(0, num_candles, step))
        ax.set_xticklabels([f"{i + offset}" for i in range(0, num_candles, step)], rotation=45)
        ax.set_xlabel('1H Candle Index', fontsize=11)
        ax.set_ylabel('Price (USDT)', fontsize=11)
        ax.legend(loc='upper left', fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.set_facecolor('#f8f9fa')

        filepath = os.path.join(BASE_DIR, 'charts', f'{coin_name}_BASES_BULL_{suffix}_{timestamp}.png')
        plt.tight_layout()
        plt.savefig(filepath, dpi=110, bbox_inches='tight', facecolor='white')
        plt.close()
        print(f"[SAVED] {filepath}")

    full_start = max(0, result['reset_idx'] - 210)
    render(full_start, len(ohlcv), 'FULL', '')

    first_high_idx = all_structs[0][0]['high_idx']
    zoom_start = max(0, first_high_idx - 15)
    render(zoom_start, len(ohlcv), 'ZOOM', ' [Zoomed]')

    for b in bases:
        c1 = b['legs'][0]
        w_start = max(0, c1['high_idx'] - 20)
        w_end = min(len(ohlcv), b['breakout_idx'] + 40)
        render(w_start, w_end, f"BASE{b['num']}", f" [Base {b['num']}]")

if __name__ == '__main__':
    main()
