"""시장 데이터 수집 (키 없이 쓰는 공개 시세).

- 국내 지수·종목 일봉: 네이버 fchart
- 투자자별 매매동향: 네이버 모바일 증권
- 해외 지수·금리·환율·원자재: 야후 파이낸스 chart API

한 곳이 실패해도 나머지는 계속 모으고, 포트폴리오 수집(collect.py)은 절대 막지 않는다.
"""
import json
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

DOMESTIC = [("KOSPI", "코스피"), ("KOSDAQ", "코스닥"), ("KPI200", "코스피200")]

# (야후 심볼, 이름, 묶음, 단위 표기)
GLOBAL = [
    ("^GSPC", "S&P 500", "미국 증시", ""),
    ("^IXIC", "나스닥", "미국 증시", ""),
    ("^DJI", "다우존스", "미국 증시", ""),
    ("^SOX", "필라델피아 반도체", "미국 증시", ""),
    ("^N225", "닛케이 225", "아시아 증시", ""),
    ("000001.SS", "상해종합", "아시아 증시", ""),
    ("^HSI", "항셍", "아시아 증시", ""),
    ("^VIX", "VIX 변동성", "위험·금리", ""),
    ("^TNX", "미국 10년물 금리", "위험·금리", "%"),
    ("KRW=X", "원/달러 환율", "환율·원자재", "원"),
    ("DX-Y.NYB", "달러 인덱스", "환율·원자재", ""),
    ("CL=F", "WTI 유가", "환율·원자재", "$"),
    ("GC=F", "금", "환율·원자재", "$"),
    ("BTC-USD", "비트코인", "환율·원자재", "$"),
]


def _get(url, raw=False, tries=3):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                body = r.read()
                return body if raw else json.loads(body)
        except Exception as e:  # noqa: BLE001 — 외부 시세는 실패해도 넘어간다
            last = e
            time.sleep(1.5 * (i + 1))
    raise last


def _num(s):
    return float(str(s).replace(",", "").replace("+", "").replace("%", "")) if s not in (None, "", "-") else 0.0


def daily_bars(symbol, count=260):
    """네이버 fchart 일봉 → [[YYYY-MM-DD, 시가, 고가, 저가, 종가, 거래량], ...]"""
    xml = _get(f"https://fchart.stock.naver.com/sise.nhn?symbol={symbol}&timeframe=day&count={count}&requestType=0",
               raw=True).decode("euc-kr", "replace")
    out = []
    for d in re.findall(r'data="([^"]+)"', xml):
        dt, o, h, l, c, v = d.split("|")
        out.append([f"{dt[:4]}-{dt[4:6]}-{dt[6:]}", float(o), float(h), float(l), float(c), float(v)])
    return out


def _returns(bars):
    return {bars[i][0]: bars[i][4] / bars[i - 1][4] - 1 for i in range(1, len(bars)) if bars[i - 1][4]}


def beta(stock_bars, index_bars, n):
    rs, rm = _returns(stock_bars), _returns(index_bars)
    days = sorted(set(rs) & set(rm))[-n:]
    if len(days) < max(20, n // 2):
        return None
    x = [rm[d] for d in days]
    y = [rs[d] for d in days]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    var = sum((a - mx) ** 2 for a in x)
    return round(sum((a - mx) * (b - my) for a, b in zip(x, y)) / var, 3) if var else None


def _chg(bars, n):
    return bars[-1][4] / bars[-1 - n][4] - 1 if len(bars) > n and bars[-1 - n][4] else None


def flows(index):
    """투자자별 순매수(억원) — 네이버는 당일 1건만 주므로 history 는 collect 쪽에서 누적"""
    d = _get(f"https://m.stock.naver.com/api/index/{index}/trend")
    return {"bizdate": d["bizdate"], "개인": _num(d["personalValue"]),
            "외국인": _num(d["foreignValue"]), "기관": _num(d["institutionalValue"])}


def stock_flows(code, days=5):
    """종목별 외국인·기관 순매수 — 수량 × 그날 종가로 금액(원) 환산해 합산"""
    rows = _get(f"https://m.stock.naver.com/api/stock/{code}/trend?pageSize={days}")
    f = sum(_num(r["foreignerPureBuyQuant"]) * _num(r["closePrice"]) for r in rows)
    o = sum(_num(r["organPureBuyQuant"]) * _num(r["closePrice"]) for r in rows)
    return {"foreign": f, "organ": o, "foreign_hold": _num(rows[0].get("foreignerHoldRatio")) if rows else None}


def yahoo(symbol):
    enc = urllib.request.quote(symbol, safe="")
    last = None
    for host in ("query1", "query2"):
        try:
            d = _get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{enc}?range=1y&interval=1d")["chart"]["result"][0]
            break
        except Exception as e:  # noqa: BLE001
            last = e
    else:
        raise last
    tz = timezone(timedelta(seconds=d["meta"].get("gmtoffset", 0)))
    closes = d["indicators"]["quote"][0]["close"]
    series = [[datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d"), round(c, 4)]
              for t, c in zip(d["timestamp"], closes) if c is not None]
    return series, d["meta"].get("regularMarketPrice")


def collect(positions, log=print):
    now = datetime.now(KST)
    out = {"captured_at": now.isoformat(), "domestic": [], "flows": {}, "global": [], "holdings": [], "errors": []}

    def safe(label, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"{label}: {type(e).__name__} {e}"[:200])
            return None

    # 국내 지수
    idx = {}
    for sym, name in DOMESTIC:
        bars = safe(f"지수 {name}", lambda s=sym: daily_bars(s))
        if bars:
            idx[sym] = bars
            out["domestic"].append({"symbol": sym, "name": name, "bars": [b[:5] for b in bars],
                                    "chg1": _chg(bars, 1), "chg5": _chg(bars, 5), "chg20": _chg(bars, 20),
                                    "chg60": _chg(bars, 60)})
    for sym in ("KOSPI", "KOSDAQ"):
        f = safe(f"수급 {sym}", lambda s=sym: flows(s))
        if f:
            out["flows"][sym] = f

    # 해외
    for sym, name, group, unit in GLOBAL:
        r = safe(f"해외 {name}", lambda s=sym: yahoo(s))
        if r:
            series, last = r
            closes = [c for _, c in series]
            def ch(n):
                return closes[-1] / closes[-1 - n] - 1 if len(closes) > n and closes[-1 - n] else None
            out["global"].append({"symbol": sym, "name": name, "group": group, "unit": unit,
                                  "price": last if last is not None else closes[-1], "date": series[-1][0],
                                  "chg1": ch(1), "chg5": ch(5), "chg20": ch(21), "series": series[-130:]})
        time.sleep(0.2)

    # 보유 종목별 시황 — 베타는 KOSPI 대비 일간 수익률 회귀
    kospi = idx.get("KOSPI")
    for p in positions:
        code = p["company_symbol"]
        bars = safe(f"일봉 {code}", lambda c=code: daily_bars(c))
        fl = safe(f"수급 {code}", lambda c=code: stock_flows(c)) or {}
        row = {"symbol": code, "name": p.get("company_alias") or p["company_name"], "side": p["side"],
               "weight": p["weight_pct"], "market_value": p["market_value"], **fl}
        if bars:
            closes = [b[4] for b in bars]
            ma20 = sum(closes[-20:]) / min(20, len(closes))
            row.update({"close": closes[-1], "date": bars[-1][0],
                        "chg1": _chg(bars, 1), "chg5": _chg(bars, 5), "chg20": _chg(bars, 20),
                        "disparity20": closes[-1] / ma20 - 1,
                        "high52": max(b[2] for b in bars), "low52": min(b[3] for b in bars),
                        "beta60": beta(bars, kospi, 60) if kospi else None,
                        "beta252": beta(bars, kospi, 252) if kospi else None})
        out["holdings"].append(row)
        time.sleep(0.1)

    if out["errors"]:
        log(f"  시장 데이터 일부 실패 {len(out['errors'])}건: {out['errors'][:3]}")
    log(f"  시장 데이터: 국내 {len(out['domestic'])} · 해외 {len(out['global'])} · 보유종목 {len(out['holdings'])}")
    return out
