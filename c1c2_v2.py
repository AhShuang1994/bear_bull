"""
C1C2 v2 —— 试验版：3 连确认的第一根可以是【锚点当根本身】
================================================================================
2026-07-20 用户口径：低点那根（空头）/ 高点那根（多头）如果自己就是合格的 C 蜡烛，
它就是反弹/回调的第一根，应当计入 3 连；不合格才从 +1 起数。判定统一交给
is_c_candle，不再因为"它是锚点"就先验排除。

依据：C2~C6 早就允许极值点当 3 连第一根（NOTES §6.6 第2条，SIREN 1928「跌出新低的
长下影反转蜡烛，紧接两根延续」）。C1 是全套规则里唯一还在搞特例的地方，本版把它拉平。

【改动范围 —— 只有一处】
    find_c1 的锚点分支     streak = 0
                      →    streak = 1 if is_c_candle(当根) else 0
    C1 的 MA50 关（低点收盘在 MA50 下方 + 反弹段触碰 MA50）原样保留。
    C2~C6 一个字没动（它们本来就是对的）。

【实现方式】不复制引擎源码，而是读取原文件、在内存里做一次字符串替换后 exec，
所以原引擎任何改动都会自动跟上；补丁锚点对不上会立刻 assert 失败，不会静默跑错。
仓库里的 plot_c1c2_bear_doge.py / plot_c1c2_bull.py 一个字节都不会被修改。

【用法】
    python c1c2_v2.py bear SLX/USDT:USDT     # 空头出图 -> charts/*_BASES_BEARV2_*.png
    python c1c2_v2.py bull LIT/USDT:USDT     # 多头出图 -> charts/*_BASES_BULLV2_*.png
    python c1c2_v2.py diff SLX/USDT:USDT     # v1 与 v2 结构逐字段对照，不出图
    python c1c2_v2.py diff                   # 对照 11 个锚点币
"""
import os
import sys
import types

sys.stdout.reconfigure(encoding='utf-8')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

ANCHOR_COINS = ['SIREN/USDT:USDT', 'SLX/USDT:USDT', 'DOGE/USDT:USDT', 'LIT/USDT:USDT',
                'UAI/USDT:USDT', 'AVAX/USDT:USDT', 'BTC/USDT:USDT', 'ETH/USDT:USDT',
                'SOL/USDT:USDT', 'SUI/USDT:USDT', 'LINK/USDT:USDT']

ENGINES = {
    'bear': {
        'file': 'plot_c1c2_bear_doge.py',
        'entry': 'find_bases',
        'streak_old': ("                low_under_ma50 = m50_at_low is not None and ohlcv[i][4] < m50_at_low\n"
                       "                streak = 0\n"),
        'streak_new': ("                low_under_ma50 = m50_at_low is not None and ohlcv[i][4] < m50_at_low\n"
                       "                # v2: 低点当根自己合格就算3连第一根\n"
                       "                streak = 1 if is_c_candle_bear(ohlcv[i]) else 0\n"),
        'name_old': "_BASES_BEAR_",
        'name_new': "_BASES_BEARV2_",
    },
    'bull': {
        'file': 'plot_c1c2_bull.py',
        'entry': 'find_bases_bull',
        'streak_old': ("                high_over_ma50 = m50_at_high is not None and ohlcv[i][4] > m50_at_high\n"
                       "                streak = 0\n"),
        'streak_new': ("                high_over_ma50 = m50_at_high is not None and ohlcv[i][4] > m50_at_high\n"
                       "                # v2: 高点当根自己合格就算3连第一根\n"
                       "                streak = 1 if is_c_candle_bull(ohlcv[i]) else 0\n"),
        'name_old': "_BASES_BULL_",
        'name_new': "_BASES_BULLV2_",
    },
}


def load(side, patch=True):
    """把引擎源码读进来，可选打 v2 补丁，然后当独立模块 exec。"""
    cfg = ENGINES[side]
    path = os.path.join(BASE_DIR, cfg['file'])
    src = open(path, encoding='utf-8').read()
    if patch:
        n = src.count(cfg['streak_old'])
        assert n == 1, f"{cfg['file']}: streak 补丁锚点命中 {n} 次（应为 1）——引擎已改动，请更新 c1c2_v2.py"
        src = src.replace(cfg['streak_old'], cfg['streak_new'])
        assert src.count(cfg['name_old']) == 1, f"{cfg['file']}: 图名锚点对不上"
        src = src.replace(cfg['name_old'], cfg['name_new'])
    mod = types.ModuleType(f"{side}_{'v2' if patch else 'v1'}")
    mod.__dict__['__file__'] = path
    mod.__dict__['__name__'] = f"{side}_{'v2' if patch else 'v1'}"
    exec(compile(src, cfg['file'], 'exec'), mod.__dict__)
    return mod


# ── diff 模式 ────────────────────────────────────────────────────────────────

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
    v1 = {s: load(s, patch=False) for s in ENGINES}
    v2 = {s: load(s, patch=True) for s in ENGINES}
    fetch = v1['bear'].fetch_ohlcv_paginated
    same = changed = 0
    for sym in symbols:
        try:
            ohlcv = fetch(sym, '1h', 2000)
        except Exception as e:
            print(f'{sym:<20} 取数失败: {e}')
            continue
        for side in ('bear', 'bull'):
            entry = ENGINES[side]['entry']
            a = summarize(getattr(v1[side], entry)(ohlcv))
            b = summarize(getattr(v2[side], entry)(ohlcv))
            tag = '空头' if side == 'bear' else '多头'
            if a == b:
                same += 1
            else:
                changed += 1
                print(f'\n{"=" * 76}\n{sym}  {tag}  ← 有变化')
                print(f'  v1: {a}')
                print(f'  v2: {b}')
    print(f'\n{"=" * 76}')
    print(f'{same + changed} 个「币×方向」中：{same} 个完全一致，{changed} 个有变化')


def main():
    args = sys.argv[1:]
    mode = args[0] if args else ''
    if mode == 'diff':
        run_diff(args[1:] or ANCHOR_COINS)
        return
    if mode not in ENGINES or len(args) < 2:
        print(__doc__)
        sys.exit(1)
    # 引擎在 import 时就从 sys.argv 读 SYMBOL，所以要先摆好再 exec
    sys.argv = [ENGINES[mode]['file']] + args[1:]
    print(f'[V2] 3连第一根可以是锚点当根 —— {"空头" if mode == "bear" else "多头"}')
    load(mode, patch=True).main()


if __name__ == '__main__':
    main()
