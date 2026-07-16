import ccxt
import requests
import schedule
import time
import os
import pytz
from datetime import datetime

# ============ 配置 ============
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID        = os.environ.get("CHAT_ID")
EXCHANGE_ID    = "bybit"
QUOTE_CURRENCY = "USDT"
MIN_DAILY_VOL  = 1_000_000

# ── TF 开关 ──
CHECK_15M = True
CHECK_30M = True
CHECK_1H  = True
CHECK_1D  = True
# ==============================

exchange = getattr(ccxt, EXCHANGE_ID)({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

def send_telegram(message):
    # url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # try:
    #     requests.post(url, json={
    #         "chat_id": CHAT_ID,
    #         "text": message,
    #         "parse_mode": "HTML"
    #     })
    # except Exception as e:
    #     print(f"Telegram error: {e}")
    print("[Telegram disabled] " + message[:80])

def get_sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period

def is_aligned(ohlcv):
    """
    检查单个 timeframe 内 MA50 > MA150 > MA200
    各自独立返回，不互相影响
    """
    closes = [c[4] for c in ohlcv]
    ma50   = get_sma(closes, 50)
    ma150  = get_sma(closes, 150)
    ma200  = get_sma(closes, 200)
    if ma50 and ma150 and ma200:
        aligned = ma50 > ma150 > ma200
    else:
        aligned = False
    return aligned, ma50, ma150, ma200

def get_projected_daily_vol(ohlcv_1h, price):
    """用最近 24 根 1H K线推算日交易额"""
    recent = ohlcv_1h[-24:] if len(ohlcv_1h) >= 24 else ohlcv_1h
    if not recent:
        return 0
    avg_hourly_vol = sum(c[5] for c in recent) / len(recent)
    return avg_hourly_vol * price * 24

def is_ma150_rising(ohlcv_1d):
    """
    检查 Daily MA150 是否趋向上
    用选项2：现在的 MA150 > 10 根前的 MA150
    忽略中间小波动，看整体方向
    """
    closes = [c[4] for c in ohlcv_1d]
    if len(closes) < 160:  # 150 + 10
        return False
    ma150_now  = get_sma(closes[-150:], 150)
    ma150_prev = get_sma(closes[-160:-10], 150)
    if not ma150_now or not ma150_prev:
        return False
    return ma150_now > ma150_prev

def check_symbol(symbol):
    try:
        # 用 500 根 Daily 判断真实上线天数
        ohlcv_1d_full = exchange.fetch_ohlcv(symbol, "1d", limit=500)
        actual_days   = len(ohlcv_1d_full)
        is_new_listing = actual_days < 200

        if actual_days < 1:
            return None

        price = ohlcv_1d_full[-1][4]

        # 先拿 1H 数据（之后 CHECK_1H 也会用到）
        ohlcv_1h = exchange.fetch_ohlcv(symbol, "1h", limit=210)
        time.sleep(0.1)

        # 用 1H 推算日交易额
        projected_vol = get_projected_daily_vol(ohlcv_1h, price)
        if projected_vol < MIN_DAILY_VOL:
            return None

        # ── 根据开关决定要检查哪些 TF ──
        tf_results = {}

        if CHECK_15M:
            ohlcv_15m = exchange.fetch_ohlcv(symbol, "15m", limit=210)
            aligned, ma50, ma150, ma200 = is_aligned(ohlcv_15m)
            tf_results["15m"] = {"aligned": aligned, "ma50": ma50, "ma150": ma150, "ma200": ma200}
            time.sleep(0.1)

        if CHECK_30M:
            ohlcv_30m = exchange.fetch_ohlcv(symbol, "30m", limit=210)
            aligned, ma50, ma150, ma200 = is_aligned(ohlcv_30m)
            tf_results["30m"] = {"aligned": aligned, "ma50": ma50, "ma150": ma150, "ma200": ma200}
            time.sleep(0.1)

        if CHECK_1H:
            # 1H 已经拿了，直接用
            aligned, ma50, ma150, ma200 = is_aligned(ohlcv_1h)
            tf_results["1h"] = {"aligned": aligned, "ma50": ma50, "ma150": ma150, "ma200": ma200}

        # 新图跳过 1D 检查
        if CHECK_1D and not is_new_listing:
            ohlcv_1d = exchange.fetch_ohlcv(symbol, "1d", limit=210)
            time.sleep(0.1)

            # Daily MA150 没趋向上，跳过
            if not is_ma150_rising(ohlcv_1d):
                return None

            aligned, ma50, ma150, ma200 = is_aligned(ohlcv_1d)
            tf_results["1d"] = {"aligned": aligned, "ma50": ma50, "ma150": ma150, "ma200": ma200}

        # 没有任何 TF align 就跳过
        if not any(v["aligned"] for v in tf_results.values()):
            return None

        return {
            "symbol":        symbol,
            "price":         price,
            "is_new":        is_new_listing,
            "actual_days":   actual_days,
            "projected_vol": projected_vol,
            "tf":            tf_results,
        }

    except Exception as e:
        print(f"  Error {symbol}: {e}")
        return None

def format_tf_block(tf_data):
    """每个 TF 独立显示状态"""
    msg = ""
    for tf, vals in tf_data.items():
        if vals["ma50"] is None:
            msg += f"  <b>{tf.upper()}</b>  ⚠️ Insufficient data\n"
            continue
        status = "✅ Aligned" if vals["aligned"] else "❌ Not aligned"
        msg += f"  <b>{tf.upper()}</b>  {status}\n"
    return msg

def build_and_send(group, label):
    chunks = [group[i:i+8] for i in range(0, len(group), 8)]
    for i, chunk in enumerate(chunks):
        msg = (
            f"<b>{label} [{i+1}/{len(chunks)}]</b>\n"
            f"<i>✅ {len(group)} coins found</i>\n"
            f"{'─' * 28}\n"
        )
        for r in chunk:
            msg += (
                f"\n<b>{r['symbol']}</b>  💰 {r['price']:.4f}\n"
                f"  📅 Actual days: {r['actual_days']}\n"
                f"  📈 Proj Vol: ${r['projected_vol']:,.0f}\n"
            )
            msg += format_tf_block(r['tf'])
        send_telegram(msg)

def screen_all():
    # 新加坡时间 01:00 - 06:59 不运行
    sg_tz  = pytz.timezone("Asia/Singapore")
    sg_now = datetime.now(sg_tz)
    if 1 <= sg_now.hour < 7:
        print(f"[SKIP] Skipping screen at SGT {sg_now.strftime('%H:%M')} (outside active hours)")
        return

    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    print(f"\n[SCREEN] Screening at {now}")

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
            tag = "[NEW]" if result['is_new'] else "[OK]"
            print(f"  {tag} {symbol} ALIGNED!")
        time.sleep(0.15)

    # 按 projected vol 排序
    results.sort(key=lambda x: x['projected_vol'], reverse=True)

    if results:
        old_listings = [r for r in results if not r['is_new']][:5]
        new_listings = [r for r in results if r['is_new']][:5]

        top_10 = old_listings + new_listings

        # 保存前 10 条到 JSON 供 plot 脚本使用
        import json
        with open('tradingMAScheduler/temp_results.json', 'w') as f:
            json.dump(top_10, f, indent=2, default=str)

        print(f"[OK] Found {len(top_10)} coins (Old: {len(old_listings)}, New: {len(new_listings)})")
        print(f"[FILE] Results saved to temp_results.json")

        # build_and_send 已注释，改用 plot 脚本生成图表
        # if old_listings:
        #     build_and_send(
        #         old_listings,
        #         "🚀 MA Stack Aligned — Old Listing"
        #     )

        # if new_listings:
        #     build_and_send(
        #         new_listings,
        #         "🆕 New Listing (&lt; 200 days)"
        #     )
    else:
        send_telegram(
            f"<b>📊 Perpetual MA Stack Screen</b>\n"
            f"<i>⏰ {now}</i>\n\n"
            f"❌ No coins matched all conditions"
        )
        print(f"[FAIL] No coins matched all conditions.")

    print(f"[DONE] Done. {len(results)} coins found (top 10 selected).")

# ============ 每 15 分钟运行 ============
screen_all()
schedule.every(15).minutes.do(screen_all)

print("⏳ Running every 15 minutes...")
while True:
    schedule.run_pending()
    time.sleep(60)