"""국내 기술적 분석 — 코스피 전 종목(보통주) + 코스닥 시가총액 상위 100 종목.

- 종목 목록·시가총액: 네이버 증권 시가총액 순위 (ETF·ETN·스팩·우선주·거래정지 종목 제외)
- 일봉: 한국투자증권 일봉(수정주가) 400거래일 (실패하면 네이버) · 장 마감(15:40) 전이면 오늘 미완성 일봉은 뺌. 마지막 날 거래량이 0이면 거래정지로 보고 제외
- 신호: 골든크로스(20·60일선), 장기 골든크로스(60·120일선), 정배열·역배열, 200일선 돌파·이탈 (데드크로스는 잡음이 많아 뺌),
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


def signals(o, h, l, c, v):
    n = len(c)
    m5, m20, m60, m120, m200 = (us.sma(c, k) for k in (5, 20, 60, 120, 200))
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
    return out, trend, (m20, m60, m120, m200), b, r


def analyze(meta, bars):
    o, h, l, c, v = ([b[k] for b in bars] for k in range(1, 6))
    n = len(c)
    sig, trend, (m20, m60, m120, m200), b, r = signals(o, h, l, c, v)
    pats = us.detect(o, h, l, c, v)
    base = n - SHOW
    w = lambda i: max(0, i - base)
    for p in pats:
        p["lines"] = [[w(a), round(y1), w(b_), round(y2), k] for a, y1, b_, y2, k in p["lines"]]
        p["pts"] = [[w(a), round(y), t] for a, y, t in p["pts"]]
        for k in ("target", "stop", "level"):
            if p.get(k) is not None:
                p[k] = round(p[k])
    iv = lambda a: [None if x is None else int(round(x)) for x in a[-SHOW:]]
    chg = lambda k: round(c[-1] / c[-1 - k] - 1, 5) if n > k and c[-1 - k] else None
    av20 = sum(v[-21:-1]) / 20
    return {**meta, "date": bars[-1][0], "o": iv(o), "h": iv(h), "l": iv(l), "c": iv(c), "v": [int(x // 1000) for x in v[-SHOW:]],
            "ma20": iv(m20), "ma60": iv(m60), "ma120": iv(m120), "ma200": iv(m200),
            "last": c[-1], "chg1": chg(1), "chg5": chg(5), "chg20": chg(20), "chg60": chg(60),
            "rsi": round(r, 1) if r is not None else None, "hi52": max(h[-250:]), "lo52": min(l[-250:]),
            "vr": round(v[-1] / av20, 2) if av20 else None, "trend": trend, "sig": sig, "pats": pats,
            "base": [w(b[0]), w(b[1]), round(b[2]), round(b[3]), b[4]] if b and b[1] >= base else None}


def collect(kis=None, held=None, log=print):
    t0 = time.time()
    held = held or {}
    uni = universe(log)
    fails, halted = [], []
    now = datetime.now(KST)
    today, closed = now.strftime("%Y-%m-%d"), now.hour * 60 + now.minute >= 15 * 60 + 40

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
            return analyze({**m, "held": held.get(m["s"])}, bars)
        except Exception as e:  # noqa: BLE001
            fails.append(f"{m['s']}:{e}")
            return None
    with ThreadPoolExecutor(max_workers=4 if kis else 6) as ex:
        stocks = [r for r in ex.map(one, uni) if r]
    last = max(s["date"] for s in stocks)
    stocks = [s for s in stocks if s["date"] == last]              # 거래정지 등으로 오늘 일봉이 없는 종목 제외
    import market
    ref = market.daily_bars("005930", 400)                          # 날짜 축 (삼성전자 기준)
    dates = [b[0] for b in ref if b[0] <= last][-SHOW:]
    cnt = {}
    for s in stocks:
        for x in s["sig"]:
            cnt[x["k"]] = cnt.get(x["k"], 0) + 1
    log(f"  기술적 분석: {len(stocks)}종목 · 거래량 0(거래정지) 제외 {len(halted)} {halted[:6]} · 실패 {len(fails)} {fails[:6]} · " + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items())) +
        f" · 패턴 {sum(len(s['pats']) for s in stocks)} · {time.time() - t0:.0f}초")
    return {"captured_at": datetime.now(KST).isoformat(), "date": last, "dates": dates, "stocks": stocks,
            "counts": cnt, "failed": len(fails)}


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
