"""
C1C2 v4 —— 统一确认机制 + boxEnd+1 段起点
================================================================================
2026-07-21 用户拍板："统一 + boxend+1 都做。"

v4 相对 v3 的两处结构性改动：
  1. 统一确认机制：C2~C6 不再用「find_c_pattern 独立找3连 + 段内取极值」，改成和 C1
     同一个 find_c —— 从段起点逐根跟踪极值(锚点)，从锚点数 streak(锚点当第一根，v2口径)，
     连续3根合格即确认，再 find_leg 找另一极值。C1 额外保留 MA50 关(低点收盘在MA50下方
     + 反弹段触碰MA50)，C2~C6 不检查 MA50。
  2. 段起点 = 上一个C的 box_end + 1（boxEnd+1，统一到所有C）。

  由此顺带得到：主动创新高/低规则不再需要(新机制里锚点天然是段内极值)；同根C例外
  也被 boxEnd+1 覆盖。SIREN 假C7 被挡(段起点推到C6框后)。
  ⚠️ 已知代价(用户接受)：单个C可能横跨很多根(锚点到确认跨度大时框膨胀)。

【实现】读原引擎源码→exec(复用其 helpers 与全部画图)→再 exec 覆盖 find_bases 为 v4 版。
仓库原文件一字节不改。图输出 *_BASES_BEARV4_*.png / *_BULLV4_*.png。

【用法】
    python c1c2_v4.py bear SIREN/USDT:USDT
    python c1c2_v4.py bull BANK/USDT:USDT
    python c1c2_v4.py diff SIREN/USDT:USDT      # v3 vs v4 对照
    python c1c2_v4.py diff                       # 全锚点
"""
import os, sys, types
sys.stdout.reconfigure(encoding='utf-8')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import c1c2_v3

ANCHOR = ['SIREN/USDT:USDT', 'SLX/USDT:USDT', 'BANK/USDT:USDT', 'DOGE/USDT:USDT',
          'LIT/USDT:USDT', 'UAI/USDT:USDT', 'AVAX/USDT:USDT', 'BTC/USDT:USDT',
          'ETH/USDT:USDT', 'SOL/USDT:USDT', 'SUI/USDT:USDT', 'LINK/USDT:USDT']

# ── v4 空头 find_bases（覆盖原引擎同名函数；在原引擎命名空间 exec，可用其 helpers）──
V4_BEAR = r'''
def find_bases(ohlcv):
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
'''

# ── v4 多头 find_bases_bull（空头的完整镜像）─────────────────────────────────────
V4_BULL = r'''
def find_bases_bull(ohlcv):
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
        if all([m150_p, m200_p, m150_i, m200_i]) and m150_p >= m200_p and m150_i < m200_i:
            last_cross_idx = i; cross_used = False
        ma50 = ma50_series[i]
        if not all([ma50, m150_i, m200_i]):
            continue
        if last_cross_idx is not None and not cross_used and ma50 > m150_i > m200_i:
            reset_end_idx = i; cross_used = True
    if reset_end_idx is None:
        return None

    def find_c(start, require_ma50):
        high_idx = None; high_over = False; failed = None; streak = 0; confirmed = False
        for i in range(start, n):
            if high_idx is None or highs[i] > highs[high_idx]:
                high_idx = i
                m = ma50_series[i]
                high_over = m is not None and ohlcv[i][4] > m
                streak = 1 if is_c_candle_bull(ohlcv[i]) else 0
                confirmed = False
                continue
            if is_c_candle_bull(ohlcv[i]):
                streak += 1
                if streak >= 3:
                    confirmed = True
            else:
                streak = 0
            ready = confirmed and (high_over if require_ma50 else True)
            if not ready or high_idx == failed:
                continue
            leg = find_leg_low(ohlcv, highs, lows, ma50_series, ma150_series, ma200_series, n,
                               high_idx, highs[high_idx])
            if leg is None:
                failed = high_idx; continue
            low, low_idx, region_end = leg
            if i > region_end:
                failed = high_idx; continue
            if require_ma50:
                touched = any(ma50_series[j] is not None and lows[j] <= ma50_series[j]
                              for j in range(high_idx, low_idx + 1))
                if not touched:
                    failed = high_idx; continue
            return {"high": highs[high_idx], "high_idx": high_idx, "low": low, "low_idx": low_idx,
                    "contraction": (highs[high_idx] - low) / highs[high_idx] if highs[high_idx] else 0,
                    "box_end_idx": max(low_idx, i), "confirm_at": i}
        return None

    bases = []; open_chain = None; search_start = reset_end_idx
    while search_start < n - 5:
        raw = find_c(search_start, True)
        if raw is None:
            break
        c1 = {"high": raw["high"], "high_idx": raw["high_idx"], "low": raw["low"],
              "low_idx": raw["low_idx"], "pullback": raw["contraction"], "box_end_idx": raw["box_end_idx"]}
        chain = [c1]; c1_high = c1['high']; outcome = None; guard = 0
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
                if highs[i] > c1_high:
                    breakout_idx = i; break
            cand = find_c(seg_start, False)
            confirm_at = cand['confirm_at'] if cand else None
            if breakout_idx is not None and (confirm_at is None or breakout_idx <= confirm_at):
                outcome = ('sealed', breakout_idx); break
            if cand is None:
                outcome = 'open'; break
            cand_high = cand['high']; cand_high_idx = cand['high_idx']
            cand_low = cand['low']; cand_low_idx = cand['low_idx']; cand_end = cand['box_end_idx']
            cand_pct = cand['contraction']; ref_pct = ref.get('contraction', ref.get('pullback'))
            if cand_low <= ref['low'] or (ref_pct is not None and cand_pct >= ref_pct):
                ref['low'] = cand_low; ref['low_idx'] = cand_low_idx
                if 'pullback' in ref:
                    ref['pullback'] = (ref['high'] - cand_low) / ref['high'] if ref['high'] else 0
                    ref['box_end_idx'] = max(ref.get('box_end_idx', 0), cand_end)
                else:
                    ref['high'] = cand_high; ref['high_idx'] = cand_high_idx
                    ref['contraction'] = cand_pct; ref['box_end_idx'] = cand_end
                    while len(chain) >= 3 and (chain[-1].get('contraction', 0)
                                               >= chain[-2].get('contraction', chain[-2].get('pullback', 0))):
                        del chain[-2]
                continue
            chain.append({"high": cand_high, "high_idx": cand_high_idx, "low": cand_low,
                          "low_idx": cand_low_idx, "box_end_idx": cand_end, "contraction": cand_pct})
        if outcome == 'open':
            open_chain = chain; break
        _, idx = outcome
        if len(chain) >= 2:
            bases.append({"num": len(bases) + 1, "legs": chain, "breakout_idx": idx, "weak": len(chain) > 6})
        search_start = idx
    if not bases and not open_chain:
        return None
    return {"reset_idx": reset_end_idx, "bases": bases, "open": open_chain}
'''

ENG = {
    'bear': {'file': 'plot_c1c2_bear_doge.py', 'entry': 'find_bases',
             'v4src': V4_BEAR, 'name_old': '_BASES_BEAR_', 'name_new': '_BASES_BEARV4_'},
    'bull': {'file': 'plot_c1c2_bull.py', 'entry': 'find_bases_bull',
             'v4src': V4_BULL, 'name_old': '_BASES_BULL_', 'name_new': '_BASES_BULLV4_'},
}


def load(side, v4=True):
    cfg = ENG[side]
    src = open(os.path.join(BASE_DIR, cfg['file']), encoding='utf-8').read()
    if v4:
        assert src.count(cfg['name_old']) == 1
        src = src.replace(cfg['name_old'], cfg['name_new'])
    m = types.ModuleType(f"{side}_v4")
    m.__dict__['__file__'] = os.path.join(BASE_DIR, cfg['file'])
    m.__dict__['__name__'] = f"{side}_v4"
    exec(compile(src, cfg['file'], 'exec'), m.__dict__)
    if v4:
        exec(compile(cfg['v4src'], f'{side}_v4_override', 'exec'), m.__dict__)  # 覆盖 find_bases
    return m


def _pct(l):
    for k in ('contraction', 'pullback', 'bounce'):
        if k in l:
            return l[k] * 100
    return 0


def _legs(ls):
    return ' '.join(f"C{i+1}(低{l['low_idx']}高{l['high_idx']}框{l.get('box_end_idx')} {_pct(l):.1f}%)"
                    for i, l in enumerate(ls))


def summarize(r):
    if not r:
        return '无结构'
    o = [f"reset={r['reset_idx']}"]
    for b in r['bases']:
        o.append(f"Base{b['num']}[{_legs(b['legs'])}]突破{b['breakout_idx']}" + ('弱' if b.get('weak') else ''))
    if r['open']:
        o.append(f"OPEN[{_legs(r['open'])}]")
    return '\n      '.join(o)


def run_diff(symbols):
    v3 = {s: c1c2_v3.load(s, 'v3') for s in ENG}
    v4 = {s: load(s, True) for s in ENG}
    fetch = v4['bear'].fetch_ohlcv_paginated
    same = chg = 0
    for sym in symbols:
        try:
            o = fetch(sym, '1h', 2000)
        except Exception as e:
            print(f'{sym} 取数失败 {e}'); continue
        for side in ('bear', 'bull'):
            e = ENG[side]['entry']
            a = summarize(getattr(v3[side], e)(o))
            b = summarize(getattr(v4[side], e)(o))
            tag = '空头' if side == 'bear' else '多头'
            if a == b:
                same += 1
            else:
                chg += 1
                print(f'\n{"="*78}\n{sym} {tag}')
                print(f'  v3: {a}')
                print(f'  v4: {b}')
    print(f'\n{"="*78}\nv3 vs v4：{same} 一致，{chg} 变化')


def main():
    args = sys.argv[1:]
    mode = args[0] if args else ''
    if mode == 'diff':
        run_diff(args[1:] or ANCHOR); return
    if mode not in ENG or len(args) < 2:
        print(__doc__); sys.exit(1)
    sys.argv = [ENG[mode]['file']] + args[1:]
    print(f'[V4] 统一确认 + boxEnd+1 —— {"空头" if mode == "bear" else "多头"}')
    load(mode, True).main()


if __name__ == '__main__':
    main()
