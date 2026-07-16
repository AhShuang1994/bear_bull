import ccxt
import time

# 测试 Bybit 连接
print("[TEST] Connecting to Bybit...")

exchange = ccxt.bybit({
    'enableRateLimit': True,
    'options': {'defaultType': 'swap'}
})

try:
    print("[TEST] Loading markets...")
    markets = exchange.load_markets()
    print(f"[OK] Loaded {len(markets)} markets")

    # 获取一个 USDT 本位的永续合约
    symbols = [s for s, m in markets.items()
               if m.get('quote') == 'USDT'
               and m.get('swap', False)
               and m.get('linear', False)
               and m.get('active', False)]

    print(f"[OK] Found {len(symbols)} USDT perpetual symbols")

    if symbols:
        test_symbol = symbols[0]
        print(f"\n[TEST] Testing with {test_symbol}...")

        # 拉取 1D 数据
        print("[TEST] Fetching 1D OHLCV...")
        ohlcv_1d = exchange.fetch_ohlcv(test_symbol, "1d", limit=10)
        print(f"[OK] Got {len(ohlcv_1d)} 1D candles")

        time.sleep(0.1)

        # 拉取 1H 数据
        print("[TEST] Fetching 1H OHLCV...")
        ohlcv_1h = exchange.fetch_ohlcv(test_symbol, "1h", limit=20)
        print(f"[OK] Got {len(ohlcv_1h)} 1H candles")

        print("\n[SUCCESS] All tests passed!")
    else:
        print("[FAIL] No symbols found")

except Exception as e:
    print(f"[ERROR] {e}")
