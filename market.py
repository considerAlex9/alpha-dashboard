"""시장 데이터 수집.

- 국내 지수·종목 일봉, 투자자별 매매동향, 공매도: 한국투자증권 오픈 API (키가 있을 때, 기본)
  → 한국투자증권 호출이 실패하거나 키가 없으면 네이버 증권으로 대신 받는다
- 해외 지수·금리·환율·원자재: 야후 파이낸스 chart API

한 곳이 실패해도 나머지는 계속 모으고, 포트폴리오 수집(collect.py)은 절대 막지 않는다.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
WORKERS = 4            # 한국투자증권 동시 조회 수 (초당 한도는 kis.py 가 지킴)
REUSE_HOURS = 6        # 이 시간 안에 받아 둔 페어 분석·시장경보 보강값은 재사용
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# (네이버 심볼, 이름, 한국투자증권 업종코드)
DOMESTIC = [("KOSPI", "코스피", "0001"), ("KOSDAQ", "코스닥", "1001"), ("KPI200", "코스피200", "2001")]

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


def _compact(v):
    return (int(v) if v == int(v) else round(v, 2)) if v is not None else None


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


KIND = "https://kind.krx.co.kr/investwarn/investattentwarnrisky.do"
KIND_KIND = {1: ("caution", "invstcautnisu_sub"), 2: ("warning", "invstwarnisu_sub"), 3: ("risk", "invstriskisu_sub")}
KIND_MARKET = {"유가증권": "코스피", "코스닥": "코스닥", "코넥스": "코넥스"}


def kind_warnings(days=7):
    """한국거래소 KIND 시장경보 — 투자주의(최근 days일 지정), 투자경고·투자위험(미해제)"""
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    ua = {**UA, "Referer": f"{KIND}?method=investattentwarnriskyMain"}
    op.open(urllib.request.Request(f"{KIND}?method=investattentwarnriskyMain", headers=ua), timeout=20).read()
    today = datetime.now(KST)
    out = {}
    for menu, (key, fwd) in KIND_KIND.items():
        start = today - timedelta(days=days if key == "caution" else 120)
        body = urllib.parse.urlencode({
            "method": "investattentwarnriskySub", "currentPageSize": "300", "pageIndex": "1", "orderMode": "4",
            "orderStat": "D", "searchCodeType": "", "searchCorpName": "", "marketType": "", "repIsuSrtCd": "",
            "menuIndex": str(menu), "forward": fwd,
            "startDate": start.strftime("%Y-%m-%d"), "endDate": today.strftime("%Y-%m-%d")}).encode()
        html = op.open(urllib.request.Request(KIND, data=body, headers={**ua, "X-Requested-With": "XMLHttpRequest"}),
                       timeout=30).read().decode("utf-8", "replace")
        if "잠시 후 다시" in html:
            raise RuntimeError("KIND 응답 거부")
        rows = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
            m = re.search(r"companysummary_open\('(\d+)'\)", tr)
            if not m:
                continue
            tds = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", t)).strip() for t in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            mk = re.search(r"alt='(유가증권|코스닥|코넥스)'", tr)
            row = {"code": m.group(1) + "0", "name": tds[1], "market": KIND_MARKET.get(mk.group(1), "") if mk else ""}
            if key == "caution":
                row.update(reason=tds[2], disclosed=tds[3], designated=tds[4])
            else:
                row.update(disclosed=tds[2], designated=tds[3], released=None if tds[4] in ("-", "") else tds[4])
            rows.append(row)
        if key != "caution":
            rows = [r for r in rows if not r["released"]]      # 경고·위험은 아직 해제되지 않은 것만
        out[key] = rows
    return out


def _kis_flows(kis, log):
    """시장별 투자자 순매수 — 최근 영업일 여러 날을 한 번에 받는다 (억원)"""
    today = datetime.now(KST).strftime("%Y%m%d")
    days = {}
    for mk, key in (("KSP", "KOSPI"), ("KSQ", "KOSDAQ")):
        for r in kis.market_investor(mk, today):
            b = r["stck_bsop_date"]
            days.setdefault(b, {"bizdate": b})[key] = {
                "개인": round(_num(r["prsn_ntby_tr_pbmn"]) / 100), "외국인": round(_num(r["frgn_ntby_tr_pbmn"]) / 100),
                "기관": round(_num(r["orgn_ntby_tr_pbmn"]) / 100)}
    return [days[b] for b in sorted(days) if "KOSPI" in days[b]]


def _kis_stock_extra(kis, code):
    """종목별 외국인·기관 5일 순매수(원)와 공매도 거래 비중"""
    inv = [r for r in kis.stock_investor(code) if r.get("frgn_ntby_tr_pbmn") not in (None, "")][:5]
    sh = kis.short_sale(code)[:5]
    ratios = [_num(r["ssts_vol_rlim"]) for r in sh]
    return {"foreign": sum(_num(r["frgn_ntby_tr_pbmn"]) for r in inv) * 1e6,
            "organ": sum(_num(r["orgn_ntby_tr_pbmn"]) for r in inv) * 1e6,
            "short_ratio1": ratios[0] / 100 if ratios else None,
            "short_ratio5": sum(ratios) / len(ratios) / 100 if ratios else None,
            "short_amt5": sum(_num(r["ssts_tr_pbmn"]) for r in sh)}


def collect(positions, kis_keys=None, log=print, prev=None):
    """prev: 지난번 market.json — REUSE_HOURS 안이면 느린 부분을 재사용한다"""
    now = datetime.now(KST)
    out = {"captured_at": now.isoformat(), "source": "네이버 증권", "domestic": [], "flows": {}, "flows_days": [],
           "global": [], "holdings": [], "errors": []}

    def safe(label, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"{label}: {type(e).__name__} {e}"[:200])
            return None

    kis = None
    if kis_keys and all(kis_keys):
        import kis as kis_mod
        kis = safe("한국투자증권 인증", lambda: kis_mod.KIS(*kis_keys))
        if kis:
            out["source"] = "한국투자증권"

    def bars_of(sym_naver, kis_fn):
        """한국투자증권 우선, 실패하면 네이버"""
        if kis:
            b = safe(f"한투 일봉 {sym_naver}", kis_fn)
            if b:
                return b
        return safe(f"네이버 일봉 {sym_naver}", lambda: daily_bars(sym_naver))

    # 국내 지수
    idx = {}
    for sym, name, up in DOMESTIC:
        bars = bars_of(sym, lambda u=up: kis.index_daily(u))
        if bars:
            idx[sym] = bars
            out["domestic"].append({"symbol": sym, "name": name, "bars": [b[:5] for b in bars],
                                    "chg1": _chg(bars, 1), "chg5": _chg(bars, 5), "chg20": _chg(bars, 20),
                                    "chg60": _chg(bars, 60)})

    # 투자자별 순매수
    days = safe("한투 시장 수급", lambda: _kis_flows(kis, log)) if kis else None
    if days:
        out["flows_days"] = days[-20:]
        last = days[-1]
        out["flows"] = {k: {"bizdate": last["bizdate"], **last[k]} for k in ("KOSPI", "KOSDAQ") if k in last}
    else:
        for sym in ("KOSPI", "KOSDAQ"):
            f = safe(f"네이버 수급 {sym}", lambda s=sym: flows(s))
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

    # 보유 종목별 시황 — 베타는 KOSPI 대비 일간 수익률 회귀 (여러 종목을 동시에 조회)
    kospi = idx.get("KOSPI")
    t0 = time.time()

    def one_holding(p):
        code = p["company_symbol"]
        bars = bars_of(code, lambda c=code: kis.stock_daily(c))
        extra = (safe(f"한투 수급·공매도 {code}", lambda c=code: _kis_stock_extra(kis, c)) if kis else None) \
            or safe(f"네이버 수급 {code}", lambda c=code: stock_flows(c)) or {}
        row = {"symbol": code, "name": p.get("company_alias") or p["company_name"], "side": p["side"],
               "weight": p["weight_pct"], "market_value": p["market_value"], **extra}
        if bars:
            row["_bars"] = bars
            closes = [b[4] for b in bars]
            ma20 = sum(closes[-20:]) / min(20, len(closes))
            row.update({"close": closes[-1], "date": bars[-1][0],
                        "chg1": _chg(bars, 1), "chg5": _chg(bars, 5), "chg20": _chg(bars, 20),
                        "disparity20": closes[-1] / ma20 - 1,
                        "high52": max(b[2] for b in bars), "low52": min(b[3] for b in bars),
                        "beta60": beta(bars, kospi, 60) if kospi else None,
                        "beta252": beta(bars, kospi, 252) if kospi else None})
        return row

    with ThreadPoolExecutor(max_workers=WORKERS if kis else 2) as ex:
        out["holdings"] = list(ex.map(one_holding, positions))
    log(f"  · 보유종목 {len(positions)}개 {time.time() - t0:.0f}초")

    # 몇 시간 안에 받아 둔 값이 있으면 그대로 쓴다 (페어 분석 종목·시장경보 종목 시가총액은 하루 중 크게 안 변함)
    fetched = ((prev or {}).get("pairs") or {}).get("captured_at")          # 페어 묶음을 '새로 수집한' 시각 기준
    prev_age = (now - datetime.fromisoformat(fetched)) if fetched else None
    reuse = prev if prev_age is not None and prev_age < timedelta(hours=REUSE_HOURS) else None

    # ---------- 시장경보 (KIND) ----------
    t0 = time.time()
    warn = safe("KIND 시장경보", kind_warnings)
    if warn:
        if kis:     # 시가총액·등락률 보강 — 이전에 받아 둔 종목은 재사용
            known = {r["code"]: r for rows in ((reuse or {}).get("warnings") or {}).values() for r in rows if r.get("mcap")}
            todo = []
            for rows in warn.values():
                for r in rows:
                    if r["code"] in known:
                        r.update({k: known[r["code"]].get(k) for k in ("mcap", "chg1", "sector")})
                    else:
                        todo.append(r)

            def enrich(r):
                info = safe(f"한투 현재가 {r['code']}", lambda c=r["code"]: kis.price(c))
                if info:
                    r.update(mcap=info["mcap"], chg1=info["chg1"], sector=info["sector"])
            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                list(ex.map(enrich, todo))
        out["warnings"] = warn
    log(f"  · 시장경보 {time.time() - t0:.0f}초")

    # ---------- 페어 분석용 종목 묶음: 보유 종목 + 코스피·코스닥 시총 상위 ----------
    t0 = time.time()
    held_bars = {h["symbol"]: h.pop("_bars") for h in out["holdings"] if "_bars" in h}
    if kis and reuse and (reuse.get("pairs") or {}).get("stocks"):
        # 재사용: 지난번 묶음을 그대로 두고, 보유 종목만 방금 받은 일봉으로 바꾸거나 새로 넣는다
        pairs = reuse["pairs"]
        dates = pairs["dates"]
        for p in positions:
            code, bars = p["company_symbol"], held_bars.get(p["company_symbol"])
            if not bars:
                continue
            c = {x[0]: x[4] for x in bars}
            st = pairs["stocks"].get(code) or {"name": p.get("company_alias") or p["company_name"], "sector": "", "market": "",
                                                "mcap": None, "chg1": None, "warn": "00"}
            tvs = [x[6] for x in bars if len(x) > 6]
            st.update({"tv3": sum(tvs[-3:]) / 3 if len(tvs) >= 3 else st.get("tv3"),
                       "tv20": sum(tvs[-20:]) / 20 if len(tvs) >= 20 else st.get("tv20"),
                       "beta60": beta(bars, kospi, 60) if kospi else None, "beta252": beta(bars, kospi, 252) if kospi else None,
                       "close": [_compact(c.get(d)) for d in dates]})
            pairs["stocks"][code] = st
        out["pairs"] = pairs
    elif kis:
        universe = {p["company_symbol"]: p.get("company_alias") or p["company_name"] for p in positions}
        for ic in ("0001", "1001"):
            for code, name in safe(f"한투 시총순위 {ic}", lambda c=ic: kis.market_cap_top(c)) or []:
                universe.setdefault(code, name)

        def one_stock(item):
            code, name = item
            bars = held_bars.get(code) or bars_of(code, lambda c=code: kis.stock_daily(c))      # 실패하면 네이버
            info = safe(f"한투 현재가 {code}", lambda c=code: kis.price(c)) or {}
            if not bars:
                return None
            tvs = [b[6] for b in bars if len(b) > 6]
            return code, {"name": name, "sector": info.get("sector", ""), "market": info.get("market", ""),
                          "mcap": info.get("mcap"), "chg1": info.get("chg1"), "warn": info.get("warn", "00"),
                          "tv3": sum(tvs[-3:]) / 3 if len(tvs) >= 3 else None,
                          "tv20": sum(tvs[-20:]) / 20 if len(tvs) >= 20 else None,
                          "beta60": beta(bars, kospi, 60) if kospi else None,
                          "beta252": beta(bars, kospi, 252) if kospi else None,
                          "_c": {b[0]: b[4] for b in bars}}
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            stocks = dict(r for r in ex.map(one_stock, universe.items()) if r)
        dates = sorted(set().union(*(st["_c"] for st in stocks.values())))[-260:] if stocks else []
        for st in stocks.values():
            c = st.pop("_c")
            st["close"] = [_compact(c.get(d)) for d in dates]
        out["pairs"] = {"dates": dates, "stocks": stocks, "captured_at": now.isoformat()}
    if out.get("pairs"):
        for h in out["holdings"]:      # 보유 종목 시장경보 코드도 붙여 둔다
            if h["symbol"] in out["pairs"]["stocks"]:
                h["warn"] = out["pairs"]["stocks"][h["symbol"]].get("warn", "00")
    log(f"  · 페어 분석 {'재사용' if reuse else '새로 수집'} {time.time() - t0:.0f}초")

    if out["errors"]:
        log(f"  시장 데이터 일부 실패 {len(out['errors'])}건: {out['errors'][:3]}")
    log(f"  시장 데이터({out['source']}): 국내 {len(out['domestic'])} · 해외 {len(out['global'])} · "
        f"보유종목 {len(out['holdings'])} · 수급 {len(out['flows_days'])}일 · "
        f"페어 {len(out.get('pairs', {}).get('stocks', {}))}종목 · 시장경보 "
        f"{'/'.join(str(len(v)) for v in out.get('warnings', {}).values()) or '없음'}")
    return out
