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
TRUNCATE_END = int(sys.argv[2]) if len(sys.argv) > 2 else None  # 只保留到这根K线为止（含），方便看图

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
        # [EXP] 排列翻转只看慢速对：MA150上穿MA200才算趋势翻转。
        # MA50和MA150谁上谁下不是结构事件（用户："reset不是150穿50或50穿150"——
        # 同理，50x150交叉也不该截断反弹区域；SIREN 07-06的C1就是被这个误杀的）
        if all([m150, m200]) and not (m200 > m150):
            region_end = i - 1
            break

    if region_end <= low_idx:
        return None

    # 高点候选从低点【当根】算起：低点那根K线自己的上影线也算反弹高点
    # （SIREN案例：区域里没有比低点当根上影线更高的高点，就画到同一根的高点）。
    # 真实反弹守卫不变：低点之后必须至少有一根没跌破低点的K线（region_end > low_idx）
    region = ohlcv[low_idx:region_end + 1]
    high     = max(c[2] for c in region)
    high_idx = low_idx + max(range(len(region)), key=lambda i: region[i][2])
    return high, high_idx, region_end

def find_bases(ohlcv):
    """
    空头 VCP 滚动 Base 检测：
    - Reset = 最近一次"MA150金叉MA200后重新回到空头排列"，是 Base 编号的起点/清零点
    - C1 资格：低点之后的反弹过程中，必须有K线穿过或碰到 MA50（影线碰到即算），
      没碰到 MA50 的弱反弹不算 C1
    - C2~C6：找法和取代规则照旧（连续3根确认；破前高或%没收缩就取代前一个C）；
      低点跌破上一段低点【不再】终止链条，只有跌破 C1 低点才有意义
    - 突破：C1 成立后，任何一根K线的 low 跌破 C1 低点的瞬间 = 突破完成，
      C1~最后一个C 的整个区域封为一个 Base（要求至少有 C2，只有 C1 就被跌破的不算）
    - Base 封口后从突破那根K线继续找下一个 C1，依次形成 Base 1, Base 2, ...
    - 一条链超过6个C = 坏结构，这个候选作废（不画），跳过去继续找
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

    # Reset 取【最近一次】"金叉后重新回到空头排列"，不是历史上第一次——
    # 这样找到的结构才贴着最新蜡烛；老结构早就被行情打穿了，对筛选没意义
    reset_end_idx = None
    last_cross_idx = None
    cross_used = False
    for i in range(200, n):
        m150_p, m200_p = ma150_series[i-1], ma200_series[i-1]
        m150_i, m200_i = ma150_series[i], ma200_series[i]
        if all([m150_p, m200_p, m150_i, m200_i]) and m150_p <= m200_p and m150_i > m200_i:
            last_cross_idx = i
            cross_used = False
        ma50 = ma50_series[i]
        if not all([ma50, m150_i, m200_i]):
            continue
        if last_cross_idx is not None and not cross_used and m200_i > m150_i > ma50:
            reset_end_idx = i  # 每次新金叉后的第一个空头排列都会覆盖掉旧的 reset
            cross_used = True

    if reset_end_idx is None:
        return None

    def find_c1(start):
        # 逐根扫：一路跟踪最低点。C1 成立需要低点之后的反弹同时满足两个条件（顺序不限）：
        #   1. 连续3根阳线/长影线确认（跟C2用的同一标准，中间夹一根不合格就重新数）
        #   2. 有K线的 high 碰到/穿过当根 MA50
        # 中途出现更低的新低 -> 低点后移，两个条件全部重新数
        low_idx = None
        low_under_ma50 = False
        streak = 0
        confirmed = False
        touched = False
        for i in range(start, n):
            if low_idx is None or lows[i] < lows[low_idx]:
                low_idx = i   # 新低出现（当根不算反弹，反弹从低点之后算）
                # [EXP] C1低点必须在MA50下方：价格从均线下面弹上来碰它才叫"碰到MA50"
                # （SIREN案例：价格本来就骑在MA50上方时，touch条件形同虚设，产生假C1）
                m50_at_low = ma50_series[i]
                low_under_ma50 = m50_at_low is not None and ohlcv[i][4] < m50_at_low
                streak = 0
                confirmed = False
                touched = False
                continue
            if is_c_candle_bear(ohlcv[i]):
                streak += 1
                if streak >= 3:
                    confirmed = True
            else:
                streak = 0
            m50 = ma50_series[i]
            if low_under_ma50 and m50 is not None and highs[i] >= m50:
                touched = True
            if not (confirmed and touched):
                continue
            leg = find_leg_high(ohlcv, highs, lows, ma50_series, ma150_series, ma200_series, n,
                                low_idx, lows[low_idx])
            if leg is None:
                continue
            high, high_idx, region_end = leg
            if i > region_end:
                continue  # 条件凑齐时已越过区域截止（均线翻转），不算这条腿的反弹
            return {
                "low": lows[low_idx], "low_idx": low_idx,
                "high": high, "high_idx": high_idx,
                "bounce": (high - lows[low_idx]) / lows[low_idx] if lows[low_idx] else 0,
                "region_end": region_end,
                "box_end_idx": max(high_idx, i),  # 框罩到确认完成/高点，取更远的
            }
        return None

    bases = []
    open_chain = None
    discards = []   # [EXP] 被丢弃的结构（C1-only被打穿 / 超6段作废），调试用
    search_start = reset_end_idx

    while search_start < n - 5:
        c1 = find_c1(search_start)
        if c1 is None:
            break
        chain = [c1]
        c1_low = c1['low']
        outcome = None  # ('sealed', idx) / ('void', idx) / 'open'
        scan_from = 0   # 无效确认点的跳过游标（低点之后没有反弹的那种）

        while True:
            ref = chain[-1]
            # 下一个C从前一个C的【高点】之后开始找（用户原始规则："C2是C1高点之后继续往后找"）
            # ——高点之后的回调K线（哪怕它参与过前一个C的确认）也属于下一个C的搜索范围。
            # 段的起点(找低点的范围)固定在高点+1；scan_from 只推进"确认触发"的搜索位置，
            # 不能把段起点一起推后（否则段被削到只剩确认组3根，低点永远无效）
            start_idx = ref['high_idx'] + 1
            trigger_from = max(start_idx, scan_from)
            if trigger_from >= n:
                outcome = 'open'
                break

            # [EXP-对照组] 甲规则暂时关闭：跌破C1低点即封口（原规则），
            # 先单独验证"C1低点必须在MA50下方"这一条的效果
            breakout_idx = None
            for i in range(start_idx, n):
                if lows[i] < c1_low:
                    breakout_idx = i
                    break

            c_idx = find_c_pattern(ohlcv[trigger_from:])
            confirm_idx = trigger_from + c_idx if c_idx is not None else None

            # 突破先于下一个C的确认（或者根本没有下一个C）-> 立刻封口
            if breakout_idx is not None and (confirm_idx is None or breakout_idx <= confirm_idx):
                outcome = ('sealed', breakout_idx)
                break
            if confirm_idx is None:
                outcome = 'open'   # 数据走完，结构仍未被跌破
                break

            segment = ohlcv[start_idx:confirm_idx + 1]
            cand_low_rel  = min(range(len(segment)), key=lambda i: segment[i][3])
            cand_low      = segment[cand_low_rel][3]
            cand_low_idx  = start_idx + cand_low_rel

            # 低点必须出现在"连续3根确认"开始之前（低点 -> 3根确认 -> 才有资格算C）。
            # 低点落在确认组3根里面的（比如确认蜡烛自己的下影线充当低点），确认作废跳过
            if cand_low_idx >= confirm_idx - 2:
                scan_from = confirm_idx + 1
                continue
            # 高点必须在低点之后：从低点往后画到这波反弹的最高点，不能回头框住低点之前的高点。
            # 反弹可以延伸到确认点之后，边界跟C1一样用"出现更低的低点或均线翻转"截止
            leg = find_leg_high(ohlcv, highs, lows, ma50_series, ma150_series, ma200_series, n,
                                cand_low_idx, cand_low)
            if leg is None:
                # 低点之后没有真实的反弹（下一根就跌破低点了）——不构成C，
                # 这个确认点作废，跳过去继续往后找
                scan_from = confirm_idx + 1
                continue
            cand_high, cand_high_idx, _ = leg
            cand_end_idx  = max(confirm_idx, cand_high_idx)

            cand_pct = (cand_high - cand_low) / cand_low if cand_low else 0
            ref_pct = ref.get('contraction', ref.get('bounce'))

            # 突破参考高点、或者这段的百分比比前一个C还大（没有收缩），
            # 都算是取代前一个C：参考高点/百分比直接更新成这个新值，重新找下一段
            if cand_high >= ref['high'] or (ref_pct is not None and cand_pct >= ref_pct):
                ref['high'] = cand_high
                ref['high_idx'] = cand_high_idx
                if 'bounce' in ref:
                    # [EXP] 甲规则配套：C1被取代延伸时，如果候选的低点更低，
                    # C1的锚也跟着下移（C1 = 结构最低点 -> 它之后的最高反弹）
                    if cand_low < ref['low']:
                        ref['low'] = cand_low
                        ref['low_idx'] = cand_low_idx
                    ref['bounce'] = (cand_high - ref['low']) / ref['low'] if ref['low'] else 0
                    ref['confirm_idx'] = cand_end_idx
                    ref['box_end_idx'] = max(ref.get('box_end_idx', 0), cand_end_idx)
                else:
                    ref['low'] = cand_low
                    ref['low_idx'] = cand_low_idx
                    ref['contraction'] = cand_pct
                    ref['box_end_idx'] = cand_end_idx  # 段被延伸，搜索起点跟着确认点走
                    # 级联取代：被撑大的C如果%反超了它前一个C，就把前一个吞掉
                    # （C1不参与被吞，它的低点是整个Base的锚）
                    while len(chain) >= 3 and (chain[-1].get('contraction', 0)
                                               >= chain[-2].get('contraction', chain[-2].get('bounce', 0))):
                        del chain[-2]
                continue

            chain.append({
                "low": cand_low, "low_idx": cand_low_idx,
                "search_start_idx": start_idx,
                "high": cand_high, "high_idx": cand_high_idx,
                "box_end_idx": cand_end_idx,
                "contraction": cand_pct,
            })

            # 超过6个C（C1~C6）就是不好的结构，这个候选整段作废
            if len(chain) > 6:
                outcome = ('void', cand_end_idx)
                break

        if outcome == 'open':
            open_chain = chain
            break

        kind, idx = outcome
        if kind == 'sealed':
            if len(chain) >= 2:
                bases.append({"num": len(bases) + 1, "legs": chain, "breakout_idx": idx})
            else:
                discards.append({"reason": "C1-only被打穿", "legs": chain, "end_idx": idx})
            # 只有C1、连C2都没有就被跌破的，不算Base，直接从突破点继续
            search_start = idx
        else:
            discards.append({"reason": "超6段作废", "legs": chain, "end_idx": idx})
            search_start = idx + 1

    if not bases and not open_chain:
        return None

    return {"reset_idx": reset_end_idx, "bases": bases, "open": open_chain, "discards": discards}

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
    plot_line(ma150_series, '#00FFFF', 'MA150')
    plot_line(ma200_series, '#FF0000', 'MA200')

def draw_price_range(ax, x_start, x_end, low, high, color, label):
    """
    模仿 TradingView 的 Price Range 工具：实心半透明矩形，从 x_start 盖到 x_end，
    覆盖低点到高点的整个价格区间，右上角标价格差/百分比
    """
    pct = (high - low) / low * 100

    left  = min(x_start, x_end)
    right = max(x_start, x_end)
    width = max(right - left, 0.6)

    ax.add_patch(mpatches.Rectangle(
        (left, low), width, high - low,
        facecolor=color, edgecolor=color, alpha=0.25, linewidth=1.5, zorder=3
    ))

    # 紧凑标签：框顶居中一行小字，避免多个C的标签互相叠死
    ax.text(
        (left + right) / 2, high, f"{label} +{pct:.1f}%",
        fontsize=8, fontweight='bold', color=color,
        va='bottom', ha='center', zorder=4
    )

def draw_base_box(ax, x_start, x_end, low, high, num):
    """
    Base 区域：固定金色的大框，纵向 = C1低点到C1高点，横向 = C1起点到突破K线，
    罩在所有 C 段的小框外面
    """
    ax.add_patch(mpatches.Rectangle(
        (x_start, low), max(x_end - x_start, 0.6), high - low,
        facecolor=BASE_FILL_COLOR, edgecolor=BASE_EDGE_COLOR,
        alpha=0.15, linewidth=2.0, zorder=2
    ))
    ax.annotate(
        f"Base {num}",
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
              f"共{len(legs)}个C，突破于idx={b['breakout_idx']}")
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

        colors = ['#1f77b4', '#9467bd', '#d62728', '#ff7f0e', '#2ca02c']

        def draw_legs(legs):
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
                                 leg['low'], leg['high'], color, f'C{idx + 1}')

        # 只画已完成（封口）的 Base；没凑齐C1+C2并突破的未封口结构不画
        for b in bases:
            c1 = b['legs'][0]
            if not in_window(c1['low_idx'], b['breakout_idx']):
                continue
            draw_base_box(ax, c1['low_idx'] - offset, b['breakout_idx'] - offset,
                          c1['low'], c1['high'], b['num'])
            draw_legs(b['legs'])

        n_base = len(bases)
        label = f"Bear VCP — {n_base} Base" + ("s" if n_base != 1 else "")
        ax.set_title(f"{SYMBOL} 1H SHORT [EXP] — {label}{title_suffix} | Price: ${price:.5f}",
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

        filepath = os.path.join(BASE_DIR, 'charts', f'{coin_name}_EXP_BEAR_{suffix}_{timestamp}.png')
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
