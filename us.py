"""미국 주식 패턴 터미널 데이터 — 시가총액 상위 500 종목의 일봉을 받아 차트 패턴을 규칙으로 찾는다. (무료)

- 종목 목록·시가총액·업종: 나스닥 공개 스크리너 (실패하면 지난번 목록 data/us_universe.json 사용)
- 일봉 (수정주가): 한국투자증권 해외주식 기간별 시세 — 시세 조회만 사용, 실패하면 야후
- 섹터: SPDR 섹터 상장지수펀드 11종 · 지수: 야후
- 실적 발표 일정: 나스닥 공개 실적 캘린더 (앞으로 2주)

패턴은 '스윙 고점·저점'(앞뒤 4거래일 중 가장 높은/낮은 날)을 이어 추세선을 긋는 방식의 단순 규칙이다.
"""
import json
import math
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
KST = timezone(timedelta(hours=9))
TOP_N = 500
SHOW = 126                 # 화면에 그리는 거래일 수 (약 6개월)
WORKERS = 4
NQ = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
EXCH = {"nasdaq": "NAS", "nyse": "NYS", "amex": "AMS"}
SECTOR_KO = {"Technology": "기술", "Health Care": "헬스케어", "Finance": "금융", "Consumer Discretionary": "경기소비재",
             "Consumer Staples": "필수소비재", "Industrials": "산업재", "Energy": "에너지", "Utilities": "유틸리티",
             "Real Estate": "부동산", "Basic Materials": "소재", "Telecommunications": "통신", "Miscellaneous": "기타"}
SECTOR_ETF = [("XLK", "기술"), ("XLC", "커뮤니케이션"), ("XLY", "경기소비재"), ("XLF", "금융"), ("XLV", "헬스케어"),
              ("XLI", "산업재"), ("XLE", "에너지"), ("XLB", "소재"), ("XLP", "필수소비재"), ("XLU", "유틸리티"), ("XLRE", "부동산")]
INDICES = [("^GSPC", "S&P 500"), ("^IXIC", "나스닥 종합"), ("^DJI", "다우존스"), ("^RUT", "러셀 2000"),
           ("^SOX", "필라델피아 반도체"), ("^VIX", "VIX 변동성")]


def _json(url, headers=NQ, tries=3):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise last


def _num(s):
    try:
        return float(str(s).replace("$", "").replace(",", "").replace("%", ""))
    except ValueError:
        return None


# ---------------- 종목 목록 ----------------
def universe(log):
    rows = []
    for ex, code in EXCH.items():
        d = _json(f"https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&exchange={ex}&download=true")
        for r in d["data"]["rows"]:
            s, mc = r["symbol"].strip(), _num(r.get("marketCap"))
            if not mc or "^" in s or len(s) > 6:
                continue
            rows.append({"s": s, "n": r["name"], "x": code, "mc": mc, "sec": SECTOR_KO.get(r.get("sector") or "", r.get("sector") or ""),
                         "ind": r.get("industry") or ""})
    rows.sort(key=lambda r: -r["mc"])
    out = rows[:TOP_N]
    for i, r in enumerate(out):
        r["r"] = i + 1
        r["n"] = _short_name(r["n"])
    log(f"  미국 종목 목록: 전체 {len(rows)}개 중 시총 상위 {len(out)}개")
    return out


def _short_name(n):
    for cut in (" Common Stock", " Class A", " Class B", " Class C", " Ordinary Shares", " American Depositary", " Depositary",
                " Common Shares", " Inc.", ","):
        if cut in n:
            n = n[:n.index(cut)] + (" Inc." if cut == " Inc." else "")
    return n.strip()


# ---------------- 일봉 ----------------
def kis_bars(kis, exch, sym, pages=4):
    """한국투자증권 해외 일봉 (수정주가) → [[YYYY-MM-DD, 시, 고, 저, 종, 거래량]] 오래된 순"""
    out, bymd = {}, ""
    for _ in range(pages):
        d = kis.get("/uapi/overseas-price/v1/quotations/dailyprice", "HHDFS76240000",
                    {"AUTH": "", "EXCD": exch, "SYMB": sym, "GUBN": "0", "BYMD": bymd, "MODP": "1"})
        rows = [r for r in d.get("output2") or [] if r.get("xymd") and _num(r.get("clos"))]
        if not rows:
            break
        for r in rows:
            x = r["xymd"]
            out[f"{x[:4]}-{x[4:6]}-{x[6:]}"] = [_num(r["open"]), _num(r["high"]), _num(r["low"]), _num(r["clos"]), _num(r["tvol"]) or 0]
        first = min(r["xymd"] for r in rows)
        bymd = (datetime.strptime(first, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        if len(rows) < 100:
            break
    return [[d, *v] for d, v in sorted(out.items())]


def yahoo_bars(sym, rng="2y"):
    enc = urllib.request.quote(sym.replace("/", "-").replace(".", "-"), safe="")
    d = _json(f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}?range={rng}&interval=1d",
              headers={"User-Agent": NQ["User-Agent"]})["chart"]["result"][0]
    q = d["indicators"]["quote"][0]
    adj = (d["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    tz = timezone(timedelta(seconds=d["meta"].get("gmtoffset", 0)))
    out = []
    for i, t in enumerate(d["timestamp"]):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c):
            continue
        f = adj[i] / c if adj and adj[i] and c else 1
        out.append([datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d"), o * f, h * f, l * f, c * f, v or 0])
    return out


# ---------------- 지표 ----------------
def sma(a, n):
    out, s = [], 0.0
    for i, v in enumerate(a):
        s += v
        if i >= n:
            s -= a[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def rsi(c, n=14):
    if len(c) <= n:
        return None
    g = l = 0.0
    for i in range(1, n + 1):
        d = c[i] - c[i - 1]
        g, l = g + max(d, 0), l + max(-d, 0)
    g, l = g / n, l / n
    for i in range(n + 1, len(c)):
        d = c[i] - c[i - 1]
        g, l = (g * (n - 1) + max(d, 0)) / n, (l * (n - 1) + max(-d, 0)) / n
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


# ---------------- 패턴 ----------------
K = 4          # 스윙 판단: 앞뒤 4거래일


def pivots(h, l, start):
    n = len(h)
    hi = [i for i in range(max(start, K), n - K) if h[i] == max(h[i - K:i + K + 1])]
    lo = [i for i in range(max(start, K), n - K) if l[i] == min(l[i - K:i + K + 1])]
    return hi, lo


def fit(pts):
    """최소제곱 직선 → (기울기, 절편, 최대 오차 비율)"""
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    var = sum((p[0] - mx) ** 2 for p in pts)
    a = sum((p[0] - mx) * (p[1] - my) for p in pts) / var if var else 0
    b = my - a * mx
    err = max(abs(p[1] - (a * p[0] + b)) / p[1] for p in pts)
    return a, b, err


def _status(bias, c, h, l, level_at, after, target, stop):
    """돌파 여부 → 상태. bias: bull/bear. level_at(i): 돌파선 값. after: 이 지점 이후만 돌파로 인정"""
    n = len(c)
    brk = None
    for i in range(after + 1, n):
        if (bias == "bull" and c[i] > level_at(i) * 1.005) or (bias == "bear" and c[i] < level_at(i) * 0.995):
            brk = i
            break
    last = c[-1]
    if (bias == "bull" and last < stop) or (bias == "bear" and last > stop):
        return None, brk                                    # 손절선 아래(위)로 깨짐 → 무효
    if brk is None:
        return "forming", None
    if bias == "bull" and max(h[brk:]) >= target or bias == "bear" and min(l[brk:]) <= target:
        return "target", brk
    lv = level_at(n - 1)
    if (bias == "bull" and last <= lv * 1.02) or (bias == "bear" and last >= lv * 0.98):
        return "retest", brk
    return "confirmed", brk


def _vol_ok(v, brk):
    if brk is None or brk < 20:
        return False
    avg = sum(v[brk - 20:brk]) / 20
    return avg > 0 and v[brk] >= 1.5 * avg


def detect(o, h, l, c, v):
    """→ 패턴 목록 (인덱스는 전체 일봉 기준)"""
    n = len(c)
    if n < 80:
        return []
    base = n - SHOW
    hi, lo = pivots(h, l, base)
    pats = []

    def add(kind, name, fam, bias, status, q, target, stop, lines, pts, brk, level):
        last = c[-1]
        if bias == "bull":
            rr = (target - last) / (last - stop) if last > stop else None
        elif bias == "bear":
            rr = (last - target) / (stop - last) if stop > last else None
        else:
            rr = None
        q = int(max(30, min(100, q + (8 if _vol_ok(v, brk) else 0))))
        pats.append({"k": kind, "name": name, "fam": fam, "bias": bias, "st": status, "q": q,
                     "target": round(target, 2), "stop": round(stop, 2), "level": round(level, 2) if level else None,
                     "rr": round(rr, 2) if rr is not None else None, "lines": lines, "pts": pts, "vol": _vol_ok(v, brk)})

    # 1) 삼각형·쐐기·채널: 최근 스윙 고점 3개, 저점 3개
    H3 = [i for i in hi if i >= n - 90][-3:]
    L3 = [i for i in lo if i >= n - 90][-3:]
    if len(H3) >= 2 and len(L3) >= 2 and max(H3[-1], L3[-1]) - min(H3[0], L3[0]) >= 20:
        ah, bh, eh = fit([(i, h[i]) for i in H3])
        al, bl, el = fit([(i, l[i]) for i in L3])
        px = c[-1]
        sh, sl = ah / px, al / px                           # 하루 기울기 (가격 대비)
        flat = 0.0007
        if eh < 0.025 and el < 0.025:
            start = min(H3[0], L3[0])
            gap0 = (ah * start + bh) - (al * start + bl)
            gapn = (ah * (n - 1) + bh) - (al * (n - 1) + bl)
            conv = gapn < gap0 * 0.85 and gapn > 0
            kind = None
            if abs(sh) < flat and sl > flat:
                kind = ("asc_tri", "상승 삼각형", "지속형", "bull")
            elif abs(sl) < flat and sh < -flat:
                kind = ("desc_tri", "하락 삼각형", "지속형", "bear")
            elif sh < -flat and sl > flat:
                kind = ("sym_tri", "대칭 삼각형", "지속형", "bull" if c[-1] > c[start] else "bear")
            elif sh > flat and sl > flat and conv:
                kind = ("rising_wedge", "상승 쐐기", "반전형", "bear")
            elif sh < -flat and sl < -flat and conv:
                kind = ("falling_wedge", "하락 쐐기", "반전형", "bull")
            elif sh > flat and sl > flat and abs(sh - sl) < flat * 0.6:
                kind = ("asc_channel", "상승 채널", "지속형", "bull")
            elif sh < -flat and sl < -flat and abs(sh - sl) < flat * 0.6:
                kind = ("desc_channel", "하락 채널", "지속형", "bear")
            if kind and gap0 > 0:
                k, name, fam, bias = kind
                res = lambda i: ah * i + bh
                sup = lambda i: al * i + bl
                last_piv = max(H3[-1], L3[-1])
                if bias == "bull":
                    level_at, stop = res, sup(n - 1) * 0.99
                    target = res(n - 1) + gap0
                else:
                    level_at, stop = sup, res(n - 1) * 1.01
                    target = sup(n - 1) - gap0
                if k in ("asc_channel", "desc_channel"):           # 채널은 추세 따라가기: 목표는 반대편 선, 손절은 이쪽 선 바깥
                    st = "forming"
                    brk = None
                    target, stop = (res(n - 1), sup(n - 1) * 0.98) if bias == "bull" else (sup(n - 1), res(n - 1) * 1.02)
                    if (bias == "bull" and c[-1] < stop) or (bias == "bear" and c[-1] > stop):
                        st = None
                else:
                    st, brk = _status(bias, c, h, l, level_at, last_piv, target, stop)
                if st:
                    q = 62 + 6 * (len(H3) + len(L3) - 4) + int((0.025 - max(eh, el)) * 600)
                    add(k, name, fam, bias, st, q, target, stop,
                        [[start, res(start), n - 1, res(n - 1), "res"], [start, sup(start), n - 1, sup(n - 1), "sup"]],
                        [[i, h[i], "H"] for i in H3] + [[i, l[i], "L"] for i in L3], brk, level_at(n - 1))

    # 2) 이중 바닥 / 이중 천장
    for bias, piv, arr in (("bull", lo, l), ("bear", hi, h)):
        P = [i for i in piv if i >= n - 100]
        if len(P) < 2:
            continue
        i1, i2 = P[-2], P[-1]
        a, b = arr[i1], arr[i2]
        if not (10 <= i2 - i1 <= 80 and i2 >= n - 60 and abs(b / a - 1) <= 0.03):
            continue
        if bias == "bull":
            neck = max(h[i1:i2 + 1])
            if neck < max(a, b) * 1.05:
                continue
            bottom = min(a, b)
            target, stop = neck + (neck - bottom), bottom * 0.98
            name, k = "이중 바닥", "dbl_bottom"
        else:
            neck = min(l[i1:i2 + 1])
            if neck > min(a, b) * 0.95:
                continue
            top = max(a, b)
            target, stop = neck - (top - neck), top * 1.02
            name, k = "이중 천장", "dbl_top"
        st, brk = _status(bias, c, h, l, lambda i: neck, i2, target, stop)
        if st:
            q = 70 + int((0.03 - abs(b / a - 1)) * 500)
            add(k, name, "반전형", bias, st, q, target, stop, [[i1, neck, n - 1, neck, "neck"]],
                [[i1, a, "1"], [i2, b, "2"]], brk, neck)

    # 3) 헤드앤숄더 / 역헤드앤숄더
    for bias, piv, arr, other in (("bull", lo, l, h), ("bear", hi, h, l)):
        P = [i for i in piv if i >= n - 110]
        if len(P) < 3:
            continue
        i1, i2, i3 = P[-3:]
        A, B, C = arr[i1], arr[i2], arr[i3]
        ok = (B < A * 0.97 and B < C * 0.97) if bias == "bull" else (B > A * 1.03 and B > C * 1.03)
        if not (ok and abs(A / C - 1) <= 0.06 and i2 - i1 >= 5 and i3 - i2 >= 5 and i3 >= n - 45):
            continue
        if bias == "bull":
            p1 = max(range(i1, i2 + 1), key=lambda i: other[i])
            p2 = max(range(i2, i3 + 1), key=lambda i: other[i])
        else:
            p1 = min(range(i1, i2 + 1), key=lambda i: other[i])
            p2 = min(range(i2, i3 + 1), key=lambda i: other[i])
        sl_ = (other[p2] - other[p1]) / (p2 - p1)
        neck = lambda i, p1=p1, sl_=sl_: other[p1] + sl_ * (i - p1)
        depth = abs(neck(i2) - B)
        if bias == "bull":
            target, stop, name, k = neck(n - 1) + depth, C * 0.98, "역헤드앤숄더", "inv_hs"
        else:
            target, stop, name, k = neck(n - 1) - depth, C * 1.02, "헤드앤숄더", "hs"
        st, brk = _status(bias, c, h, l, neck, i3, target, stop)
        if st:
            q = 74 + int((0.06 - abs(A / C - 1)) * 250)
            add(k, name, "반전형", bias, st, q, target, stop, [[p1, neck(p1), n - 1, neck(n - 1), "neck"]],
                [[i1, A, "어깨"], [i2, B, "머리"], [i3, C, "어깨"]], brk, neck(n - 1))

    # 4) 박스권 (최근 45일, 마지막 3일 제외)
    s0, s1 = n - 45, n - 3
    top, bot = max(h[s0:s1]), min(l[s0:s1])
    if 0.04 <= top / bot - 1 <= 0.12:
        tt = sum(1 for i in range(s0, s1) if h[i] >= top * 0.985)
        bt = sum(1 for i in range(s0, s1) if l[i] <= bot * 1.015)
        if tt >= 2 and bt >= 2:
            ht = top - bot
            if c[-1] > top * 1.005:
                bias, st, target, stop, lv = "bull", "confirmed", top + ht, bot, top
            elif c[-1] < bot * 0.995:
                bias, st, target, stop, lv = "bear", "confirmed", bot - ht, top, bot
            else:
                bias, st, target, stop, lv = "neutral", "forming", top, bot, None
            add("range", "박스권", "박스권", bias, st, 58 + 4 * min(tt + bt, 8), target, stop,
                [[s0, top, n - 1, top, "res"], [s0, bot, n - 1, bot, "sup"]], [], None, lv)

    # 5) 깃발 (최근 30일 안의 급등·급락 뒤 5~20일 조정)
    for bias in ("bull", "bear"):
        best = None
        for j in range(n - 30, n - 5):
            for i in range(max(j - 15, 0), j - 3):
                mv = c[j] / c[i] - 1
                if (bias == "bull" and mv >= 0.15) or (bias == "bear" and mv <= -0.15):
                    if best is None or abs(mv) > abs(best[2]):
                        best = (i, j, mv)
        if not best:
            continue
        i, j, mv = best
        seg = range(j, n)
        if not 5 <= len(seg) <= 20:
            continue
        pole = abs(c[j] - c[i])
        if bias == "bull":
            retr = (c[j] - min(l[j:])) / pole
            if retr > 0.5:
                continue
            a_, b_, _ = fit([(x, h[x]) for x in seg])
            if a_ > 0:
                continue
            line = lambda x, a_=a_, b_=b_: a_ * x + b_
            target, stop, name, k = line(n - 1) + pole, min(l[j:]) * 0.99, "상승 깃발", "bull_flag"
        else:
            retr = (max(h[j:]) - c[j]) / pole
            if retr > 0.5:
                continue
            a_, b_, _ = fit([(x, l[x]) for x in seg])
            if a_ < 0:
                continue
            line = lambda x, a_=a_, b_=b_: a_ * x + b_
            target, stop, name, k = line(n - 1) - pole, max(h[j:]) * 1.01, "하락 깃발", "bear_flag"
        st, brk = _status(bias, c, h, l, line, j + 2, target, stop)
        if st:
            add(k, name, "지속형", bias, st, 66 + int((0.5 - retr) * 30), target, stop,
                [[i, c[i], j, c[j], "pole"], [j, line(j), n - 1, line(n - 1), "res" if bias == "bull" else "sup"]],
                [], brk, line(n - 1))

    # 6) 둥근 바닥 (최근 100일 종가에 2차 곡선)
    m = min(100, n)
    ys = c[-m:]
    xs = [x / (m - 1) for x in range(m)]
    sx = [sum(x ** p for x in xs) for p in range(5)]
    sxy = [sum((x ** p) * y for x, y in zip(xs, ys)) for p in range(3)]
    A_ = [[sx[4], sx[3], sx[2]], [sx[3], sx[2], sx[1]], [sx[2], sx[1], sx[0]]]
    try:
        coef = _solve3(A_, [sxy[2], sxy[1], sxy[0]])
    except ZeroDivisionError:
        coef = None
    if coef and coef[0] > 0:
        a2, b2, c2 = coef
        vx = -b2 / (2 * a2)
        pred = [a2 * x * x + b2 * x + c2 for x in xs]
        my = sum(ys) / m
        r2 = 1 - sum((y - p) ** 2 for y, p in zip(ys, pred)) / max(1e-9, sum((y - my) ** 2 for y in ys))
        vy = a2 * vx * vx + b2 * vx + c2
        if 0.3 <= vx <= 0.75 and r2 >= 0.65 and ys[0] >= vy * 1.1 and c[-1] >= vy * 1.08:
            rim = max(ys[:m // 5])
            st, brk = _status("bull", c, h, l, lambda i: rim, n - m // 3, rim + (rim - vy), vy * 0.97)
            if st:
                start = n - m
                curve = [[start + int(x * (m - 1)), a2 * x * x + b2 * x + c2] for x in [t / 12 for t in range(13)]]
                add("round_bottom", "둥근 바닥", "곡선형", "bull", st, 55 + int(r2 * 40), rim + (rim - vy), vy * 0.97,
                    [[start, rim, n - 1, rim, "neck"]] + [[curve[t][0], curve[t][1], curve[t + 1][0], curve[t + 1][1], "curve"] for t in range(12)],
                    [], brk, rim)

    order = {"confirmed": 0, "retest": 1, "forming": 2, "target": 3}
    pats.sort(key=lambda p: (order[p["st"]], -p["q"]))
    return pats[:3]


def _solve3(A, y):
    M = [row[:] + [v] for row, v in zip(A, y)]
    for i in range(3):
        p = max(range(i, 3), key=lambda r: abs(M[r][i]))
        M[i], M[p] = M[p], M[i]
        if abs(M[i][i]) < 1e-12:
            raise ZeroDivisionError
        for r in range(3):
            if r != i:
                f = M[r][i] / M[i][i]
                M[r] = [a - f * b for a, b in zip(M[r], M[i])]
    return [M[i][3] / M[i][i] for i in range(3)]


def _r(v, d=2):
    return None if v is None else round(v, d)


def analyze(meta, bars, dates):
    """일봉 → 화면용 압축 데이터 + 패턴 (선 좌표는 화면 창 기준으로 바꿈)"""
    o, h, l, c, v = ([b[k] for b in bars] for k in range(1, 6))
    n = len(c)
    ma20, ma50, ma200 = sma(c, 20), sma(c, 50), sma(c, 200)
    pats = detect(o, h, l, c, v)
    pos = {d: i for i, d in enumerate(b[0] for b in bars)}
    idx = [pos.get(d) for d in dates]                      # 공통 날짜 → 이 종목 인덱스
    base_i = {i: k for k, i in enumerate(idx) if i is not None}

    def tx(i):                                            # 전체 인덱스 → 화면 창 인덱스 (창 밖이면 앞쪽으로 자름)
        if i in base_i:
            return base_i[i]
        return 0 if i < (idx[0] or 0) else len(dates) - 1
    for p in pats:
        p["lines"] = [[tx(a), _r(y1), tx(b), _r(y2), kind] for a, y1, b, y2, kind in p["lines"]]
        p["pts"] = [[tx(a), _r(y), t] for a, y, t in p["pts"]]
    pick = lambda arr, d=2: [_r(arr[i], d) if i is not None else None for i in idx]
    hi52, lo52 = max(h[-252:]), min(l[-252:])
    av20 = sum(v[-20:]) / 20
    chg = lambda k: c[-1] / c[-1 - k] - 1 if n > k and c[-1 - k] else None
    return {**meta, "o": pick(o), "h": pick(h), "l": pick(l), "c": pick(c), "v": [int(v[i]) if i is not None else None for i in idx],
            "ma20": pick(ma20), "ma50": pick(ma50), "ma200": pick(ma200),
            "last": _r(c[-1]), "chg1": _r(chg(1), 5), "chg5": _r(chg(5), 5), "chg20": _r(chg(20), 5), "chg60": _r(chg(60), 5),
            "rsi": _r(rsi(c), 1), "hi52": _r(hi52), "lo52": _r(lo52), "vr": _r(v[-1] / av20, 2) if av20 else None,
            "above50": bool(ma50[-1] and c[-1] > ma50[-1]), "above200": bool(ma200[-1] and c[-1] > ma200[-1]),
            "date": bars[-1][0], "pats": pats}


# ---------------- 실적 일정 ----------------
def earnings(symbols, now, log, days=14):
    out = []
    for k in range(days):
        d = (now + timedelta(days=k)).date()
        if d.weekday() >= 5:
            continue
        try:
            rows = (_json(f"https://api.nasdaq.com/api/calendar/earnings?date={d.isoformat()}").get("data") or {}).get("rows") or []
        except Exception as e:  # noqa: BLE001
            log(f"  [경고] 실적 일정 {d} 실패: {e}")
            continue
        for r in rows:
            if r["symbol"] in symbols:
                out.append({"date": d.isoformat(), "s": r["symbol"], "time": {"time-pre-market": "장 전", "time-after-hours": "장 마감 후"}.get(r.get("time"), "시간 미정"),
                            "eps_f": r.get("epsForecast") or "", "eps_ly": r.get("lastYearEPS") or "", "n_est": r.get("noOfEsts") or "",
                            "q": r.get("fiscalQuarterEnding") or ""})
        time.sleep(0.4)
    log(f"  실적 일정: {len(out)}건 (앞으로 {days}일, 시총 상위 {TOP_N} 안)")
    return out


def us_last_closed(now_utc):
    """미국장이 마감된 가장 최근 날짜 (뉴욕 16:30 이후면 그날, 아니면 직전 평일). 서머타임: 3월 둘째 일요일 ~ 11월 첫째 일요일"""
    y = now_utc.year
    mar = datetime(y, 3, 8, 7, tzinfo=timezone.utc)
    dst_on = mar + timedelta(days=(6 - mar.weekday()) % 7)            # 3월 둘째 일요일 (미국 동부 02시 = 07시 UTC)
    nov = datetime(y, 11, 1, 6, tzinfo=timezone.utc)
    dst_off = nov + timedelta(days=(6 - nov.weekday()) % 7)           # 11월 첫째 일요일
    ny = now_utc + timedelta(hours=-4 if dst_on <= now_utc < dst_off else -5)
    d = ny.date() if ny.hour * 60 + ny.minute >= 16 * 60 + 30 else ny.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


# ---------------- 전체 ----------------
def collect(kis, log=print, global_rows=None):
    t0 = time.time()
    now = datetime.now(KST)
    uni_p = DATA / "us_universe.json"
    try:
        uni = universe(log)
        uni_p.write_text(json.dumps(uni, ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log(f"  [경고] 나스닥 종목 목록 실패 → 지난 목록 사용: {e}")
        uni = json.loads(uni_p.read_text(encoding="utf-8"))
    fails = []

    def one(m):
        bars = None
        if kis:
            for sym in dict.fromkeys([m["s"], m["s"].replace("/", "."), m["s"].replace("/", "-")]):
                try:
                    bars = kis_bars(kis, m["x"], sym)
                    if len(bars) >= 60:
                        break
                except Exception:  # noqa: BLE001
                    bars = None
        if not bars or len(bars) < 60:
            try:
                bars = yahoo_bars(m["s"])
            except Exception:  # noqa: BLE001
                bars = None
        if not bars or len(bars) < 60:
            fails.append(m["s"])
            return None
        return m, bars
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        got = [r for r in ex.map(one, uni) if r]
    log(f"  미국 일봉: {len(got)}/{len(uni)}개 {time.time() - t0:.0f}초" + (f" · 실패 {fails[:8]}" if fails else ""))

    # 공통 날짜: 미국장이 끝난 날까지(진행 중인 날의 미완성 일봉 제외), 가장 많은 종목이 가진 마지막 날짜, 최근 SHOW 거래일
    cutoff = us_last_closed(datetime.now(timezone.utc))
    got = [(m, [b for b in bars if b[0] <= cutoff]) for m, bars in got]
    got = [(m, bars) for m, bars in got if len(bars) >= 60]
    last = Counter(b[-1][0] for _, b in got).most_common(1)[0][0]
    alld = sorted({b[0] for _, bars in got for b in bars if b[0] <= last})
    cnt = Counter(b[0] for _, bars in got for b in bars)
    dates = [d for d in alld if cnt[d] >= len(got) * 0.5][-SHOW:]
    stocks = [analyze(m, [b for b in bars if b[0] <= last], dates) for m, bars in got]

    # 섹터 상장지수펀드
    sectors = []
    for sym, name in SECTOR_ETF:
        try:
            bars = kis_bars(kis, "AMS", sym, pages=2) if kis else yahoo_bars(sym, "1y")
        except Exception:  # noqa: BLE001
            try:
                bars = yahoo_bars(sym, "1y")
            except Exception:  # noqa: BLE001
                continue
        c = [b[4] for b in bars if b[0] <= last]
        ch = lambda k: c[-1] / c[-1 - k] - 1 if len(c) > k else None
        sectors.append({"s": sym, "name": name, "last": _r(c[-1]), "chg1": _r(ch(1), 5), "chg5": _r(ch(5), 5),
                        "chg20": _r(ch(20), 5), "chg60": _r(ch(60), 5), "series": [_r(x) for x in c[-60:]]})
    # 지수
    indices = []
    for sym, name in INDICES:
        try:
            bars = [b for b in yahoo_bars(sym, "1y") if b[0] <= last]
            c = [b[4] for b in bars]
            indices.append({"s": sym, "name": name, "last": _r(c[-1]), "date": bars[-1][0],
                            "chg1": _r(c[-1] / c[-2] - 1, 5), "chg5": _r(c[-1] / c[-6] - 1, 5), "chg20": _r(c[-1] / c[-21] - 1, 5),
                            "series": [_r(x) for x in c[-126:]]})
        except Exception as e:  # noqa: BLE001
            log(f"  [경고] 지수 {name} 실패: {e}")

    ern = []
    try:
        ern = earnings({s["s"] for s in stocks}, now, log)
    except Exception as e:  # noqa: BLE001
        log(f"  [경고] 실적 일정 실패: {e}")

    prim = [s["pats"][0] for s in stocks if s["pats"]]
    allp = [p for s in stocks for p in s["pats"]]
    stats = {"universe": len(uni), "loaded": len(stocks), "failed": len(fails), "patterns": len(allp),
             "with_pat": len(prim), "confirmed": sum(p["st"] in ("confirmed", "retest") for p in allp),
             "retest": sum(p["st"] == "retest" for p in allp), "forming": sum(p["st"] == "forming" for p in allp),
             "bull": sum(p["bias"] == "bull" for p in prim), "bear": sum(p["bias"] == "bear" for p in prim),
             "target": sum(p["st"] == "target" for p in allp), "low_rr": sum(1 for p in allp if p["rr"] is not None and p["rr"] < 1),
             "above50": sum(s["above50"] for s in stocks), "above200": sum(s["above200"] for s in stocks),
             "new_hi": sum(1 for s in stocks if s["last"] >= s["hi52"] * 0.995), "new_lo": sum(1 for s in stocks if s["last"] <= s["lo52"] * 1.005),
             "adv": sum(1 for s in stocks if (s["chg1"] or 0) > 0), "dec": sum(1 for s in stocks if (s["chg1"] or 0) < 0)}
    log(f"  미국 패턴: {stats['patterns']}개 ({stats['with_pat']}종목) · 확정 {stats['confirmed']} · 진행 중 {stats['forming']} · 총 {time.time() - t0:.0f}초")
    return {"captured_at": now.isoformat(), "data_date": last, "dates": dates, "stocks": stocks, "sectors": sectors,
            "indices": indices, "earnings": ern, "stats": stats, "macro": global_rows or []}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    import collect as col
    import kis as kis_mod
    env = col.load_env()
    k = kis_mod.KIS(env["KIS_APP_KEY"], env["KIS_APP_SECRET"])
    mk = col.read_json(DATA / "market.json", {}) or {}
    (DATA / "us.json").write_text(json.dumps(collect(k, global_rows=[g for g in mk.get("global", []) if g["group"] in ("위험·금리", "환율·원자재")]), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
