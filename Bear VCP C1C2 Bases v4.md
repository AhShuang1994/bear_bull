//@version=6
// ═══════════════════════════════════════════════════════════════════════════════
// Bear VCP C1C2 Bases v4 — 统一确认机制 + 段起点 boxEnd+1
//
// v4 相对 v3 两处结构性改动(与 c1c2_v4.py 同源, 2026-07-21 用户拍板)：
//   1. 统一：C2~C6 不再用 findCPattern+段内取极值,改成和 C1 同一个 findC——
//      从段起点逐根跟踪极值(锚点),从锚点数streak(锚点当第一根),连续3根确认.
//      C1 额外保留 MA50 关(requireMa50=true); C2~C6 不检查 MA50.
//   2. 段起点 = 上一个C的 boxEnd+1(统一到所有C).
//   由此: 主动创新低/高、同根C例外都不再需要(被机制吸收); SIREN 假C7 被挡.
//   ⚠️ 已知代价(用户接受): 锚点到确认跨度大时单个C的框可能膨胀.
//   Python 侧 c1c2_v4.py 全锚点回归: SIREN 6C/BANK C4/SLX C3 均符合预期.
//   TradingView 上需与 c1c2_v4.py 对照 SIREN/BANK 验收(§8.10 纪律).
//
// v3 = v2（3连第一根可以是锚点当根）+ 去掉 §4.3.3b「候选低点必须 low<前一根low」。
//   该规则 2026-07-16 为杀 SIREN 假C7 而加；2026-07-21 用户指令整条拿掉。
//   ⚠️ 代价（实测）：SIREN Base2 假C7 回归(6C→7C,标弱)、SLX Base1 假C3 回归。
//   收益：BANK Base2 恢复为 C4（46.7→26.2→19.9→8.6，段起点不再被C1反转柱插针挡掉）。
// —— 以下为 v2 原注释 ——
// v2 改动：findC1 的新低分支  streak := 0  →  streak := isCCandle(i) ? 1 : 0
//
// v2 唯一改动：findC1 的新低分支  streak := 0  →  streak := isCCandle(i) ? 1 : 0
//   低点那根若自己就是合格C蜡烛(阳线或长影线)，它就是反弹的第一根、计入3连；
//   不合格则仍从 +1 起数。判定统一交给 isCCandle，锚点不再享有豁免。
//   C1 的 MA50 关(低点收盘在MA50下方 + 反弹段触碰MA50)原样保留；C2~C6 一个字未动。
//   依据：C2~C6 早已允许极值点当3连第一根(NOTES §6.6 第2条，SIREN 1928 长下影反转
//   蜡烛紧接两根延续)。C1 原本是全套规则里唯一的特例，本版把它拉平。
//   2026-07-20 用户口径，与 c1c2_v2.py 同源。实测 11币×2方向：21 个结构完全一致，
//   唯一差异 = SLX Base1 的 C1 框尾 885→884。
//
// 基线：Bear VCP C1C2 Bases — plot_c1c2_bear_doge.py 的 find_bases() Pine 直译版
//
// 规格书: BEAR_C1C2_NOTES.md §4(规则) §6(踩坑) §7(锚点案例) §8.10(移植路线)
// 架构  : 已收盘K线逐根存入数组(=Python 的 ohlcv[:-1]，§8.0 已收盘纪律 =
//         barstate.isconfirmed)，每根收盘后在最后一根 bar 上全量重跑一遍
//         批处理算法并重画所有对象(2026-07-19 用户拍板，取代"逐bar状态机"设想)。
//         配合 TV Bar Replay 可复现任意历史时点(等价 Python 的 TRUNCATE_END)。
// 验收  : BYBIT:SIRENUSDT.P 1H 必须复现 Base2 六个C/同根C2C6/无C7 (§7)；
//         对照工具 tv_verify_summary.py。
// 引擎同步基准: 2026-07-19 (repo bear_bull @ main)
// ═══════════════════════════════════════════════════════════════════════════════
indicator("Bear VCP C1C2 Bases v4", "BEAR C1C2 v4", overlay = true, max_boxes_count = 500, max_lines_count = 100, max_labels_count = 500)

// ── inputs ─────────────────────────────────────────────────────────────────────
lookbackN  = input.int(2000, "最多回看已收盘K线数 (0=不限)", minval = 0, tooltip = "默认 2000 对齐 Python 引擎窗口(LIMIT=2000)；0 = 用尽图表全部历史。窗口只影响最早期老结构，近端结构不受影响")
showMA     = input.bool(true, "显示 MA50/150/200")
showClosed = input.bool(true, "显示已封口 Base(金框+实线C框)")

// ── MA 线(图表层，含实时bar；结构检测用下面数组里的已收盘值) ──────────────────
ma50s  = ta.sma(close, 50)
ma150s = ta.sma(close, 150)
ma200s = ta.sma(close, 200)
plot(showMA ? ma50s : na, "MA50", color = #808080, linewidth = 2)
plot(showMA ? ma150s : na, "MA150", color = #008B8B, linewidth = 2)
plot(showMA ? ma200s : na, "MA200", color = #FF0000, linewidth = 2)

// ── 已收盘K线数组(§8.0 已收盘纪律) ────────────────────────────────────────────
var aO   = array.new<float>()
var aH   = array.new<float>()
var aL   = array.new<float>()
var aC   = array.new<float>()
var a50  = array.new<float>()
var a150 = array.new<float>()
var a200 = array.new<float>()
var aBI  = array.new<int>()   // 每根已收盘bar的真实 bar_index，画图坐标用

if barstate.isconfirmed
    array.push(aO, open)
    array.push(aH, high)
    array.push(aL, low)
    array.push(aC, close)
    array.push(a50, ma50s)
    array.push(a150, ma150s)
    array.push(a200, ma200s)
    array.push(aBI, bar_index)

// 窗口偏移：lookbackN>0 时只用最新 N 根已收盘K线(对齐 Python 2000 窗口)。
// 不做数组 shift(历史加载阶段逐根 shift 是 O(n^2)会超时)，用偏移量访问。
var int OFF = 0

// ── 访问器(所有算法索引都在窗口坐标 0..n-1 里，经 OFF 映射到数组) ────────────
cO(int i) => array.get(aO, OFF + i)
cH(int i) => array.get(aH, OFF + i)
cL(int i) => array.get(aL, OFF + i)
cC(int i) => array.get(aC, OFF + i)
m50(int i) => array.get(a50, OFF + i)
m150(int i) => array.get(a150, OFF + i)
m200(int i) => array.get(a200, OFF + i)
bIdx(int i) => array.get(aBI, OFF + i)

// ── 结构类型 ───────────────────────────────────────────────────────────────────
type Leg
    float lowP
    int   lowIdx
    float highP
    int   highIdx
    float pct       // C1 存反弹%(bounce)，C2+ 存收缩%(contraction)
    int   boxEnd    // 框右边界 = max(确认完成点, 高点)
    int   confirmAt // v4: 3连确认完成的位置(突破比较用)

type CBase
    array<Leg> legs
    int  breakoutIdx
    bool weak       // §4.5 超过6个C：Base成立但强度不强

// ── §6.4 长影线定义(最终版，不要改)：阳线，或 wick>body(十字星无条件算) ──────
isCCandle(int i) =>
    o = cO(i)
    h = cH(i)
    l = cL(i)
    c = cC(i)
    body = math.abs(c - o)
    wick = (h - l) - body
    isGreen = c > o
    isLongWick = body > 0 ? wick > body : true
    isGreen or isLongWick

// ── §4.3.2 连续3根触发：从 fromIdx 起第一处 i 使 i-2,i-1,i 全合格；无则 -1 ────
findCPattern(int fromIdx, int n) =>
    int res = -1
    if fromIdx + 2 <= n - 1
        for i = fromIdx + 2 to n - 1
            if isCCandle(i) and isCCandle(i - 1) and isCCandle(i - 2)
                res := i
                break
    res

// ── §4.2.4 反弹区域高点：截止于"跌破lowPrice的新低"或"MA150上穿MA200" ────────
// (翻转只看慢速对，50×150穿越不是结构事件——SIREN 07-06 误杀案例)
// 高点候选从低点【当根】算起(同根C合法，§4.3.4)。不成立时返回 highIdx=-1。
findLegHigh(int lowIdx, float lowPrice, int n) =>
    float rHigh   = na
    int   rHighIdx = -1
    int   regionEnd = n - 1
    if lowIdx < n - 1
        for i = lowIdx + 1 to n - 1
            if cL(i) < lowPrice
                regionEnd := i - 1
                break
            mA = m150(i)
            mB = m200(i)
            if not na(mA) and not na(mB) and not (mB > mA)
                regionEnd := i - 1
                break
        if regionEnd > lowIdx
            for j = lowIdx to regionEnd
                hj = cH(j)
                if na(rHigh) or hj > rHigh   // 严格大于：并列时取第一个(对齐 Python max)
                    rHigh := hj
                    rHighIdx := j
    [rHigh, rHighIdx, regionEnd]

// ── §4.2 C1 四条件：新低跟踪 + 3连确认 + 触碰MA50(须发生在自己反弹段内、
//    低点收盘在MA50下方) + find_leg_high 高点。不成立返回 na ──────────────────
findC(int start, int n, bool requireMa50) =>
    // v4 统一找C：从 start 逐根跟踪最低点(锚点)，从锚点数streak(锚点当第一根)，连续3根确认。
    // requireMa50=true 时额外要求 C1 的 MA50 关(低点收盘在MA50下方 + 反弹段触碰MA50)。
    Leg  res = na
    int  lowIdx = -1
    bool lowUnder50 = false
    int  failedLow = -1
    int  streak = 0
    bool confirmed = false
    for i = start to n - 1
        if lowIdx == -1 or cL(i) < cL(lowIdx)
            lowIdx := i
            mAt = m50(i)
            lowUnder50 := not na(mAt) and cC(i) < mAt
            streak := isCCandle(i) ? 1 : 0
            confirmed := false
            continue
        if isCCandle(i)
            streak += 1
            if streak >= 3
                confirmed := true
        else
            streak := 0
        bool ready = confirmed and (requireMa50 ? lowUnder50 : true)
        if not ready or lowIdx == failedLow
            continue
        [legHigh, legHighIdx, regionEnd] = findLegHigh(lowIdx, cL(lowIdx), n)
        if legHighIdx == -1
            failedLow := lowIdx
            continue
        if i > regionEnd
            failedLow := lowIdx
            continue
        if requireMa50
            bool touched = false   // C1专属：触碰必须在低点~高点之间(HIGH案例)
            for j = lowIdx to legHighIdx
                mj = m50(j)
                if not na(mj) and cH(j) >= mj
                    touched := true
                    break
            if not touched
                failedLow := lowIdx
                continue
        res := Leg.new(cL(lowIdx), lowIdx, legHigh, legHighIdx, (legHigh - cL(lowIdx)) / cL(lowIdx), math.max(legHighIdx, i), i)
        break
    res

// ── 画图对象池(每次重算全删重画) ──────────────────────────────────────────────
var gBoxes  = array.new<box>()
var gLines  = array.new<line>()
var gLabels = array.new<label>()
var cColors = array.from(#1f77b4, #9467bd, #ff7f0e, #17becf, #8c564b)

clearDrawings() =>
    while array.size(gBoxes) > 0
        box.delete(array.pop(gBoxes))
    while array.size(gLines) > 0
        line.delete(array.pop(gLines))
    while array.size(gLabels) > 0
        label.delete(array.pop(gLabels))

// §4.6 C段框：左=低点，右=max(确认完成点,高点)，但不与下一个C重叠(纯画法裁剪)
drawLegs(array<Leg> legs, bool dashed) =>
    for k = 0 to array.size(legs) - 1
        Leg leg = array.get(legs, k)
        color col = array.get(cColors, k % 5)
        int rightAbs = math.max(leg.highIdx, leg.boxEnd)
        if k + 1 < array.size(legs)
            rightAbs := math.min(rightAbs, array.get(legs, k + 1).lowIdx - 1)
        rightAbs := math.max(rightAbs, leg.highIdx)
        b = box.new(bIdx(leg.lowIdx), leg.highP, bIdx(rightAbs), leg.lowP, border_color = col, border_width = 2, border_style = dashed ? line.style_dashed : line.style_solid, bgcolor = color.new(col, dashed ? 80 : 62))
        array.push(gBoxes, b)
        lb = label.new(math.floor((bIdx(leg.lowIdx) + bIdx(rightAbs)) / 2), leg.highP, "C" + str.tostring(k + 1) + " +" + str.tostring(leg.pct * 100, "0.0") + "%", style = label.style_label_down, color = color.new(color.white, 100), textcolor = col, size = size.large)
        array.push(gLabels, lb)

// ── 主流程：每根收盘后全量重算(historical 阶段在最后一根已确认bar上跑一次) ────
var int   lastN = -1
var float alertC1Low = na   // 活结构 C1 低点(突破警报用)，无活结构 = na
var table infoT = table.new(position.top_right, 1, 1)

if (barstate.islast or barstate.islastconfirmedhistory) and array.size(aC) != lastN
    lastN := array.size(aC)
    OFF := lookbackN > 0 ? math.max(0, array.size(aC) - lookbackN) : 0
    int n = array.size(aC) - OFF
    clearDrawings()
    alertC1Low := na
    string status = ""

    if n < 210
        status := "K线不足210根"
    else
        // ── Reset：最近一次"MA150金叉MA200后第一次重回空头排列"(§4.1) ────────
        int  resetIdx = -1
        int  lastCross = -1
        bool crossUsed = false
        for i = 200 to n - 1
            m150p = m150(i - 1)
            m200p = m200(i - 1)
            m150i = m150(i)
            m200i = m200(i)
            if not na(m150p) and not na(m200p) and not na(m150i) and not na(m200i) and m150p <= m200p and m150i > m200i
                lastCross := i
                crossUsed := false
            m50i = m50(i)
            if na(m50i) or na(m150i) or na(m200i)
                continue
            if lastCross != -1 and not crossUsed and m200i > m150i and m150i > m50i
                resetIdx := i   // 每次新金叉后的第一个空头排列覆盖旧 reset
                crossUsed := true

        if resetIdx == -1
            status := "无 Reset(近端无金叉→空头排列序列)"
        else
            // ── 滚动 Base 检测主循环(find_bases 直译) ────────────────────────
            array<CBase> basesArr = array.new<CBase>()
            array<Leg>   openChain = na
            int searchStart = resetIdx
            while searchStart < n - 5
                Leg c1 = findC(searchStart, n, true)
                if na(c1)
                    break
                array<Leg> chain = array.new<Leg>()
                array.push(chain, c1)
                float c1Low = c1.lowP
                int  sealedIdx = -1
                bool isOpen = false
                int  guard = 0
                while true
                    guard += 1
                    if guard > n
                        isOpen := true
                        break
                    Leg ref = array.get(chain, array.size(chain) - 1)
                    int segStart = ref.boxEnd + 1   // v4: 段起点统一 = 上一个C的框尾+1
                    if segStart >= n
                        isOpen := true
                        break
                    // 突破检查：segStart 起第一根 low 跌破 C1 低点
                    int breakoutIdx = -1
                    for i = segStart to n - 1
                        if cL(i) < c1Low
                            breakoutIdx := i
                            break
                    // v4 统一：下一个C也用 findC(无MA50)，从锚点数streak
                    Leg cand = findC(segStart, n, false)
                    int confirmAt = na(cand) ? -1 : cand.confirmAt
                    if breakoutIdx != -1 and (na(cand) or breakoutIdx <= confirmAt)
                        sealedIdx := breakoutIdx
                        break
                    if na(cand)
                        isOpen := true
                        break
                    // §4.4 取代：破参考高点 或 %没收缩 → 原地延伸参考段，不新增
                    if cand.highP >= ref.highP or cand.pct >= ref.pct
                        ref.highP := cand.highP
                        ref.highIdx := cand.highIdx
                        if array.size(chain) == 1
                            ref.pct := (cand.highP - ref.lowP) / ref.lowP
                            ref.boxEnd := math.max(ref.boxEnd, cand.boxEnd)
                        else
                            ref.lowP := cand.lowP
                            ref.lowIdx := cand.lowIdx
                            ref.pct := cand.pct
                            ref.boxEnd := cand.boxEnd
                            while array.size(chain) >= 3 and array.get(chain, array.size(chain) - 1).pct >= array.get(chain, array.size(chain) - 2).pct
                                array.remove(chain, array.size(chain) - 2)
                        continue
                    array.push(chain, cand)
                if isOpen
                    openChain := chain
                    break
                if array.size(chain) >= 2
                    array.push(basesArr, CBase.new(chain, sealedIdx, array.size(chain) > 6))
                // 只有C1、连C2都没有就被跌破的不算Base，直接从突破点继续
                searchStart := sealedIdx

            // ── 画图(§4.6) ───────────────────────────────────────────────────
            // Reset 竖线(橙色点线)
            ln = line.new(bIdx(resetIdx), cL(resetIdx), bIdx(resetIdx), cH(resetIdx), extend = extend.both, color = color.new(color.orange, 20), style = line.style_dotted, width = 1)
            array.push(gLines, ln)
            lbR = label.new(bIdx(resetIdx), cH(resetIdx), "Reset", yloc = yloc.abovebar, style = label.style_none, textcolor = color.orange, size = size.small)
            array.push(gLabels, lbR)

            // 已封口 Base：金色大框 + 实线C框
            if showClosed and array.size(basesArr) > 0
                for bi = 0 to array.size(basesArr) - 1
                    CBase bb = array.get(basesArr, bi)
                    Leg c1b = array.get(bb.legs, 0)
                    bx = box.new(bIdx(c1b.lowIdx), c1b.highP, bIdx(bb.breakoutIdx), c1b.lowP, border_color = #A93226, border_width = 3, bgcolor = color.new(#E24B4A, 84))
                    array.push(gBoxes, bx)
                    lbB = label.new(bIdx(c1b.lowIdx), c1b.lowP, "Base " + str.tostring(bi + 1) + (bb.weak ? " (weak)" : ""), style = label.style_label_up, color = color.new(color.white, 100), textcolor = #A93226, size = size.large)
                    array.push(gLabels, lbB)
                    drawLegs(bb.legs, false)

            // 活结构：虚线C框 + C1低点金色虚线触发线 + FLIP线
            if not na(openChain)
                drawLegs(openChain, true)
                Leg c1o = array.get(openChain, 0)
                trigLn = line.new(bIdx(c1o.lowIdx), c1o.lowP, bIdx(n - 1), c1o.lowP, extend = extend.right, color = #B8860B, style = line.style_dashed, width = 2)
                array.push(gLines, trigLn)
                trigLb = label.new(bar_index + 2, c1o.lowP, "C1 low " + str.tostring(c1o.lowP, format.mintick), style = label.style_label_left, color = color.new(color.white, 100), textcolor = #B8860B, size = size.normal)
                array.push(gLabels, trigLb)
                alertC1Low := c1o.lowP
                // §4.6 FLIP：C1低点之后慢速对第一次翻多(MA150上穿MA200) = 活结构失效点
                int flipIdx = -1
                for i = c1o.lowIdx + 1 to n - 1
                    fA = m150(i)
                    fB = m200(i)
                    if not na(fA) and not na(fB) and not (fB > fA)
                        flipIdx := i
                        break
                if flipIdx != -1
                    flLn = line.new(bIdx(flipIdx), cL(flipIdx), bIdx(flipIdx), cH(flipIdx), extend = extend.both, color = #FF00FF, style = line.style_solid, width = 3)
                    array.push(gLines, flLn)
                    flLb = label.new(bIdx(flipIdx), cH(flipIdx), "FLIP", yloc = yloc.abovebar, style = label.style_none, textcolor = #FF00FF, size = size.large)
                    array.push(gLabels, flLb)

            int nB = array.size(basesArr)
            status := str.tostring(nB) + " Base" + (nB != 1 ? "s" : "")
            if not na(openChain)
                status := status + " + OPEN C" + str.tostring(array.size(openChain))

    table.cell(infoT, 0, 0, "Bear VCP v4 — " + status + " | 窗口" + str.tostring(n) + "根已收盘", text_color = #B8860B, text_size = size.small, bgcolor = color.new(color.gray, 90))

// ── 突破警报：活结构 C1 低点被跌破的瞬间(盘中即触发，§4.5"瞬间就算突破") ─────
alertcondition(not na(alertC1Low) and low < alertC1Low, title = "跌破C1低点 (Base突破)", message = "Bear VCP: 价格跌破活结构 C1 低点 — 突破/封口触发")
