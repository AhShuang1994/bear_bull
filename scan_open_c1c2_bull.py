import sys
sys.stdout.reconfigure(encoding='utf-8')

import os
import time
import json
import ccxt
import requests
import matplotlib.pyplot as plt

import plot_c1c2_bull as mod

# 多头版全市场三阶段筛选（scan_open_c1c2.py 的镜像）：
#   第一关：日线 MA150 向上（新币<200天豁免）
#   第二关：1H 多头排列 (MA50>MA150>MA200)
#   图池内检查活结构（C1+至少一个C2、还没升破C1高点）
# 按最近一次 Reset 之后已完成的 Base 数量分级：STRONG(0) / NORMAL(1) / WEAK(>=2)
STABLE_BASES = {'USDC', 'USD1', 'USDE', 'DAI', 'TUSD', 'FDUSD', 'USDD', 'PYUSD',
                'BUSD', 'USDP', 'GUSD', 'USDY', 'USDR', 'EURT', 'USTC', 'XUSD'}

MIN_C1_PCT     = 0.05        # C1 回调不足5% = 不健康的C1，直接过滤
MIN_DAILY_USDT = 1_000_000   # 日均成交额下限：平均量 × 现价 × 每天根数 >= 1M
VOL_MA_LEN     = 50          # 平均量窗口 = 最近50根（对齐 TradingView 的 Volume MA50）

PUSH_TELEGRAM  = True
from telegram_config import TELEGRAM_TOKEN, CHAT_ID  # 私密配置不入库（.gitignore）

exchange = mod.exchange
BASE_DIR = os.path.dirname(__file__)


def is_ma150_rising(ohlcv_1d):
    """日线 MA150 是否趋向上：现在的 MA150 > 10根前的 MA150（同 main.py）"""
    closes = [c[4] for c in ohlcv_1d]
    if len(closes) < 160:
        return False
    ma150_now  = mod.get_sma(closes[-150:], 150)
    ma150_prev = mod.get_sma(closes[-160:-10], 150)
    if not ma150_now or not ma150_prev:
        return False
    return ma150_now > ma150_prev


def tg_send_text(text):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          data={"chat_id": CHAT_ID, "text": text}, timeout=30)
        print(f"[TG] sendMessage ok={r.json().get('ok')}")
    except Exception as e:
        print(f"[TG] sendMessage error: {e}")


def tg_send_album(items):
    media, files = [], {}
    try:
        for i, (fp, cap) in enumerate(items):
            key = f"photo{i}"
            files[key] = open(fp, 'rb')
            media.append({"type": "photo", "media": f"attach://{key}", "caption": cap})
        r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMediaGroup",
                          data={"chat_id": CHAT_ID, "media": json.dumps(media)},
                          files=files, timeout=120)
        print(f"[TG] sendMediaGroup x{len(items)} ok={r.json().get('ok')}")
    except Exception as e:
        print(f"[TG] sendMediaGroup error: {e}")
    finally:
        for f in files.values():
            f.close()


def plot_open(sym, ohlcv, res, timestamp, tier='LIVE'):
    legs = res['open']
    n = len(ohlcv)
    closes = [c[4] for c in ohlcv]
    ma50 = mod.calc_sma_series(closes, 50)
    ma150 = mod.calc_sma_series(closes, 150)
    ma200 = mod.calc_sma_series(closes, 200)

    w_start = max(0, legs[0]['high_idx'] - 25)
    view = ohlcv[w_start:]
    offset = w_start

    fig, ax = plt.subplots(figsize=(16, 8))
    mod.plot_candlestick(ax, view)
    mod.plot_ma_lines_from_series(ax, ma50, ma150, ma200, w_start, n)

    y_lo = min(c[3] for c in view)
    y_hi = max(c[2] for c in view)
    pad = (y_hi - y_lo) * 0.04
    ax.set_xlim(-2, len(view) + 1)
    ax.set_ylim(y_lo - pad * 1.5, y_hi + pad * 2)

    colors = ['#1f77b4', '#9467bd', '#d62728', '#ff7f0e', '#2ca02c']
    for idx, leg in enumerate(legs):
        right = max(leg['low_idx'], leg.get('box_end_idx', leg['low_idx']))
        if idx + 1 < len(legs):
            right = min(right, legs[idx + 1]['high_idx'] - 1)  # 不与下一个C的框重叠
        right = max(right, leg['low_idx'])
        mod.draw_price_range(ax, leg['high_idx'] - offset, right - offset,
                             leg['low'], leg['high'], colors[idx % len(colors)], f'C{idx + 1}')

    # C1 高点画一条水平虚线：升破它 = 突破/买入触发位
    ax.axhline(legs[0]['high'], color='#B8860B', linestyle='--', linewidth=1.2, alpha=0.8)
    ax.text(len(view), legs[0]['high'], f" C1 high {legs[0]['high']:.5f}",
            color='#B8860B', fontsize=9, va='center', ha='left')

    price = ohlcv[-1][4]
    ax.set_title(f"{sym} 1H LONG — [{tier}] C1..C{len(legs)} | Bases done: {len(res['bases'])} | Price: ${price:.5f}",
                 fontsize=14, fontweight='bold', pad=20)
    num = len(view)
    step = max(1, num // 15)
    ax.set_xticks(range(0, num, step))
    ax.set_xticklabels([f"{i + offset}" for i in range(0, num, step)], rotation=45)
    ax.set_xlabel('1H Candle Index', fontsize=11)
    ax.set_ylabel('Price (USDT)', fontsize=11)
    ax.legend(loc='upper left', fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_facecolor('#f8f9fa')

    coin = sym.split('/')[0]
    filepath = os.path.join(BASE_DIR, 'charts', f'{coin}_OPEN_BULL_{timestamp}.png')
    plt.tight_layout()
    plt.savefig(filepath, dpi=110, bbox_inches='tight', facecolor='white')
    plt.close()
    print(f"[SAVED] {filepath}")
    return filepath


def cleanup_old_open_charts(max_age_hours=24):
    """清掉超过24小时的扫描图（*_OPEN_*.png）；单币验证图（*_BASES_*）不动"""
    charts_dir = os.path.join(BASE_DIR, 'charts')
    if not os.path.isdir(charts_dir):
        return
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for fn in os.listdir(charts_dir):
        if '_OPEN_' in fn and fn.endswith('.png'):
            fp = os.path.join(charts_dir, fn)
            try:
                if os.path.getmtime(fp) < cutoff:
                    os.remove(fp)
                    removed += 1
            except OSError:
                pass
    if removed:
        print(f"[CLEAN] 已删除 {removed} 张超过{max_age_hours}小时的OPEN旧图")


def main():
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    cleanup_old_open_charts()
    markets = exchange.load_markets()
    symbols = [s for s, m in markets.items()
               if m.get('swap') and m.get('linear') and m.get('quote') == 'USDT'
               and m.get('active') and m.get('base') not in STABLE_BASES]
    print(f"[SCAN] 全市场 {len(symbols)} 个USDT永续（已剔除稳定币），多头三阶段筛选...")

    candles_per_day = 86400 // exchange.parse_timeframe(mod.TIMEFRAME)
    pool = []
    for i, sym in enumerate(symbols):
        try:
            ohlcv_1d = exchange.fetch_ohlcv(sym, '1d', limit=210)
            is_new_listing = len(ohlcv_1d) < 200
            if not is_new_listing and not is_ma150_rising(ohlcv_1d):
                continue
            # 轻量预检（2026-07-18 提速，判定语义不变，镜像空头扫描器）：先拉210根做完
            # 流动性+排列两关，过关才分页拉全量2000根给结构检测
            ohlcv_lite = exchange.fetch_ohlcv(sym, mod.TIMEFRAME, limit=210)
            # 流动性关卡：最近50根平均量（=TV的Volume MA50）× 现价 × 每天根数（1H就是x24）>= 1M USDT
            avg_vol = sum(c[5] for c in ohlcv_lite[-VOL_MA_LEN:]) / VOL_MA_LEN
            if avg_vol * ohlcv_lite[-1][4] * candles_per_day < MIN_DAILY_USDT:
                continue
            closes = [c[4] for c in ohlcv_lite]
            m50 = mod.get_sma(closes, 50)
            m150 = mod.get_sma(closes, 150)
            m200 = mod.get_sma(closes, 200)
            if not (m50 and m150 and m200 and m50 > m150 > m200):
                continue
            ohlcv = mod.fetch_ohlcv_paginated(sym, mod.TIMEFRAME, mod.LIMIT)
            if len(ohlcv) < 300:
                continue
        except ccxt.RateLimitExceeded:
            print(f"  [{sym}] 限流，等30秒继续")
            time.sleep(30)
            continue
        except Exception as e:
            print(f"  [{sym}] error: {e}")
            continue
        pool.append((sym, ohlcv))
        print(f"  [POOL] {sym}  (日线MA150向上 + 1H多头排列)")

    print(f"\n[POOL] 图池共 {len(pool)} 个，开始检查活结构...")

    hits = []
    for sym, ohlcv in pool:
        res = mod.find_bases_bull(ohlcv)
        if not res or not res.get('open') or len(res['open']) < 2:
            continue
        legs = res['open']
        c1_pct = legs[0].get('pullback', legs[0].get('bounce'))
        if c1_pct < MIN_C1_PCT:
            print(f"  [FILTER] {sym}: C1只有{c1_pct*100:.1f}% (<{MIN_C1_PCT*100:.0f}%) 不健康，跳过")
            continue
        last_pct = legs[-1].get('contraction', legs[-1].get('pullback'))
        n_bases = len(res['bases'])
        tier = 'STRONG' if n_bases == 0 else ('NORMAL' if n_bases == 1 else 'WEAK')
        print(f"  [{tier}] {sym}: 已完成Base {n_bases}个  当前结构{len(legs)}个C  "
              f"C1回调-{legs[0]['pullback']*100:.1f}%  最新收缩-{last_pct*100:.1f}%")
        hits.append((tier, sym, ohlcv, res))

    print(f"\n[SCAN] 完成：{len(hits)} 个命中")
    summary_lines = [f"🐂 多头VCP扫描 {timestamp}", ""]
    for tier in ('STRONG', 'NORMAL', 'WEAK'):
        members = [(sym, res) for t, sym, _, res in hits if t == tier]
        names = ", ".join(
            f"{sym.split('/')[0]}(C{len(res['open'])}, "
            f"-{res['open'][-1].get('contraction', res['open'][-1].get('pullback'))*100:.1f}%)"
            for sym, res in members)
        line = f"[{tier}] {len(members)}个: {names if names else '-'}"
        print(line)
        summary_lines.append(line)

    os.makedirs(os.path.join(BASE_DIR, 'charts'), exist_ok=True)

    order = {'STRONG': 0, 'NORMAL': 1}
    to_plot = sorted([h for h in hits if h[0] in order], key=lambda h: order[h[0]])
    plotted = []
    for tier, sym, ohlcv, res in to_plot:
        fp = plot_open(sym, ohlcv, res, timestamp, tier)
        legs = res['open']
        last_pct = legs[-1].get('contraction', legs[-1].get('pullback')) * 100
        cap = (f"[{tier}] {sym.split('/')[0]} LONG | C{len(legs)} 收缩-{last_pct:.1f}% | "
               f"C1高点(触发) {legs[0]['high']:.5f} | 已完成Base {len(res['bases'])}")
        plotted.append((tier, fp, cap))

    if PUSH_TELEGRAM:
        tg_send_text("\n".join(summary_lines))
        for tier in ('STRONG', 'NORMAL'):
            items = [(fp, cap) for t, fp, cap in plotted if t == tier]
            for i in range(0, len(items), 10):
                tg_send_album(items[i:i + 10])


if __name__ == '__main__':
    main()
