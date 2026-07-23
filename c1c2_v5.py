"""
C1C2 v5 —— 在 v4(现主引擎)基础上加【FLIP 即终止】
================================================================================
2026-07-21 用户拍板。治 DOGE 那类"慢速对翻转后结构还继续冒 C"的问题。

FLIP = C1 锚点之后慢速对第一次翻转（活结构失效点，原本只画线不参与检测）：
  多头：C1 高点之后 MA150 第一次下穿 MA200（翻空）
  空头：C1 低点之后 MA150 第一次上穿 MA200（翻多）
v5 改动：检测主循环里，若候选 C 的锚点(多头high_idx/空头low_idx)落在 FLIP 之后，
  就不再加它——当前结构到此为止(open)。已在 FLIP 之前突破封口的 Base 不受影响。

【实现】读主引擎源码(现已是v4)→exec→再 exec 覆盖 find_bases 为 v5(=v4+FLIP)。
V5 源码由 c1c2_v4 的 V4_BEAR/V4_BULL 字符串插入 FLIP 两段生成，仓库文件零改动。
图输出 *_BASES_BEARV5_*.png / *_BULLV5_*.png。

【用法】
    python c1c2_v5.py bull DOGE/USDT:USDT     # DOGE OPEN 应只剩 C1(C2/C3 被FLIP挡)
    python c1c2_v5.py bear SIREN/USDT:USDT
    python c1c2_v5.py diff DOGE/USDT:USDT       # v4(主引擎) vs v5 对照
    python c1c2_v5.py diff                       # 全锚点
"""
import os, sys, types
sys.stdout.reconfigure(encoding='utf-8')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import c1c2_v4

ANCHOR = ['SIREN/USDT:USDT', 'SLX/USDT:USDT', 'BANK/USDT:USDT', 'DOGE/USDT:USDT',
          'LIT/USDT:USDT', 'UAI/USDT:USDT', 'AVAX/USDT:USDT', 'BTC/USDT:USDT',
          'ETH/USDT:USDT', 'SOL/USDT:USDT', 'SUI/USDT:USDT', 'LINK/USDT:USDT']

# ── 从 v4 find_bases 字符串插入 FLIP 两段，生成 v5 ──────────────────────────────
# 多头：flip 基准 = C1 high_idx，条件 MA150 下穿 MA200 (not m150>m200)；拦 cand.high_idx
_BULL_INIT_OLD = "        chain = [c1]; c1_high = c1['high']; outcome = None; guard = 0\n        while True:"
_BULL_INIT_NEW = ("        chain = [c1]; c1_high = c1['high']; outcome = None; guard = 0\n"
                  "        flip_idx = -1   # v5: C1高点之后慢速对第一次翻空\n"
                  "        for fi in range(c1['high_idx'] + 1, n):\n"
                  "            if ma150_series[fi] is not None and ma200_series[fi] is not None and not (ma150_series[fi] > ma200_series[fi]):\n"
                  "                flip_idx = fi; break\n"
                  "        while True:")
_BULL_GUARD_OLD = ("            if cand is None:\n                outcome = 'open'; break\n"
                   "            cand_high = cand['high']")
_BULL_GUARD_NEW = ("            if cand is None:\n                outcome = 'open'; break\n"
                   "            if flip_idx != -1 and cand['high_idx'] >= flip_idx:\n"
                   "                outcome = 'open'; break   # v5: FLIP后不再加C\n"
                   "            cand_high = cand['high']")

# 空头：flip 基准 = C1 low_idx，条件 MA150 上穿 MA200 (not m200>m150)；拦 cand.low_idx
_BEAR_INIT_OLD = "        chain = [c1]; c1_low = c1['low']; outcome = None; guard = 0\n        while True:"
_BEAR_INIT_NEW = ("        chain = [c1]; c1_low = c1['low']; outcome = None; guard = 0\n"
                  "        flip_idx = -1   # v5: C1低点之后慢速对第一次翻多\n"
                  "        for fi in range(c1['low_idx'] + 1, n):\n"
                  "            if ma150_series[fi] is not None and ma200_series[fi] is not None and not (ma200_series[fi] > ma150_series[fi]):\n"
                  "                flip_idx = fi; break\n"
                  "        while True:")
_BEAR_GUARD_OLD = ("            if cand is None:\n                outcome = 'open'; break\n"
                   "            cand_low = cand['low']")
_BEAR_GUARD_NEW = ("            if cand is None:\n                outcome = 'open'; break\n"
                   "            if flip_idx != -1 and cand['low_idx'] >= flip_idx:\n"
                   "                outcome = 'open'; break   # v5: FLIP后不再加C\n"
                   "            cand_low = cand['low']")


def _make_v5(v4src, init_old, init_new, guard_old, guard_new):
    src = v4src
    for old, new in [(init_old, init_new), (guard_old, guard_new)]:
        assert src.count(old) == 1, f'v5 生成: 锚点命中 {src.count(old)} 次'
        src = src.replace(old, new)
    return src


V5_BEAR = _make_v5(c1c2_v4.V4_BEAR, _BEAR_INIT_OLD, _BEAR_INIT_NEW, _BEAR_GUARD_OLD, _BEAR_GUARD_NEW)
V5_BULL = _make_v5(c1c2_v4.V4_BULL, _BULL_INIT_OLD, _BULL_INIT_NEW, _BULL_GUARD_OLD, _BULL_GUARD_NEW)

ENG = {
    'bear': {'file': 'plot_c1c2_bear_doge.py', 'entry': 'find_bases',
             'v5src': V5_BEAR, 'name_old': '_BASES_BEAR_', 'name_new': '_BASES_BEARV5_'},
    'bull': {'file': 'plot_c1c2_bull.py', 'entry': 'find_bases_bull',
             'v5src': V5_BULL, 'name_old': '_BASES_BULL_', 'name_new': '_BASES_BULLV5_'},
}


def load(side, v5=True):
    cfg = ENG[side]
    src = open(os.path.join(BASE_DIR, cfg['file']), encoding='utf-8').read()
    if v5:
        assert src.count(cfg['name_old']) == 1
        src = src.replace(cfg['name_old'], cfg['name_new'])
    m = types.ModuleType(f"{side}_v5")
    m.__dict__['__file__'] = os.path.join(BASE_DIR, cfg['file'])
    m.__dict__['__name__'] = f"{side}_v5"
    exec(compile(src, cfg['file'], 'exec'), m.__dict__)
    if v5:
        exec(compile(cfg['v5src'], f'{side}_v5_override', 'exec'), m.__dict__)
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
    v4 = {s: load(s, v5=False) for s in ENG}   # 主引擎(现v4)本身
    v5 = {s: load(s, v5=True) for s in ENG}
    fetch = v5['bear'].fetch_ohlcv_paginated
    same = chg = 0
    for sym in symbols:
        try:
            o = fetch(sym, '1h', 2000)
        except Exception as e:
            print(f'{sym} 取数失败 {e}'); continue
        for side in ('bear', 'bull'):
            e = ENG[side]['entry']
            a = summarize(getattr(v4[side], e)(o))
            b = summarize(getattr(v5[side], e)(o))
            tag = '空头' if side == 'bear' else '多头'
            if a == b:
                same += 1
            else:
                chg += 1
                print(f'\n{"="*78}\n{sym} {tag}')
                print(f'  v4: {a}')
                print(f'  v5: {b}')
    print(f'\n{"="*78}\nv4(主引擎) vs v5：{same} 一致，{chg} 变化')


def main():
    args = sys.argv[1:]
    mode = args[0] if args else ''
    if mode == 'diff':
        run_diff(args[1:] or ANCHOR); return
    if mode not in ENG or len(args) < 2:
        print(__doc__); sys.exit(1)
    sys.argv = [ENG[mode]['file']] + args[1:]
    print(f'[V5] v4 + FLIP即终止 —— {"空头" if mode == "bear" else "多头"}')
    load(mode, v5=True).main()


if __name__ == '__main__':
    main()
