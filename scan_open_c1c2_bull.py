import sys
sys.stdout.reconfigure(encoding='utf-8')

import os
import time
import json
import ccxt
import requests
import matplotlib.pyplot as plt

import plot_c1c2_bull as mod
import stage_daily

# 多头版全市场三阶段筛选（scan_open_c1c2.py 的镜像）：
#   第一关：日线 Stage 关——只收 Stage 2（MA150 趋上，±0.3%平带，只用已收盘日线；
#           新币<200天豁免；2026-07-18 起替代旧的二元 is_ma150_rising，见 stage_daily.py）
#   第二关：1H 多头排列 (MA50>MA150>MA200)
#   图池内检查活结构（C1+至少一个C2、还没升破C1高点）
# 按最近一次 Reset 之后已完成的 Base 数量分级：STRONG(0) / NORMAL(1) / WEAK(>=2)
STABLE_BASES = {'USDC', 'USD1', 'USDE', 'DAI', 'TUSD', 'FDUSD', 'USDD', 'PYUSD',
                'BUSD', 'USDP', 'GUSD', 'USDY', 'USDR', 'EURT', 'USTC', 'XUSD',
                'RLUSD'}  # RLUSD=Ripple USD，2026-07-18 S1箱体扫描时发现漏网补进

MIN_C1_PCT     = 0.05        # C1 回调不足5% = 不健康的C1，直接过滤
MIN_DAILY_USDT = 1_000_000   # 日均成交额下限：平均量 × 现价 × 每天根数 >= 1M
VOL_MA_LEN     = 50          # 平均量窗口 = 最近50根（对齐 TradingView 的 Volume MA50）

PUSH_TELEGRAM  = True
from telegram_config import TELEGRAM_TOKEN, CHAT_ID  # 私密配置不入库（.gitignore）

exchange = mod.exchange
BASE_DIR = os.path.dirname(__file__)


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
    # 股票/商品类代币化永续（Bybit symbolType 元数据）单独分组：照跑全管线，输出分开（2026-07-18 用户定）
    tradfi = {s: markets[s]['info'].get('symbolType', '') in ('stock', 'commodity') for s in symbols}
    print(f"[SCAN] 全市场 {len(symbols)} 个USDT永续（已剔除稳定币，含股票/商品类 {sum(tradfi.values())} 个单独分组），多头三阶段筛选...")

    # ④a 基准：BTC 日线 stage（拉1000根，尽量让"第N天"不封顶）
    try:
        btc_st = stage_daily.classify(exchange.fetch_ohlcv('BTC/USDT:USDT', '1d', limit=1000))
        print(f"[BTC] {stage_daily.STAGE_NAMES[btc_st['stage']]} "
              f"第{btc_st['days']}天{'+' if btc_st['capped'] else ''}  斜率{btc_st['slope']:+.2f}%")
    except Exception as e:
        btc_st = None
        print(f"[BTC] 基准获取失败: {e}")

    candles_per_day = 86400 // exchange.parse_timeframe(mod.TIMEFRAME)
    pool = []
    year_high = {}   # sym -> 是否破一年新高（Stage加分项，只标注不过滤，2026-07-18 用户定，镜像空头）
    stage_info = {}  # sym -> stage_daily.classify 结果（caption 里标 S2第N天/vs BTC）
    s1_watch = []    # ⑤ S1箱体观察清单：(sym, (box_high, box_low, span_days))
    for i, sym in enumerate(symbols):
        try:
            # 400根日线：Stage判定要161根已收盘，破一年新高要365根+当日，一次请求都够
            ohlcv_1d = exchange.fetch_ohlcv(sym, '1d', limit=400)
            is_new_listing = len(ohlcv_1d) < 200
            # 日线 Stage 关：多头只收 Stage 2；顺手收集 S1 箱体观察清单
            # （S1 = 趋平+长箱体+量能低迷 = 蓄势，箱体顶被放量突破就是 S2 启动）
            st = stage_daily.classify(ohlcv_1d)
            stage_info[sym] = st
            if not is_new_listing and st['stage'] != 2:
                if st['stage'] == 1:
                    # 箱体天数门槛分尺度（2026-07-18 用户定）：加密60天 / 股票商品180天
                    box = stage_daily.find_s1_box(
                        ohlcv_1d, min_days=180 if tradfi.get(sym) else 60)
                    if box and stage_daily.vol_subdued(ohlcv_1d):
                        s1_watch.append((sym, box, tradfi.get(sym)))
                continue
            # 当日（可以是未收盘的）高点升破之前365根已收盘日线的最高点 = 破一年新高；
            # 新币不足365天就看全部历史（=历史新高）
            prior_1d = ohlcv_1d[:-1][-365:]
            year_high[sym] = bool(prior_1d) and ohlcv_1d[-1][2] > max(c[2] for c in prior_1d)
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
        print(f"  [POOL] {sym}  (日线S2 + 1H多头排列)" + ("[股票/商品]" if tradfi.get(sym) else ""))

    print(f"\n[POOL] 图池共 {len(pool)} 个，开始检查活结构...")

    hits = []
    vol_ud = {}  # sym -> (up, dn) 破VolMA50的阳/阴柱根数（C1起点~最新收盘，NOTES 4.10）
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
        vol_ud[sym] = stage_daily.vol_break_counts(ohlcv, legs[0]['high_idx'])
        hits.append((tier, sym, ohlcv, res))

    print(f"\n[SCAN] 完成：{len(hits)} 个命中")

    def is_buy(res):
        # 🎯买点（2026-07-18 用户定）：最新一个C收缩<=10%（只看最新C，C1不参与——
        # 它是首次深回调本来就该大）。只标注不过滤；空头镜像同一个10%
        return res['open'][-1].get('contraction', res['open'][-1].get('pullback')) <= 0.10

    def vol_tag(sym):
        # 🔊VOL+/⚠️VOL-（NOTES 4.10，2026-07-19 用户定，镜像空头）：多头顺方向=阳柱，
        # 顺方向>=逆方向×1.5 且 >=5根 = VOL+；反向同标准 = VOL-警示
        up, dn = vol_ud.get(sym, (0, 0))
        if up >= 1.5 * dn and up >= 5:
            return '🔊'
        if dn >= 1.5 * up and dn >= 5:
            return '⚠️'
        return ''

    summary_lines = [f"🐂 多头VCP扫描 {timestamp}", ""]
    for tier in ('STRONG', 'NORMAL', 'WEAK'):
        members = [(sym, res) for t, sym, _, res in hits if t == tier and not tradfi.get(sym)]
        names = ", ".join(
            f"{'🎯' if is_buy(res) else ''}{sym.split('/')[0]}{vol_tag(sym)}(C{len(res['open'])}, "
            f"-{res['open'][-1].get('contraction', res['open'][-1].get('pullback'))*100:.1f}%)"
            for sym, res in members)
        line = f"[{tier}] {len(members)}个: {names if names else '-'}"
        print(line)
        summary_lines.append(line)

    # 股票/商品类单独一节（照跑管线，只是分开呈现，2026-07-18 用户定）
    stock_hits = [(t, sym, res) for t, sym, _, res in hits if tradfi.get(sym)]
    if stock_hits:
        names = ", ".join(
            f"[{t}]{'🎯' if is_buy(res) else ''}{sym.split('/')[0]}{vol_tag(sym)}(C{len(res['open'])}, "
            f"-{res['open'][-1].get('contraction', res['open'][-1].get('pullback'))*100:.1f}%)"
            for t, sym, res in stock_hits)
        line = f"📈 股票/商品 {len(stock_hits)}个: {names}"
        print(line)
        summary_lines.append("")
        summary_lines.append(line)

    # ⑤ S1箱体观察清单：趋平+两个相似高/低点相隔>=60天(加密)/180天(股票)+量能低迷
    #    ——箱体顶放量突破=S2启动
    if s1_watch:
        names = ", ".join(
            f"{sym.split('/')[0]}{'(股)' if tf else ''}(顶{hh:.4g}/底{ll:.4g}/{span}天)"
            for sym, (hh, ll, span), tf in s1_watch[:20])
        more = f" +{len(s1_watch) - 20}个" if len(s1_watch) > 20 else ""
        line = f"📦 S1箱体观察 {len(s1_watch)}个: {names}{more}"
        print(line)
        summary_lines.append("")
        summary_lines.append(line)

    os.makedirs(os.path.join(BASE_DIR, 'charts'), exist_ok=True)

    order = {'STRONG': 0, 'NORMAL': 1}
    to_plot = sorted([h for h in hits if h[0] in order], key=lambda h: order[h[0]])
    plotted = []
    for tier, sym, ohlcv, res in to_plot:
        fp = plot_open(sym, ohlcv, res, timestamp, tier)
        legs = res['open']
        last_pct = legs[-1].get('contraction', legs[-1].get('pullback')) * 100
        # ④a：加密币标 "S2第N天·比BTC早/晚N天"；股票/商品类不跟BTC比
        vsbtc = "" if tradfi.get(sym) else stage_daily.vs_btc_text(stage_info.get(sym), btc_st)
        up, dn = vol_ud.get(sym, (0, 0))
        vt = vol_tag(sym)
        volcap = (f" | 🔊VOL+(阳{up}/阴{dn})" if vt == '🔊'
                  else (f" | ⚠️VOL-(阴{dn}/阳{up})" if vt == '⚠️' else ""))
        cap = (f"[{tier}] {sym.split('/')[0]} LONG | C{len(legs)} 收缩-{last_pct:.1f}% | "
               f"C1高点(触发) {legs[0]['high']:.5f} | 已完成Base {len(res['bases'])}"
               + (" | 🎯买点(C≤10%)" if is_buy(res) else "")
               + volcap
               + (f" | {vsbtc}" if vsbtc else "")
               + (" | ⭐破一年新高" if year_high.get(sym) else ""))
        plotted.append((tier, fp, cap, tradfi.get(sym, False)))

    if PUSH_TELEGRAM:
        tg_send_text("\n".join(summary_lines))
        for tier in ('STRONG', 'NORMAL'):
            items = [(fp, cap) for t, fp, cap, tf in plotted if t == tier and not tf]
            for i in range(0, len(items), 10):
                tg_send_album(items[i:i + 10])
        # 股票/商品类单独相册（caption 自带 [tier] 前缀）
        stock_items = [(fp, cap) for t, fp, cap, tf in plotted if tf]
        for i in range(0, len(stock_items), 10):
            tg_send_album(stock_items[i:i + 10])


if __name__ == '__main__':
    main()
