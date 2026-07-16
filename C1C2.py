import ccxt
import requests
import schedule
import time
import os
from datetime import datetime

# ============ 配置 ============
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "你的Bot Token")
CHAT_ID        = os.environ.get("CHAT_ID", "你的Chat ID")
EXCHANGE_ID    = "bybit"
QUOTE_CURRENCY = "USDT"
MIN_DAILY_VOL  = 1_000_000
# ==============================

exchange = getattr(ccxt, EXCHANGE_ID)({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        })
    except Exception as e:
        print(f"Telegram error: {e}")

def get_sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

# ─────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────

def is_c_candle(candle):
    """
    判断单根蜡烛是否符合 C 条件：
    - 红色蜡烛（收跌）或
    - 影线长过身体
    """
    o, h, l, c = candle[1], candle[2], candle[3], candle[4]
    body   = abs(c - o)
    wick   = (h - l) - body
    is_red = c < o
    is_long_wick = wick > body if body > 0 else True
    return is_red or is_long_wick

def find_c_pattern(candles):
    """
    在一段蜡烛里找连续 3 根都符合 C 条件的起始 index
    返回第一个符合的连续3根的 index（最后一根的位置）
    """
    for i in range(2, len(candles)):
        if (is_c_candle(candles[i])
                and is_c_candle(candles[i-1])
                and is_c_candle(candles[i-2])):
            return i  # 返回第3根蜡烛的位置
    return None

def calc_sma_series(closes, period):
    """计算每根蜡烛的 SMA 序列"""
    result = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(closes[i-period+1:i+1]) / period)
    return result

# ─────────────────────────────────────────
# 主要检测逻辑
# ─────────────────────────────────────────

def check_stage2_daily(symbol):
    """
    检查 Daily MA150 是否向上倾斜
    当前 MA150 > 前一根 MA150
    """
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, "1d", limit=160)
        if len(ohlcv) < 152:
            return False
        closes = [c[4] for c in ohlcv]
        ma150_now  = get_sma(closes[-150:], 150)
        ma150_prev = get_sma(closes[-151:-1], 150)
        if not ma150_now or not ma150_prev:
            return False
        return ma150_now > ma150_prev
    except:
        return False

def find_c1_c2(ohlcv, tf_label):
    """
    在指定 timeframe 的 K 线里寻找 C1 和 C2
    返回结果 dict 或 None
    """
    if len(ohlcv) < 210:
        return None

    closes = [c[4] for c in ohlcv]
    highs  = [c[2] for c in ohlcv]
    lows   = [c[3] for c in ohlcv]

    # 计算 MA 序列
    ma50_series  = calc_sma_series(closes, 50)
    ma150_series = calc_sma_series(closes, 150)
    ma200_series = calc_sma_series(closes, 200)

    n = len(ohlcv)

    # ── Step 1: 找最近一次 Reset ──
    # Reset = MA150 死叉 MA200（MA150 从上往下穿 MA200）之后再回到多头排列
    reset_end_idx = None

    for i in range(201, n):
        ma50  = ma50_series[i]
        ma150 = ma150_series[i]
        ma200 = ma200_series[i]
        if not all([ma50, ma150, ma200]):
            continue

        # 检查当前是多头排列
        if not (ma50 > ma150 > ma200):
            continue

        # 往前找有没有发生过 MA150 死叉 MA200
        had_death_cross = False
        for j in range(200, i):
            m150_j      = ma150_series[j]
            m200_j      = ma200_series[j]
            m150_j_prev = ma150_series[j-1]
            m200_j_prev = ma200_series[j-1]
            if not all([m150_j, m200_j, m150_j_prev, m200_j_prev]):
                continue
            # MA150 从上往下穿 MA200
            if m150_j_prev >= m200_j_prev and m150_j < m200_j:
                had_death_cross = True
                break

        if had_death_cross:
            reset_end_idx = i
            break

    if reset_end_idx is None:
        return None

    # ── Step 2: 从 Reset 之后找多头排列开始的最高点 ──
    # 找 reset_end_idx 之后的最高点（作为 C1 的起点）
    search_candles = ohlcv[reset_end_idx:]
    if len(search_candles) < 10:
        return None

    # 找多头排列以来的最高点
    highest_idx_rel = 0
    highest_price   = highs[reset_end_idx]

    for i in range(len(search_candles)):
        idx = reset_end_idx + i
        if highs[idx] > highest_price:
            highest_price   = highs[idx]
            highest_idx_rel = i

    highest_idx = reset_end_idx + highest_idx_rel

    # ── Step 3: 找 C1 ──
    # 从最高点开始往后找：
    # - 连续3根符合 C 条件
    # - 最低点碰到或低于 MA50
    # - 回调不超过 37%（最高点到最低点 / 最高点 <= 37%）

    c1_result = None
    search_start = highest_idx

    if search_start >= n - 5:
        return None

    c1_candles = ohlcv[search_start:]

    c_end_idx = find_c_pattern(c1_candles)
    if c_end_idx is None:
        return None

    # C1 段的最低点
    c1_segment    = c1_candles[:c_end_idx + 1]
    c1_low        = min(c[3] for c in c1_segment)
    c1_low_idx    = search_start + min(range(len(c1_segment)), key=lambda i: c1_segment[i][3])
    c1_high       = highest_price
    c1_high_idx   = highest_idx

    # 检查 MA50 是否被碰到
    ma50_at_c1 = ma50_series[c1_low_idx]
    if not ma50_at_c1:
        return None
    touched_ma50 = c1_low <= ma50_at_c1

    # 检查回调不超过 37%
    if c1_high == 0:
        return None
    pullback_pct = (c1_high - c1_low) / c1_high
    if pullback_pct > 0.37:
        return None
    if not touched_ma50:
        return None

    c1_result = {
        "high":       c1_high,
        "high_idx":   c1_high_idx,
        "low":        c1_low,
        "low_idx":    c1_low_idx,
        "pullback":   pullback_pct,
    }

    # ── Step 4: 找 C2 ──
    # 从 C1 最低点之后开始找
    # - 连续3根符合 C 条件
    # - 底部 > C1 底部
    # - 底部 < C1 最高点
    # - Contraction：C2 高低点距离 < 10%

    c2_start_idx = c1_low_idx + 1
    if c2_start_idx >= n - 5:
        return None

    c2_candles = ohlcv[c2_start_idx:]
    c2_c_idx   = find_c_pattern(c2_candles)
    if c2_c_idx is None:
        return None

    c2_segment = c2_candles[:c2_c_idx + 1]
    c2_low     = min(c[3] for c in c2_segment)
    c2_high    = max(c[2] for c in c2_segment)

    # 底部条件
    if not (c1_result["low"] < c2_low < c1_result["high"]):
        return None

    # Contraction < 10%
    if c2_high == 0:
        return None
    contraction = (c2_high - c2_low) / c2_high
    if contraction >= 0.10:
        return None

    c2_result = {
        "high":        c2_high,
        "low":         c2_low,
        "contraction": contraction,
    }

    return {
        "tf":          tf_label,
        "c1":          c1_result,
        "c2":          c2_result,
        "reset_idx":   reset_end_idx,
    }

def check_symbol(symbol):
    try:
        # Step 1: Daily Stage 2 检查
        if not check_stage2_daily(symbol):
            return None

        # Step 2: 日均交易量检查
        ohlcv_1d = exchange.fetch_ohlcv(symbol, "1d", limit=60)
        daily_vols    = [c[4] * c[5] for c in ohlcv_1d]
        avg_daily_vol = get_sma(daily_vols, 50)
        if not avg_daily_vol or avg_daily_vol < MIN_DAILY_VOL:
            return None

        price = ohlcv_1d[-1][4]

        # Step 3: 在 15M / 30M / 1H 找 C1 C2
        results = []
        for tf, tf_label in [("15m", "15M"), ("30m", "30M"), ("1h", "1H")]:
            ohlcv = exchange.fetch_ohlcv(symbol, tf, limit=300)
            found = find_c1_c2(ohlcv, tf_label)
            if found:
                results.append(found)
            time.sleep(0.1)

        if not any(results):
            return None

        return {
            "symbol":        symbol,
            "price":         price,
            "avg_daily_vol": avg_daily_vol,
            "signals":       results,
        }

    except Exception as e:
        print(f"  Error {symbol}: {e}")
        return None

def screen_all():
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    print(f"\n🔍 Screening at {now}")

    try:
        markets = exchange.load_markets()
    except Exception as e:
        print(f"Failed to load markets: {e}")
        return

    symbols = [
        s for s, m in markets.items()
        if m.get('quote') == QUOTE_CURRENCY
        and m.get('swap', False)
        and m.get('linear', False)
        and m.get('active', False)
    ]

    print(f"   Scanning {len(symbols)} perpetual symbols...")
    results = []

    for symbol in symbols:
        print(f"  Checking {symbol}...")
        result = check_symbol(symbol)
        if result:
            results.append(result)
            print(f"  ✅ {symbol} C1+C2 found!")
        time.sleep(0.15)

    results.sort(key=lambda x: x['avg_daily_vol'], reverse=True)

    if results:
        chunks = [results[i:i+8] for i in range(0, len(results), 8)]
        for i, chunk in enumerate(chunks):
            msg = (
                f"<b>🎯 C1 + C2 Setup Found [{i+1}/{len(chunks)}]</b>\n"
                f"<i>⏰ {now}</i>\n"
                f"<i>✅ {len(results)} coins found</i>\n"
                f"{'─' * 28}\n"
            )
            for r in chunk:
                msg += (
                    f"\n<b>{r['symbol']}</b>  💰 {r['price']:.4f}\n"
                    f"  📈 Avg Vol: ${r['avg_daily_vol']:,.0f}\n"
                )
                for sig in r['signals']:
                    msg += (
                        f"  <b>{sig['tf']}</b>\n"
                        f"    C1: High {sig['c1']['high']:.4f} → "
                        f"Low {sig['c1']['low']:.4f} "
                        f"({sig['c1']['pullback']*100:.1f}% pullback) ✅\n"
                        f"    C2: High {sig['c2']['high']:.4f} → "
                        f"Low {sig['c2']['low']:.4f} "
                        f"({sig['c2']['contraction']*100:.1f}% contraction) ✅\n"
                    )
            send_telegram(msg)
    else:
        send_telegram(
            f"<b>🔍 C1+C2 Screen</b>\n"
            f"<i>⏰ {now}</i>\n\n"
            f"❌ No setups found"
        )

    print(f"✅ Done. {len(results)} setups found.")

# ============ 每 15 分钟运行 ============
screen_all()
schedule.every(15).minutes.do(screen_all)

print("⏳ Running every 15 minutes...")
while True:
    schedule.run_pending()
    time.sleep(60)