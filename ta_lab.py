"""기술적 분석 보조 — 신호 성적표(과거 검증), 시장 환경, 신호 기록장.

신호 성적표: 종목마다 일봉 400일 중 최근 약 120거래일 동안 그날 '새로' 나온 신호를 모두 찾아,
           그다음 5·10·20거래일 뒤 수익률과 (패턴이면) 목표·손절 중 무엇에 먼저 닿았는지 센다.
           그날 코스피가 200일선 위면 '상승장', 아래면 '하락장'으로 나눠서도 본다.
신호 기록장: 매일의 종합 판단(롱·숏)을 저장해 두고, 5·20거래일 뒤 실제 결과를 채운다.
"""
from datetime import date

import us

BT_SIGS = {
    "gc": ("골든크로스 (20·60일선)", "bull"), "gc_long": ("장기 골든크로스 (60·120일선)", "bull"),
    "ma200_up": ("200일선 돌파", "bull"), "ma200_dn": ("200일선 이탈", "bear"),
    "w50_up": ("50주선 돌파", "bull"), "w50_dn": ("50주선 이탈", "bear"),
    "align_up": ("정배열 전환", "bull"), "align_dn": ("역배열 전환", "bear"),
    "hi52": ("52주 신고가 (첫날)", "bull"), "base_bo": ("베이스 돌파", "bull"),
    "vol_up": ("거래량 급증 + 양봉", "bull"), "vol_dn": ("거래량 급증 + 음봉", "bear"),
    "rsi_lo": ("RSI 30 아래로 (과매도)", "bull"), "rsi_hi": ("RSI 70 위로 (과매수)", "bear"),
}
H = 20            # 성적을 보는 기간 (거래일)
START = 260       # 52주·50주선 계산에 필요한 앞부분


def rsi_series(c, n=14):
    out = [None] * len(c)
    g = lo = 0.0
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        if i <= n:
            g += max(d, 0)
            lo += max(-d, 0)
            if i == n:
                g, lo = g / n, lo / n
                out[i] = 100.0 if lo == 0 else 100 - 100 / (1 + g / lo)
        else:
            g = (g * (n - 1) + max(d, 0)) / n
            lo = (lo * (n - 1) + max(-d, 0)) / n
            out[i] = 100.0 if lo == 0 else 100 - 100 / (1 + g / lo)
    return out


def regime_map(bars):
    """코스피 일봉 → {날짜: 'up'(200일선 위) / 'down'(아래)}"""
    c = [b[4] for b in bars]
    m = us.sma(c, 200)
    return {b[0]: ("up" if c[i] > m[i] else "down") for i, b in enumerate(bars) if m[i]}


def backtest(bars, regime):
    """한 종목 → (신호 사건 목록, 아무 날이나 샀을 때의 기준 성적)"""
    from kr_ta import week_ma
    o, h, l, c, v = ([b[k] for b in bars] for k in range(1, 6))
    n = len(c)
    base = {"up": [0, 0.0, 0], "down": [0, 0.0, 0]}            # [표본 수, 20일 수익 합, 오른 수]
    if n < START + H + 1:
        return [], base
    m5, m20, m60, m120, m200 = (us.sma(c, k) for k in (5, 20, 60, 120, 200))
    w50 = week_ma(c, [b[0] for b in bars])
    rs = rsi_series(c)

    def cross(a, b, t):
        return None not in (a[t], b[t], a[t - 1], b[t - 1]) and a[t - 1] <= b[t - 1] and a[t] > b[t]

    def aligned(t, up):
        vals = (m5[t], m20[t], m60[t], m120[t])
        if None in vals:
            return False
        return vals[0] > vals[1] > vals[2] > vals[3] if up else vals[0] < vals[1] < vals[2] < vals[3]
    ev = []
    for t in range(START, n - H):
        g = regime.get(bars[t][0])
        r = (c[t + 5] / c[t] - 1, c[t + 10] / c[t] - 1, c[t + 20] / c[t] - 1)
        if g:
            base[g][0] += 1
            base[g][1] += r[2]
            base[g][2] += r[2] > 0
        fired = []
        if cross(m20, m60, t):
            fired.append("gc")
        if cross(m60, m120, t):
            fired.append("gc_long")
        if cross(c, m200, t):
            fired.append("ma200_up")
        if cross(m200, c, t):
            fired.append("ma200_dn")
        if cross(c, w50, t):
            fired.append("w50_up")
        if cross(w50, c, t):
            fired.append("w50_dn")
        if aligned(t, True) and not aligned(t - 1, True):
            fired.append("align_up")
        if aligned(t, False) and not aligned(t - 1, False):
            fired.append("align_dn")
        if c[t] > max(h[t - 250:t]) and c[t - 1] <= max(h[t - 251:t - 1]):
            fired.append("hi52")
        av = sum(v[t - 20:t]) / 20
        if av and v[t] >= 3 * av:
            fired.append("vol_up" if c[t] >= o[t] else "vol_dn")
        if rs[t] is not None and rs[t - 1] is not None:
            if rs[t] <= 30 < rs[t - 1]:
                fired.append("rsi_lo")
            if rs[t] >= 70 > rs[t - 1]:
                fired.append("rsi_hi")
        top, bot = max(h[t - 40:t]), min(l[t - 40:t])
        if (top - bot) / top <= 0.15 and min(l[t - 160:t - 40]) * 1.3 <= top and c[t] > top * 1.005 and c[t - 1] <= top:
            fired.append("base_bo")
        for f in fired:
            ev.append([f, g, *r, None])
    # 차트 패턴: 돌파 확정이 '새로' 나온 날 (같은 패턴은 15일 안에 한 번만)
    seen = {}
    for t in range(START, n - H):
        for p in us.detect(o[:t + 1], h[:t + 1], l[:t + 1], c[:t + 1], v[:t + 1]):
            if p["st"] not in ("confirmed", "retest") or p.get("bi") is None or t - p["bi"] > 2 or t - seen.get(p["k"], -99) < 15:
                continue
            seen[p["k"]] = t
            out = "open"
            for k in range(1, min(40, n - 1 - t) + 1):
                if p["bias"] == "bull":
                    if l[t + k] <= p["stop"]:
                        out = "stop"
                        break
                    if h[t + k] >= p["target"]:
                        out = "hit"
                        break
                else:
                    if h[t + k] >= p["stop"]:
                        out = "stop"
                        break
                    if l[t + k] <= p["target"]:
                        out = "hit"
                        break
            ev.append(["pat:" + p["k"] + ":" + p["bias"] + ":" + p["name"], regime.get(bars[t][0]),
                       c[t + 5] / c[t] - 1, c[t + 10] / c[t] - 1, c[t + 20] / c[t] - 1, out])
    return ev, base


def aggregate(events, bases):
    """사건 → 신호별·시장별 성적표"""
    base = {"up": [0, 0.0, 0], "down": [0, 0.0, 0]}
    for b in bases:
        for g in base:
            for i in range(3):
                base[g][i] += b[g][i]
    allb = [base["up"][i] + base["down"][i] for i in range(3)]
    baseline = {g: {"n": x[0], "avg20": x[1] / x[0] if x[0] else None, "up20": x[2] / x[0] if x[0] else None}
                for g, x in (("all", allb), ("up", base["up"]), ("down", base["down"]))}
    groups = {}
    for e in events:
        groups.setdefault(e[0], []).append(e)
    rows = []
    for key, es in groups.items():
        if key.startswith("pat:"):
            _, k, bias, name = key.split(":", 3)
            kind = "pattern"
        else:
            name, bias = BT_SIGS[key]
            k, kind = key, "signal"
        row = {"k": k, "name": name, "bias": bias, "kind": kind}
        for g in ("all", "up", "down"):
            sel = [e for e in es if g == "all" or e[1] == g]
            if not sel:
                row[g] = {"n": 0}
                continue
            m = len(sel)
            win = sum(1 for e in sel if (e[4] > 0 if bias == "bull" else e[4] < 0)) / m
            st = {"n": m, "avg5": sum(e[2] for e in sel) / m, "avg10": sum(e[3] for e in sel) / m,
                  "avg20": sum(e[4] for e in sel) / m, "win20": win}
            if kind == "pattern":
                st["hit"] = sum(1 for e in sel if e[5] == "hit") / m
                st["stop"] = sum(1 for e in sel if e[5] == "stop") / m
            row[g] = {kk: (round(vv, 4) if isinstance(vv, float) else vv) for kk, vv in st.items()}
        rows.append(row)
    rows.sort(key=lambda r: (r["kind"] != "signal", r["bias"] != "bull", -r["all"]["n"]))
    return {"rows": rows, "baseline": {g: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in x.items()} for g, x in baseline.items()}}


def market_env(kospi, kosdaq, stocks):
    """지금 시장 환경 → 상승장 / 약세장 / 조정·혼조"""
    def one(bars):
        c = [b[4] for b in bars]
        m20, m50, m200 = us.sma(c, 20), us.sma(c, 50), us.sma(c, 200)
        return {"close": c[-1], "ma50": m50[-1], "ma200": m200[-1], "above50": c[-1] > m50[-1], "above200": c[-1] > m200[-1],
                "ma20_up": m20[-1] > m20[-6], "chg20": c[-1] / c[-21] - 1, "date": bars[-1][0]}
    k, q = one(kospi), one(kosdaq) if kosdaq else None
    n = len(stocks) or 1
    a200 = sum(1 for s in stocks if s["ma200"][-1] is not None and s["c"][-1] > s["ma200"][-1]) / n
    up = sum(1 for s in stocks if s["trend"] == "up") / n
    dn = sum(1 for s in stocks if s["trend"] == "down") / n
    if k["above200"] and k["ma50"] > k["ma200"] and a200 >= 0.5:
        reg, label, note = "up", "상승장", "롱 신호가 잘 통하는 환경이에요."
    elif not k["above200"] and a200 < 0.4:
        reg, label, note = "down", "약세장", "롱 신호의 신뢰도가 낮아요. 숏·관망 위주로 보고, 롱은 손절을 짧게 잡는 게 좋아요."
    else:
        reg, label, note = "mixed", "조정·혼조", "방향이 엇갈리는 구간이에요. 신호를 골라서 보고 비중을 줄이는 게 좋아요."
    return {"regime": reg, "label": label, "note": note, "kospi": k, "kosdaq": q,
            "breadth": {"above200": round(a200, 3), "trend_up": round(up, 3), "trend_down": round(dn, 3)}}


def update_log(log, today, stocks):
    """오늘의 롱·숏 판단 저장 → {날짜: [[코드, 이름, 방향, 점수, 종가], ...]} (최근 120거래일)"""
    log = dict(log or {})
    log[today] = [[s["s"], s["n"], s["vd"]["dir"], s["vd"]["score"], s["last"]] for s in stocks if s["vd"]["dir"] != "wait"]
    return dict(sorted(log.items())[-120:])


def log_summary(log, stocks, dates, kospi):
    """기록장의 날짜별 성적: 롱·숏 판단 종목들의 5·20거래일 뒤 평균 수익률 (코스피와 비교)"""
    idx = {d: i for i, d in enumerate(dates)}
    closes = {s["s"]: s["c"] for s in stocks}
    kc = {b[0]: b[4] for b in kospi}
    kd = [b[0] for b in kospi]
    out = []
    for d, rows in sorted(log.items(), reverse=True):
        i = idx.get(d)
        rec = {"date": d, "n_long": sum(1 for r in rows if r[2] == "long"), "n_short": sum(1 for r in rows if r[2] == "short")}
        for k in (5, 20):
            if i is None or i + k >= len(dates):
                continue
            for side in ("long", "short"):
                rs = [closes[r[0]][i + k] / r[4] - 1 for r in rows if r[2] == side and r[0] in closes and closes[r[0]][i + k]]
                if rs:
                    rec[f"{side}{k}"] = round(sum(rs) / len(rs), 4)
                    rec[f"{side}{k}_win"] = round(sum(1 for x in rs if (x > 0 if side == "long" else x < 0)) / len(rs), 3)
            j = kd.index(d) if d in kc else None
            if j is not None and j + k < len(kd):
                rec[f"kospi{k}"] = round(kc[kd[j + k]] / kc[d] - 1, 4)
        out.append(rec)
    return out
