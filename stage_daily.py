# 日线 Stage 层 —— 单一权威实现，空头/多头两个扫描器共用（2026-07-18 用户口述定稿）
#
# Stage 定义（多头视角，空头=镜像）：
#   S1 蓄势：MA150 趋平 + 箱体横盘>=180天(两个相似高点或低点相隔>=180天) + 量能低迷(vol<VolMA50)
#   S2 主升：MA150 趋上（阳烛放量/破一年新高 = 加分项，不是门槛）
#   S3 派发：MA150 趋平 + 此前是 S2（跌穿MA150/阴烛放量是特征，v1 只用"30天前趋上"区分）
#   S4 主跌：MA150 趋下
#   循环：1 -> 2 -> 3 -> 4 -> 1
#
# 趋平阈值 ±0.3%/10天（2026-07-18 定稿）：全市场486币分布 P10=0.46%，±0.3% 圈住最平的6%；
# ±0.5% 实测会误杀 VANRY(当日1H引擎评STRONG)和BTC，±0.3% 只剔真·走平的XPL——宁窄勿宽，
# 因为漏进来的还有1H两道关兜底，被划平的图没有任何补救层。
#
# 一律只用已收盘日线（进场丢掉最后一根未收盘的）——跟引擎收盘K线纪律（NOTES §8.0）一致，
# 也顺手修掉旧日线关用未收盘蜡烛导致的边界抖动（TLM 案例：MA150在涨却进了空头图池）。

FLAT_BAND = 0.3     # ±0.3% / 10天 = 趋平
S3_LOOKBACK = 30    # 趋平且30天前还在趋上 => 刚从S2下来 => S3；否则 S1
MAX_WALK = 400      # days_in_stage 最多回看400天（个币400根日线下实际上限~239，BTC拉1000根可走满）

STAGE_NAMES = {0: 'S?', 1: 'S1', 2: 'S2', 3: 'S3', 4: 'S4'}


def _direction(slope):
    """斜率三态：'up' / 'down' / 'flat'（slope=None -> None）"""
    if slope is None:
        return None
    if slope > FLAT_BAND:
        return 'up'
    if slope < -FLAT_BAND:
        return 'down'
    return 'flat'


def classify(ohlcv_1d):
    """输入原始日线（含未收盘的最后一根），返回：
    {stage: 0|1|2|3|4, slope: float|None, days: int|None, capped: bool}
    stage=0 = 已收盘日线不足161根（新图），days = 进入当前方向状态的天数（第days天）。
    S1/S3 的箱体/量能细节不在这里判——见 find_s1_box / vol_subdued。"""
    closes = [c[4] for c in ohlcv_1d[:-1]]
    n = len(closes)
    if n < 161:
        return {'stage': 0, 'slope': None, 'days': None, 'capped': False}

    pref = [0.0]
    for v in closes:
        pref.append(pref[-1] + v)

    def ma150_at(end):  # end = closes 的排他右边界
        return (pref[end] - pref[end - 150]) / 150

    def slope_at(k):    # k=0 是最新已收盘那天
        end = n - k
        if end - 160 < 0:
            return None
        now, prev = ma150_at(end), ma150_at(end - 10)
        return (now / prev - 1) * 100

    s0 = slope_at(0)
    d0 = _direction(s0)
    if d0 == 'up':
        stage = 2
    elif d0 == 'down':
        stage = 4
    else:
        # 趋平：刚从趋上下来的是 S3（顶部派发），其余算 S1（蓄势）
        stage = 3 if _direction(slope_at(S3_LOOKBACK)) == 'up' else 1

    days, capped = 1, True
    limit = min(MAX_WALK, n - 160)
    for k in range(1, limit + 1):
        if _direction(slope_at(k)) != d0:
            days, capped = k, False
            break

    return {'stage': stage, 'slope': s0, 'days': days if not capped else limit,
            'capped': capped}


def vs_btc_text(st, btc_st):
    """④a：'S4第14天·比BTC早12天' / '...晚3天' / '...同天' / '...BTC在S2'；无stage返回''"""
    if not st or st['stage'] in (0, None):
        return ""
    days = f">{st['days']}" if st['capped'] else f"{st['days']}"
    head = f"{STAGE_NAMES[st['stage']]}第{days}天"
    if not btc_st or btc_st['stage'] == 0:
        return head
    if btc_st['stage'] != st['stage']:
        return f"{head}·BTC在{STAGE_NAMES[btc_st['stage']]}"
    diff = st['days'] - btc_st['days']
    if st['capped'] and btc_st['capped']:
        return head                      # 两边都超出可测范围，先后无法判定
    if btc_st['capped']:                 # BTC 进入得比可测范围还早
        return f"{head}·比BTC晚≥{-diff}天" if diff < 0 else head
    if st['capped']:                     # 本币进入得比可测范围还早
        return f"{head}·比BTC早≥{diff}天" if diff > 0 else head
    if diff > 0:
        return f"{head}·比BTC早{diff}天"
    if diff < 0:
        return f"{head}·比BTC晚{-diff}天"
    return f"{head}·与BTC同天"


def find_s1_box(ohlcv_1d, tol=0.03, min_days=60, look=365, max_width=1.0):
    """S1箱体（用户定义：两个相似的高点或低点相隔>=min_days天，期间价格横盘）。
    min_days 分尺度（2026-07-18 用户定）：加密货币 60 天，股票/商品类 180 天（横盘半年
    是股票的尺度，加密节奏快）——调用方按组别传。
    实现（v3=B宽松版，2026-07-18 晚用户从 A/B/B50 三个实测名单里拍板）：
    从最新一根往回找【最长横盘尾窗】（窗内 最高/最低-1 <= max_width，默认100%），
    窗长 >= min_days 即为箱体——"横盘+看不出方向"的本质就是长期被关在区间里。
    迭代记录（都是预览时抓的坑）：v1 锚定全年极值，崩盘图的低点重测冒充箱体（AR案例
    8.9->1.25）、真箱体边界够不着年高就漏检；v2 加"边界±tol触碰首末相隔>=min_days"
    （="两个相似高点"的字面直译）——实测漂移型横盘全被排除（24个S1只剩KGEN），
    用户看名单后选了 containment-only 的 B 版；A(严格边界重测)/B50(振幅±50%)被否。
    tol 参数保留但 v3 不再使用；max_width 是下一个校准旋钮（收紧到0.5=B50）。
    返回 (box_high, box_low, window_days) 或 None。"""
    closed = ohlcv_1d[:-1][-look:]
    n = len(closed)
    if n < min_days:
        return None
    highs = [c[2] for c in closed]
    lows = [c[3] for c in closed]

    # 最长横盘尾窗：从最新往回扩，振幅一超 max_width 就停
    run_max, run_min = 0.0, float('inf')
    window = 0
    for k in range(1, n + 1):
        run_max = max(run_max, highs[n - k])
        run_min = min(run_min, lows[n - k])
        if run_min <= 0 or run_max / run_min - 1 > max_width:
            break
        window = k
    if window < min_days:
        return None
    return max(highs[n - window:]), min(lows[n - window:]), window


def vol_break_counts(ohlcv, start_idx):
    """1H结构量能（NOTES 4.10，2026-07-19 用户VOL教学定稿）：start_idx~最后一根已收盘，
    破当根 VolMA50（含当根，TV口径）的阳柱/阴柱根数；十字星不计。
    这是 O'Neil U/D Volume Ratio 的"根数版"变体（数根数而非加总量，抗单根天量干扰）。
    扫描器用它打 🔊VOL+/⚠️VOL- 标：顺方向 >= 逆方向×1.5 且 >=5根（对齐 IBD
    U/D>=1.5 的强吸筹惯例）；空头顺方向=阴柱，多头=阳柱。
    返回 (up_count, dn_count)。"""
    closed = ohlcv[:-1]
    vols = [c[5] for c in closed]
    pref = [0.0]
    for v in vols:
        pref.append(pref[-1] + v)
    up = dn = 0
    for i in range(max(start_idx, 49), len(closed)):
        ma50 = (pref[i + 1] - pref[i - 49]) / 50
        if closed[i][5] > ma50:
            if closed[i][4] > closed[i][1]:
                up += 1
            elif closed[i][4] < closed[i][1]:
                dn += 1
    return up, dn


def vol_subdued(ohlcv_1d, window=30, ratio=0.6):
    """量能低迷（S1特征）：最近 window 根已收盘日线里，>=ratio 比例的 vol < 当根VolMA50"""
    vols = [c[5] for c in ohlcv_1d[:-1]]
    if len(vols) < 50 + window:
        return False
    pref = [0.0]
    for v in vols:
        pref.append(pref[-1] + v)
    cnt = 0
    n = len(vols)
    for k in range(window):
        end = n - k
        ma50 = (pref[end] - pref[end - 50]) / 50
        if vols[end - 1] < ma50:
            cnt += 1
    return cnt >= ratio * window
