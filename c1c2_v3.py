"""
C1C2 v3 —— 试验版：在 v2 基础上【移除「主动创新高/创新低」规则】
================================================================================
2026-07-21 用户指令："帮我这条规则拿掉。"

v3 = v2 的全部改动 + 去掉一条规则：
  · 继承 v2：3 连确认的第一根可以是锚点当根本身（C1 拉平到与 C2~C6 一致）。
  · 移除：§4.3.3b「候选极值那根必须主动创新高/创新低（high>前一根high / low<前一根low）」。
    该规则 2026-07-16 为杀 SIREN 假 C7 而加；但它在 BANK Base2 误伤——比较对象
    恰是 C1 的巨型反转柱插针，把真实的段起点 1927（同根C −26.2%）挡掉了。

【改动范围 —— 每个引擎两处】
  1. find_c1 锚点分支     streak = 0  →  streak = 1 if is_c_candle(当根) else 0   (= v2)
  2. 主动创新高/低那个 if 判断  →  if False（规则短路，永不作废）
  C1 的 MA50 关原样保留；C2~C6 的其他规则（3连、不落第2/3根、取代、级联）都不动。

【实现方式】读原引擎源码 → 内存里字符串替换 → exec。仓库文件一字节不改；
补丁锚点对不上立刻 assert 失败，不会静默跑错。

【用法】
    python c1c2_v3.py bear SIREN/USDT:USDT   # 空头出图 -> charts/*_BEARV3_*.png
    python c1c2_v3.py bull BANK/USDT:USDT     # 多头出图 -> charts/*_BULLV3_*.png
    python c1c2_v3.py diff BANK/USDT:USDT      # v2 与 v3 逐字段对照（隔离本条规则）
    python c1c2_v3.py diff                     # 对照 11 个锚点币
"""
import os
import sys
import types

sys.stdout.reconfigure(encoding='utf-8')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

ANCHOR_COINS = ['SIREN/USDT:USDT', 'SLX/USDT:USDT', 'DOGE/USDT:USDT', 'LIT/USDT:USDT',
                'UAI/USDT:USDT', 'AVAX/USDT:USDT', 'BTC/USDT:USDT', 'ETH/USDT:USDT',
                'SOL/USDT:USDT', 'SUI/USDT:USDT', 'LINK/USDT:USDT', 'BANK/USDT:USDT']

ENGINES = {
    'bear': {
        'file': 'plot_c1c2_bear_doge.py',
        'entry': 'find_bases',
        # v2 改动
        'streak_old': ("                low_under_ma50 = m50_at_low is not None and ohlcv[i][4] < m50_at_low\n"
                       "                streak = 0\n"),
        'streak_new': ("                low_under_ma50 = m50_at_low is not None and ohlcv[i][4] < m50_at_low\n"
                       "                # v2: 低点当根自己合格就算3连第一根\n"
                       "                streak = 1 if is_c_candle_bear(ohlcv[i]) else 0\n"),
        # v3 改动：移除主动创新低
        'newext_old': "            if cand_low_idx > 0 and cand_low >= lows[cand_low_idx - 1]:\n",
        'newext_new': "            if False:  # v3: 主动创新低规则已移除\n",
        'name_old': "_BASES_BEAR_",
        'name_new': "_BASES_BEARV3_",
    },
    'bull': {
        'file': 'plot_c1c2_bull.py',
        'entry': 'find_bases_bull',
        'streak_old': ("                high_over_ma50 = m50_at_high is not None and ohlcv[i][4] > m50_at_high\n"
                       "                streak = 0\n"),
        'streak_new': ("                high_over_ma50 = m50_at_high is not None and ohlcv[i][4] > m50_at_high\n"
                       "                # v2: 高点当根自己合格就算3连第一根\n"
                       "                streak = 1 if is_c_candle_bull(ohlcv[i]) else 0\n"),
        'newext_old': "            if cand_high_idx > 0 and cand_high <= highs[cand_high_idx - 1]:\n",
        'newext_new': "            if False:  # v3: 主动创新高规则已移除\n",
        'name_old': "_BASES_BULL_",
        'name_new': "_BASES_BULLV3_",
    },
}


def load(side, level='v3'):
    """level: 'v1' 原版 / 'v2' 只 C1拉回 / 'v3' C1拉回 + 移除主动创新高"""
    cfg = ENGINES[side]
    path = os.path.join(BASE_DIR, cfg['file'])
    src = open(path, encoding='utf-8').read()
    if level in ('v2', 'v3'):
        assert src.count(cfg['streak_old']) == 1, f"{cfg['file']}: streak 补丁锚点对不上"
        src = src.replace(cfg['streak_old'], cfg['streak_new'])
    if level == 'v3':
        assert src.count(cfg['newext_old']) == 1, f"{cfg['file']}: 主动创新高补丁锚点对不上"
        src = src.replace(cfg['newext_old'], cfg['newext_new'])
    if level != 'v1':
        assert src.count(cfg['name_old']) == 1, f"{cfg['file']}: 图名锚点对不上"
        src = src.replace(cfg['name_old'], cfg['name_new'])
    mod = types.ModuleType(f"{side}_{level}")
    mod.__dict__['__file__'] = path
    mod.__dict__['__name__'] = f"{side}_{level}"
    exec(compile(src, cfg['file'], 'exec'), mod.__dict__)
    return mod


def _pct(leg):
    for k in ('contraction', 'pullback', 'bounce'):
        if k in leg:
            return leg[k] * 100
    return 0.0


def _legs(legs):
    return ' '.join(f"C{i+1}(低{l['low_idx']} 高{l['high_idx']} 框尾{l.get('box_end_idx')} "
                    f"{_pct(l):.1f}%)" for i, l in enumerate(legs))


def summarize(res):
    if not res:
        return '无结构'
    out = [f"reset={res['reset_idx']}"]
    for b in res['bases']:
        out.append(f"Base{b['num']}[{_legs(b['legs'])}] 突破={b['breakout_idx']}"
                   + ('  [弱]' if b.get('weak') else ''))
    if res['open']:
        out.append(f"OPEN[{_legs(res['open'])}]")
    return '\n      '.join(out)


def run_diff(symbols):
    base = {s: load(s, 'v2') for s in ENGINES}   # 基线 = v2，隔离出「移除主动创新高」这一条
    v3 = {s: load(s, 'v3') for s in ENGINES}
    fetch = base['bear'].fetch_ohlcv_paginated
    same = changed = 0
    for sym in symbols:
        try:
            ohlcv = fetch(sym, '1h', 2000)
        except Exception as e:
            print(f'{sym:<20} 取数失败: {e}')
            continue
        for side in ('bear', 'bull'):
            entry = ENGINES[side]['entry']
            a = summarize(getattr(base[side], entry)(ohlcv))
            b = summarize(getattr(v3[side], entry)(ohlcv))
            tag = '空头' if side == 'bear' else '多头'
            if a == b:
                same += 1
            else:
                changed += 1
                print(f'\n{"=" * 78}\n{sym}  {tag}  ← 有变化')
                print(f'  v2: {a}')
                print(f'  v3: {b}')
    print(f'\n{"=" * 78}')
    print(f'v2 vs v3：{same + changed} 个「币×方向」中 {same} 个一致，{changed} 个有变化')


def main():
    args = sys.argv[1:]
    mode = args[0] if args else ''
    if mode == 'diff':
        run_diff(args[1:] or ANCHOR_COINS)
        return
    if mode not in ENGINES or len(args) < 2:
        print(__doc__)
        sys.exit(1)
    sys.argv = [ENGINES[mode]['file']] + args[1:]
    print(f'[V3] C1拉平 + 移除主动创新高 —— {"空头" if mode == "bear" else "多头"}')
    load(mode, 'v3').main()


if __name__ == '__main__':
    main()
