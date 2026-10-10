"""국내 기술적 분석 — 코스피 전 종목(보통주) + 코스닥 시가총액 상위 100 종목.

- 종목 목록·시가총액: 네이버 증권 시가총액 순위 (ETF·ETN·스팩·우선주·거래정지 종목 제외)
- 일봉: 한국투자증권 일봉(수정주가) 400거래일 (실패하면 네이버) · 장 마감(15:40) 전이면 오늘 미완성 일봉은 뺌. 마지막 날 거래량이 0이면 거래정지로 보고 제외
- 신호: 골든크로스(20·60일선), 장기 골든크로스(60·120일선), 정배열·역배열, 200일선·50주선 돌파·이탈 (데드크로스는 잡음이 많아 뺌),
        베이스(가격이 좁은 범위에서 오래 다져지는 구간)와 베이스 돌파, 52주 신고가, 거래량 급증, RSI 과매수·과매도
- 차트 패턴: 미국 패턴 터미널과 같은 규칙 (us.detect)
결과는 data/kr_ta.json (기술적 분석 탭을 열 때 따로 불러옴 — 대시보드 본문을 가볍게 유지)
"""
import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import us

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
KST = timezone(timedelta(hours=9))
SHOW = 120
KOSDAQ_N = 100
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
PREF = re.compile(r"(\d?우[A-C]?|우\(전환\))$")


def _json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return json.load(r)


def _n(s):
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def universe(log):
    out, halted = [], []
    for mk, limit in (("KOSPI", None), ("KOSDAQ", KOSDAQ_N)):
        rows, page = [], 1
        while True:
            d = _json(f"https://m.stock.naver.com/api/stocks/marketValue/{mk}?page={page}&pageSize=100")
            st = d.get("stocks") or []
            for s in st:
                name = s["stockName"]
                if s.get("stockEndType") != "stock" or "스팩" in name or PREF.search(name):
                    continue
                if (s.get("tradeStopType") or {}).get("name", "TRADING") != "TRADING":    # 거래정지 (예: 금양)
                    halted.append(name)
                    continue
                rows.append({"s": s["itemCode"], "n": name, "m": mk, "mc": (_n(s.get("marketValue")) or 0) * 1e8})
            if not st or (limit and len(rows) >= limit) or page * 100 >= d.get("totalCount", 0):
                break
            page += 1
            time.sleep(0.15)
        rows = rows[:limit] if limit else rows
        for i, r in enumerate(rows):
            r["r"] = i + 1
        out += rows
    log(f"  국내 종목: 코스피 {sum(r['m'] == 'KOSPI' for r in out)} · 코스닥 {sum(r['m'] == 'KOSDAQ' for r in out)}"
        f" · 거래정지 제외 {len(halted)} {halted[:8]}")
    return out


def naver_bars(code, count=400):
    import market
    return market.daily_bars(code, count)


# ---------------- 신호 ----------------
def _cross(a, b, days):
    """a 가 b 를 위로 뚫은 날이 최근 days 거래일 안에 있으면 며칠 전인지 (0 = 오늘)"""
    n = len(a)
    for k in range(days):
        i = n - 1 - k
        if i < 1 or None in (a[i], b[i], a[i - 1], b[i - 1]):
            break
        if a[i - 1] <= b[i - 1] and a[i] > b[i]:
            return k
    return None


def base_of(h, l, c, v):
    """베이스: 앞서 30% 이상 오른 뒤, 30~120거래일 동안 고가·저가 폭이 좁게(60일 미만 12%, 그 이상 18% 이내) 다져진 구간.
    → (시작, 끝(포함), 상단, 하단, 상태, 돌파일) — 끝은 베이스 마지막 날"""
    n = len(c)
    for e in range(1, 7):                          # e=1: 어제까지가 베이스(오늘은 지켜보는 중), e≥2: e일 전 돌파
        end = n - e
        best = None
        for L in range(120, 29, -5):
            s0 = end - L
            if s0 < 0:
                continue
            top, bot = max(h[s0:end]), min(l[s0:end])
            prior = min(l[max(0, s0 - 120):s0]) if s0 > 20 else None
            if (top - bot) / top <= (0.12 if L < 60 else 0.18) and prior and top / prior >= 1.3:   # 앞서 30% 이상 오른 뒤 다지는 구간만
                best = (s0, end - 1, top, bot, L)
                break
        if not best:
            continue
        s0, e1, top, bot, L = best
        if e == 1:
            if c[-1] > top * 1.005:
                return s0, e1, top, bot, "breakout", n - 1
            st = "near" if c[-1] >= top * 0.95 else "forming"
            if c[-1] < bot * 0.97:
                return None
            return s0, e1, top, bot, st, None
        if c[end] > top * 1.005 and c[-1] >= top * 0.97:     # 돌파한 날 = end, 지금도 상단 근처 이상 유지
            return s0, e1, top, bot, "breakout", end
    return None


def week_ma(c, dates, weeks=50):
    """50주선: 지난 (weeks-1)주 주봉 종가 + 오늘 종가의 평균 (주봉 차트의 50주 이동평균과 같은 값)"""
    from datetime import date
    wk = [date.fromisoformat(d).isocalendar()[:2] for d in dates]
    out, closes = [], []                       # closes: 끝난 주들의 주봉 종가
    for i in range(len(c)):
        if i > 0 and wk[i] != wk[i - 1]:
            closes.append(c[i - 1])            # 지난주 마지막 날 종가 = 지난주 주봉 종가
        out.append((sum(closes[-(weeks - 1):]) + c[i]) / weeks if len(closes) >= weeks - 1 else None)
    return out


def signals(o, h, l, c, v, dates=None):
    n = len(c)
    m5, m20, m60, m120, m200 = (us.sma(c, k) for k in (5, 20, 60, 120, 200))
    w50 = week_ma(c, dates) if dates else [None] * len(c)
    out = []

    def add(k, name, tone, ago=None, note=""):
        out.append({"k": k, "name": name, "tone": tone, "ago": ago, "note": note})
    k = _cross(m20, m60, 5)
    if k is not None:
        add("gc", "골든크로스", "bull", k, "20일선이 60일선을 위로 뚫음")
    k = _cross(m60, m120, 10)
    if k is not None:
        add("gc_long", "장기 골든크로스", "bull", k, "60일선이 120일선을 위로 뚫음")
    up = lambda i: None not in (m5[i], m20[i], m60[i], m120[i]) and m5[i] > m20[i] > m60[i] > m120[i]
    dn = lambda i: None not in (m5[i], m20[i], m60[i], m120[i]) and m5[i] < m20[i] < m60[i] < m120[i]
    if up(n - 1):
        k = next((j for j in range(1, 6) if not up(n - 1 - j)), None)
        add("align_up", "정배열 전환" if k else "정배열", "bull", (k - 1) if k else None, "5 > 20 > 60 > 120일선 순서")
    if dn(n - 1):
        k = next((j for j in range(1, 6) if not dn(n - 1 - j)), None)
        add("align_dn", "역배열 전환" if k else "역배열", "bear", (k - 1) if k else None, "5 < 20 < 60 < 120일선 순서")
    if w50[-1] is not None:
        k = _cross(c, w50, 5)
        if k is not None and c[-1] > w50[-1]:
            add("w50_up", "50주선 돌파", "bull", k, "종가가 50주선(주봉 50개 평균)을 위로 뚫음")
        k = _cross(w50, c, 5)
        if k is not None and c[-1] < w50[-1]:
            add("w50_dn", "50주선 이탈", "bear", k, "종가가 50주선 아래로 내려감")
    if m200[-1] is not None:
        k = _cross(c, m200, 5)
        if k is not None and c[-1] > m200[-1]:
            add("ma200_up", "200일선 돌파", "bull", k, "종가가 200일선을 위로 뚫음")
        k = _cross(m200, c, 5)
        if k is not None and c[-1] < m200[-1]:
            add("ma200_dn", "200일선 이탈", "bear", k, "종가가 200일선 아래로 내려감")
    b = base_of(h, l, c, v)
    if b:
        s0, e1, top, bot, st, bd = b
        L = e1 - s0 + 1
        avg = sum(v[max(0, (bd or n) - 20):(bd or n)]) / 20 if bd else 0
        vol = bool(bd and avg and v[bd] >= 1.5 * avg)
        if st == "breakout":
            add("base_bo", "베이스 돌파", "bull", n - 1 - bd, f"{L}일 베이스(폭 {(top - bot) / top * 100:.0f}%) 상단 돌파" + (" · 거래량 동반" if vol else ""))
        else:
            add("base", "베이스 상단 근접" if st == "near" else "베이스 형성 중", "info", None, f"앞선 상승 뒤 {L}일째 폭 {(top - bot) / top * 100:.0f}% 안에서 다지는 중")
    hi = max(h[-250:])
    if c[-1] >= hi * 0.995:
        add("hi52", "52주 신고가", "bull", None, "1년 중 가장 높은 가격권")
    elif c[-1] >= hi * 0.97:
        add("hi52_near", "신고가 근접", "bull", None, f"52주 고가까지 {(hi / c[-1] - 1) * 100:.1f}%")
    av = sum(v[-21:-1]) / 20
    if av and v[-1] >= 3 * av:
        add("vol", "거래량 급증", "info", 0, f"20일 평균의 {v[-1] / av:.1f}배")
    r = us.rsi(c)
    if r is not None and r >= 70:
        add("rsi_hi", "RSI 과매수", "bear", None, f"RSI {r:.0f}")
    elif r is not None and r <= 30:
        add("rsi_lo", "RSI 과매도", "bull", None, f"RSI {r:.0f}")
    trend = "up" if up(n - 1) or (None not in (m20[-1], m60[-1], m120[-1]) and c[-1] > m20[-1] > m60[-1] > m120[-1]) else \
        "down" if dn(n - 1) or (None not in (m20[-1], m60[-1], m120[-1]) and c[-1] < m20[-1] < m60[-1] < m120[-1]) else "side"
    return out, trend, (m20, m60, m120, m200, w50), b, r


# ---------------- 종합 판단: 롱 / 숏 / 관망 + 그 방향의 목표·손절 ----------------
SIG_SCORE = {"w50_up": (2, "50주선 돌파"), "w50_dn": (-2, "50주선 이탈"), "gc": (2, "골든크로스 (20·60일선)"), "gc_long": (1, "장기 골든크로스 (60·120일선)"), "ma200_up": (2, "200일선 돌파"),
             "ma200_dn": (-2, "200일선 이탈"), "base_bo": (2, "베이스 돌파"), "hi52": (1, "52주 신고가"), "hi52_near": (1, "신고가 근접"),
             "div_bull": (1, "상승 다이버전스"), "div_bear": (-1, "하락 다이버전스"), "rs_hi": (1, "상대강도 신고가")}
ST_W = {"confirmed": 2, "retest": 2, "forming": 1, "target": 0}


def verdict(h, l, c, sig, trend, pats, b, r, mas):
    m20, m60, m120, m200 = mas[:4]
    last, n = c[-1], len(c)
    why = []

    def add(pt, txt):
        if pt:
            why.append([pt, txt])
    add(2 if trend == "up" else -2 if trend == "down" else 0, "정배열·상승 추세" if trend == "up" else "역배열·하락 추세")
    if m200[-1] is not None and not any(x["k"] in ("ma200_up", "ma200_dn") for x in sig):
        add(1 if last > m200[-1] else -1, "200일선 위" if last > m200[-1] else "200일선 아래")
    for x in sig:
        if x["k"] in SIG_SCORE:
            add(*SIG_SCORE[x["k"]])
        elif x["k"] == "base" and x["name"] == "베이스 상단 근접":
            add(1, "베이스 상단 근접")
    if r is not None and r >= 75:
        add(-1, f"RSI {r:.0f} 과열")
    elif r is not None and r <= 25:
        add(1, f"RSI {r:.0f} 과매도")
    for side_ in ("bull", "bear"):                    # 방향별로 가장 강한 패턴 하나씩만 반영
        ps = [p for p in pats if p["bias"] == side_ and ST_W[p["st"]]]
        if ps:
            p = max(ps, key=lambda p: (ST_W[p["st"]], p["q"]))
            w = ST_W[p["st"]] * (1 if side_ == "bull" else -1)
            add(w, f"{p['name']} {'돌파 확정' if p['st'] == 'confirmed' else '되돌림' if p['st'] == 'retest' else '형성 중'}")
    score = sum(x[0] for x in why)
    d = "long" if score >= 3 else "short" if score <= -3 else "wait"
    plan = None
    if d != "wait":
        bias = "bull" if d == "long" else "bear"
        ps = sorted([p for p in pats if p["bias"] == bias and p["st"] != "target" and p.get("rr")],
                    key=lambda p: (-ST_W[p["st"]], -p["q"]))
        if ps:
            p = ps[0]
            plan = {"target": p["target"], "stop": p["stop"], "basis": f"{p['name']} 패턴의 목표·손절"}
        else:                                        # 패턴이 없으면 지지·저항선으로
            hi_, lo_ = us.pivots(h, l, max(0, n - 120))
            piv_lo = [l[i] for i in lo_][-6:]
            piv_hi = [h[i] for i in hi_][-6:]
            mav = [a[-1] for a in (m20, m60, m120, m200) if a[-1] is not None]
            base_lv = [b[2], b[3]] if b else []
            if d == "long":
                sup = [x for x in piv_lo + mav + base_lv if x < last * 0.98]
                res = [x for x in piv_hi + [max(h[-250:])] + base_lv if x > last * 1.03]
                if b and b[4] in ("breakout", "near"):
                    res.append(b[2] + (b[2] - b[3]))
                if sup:
                    stop = max(sup) * 0.99
                    risk = last - stop
                    good = sorted(x for x in res if (x - last) >= 1.5 * risk)
                    target, basis = (good[0], "아래 지지선 밑 손절 · 위쪽 저항선 목표") if good else (last + 2 * risk, "아래 지지선 밑 손절 · 손절 폭의 2배 목표")
                    plan = {"target": target, "stop": stop, "basis": basis}
            else:
                res = [x for x in piv_hi + mav + base_lv if x > last * 1.02]
                sup = [x for x in piv_lo + [min(l[-250:])] + base_lv if x < last * 0.97]
                if res:
                    stop = min(res) * 1.01
                    risk = stop - last
                    good = sorted((x for x in sup if (last - x) >= 1.5 * risk), reverse=True)
                    target, basis = (good[0], "위 저항선 위 손절 · 아래쪽 지지선 목표") if good else (last - 2 * risk, "위 저항선 위 손절 · 손절 폭의 2배 목표")
                    plan = {"target": target, "stop": stop, "basis": basis}
        if plan:
            t, st = plan["target"], plan["stop"]
            rr = (t - last) / (last - st) if d == "long" else (last - t) / (st - last)
            plan.update(target=round(t), stop=round(st), rr=round(rr, 2))
    return {"dir": d, "score": score, "why": why, "plan": plan}


def lead(o, h, l, c, v, dates, kmap):
    """선행 신호: 변동성 수축, 다이버전스, 상대강도, 매물대, 주봉 추세 → (신호 목록, 화면용 값)"""
    import ta_lab
    from datetime import date as _d
    n, out, sig = len(c), {}, []
    # 볼린저 밴드 폭(20일, ±2표준편차)이 최근 120일 중 가장 좁음 = 변동성 수축
    bw = []
    for i in range(n):
        if i < 19:
            bw.append(None)
            continue
        w_ = c[i - 19:i + 1]
        m_ = sum(w_) / 20
        sd = (sum((x - m_) ** 2 for x in w_) / 20) ** .5
        bw.append(4 * sd / m_ if m_ else None)
    rec = [x for x in bw[-120:] if x is not None]
    dry = sum(v[-10:]) / 10 < 0.7 * (sum(v[-50:]) / 50)
    if bw[-1] is not None and rec and bw[-1] <= min(rec) * 1.05:
        sig.append({"k": "squeeze", "name": "변동성 수축", "tone": "info", "ago": None,
                    "note": "볼린저 밴드 폭이 6개월 중 가장 좁음" + (" · 거래량도 마름" if dry else "")})
    out["bw"] = round(bw[-1], 4) if bw[-1] else None
    out["dry"] = dry
    # 다이버전스: 최근 60일 스윙 저점(고점) 두 개 — 가격은 더 낮은(높은) 저점(고점)인데 RSI는 반대
    rs = ta_lab.rsi_series(c)
    hi_, lo_ = us.pivots(h, l, max(0, n - 80))
    L2 = [i for i in lo_ if i >= n - 60][-2:]
    H2 = [i for i in hi_ if i >= n - 60][-2:]
    div = None
    if len(L2) == 2 and L2[1] >= n - 15 and l[L2[1]] < l[L2[0]] and rs[L2[0]] and rs[L2[1]] and rs[L2[1]] > rs[L2[0]] + 3 and rs[L2[1]] < 45:
        div = ("bull", L2[0], L2[1], l[L2[0]], l[L2[1]])
        sig.append({"k": "div_bull", "name": "상승 다이버전스", "tone": "bull", "ago": n - 1 - L2[1],
                    "note": f"가격은 더 낮은 저점, RSI는 더 높은 저점 ({rs[L2[0]]:.0f} → {rs[L2[1]]:.0f})"})
    elif len(H2) == 2 and H2[1] >= n - 15 and h[H2[1]] > h[H2[0]] and rs[H2[0]] and rs[H2[1]] and rs[H2[1]] < rs[H2[0]] - 3 and rs[H2[0]] > 55:
        div = ("bear", H2[0], H2[1], h[H2[0]], h[H2[1]])
        sig.append({"k": "div_bear", "name": "하락 다이버전스", "tone": "bear", "ago": n - 1 - H2[1],
                    "note": f"가격은 더 높은 고점, RSI는 더 낮은 고점 ({rs[H2[0]]:.0f} → {rs[H2[1]]:.0f})"})
    out["_div"] = div
    out["_rsi"] = rs
    # 상대강도: 3·6·9·12개월 수익률 가중합 (순위는 전체 종목 계산 후), 코스피 대비 비율선 신고가
    def ret(k):
        return c[-1] / c[-1 - k] - 1 if n > k else None
    parts = [(0.4, ret(63)), (0.2, ret(126)), (0.2, ret(189)), (0.2, ret(252))]
    wsum = sum(wt for wt, x in parts if x is not None)
    out["rs_raw"] = sum(wt * x for wt, x in parts if x is not None) / wsum if wsum else None
    if kmap:
        ratio = [c[i] / kmap[dates[i]] for i in range(max(0, n - 250), n) if kmap.get(dates[i])]
        if len(ratio) > 120 and ratio[-1] >= max(ratio) * 0.999 and ratio[-2] < max(ratio[:-1]):
            sig.append({"k": "rs_hi", "name": "상대강도 신고가", "tone": "bull", "ago": 0, "note": "코스피 대비 비율선이 1년 중 최고 — 시장보다 먼저 강해짐"})
    # 매물대: 최근 120일 가격대별 거래량 (24칸)
    lo_p, hi_p = min(l[-120:]), max(h[-120:])
    step = (hi_p - lo_p) / 24 or 1
    vp = [0.0] * 24
    for i in range(n - 120, n):
        vp[min(23, int(((h[i] + l[i] + c[i]) / 3 - lo_p) / step))] += v[i]
    tot = sum(vp) or 1
    out["vp"] = {"lo": round(lo_p), "step": round(step, 2), "v": [round(x / tot, 4) for x in vp]}
    # 주봉 추세: 주봉 종가의 10주·30주 평균
    wk, wc = [_d.fromisoformat(d).isocalendar()[:2] for d in dates], []
    for i in range(n):
        if i == n - 1 or wk[i + 1] != wk[i]:
            wc.append(c[i])
    if len(wc) >= 30:
        m10, m30 = sum(wc[-10:]) / 10, sum(wc[-30:]) / 30
        out["wk"] = "up" if wc[-1] > m10 > m30 else "down" if wc[-1] < m10 < m30 else "side"
    return sig, out


def analyze(meta, bars, kmap=None):
    o, h, l, c, v = ([b[k] for b in bars] for k in range(1, 6))
    n = len(c)
    sig, trend, (m20, m60, m120, m200, w50), b, r = signals(o, h, l, c, v, [x[0] for x in bars])
    lsig, lx = lead(o, h, l, c, v, [x[0] for x in bars], kmap)
    sig += lsig
    pats = us.detect(o, h, l, c, v)
    vd = verdict(h, l, c, sig, trend, pats, b, r, (m20, m60, m120, m200))
    order = {"long": "bull", "short": "bear"}.get(vd["dir"])
    pats.sort(key=lambda p: p["bias"] != order)       # 종합 판단과 같은 방향 패턴을 앞에
    base = n - SHOW
    w = lambda i: max(0, i - base)
    for p in pats:
        p["lines"] = [[w(a), round(y1), w(b_), round(y2), k] for a, y1, b_, y2, k in p["lines"]]
        p["pts"] = [[w(a), round(y), t] for a, y, t in p["pts"]]
        for k in ("target", "stop", "level"):
            if p.get(k) is not None:
                p[k] = round(p[k])
        if p.get("bi") is not None:
            p["bi"] = w(p["bi"])                       # 돌파일도 화면 창 기준으로
    iv = lambda a: [None if x is None else int(round(x)) for x in a[-SHOW:]]
    chg = lambda k: round(c[-1] / c[-1 - k] - 1, 5) if n > k and c[-1 - k] else None
    av20 = sum(v[-21:-1]) / 20
    return {**meta, "date": bars[-1][0], "o": iv(o), "h": iv(h), "l": iv(l), "c": iv(c), "v": [int(x // 1000) for x in v[-SHOW:]],
            "ma20": iv(m20), "ma60": iv(m60), "ma120": iv(m120), "ma200": iv(m200), "ma50w": iv(w50),
            "last": c[-1], "chg1": chg(1), "chg5": chg(5), "chg20": chg(20), "chg60": chg(60),
            "rsi": round(r, 1) if r is not None else None, "hi52": max(h[-250:]), "lo52": min(l[-250:]),
            "vr": round(v[-1] / av20, 2) if av20 else None, "trend": trend, "sig": sig, "pats": pats, "vd": vd,
            "base": [w(b[0]), w(b[1]), round(b[2]), round(b[3]), b[4]] if b and b[1] >= base else None,
            "bw": lx["bw"], "dry": lx["dry"], "rs_raw": lx["rs_raw"], "vp": lx["vp"], "wk": lx.get("wk"),
            "div": [lx["_div"][0], w(lx["_div"][1]), w(lx["_div"][2]), round(lx["_div"][3]), round(lx["_div"][4]),
                    round(lx["_rsi"][lx["_div"][1]], 1), round(lx["_rsi"][lx["_div"][2]], 1)] if lx["_div"] and lx["_div"][1] >= base else None}


def collect(kis=None, held=None, log=print):
    t0 = time.time()
    held = held or {}
    import market
    import ta_lab
    uni = universe(log)
    fails, halted = [], []
    now = datetime.now(KST)
    today, closed = now.strftime("%Y-%m-%d"), now.hour * 60 + now.minute >= 15 * 60 + 40

    def index_bars(kis_code, naver_sym):
        try:
            bars = market.daily_bars(naver_sym, 700)          # 성적표 기간 전체의 시장 환경을 보려고 길게 (네이버)
        except Exception:  # noqa: BLE001
            bars = kis.index_daily(kis_code, 420) if kis else []
        bars = [b[:6] for b in bars]
        return bars[:-1] if bars and bars[-1][0] == today and not closed else bars
    kospi, kosdaq = index_bars("0001", "KOSPI"), index_bars("1001", "KOSDAQ")
    regime = ta_lab.regime_map(kospi)
    kmap = {b[0]: b[4] for b in kospi}

    def one(m):
        bars = None
        if kis:
            try:
                bars = kis.stock_daily(m["s"], 400)
            except Exception:  # noqa: BLE001
                bars = None
        if not bars or len(bars) < 130:
            try:
                bars = naver_bars(m["s"])
            except Exception:  # noqa: BLE001
                pass
        bars = [b[:6] for b in (bars or []) if b[2] > 0 and b[3] > 0 and b[4] > 0]   # 가격 0인 날 제외
        if bars and bars[-1][0] == today and not closed:                 # 장중·장 시작 전이면 오늘 미완성 일봉은 뺌
            bars = bars[:-1]
        for b in bars:
            if not b[1]:
                b[1] = b[4]
        if bars and bars[-1][5] == 0:                                    # 마지막 날 거래량 0 → 거래정지
            halted.append(m["n"])
            return None
        if not bars or len(bars) < 130:
            fails.append(f"{m['s']}:{len(bars) if bars else 0}일")
            return None
        try:
            res = analyze({**m, "held": held.get(m["s"])}, bars, kmap)
            if kis:                                             # 외국인·기관 연속 순매수
                try:
                    fl = ta_lab.flows(kis.stock_investor(m["s"]))
                    res["fl"] = fl
                    for who, ko in (("f", "외국인"), ("o", "기관")):
                        if fl[who] >= 3:
                            res["sig"].append({"k": who + "_buy", "name": f"{ko} {fl[who]}일 연속 순매수", "tone": "bull", "ago": None,
                                               "note": f"최근 5일 {fl[who + '5'] / 1e8:+,.0f}억"})
                except Exception:  # noqa: BLE001
                    pass
            try:                                                # 신호 성적표는 더 긴 네이버 일봉(700일)으로 검증
                long_bars = [b[:6] for b in naver_bars(m["s"], 700) if b[2] > 0 and b[3] > 0 and b[4] > 0 and b[0] <= bars[-1][0]]
            except Exception:  # noqa: BLE001
                long_bars = None
            try:
                res["_bt"] = ta_lab.backtest(long_bars if long_bars and len(long_bars) > len(bars) else bars, regime, kmap)
            except Exception:  # noqa: BLE001 — 성적표 계산이 실패해도 종목 분석은 살림
                res["_bt"] = ([], {"up": [0, 0.0, 0], "down": [0, 0.0, 0]})
            return res
        except Exception as e:  # noqa: BLE001
            fails.append(f"{m['s']}:{e}")
            return None
    with ThreadPoolExecutor(max_workers=4 if kis else 6) as ex:
        stocks = [r for r in ex.map(one, uni) if r]
    bt_ev, bt_base = [], []
    for s in stocks:
        ev, b = s.pop("_bt")
        bt_ev += ev
        bt_base.append(b)
    bt = ta_lab.aggregate(bt_ev, bt_base)
    last = max(s["date"] for s in stocks)
    stocks = [s for s in stocks if s["date"] == last]              # 거래정지 등으로 오늘 일봉이 없는 종목 제외
    ref = market.daily_bars("005930", 400)                          # 날짜 축 (삼성전자 기준)
    dates = [b[0] for b in ref if b[0] <= last][-SHOW:]
    cnt = {}
    for s in stocks:
        for x in s["sig"]:
            cnt[x["k"]] = cnt.get(x["k"], 0) + 1
    log(f"  기술적 분석: {len(stocks)}종목 · 거래량 0(거래정지) 제외 {len(halted)} {halted[:6]} · 실패 {len(fails)} {fails[:6]} · " + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items())) +
        f" · 패턴 {sum(len(s['pats']) for s in stocks)} · {time.time() - t0:.0f}초")
    # 상대강도 점수 (1~99): 3·6·9·12개월 가중 수익률의 전체 순위
    ranked = sorted((s for s in stocks if s.get("rs_raw") is not None), key=lambda s: s["rs_raw"])
    for i, s in enumerate(ranked):
        s["rs"] = max(1, min(99, int(round((i + 1) / len(ranked) * 99))))
    for s in stocks:
        s["w"] = ta_lab.watch(s)                        # 돌파 대기 (트리거 가격)
    wl = [[s["s"], s["n"], s["w"]["dir"], s["w"]["trigger"], s["w"]["stop"], s["w"]["target"], s["w"]["why"], s["w"]["dist"], s["w"]["rr"]]
          for s in stocks if s["w"]]
    wl.sort(key=lambda x: abs(x[7]))
    env_ = ta_lab.market_env(kospi, kosdaq, stocks)
    tlog = ta_lab.update_log(json.loads((DATA / "ta_log.json").read_text(encoding="utf-8")) if (DATA / "ta_log.json").exists() else {},
                             last, stocks)
    (DATA / "ta_log.json").write_text(json.dumps(tlog, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    kd = [b[0] for b in kospi]
    bt["period"] = [kd[min(ta_lab.START, len(kd) - 1)], kd[-1 - ta_lab.H]] if len(kd) > ta_lab.START + ta_lab.H else None
    log(f"  돌파 대기: 롱 {sum(1 for x in wl if x[2] == 'long')} · 숏 {sum(1 for x in wl if x[2] == 'short')}")
    log(f"  신호 성적표: 신호·패턴 {len(bt['rows'])}종 · 사건 {len(bt_ev)}건 · 시장 환경 {env_['label']} · 기록장 {len(tlog)}일")
    return {"captured_at": datetime.now(KST).isoformat(), "date": last, "dates": dates, "stocks": stocks,
            "counts": cnt, "failed": len(fails), "bt": bt, "market": env_, "watch": wl,
            "log": ta_lab.log_summary(tlog, stocks, dates, kospi)}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    import collect as col
    snaps = sorted((DATA / "snapshots").glob("*.json"))
    held = {}
    if snaps:
        held = {p["company_symbol"]: p["side"] for p in json.loads(snaps[-1].read_text(encoding="utf-8"))["portfolio"]["positions"]}
    env = col.load_env()
    k = None
    if env.get("KIS_APP_KEY"):
        import kis as kis_mod
        try:
            k = kis_mod.KIS(env["KIS_APP_KEY"], env["KIS_APP_SECRET"])
        except Exception as e:  # noqa: BLE001
            print(f"  [경고] 한국투자증권 연결 실패 (네이버만 사용): {e}")
    d = collect(k, held)
    old = col.read_json(DATA / "kr_ta.json", None)
    if old and old.get("date") == d["date"] and "--force" not in sys.argv:
        print(f"  기술적 분석: {d['date']} 자료가 이미 있음 (휴장일) → 저장 안 함")
        sys.exit(0)
    (DATA / "kr_ta.json").write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):        # 내 텔레그램으로 오늘의 기술적 분석 요약
        try:
            import alerts
            alerts.ta_digest(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"])
        except Exception as e:  # noqa: BLE001
            print(f"  [경고] 텔레그램 기술적 분석 요약 실패: {e}")
