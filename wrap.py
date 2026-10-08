"""장 마감 시황 — 거래일 16:30 이후 첫 자동 업데이트 때 그날 국내 증시를 한 장으로 정리한다. (규칙 기반, 무료)

재료
  - 한국투자증권: 코스피·코스닥 지수·거래대금·상승/하락 종목 수, 5분 단위 장중 흐름, 거래대금 상위 종목, 국고채·미국 금리
  - 이미 모아 둔 것: 투자자별 순매수, 테마 등락판, 해외 지표, 매크로 뉴스 브리핑, 경제 일정, 보유 종목 공시
"""
import json
import re
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KEEP = 10
ETF = re.compile(r"^(KODEX|TIGER|ACE|RISE|SOL|KBSTAR|HANARO|PLUS|KOSEF|ARIRANG|TIMEFOLIO|KIWOOM|1Q|BNK|WON|ITF|KoAct|TREX|"
                 r"마이다스|에셋플러스|TRUSTON|UNICORN|파워|히어로즈|VITA|FOCUS|HK|KCGI|마이티|DAISHIN|코덱스)|ETN|레버리지|인버스|선물")


def eok(v):
    """억원 → '2.6조' / '6,684억'"""
    if v is None:
        return "—"
    s = "+" if v > 0 else "−" if v < 0 else ""
    a = abs(v)
    return f"{s}{a / 10000:.1f}조" if a >= 10000 else f"{s}{a:,.0f}억"


def pct(v, d=2):
    return "—" if v is None else f"{'+' if v > 0 else '−' if v < 0 else ''}{abs(v) * 100:.{d}f}%"


def sg(v, fmt):
    """부호를 −/+ 로 붙인 숫자"""
    return ("+" if v > 0 else "−" if v < 0 else "") + format(abs(v), fmt)


def jo(won):
    return f"{won / 1e12:.1f}조"


def _theme_of():
    T = json.loads((ROOT / "market_themes.json").read_text(encoding="utf-8"))
    return {c: t for t, m in T.items() if not t.startswith("_") for c in m}


def _streak(days, who="외국인", mk="KOSPI"):
    """최근부터 같은 방향(순매수/순매도)이 며칠 이어졌는지"""
    vals = [d[mk][who] for d in days if mk in d]
    if not vals or not vals[-1]:
        return 0
    sign, n = vals[-1] > 0, 0
    for v in reversed(vals):
        if (v > 0) != sign or v == 0:
            break
        n += 1
    return n


def build(kis, mk, news, cal, disc, positions, now, log=print):
    today = now.strftime("%Y-%m-%d")
    held = {p["company_symbol"]: p["side"] for p in positions}
    theme_of = _theme_of()

    # ---------- 지수 ----------
    idx = {}
    for key, code, name in (("KOSPI", "0001", "코스피"), ("KOSDAQ", "1001", "코스닥")):
        p = kis.index_price(code)
        prev = p["close"] / (1 + p["chg1"]) if p["chg1"] > -1 else None
        mins = kis.index_minutes(code)
        bar = next((b for d in mk.get("domestic", []) if d["symbol"] == key for b in d["bars"] if b[0] == today), None)
        hi = max([v for _, v in mins] + ([bar[2]] if bar else [])) if mins or bar else None
        lo = min([v for _, v in mins] + ([bar[3]] if bar else [])) if mins or bar else None
        idx[key] = {**p, "name": name, "prev": prev, "open": bar[1] if bar else (mins[0][1] if mins else None), "high": hi, "low": lo,
                    "intraday": [[f"{t[:2]}:{t[2:]}", round(v / prev - 1, 5)] for t, v in mins] if prev else []}

    # ---------- 금리·환율 ----------
    rates = kis.rates()
    G = {g["symbol"]: g for g in mk.get("global", [])}
    fx = G.get("KRW=X")
    fx_chg = fx["price"] - fx["price"] / (1 + fx["chg1"]) if fx and fx.get("chg1") is not None else None
    tiles = [
        {"label": "코스피", "value": f"{idx['KOSPI']['close']:,.2f}", "chg": pct(idx["KOSPI"]["chg1"]), "dir": idx["KOSPI"]["chg1"]},
        {"label": "코스닥", "value": f"{idx['KOSDAQ']['close']:,.2f}", "chg": pct(idx["KOSDAQ"]["chg1"]), "dir": idx["KOSDAQ"]["chg1"]},
    ]
    if fx:
        tiles.append({"label": "원/달러", "value": f"{fx['price']:,.1f}", "chg": sg(fx_chg, ",.1f") + "원" if fx_chg is not None else "", "dir": fx_chg or 0,
                      "note": "16시 무렵"})
    for nm, label in (("국고채 3년", "국고 3년"), ("국고채 10년", "국고 10년"), ("미국 10년T-NOTE 수익률", "미국 10년")):
        if nm in rates:
            v, c = rates[nm]
            tiles.append({"label": label, "value": f"{v:.3f}%", "chg": sg(c * 100, ".1f") + "bp", "dir": c})

    # ---------- 수급 ----------
    biz = now.strftime("%Y%m%d")
    days = mk.get("flows_days", [])
    flows = next((d for d in days if d["bizdate"] == biz), None)

    # ---------- 테마 ----------
    trows = (mk.get("themes") or {}).get("rows", []) if (mk.get("themes") or {}).get("date") == today else []
    strong = [t for t in trows if t["chg1"] > 0][:4]
    weak = [t for t in reversed(trows) if t["chg1"] < 0][:4]

    # ---------- 거래대금 상위·특징주 ----------
    movers = []
    for ic, mname in (("0001", "코스피"), ("1001", "코스닥")):
        try:
            rows = [r for r in kis.value_rank(ic) if not ETF.search(r["name"])]
        except Exception as e:  # noqa: BLE001
            log(f"  [경고] 거래대금 상위 {mname} 실패: {e}")
            rows = []
        for i, r in enumerate(rows):
            movers.append({**r, "market": mname, "rank": i + 1, "theme": theme_of.get(r["code"]), "held": held.get(r["code"])})
    top_value = sorted(movers, key=lambda r: -r["value"])[:10]
    hot = sorted([r for r in movers if abs(r["chg1"]) >= 0.05], key=lambda r: -abs(r["chg1"]))[:8]

    # 오늘 09:00 이후 텔레그램 글 (원문은 싣지 않고 요약에만 씀)
    import digest
    import news as tg
    errs = []
    posts = sorted(tg.fetch_posts(tg.MARKET_CHANNELS, datetime.combine(now.date(), dtime(9, 0), now.tzinfo), now, errs),
                   key=lambda p: p["time"])

    def mention(name):
        for p in posts:
            if name in p["text"] and not digest.KR_WRAP.search(p["text"][:40]):
                r = tg.parse(p["text"])
                if r:
                    return {"title": r["title"]}
        return None
    featured = []
    for r in (hot + top_value[:4]):
        if any(f["code"] == r["code"] for f in featured):
            continue
        featured.append({**r, "news": mention(r["name"])})
    featured = featured[:10]

    # ---------- 문장 만들기 ----------
    K, Q = idx["KOSPI"], idx["KOSDAQ"]
    word = lambda v: "상승" if v > 0 else "하락" if v < 0 else "보합"

    def where(i):
        if not (i["high"] and i["low"]) or i["high"] == i["low"]:
            return ""
        r = (i["close"] - i["low"]) / (i["high"] - i["low"])
        place = "저가 부근" if r < .25 else "고가 부근" if r > .75 else "장중 고가와 저가 사이"
        drop = i["close"] / i["high"] - 1
        return (f" 장중 고가 {i['high']:,.2f} · 저가 {i['low']:,.2f}, {place}에서 마감"
                + (f"(고가 대비 {pct(drop)})" if drop <= -0.01 else "") + ".")
    glance = [f"지수 — 코스피 {K['close']:,.2f}({pct(K['chg1'])}), 코스닥 {Q['close']:,.2f}({pct(Q['chg1'])})." + where(K)]
    if flows:
        f1, f2 = flows["KOSPI"], flows.get("KOSDAQ") or {}
        st = _streak(days)
        glance.append(f"수급 — 코스피 외국인 {eok(f1['외국인'])} · 기관 {eok(f1['기관'])} · 개인 {eok(f1['개인'])}"
                      + (f", 코스닥 외국인 {eok(f2.get('외국인'))} · 기관 {eok(f2.get('기관'))}" if f2 else "")
                      + (f". 코스피 외국인 {st}거래일 연속 순{'매수' if f1['외국인'] > 0 else '매도'}" if st >= 2 else "") + ".")
    ratio = K["down"] / K["up"] if K["up"] else None
    glance.append(f"시장 내부 — 코스피 상승 {K['up']} · 하락 {K['down']}, 코스닥 상승 {Q['up']} · 하락 {Q['down']}"
                  + (f" (코스피 하락 종목이 상승의 {ratio:.1f}배)" if ratio and ratio >= 1.5 else
                     f" (코스피 상승 종목이 하락의 {1 / ratio:.1f}배)" if ratio and ratio <= 1 / 1.5 else "")
                  + f". 거래대금 코스피 {jo(K['value'])} · 코스닥 {jo(Q['value'])}.")
    if strong or weak:
        tl = lambda ts: ", ".join(f"{t['theme']}({pct(t['chg1'])})" for t in ts[:3])
        glance.append("테마 — " + (f"강세 {tl(strong)}" if strong else "강세 테마 없음")
                      + " / " + (f"약세 {tl(weak)}" if weak else "약세 테마 없음") + ".")
    if top_value:
        glance.append("거래대금 상위 — " + ", ".join(f"{r['name']}({pct(r['chg1'])}, {jo(r['value'])})" for r in top_value[:3]) + ".")
    if len(tiles) > 3:
        glance.append("금리·환율 — " + " · ".join(f"{t['label']} {t['value']}({t['chg']})" for t in tiles[2:5]) + ".")

    # ---------- 주식·채권 마감 시황 (한화투자증권 리서치센터 우선, 다른 채널로 보충) ----------
    ch = digest.channel_of
    is_stock_wrap = lambda p: digest.KR_WRAP.search(p["text"][:60]) and re.search(r"KOSPI|코스피|증시", p["text"][:300]) and "채권" not in p["text"][:30]
    is_bond_wrap = lambda p: re.search(r"채권|국고채|금리", p["text"][:60]) and digest.KR_WRAP.search(p["text"][:80])
    prio = lambda ps: sorted(ps, key=lambda p: (ch(p) != "hanwhastrategy", ch(p) != "strategy_kis", p["time"]))
    sw = prio([p for p in posts if is_stock_wrap(p)])
    bw = prio([p for p in posts if is_bond_wrap(p)])
    src = lambda ps: list(dict.fromkeys(digest.CHANNEL.get(ch(p), ch(p)) for p in ps))
    stock_wrap = {"lines": digest.digest(sw, 6), "sources": src(sw)}
    bond_wrap = {"lines": digest.digest(bw, 5), "nums": digest.numbers(bw, r"국고채|국채|회사채|통안"), "sources": src(bw)}
    # ---------- 한국장 시간(09:00~15:40) 미국 주식 관련 소식 ----------
    us_news = []
    for p in digest.merge_runs([p for p in posts if p["time"].strftime("%H:%M") <= "15:40" and not digest.KR_WRAP.search(p["text"][:60])]):
        if not digest.US_KW.search(p["text"]):
            continue
        t = digest.title(p["text"])
        if not t or any(digest.similar(t, u["title"]) for u in us_news):
            continue
        lines = [l for l in digest.digest([p], 4, need_reason=False) if not digest.similar(l, t)][:2]
        us_news.append({"time": p["time"].strftime("%H:%M"), "title": t, "lines": lines, "sources": tg.sources_of(p["text"])[:2]})
    us_news = us_news[:8]

    # 헤드라인: 코스피 방향 + 외국인 + 강·약 테마
    head = f"코스피 {abs(K['chg1']) * 100:.2f}% {word(K['chg1'])}"
    if flows:
        fv = flows["KOSPI"]["외국인"] + (flows.get("KOSDAQ") or {}).get("외국인", 0)
        head += f"·외국인 {eok(abs(fv)).lstrip('+')} 순{'매수' if fv > 0 else '매도'}"
    tail = [f"{strong[0]['theme']} 강세"] if strong else []
    tail += [f"{weak[0]['theme']} 약세"] if weak else []
    if tail:
        head += "…" + ", ".join(tail)

    # 장 마감 후 체크: 오늘 보유 종목 중요 공시 / 다음 거래일 체크: 경제 일정
    after = [{"name": d["name"], "side": d["side"], "title": d["title"], "category": d["category"], "rcept_no": d["rcept_no"]}
             for d in (disc or {}).get("items", []) if d["date"] == today and d.get("important")]
    horizon = now + timedelta(days=3 if now.weekday() == 4 else 1, hours=1)
    nxt = [e for e in (cal or {}).get("events", [])
           if now <= datetime.fromisoformat(e["time"]) <= horizon and e["impact"] in ("높음", "보통", "휴일")]

    final = now.hour >= 18
    log(f"  장 마감 시황{' (최종)' if final else ' (1차)'}: {head} · 주식 마감 {len(stock_wrap['lines'])}줄({', '.join(stock_wrap['sources']) or '없음'})"
        f" · 채권 마감 {len(bond_wrap['lines'])}줄 · 미국 주식 소식 {len(us_news)}건 · 특징주 {len(featured)}")
    return {"date": today, "built_at": now.isoformat(), "final": final, "headline": head, "tiles": tiles,
            "index": idx, "flows": flows, "glance": glance, "stock_wrap": stock_wrap, "bond_wrap": bond_wrap, "us_news": us_news,
            "featured": featured, "top_value": top_value, "strong": strong, "weak": weak, "after": after, "next": nxt}


def collect(prev, kis, mk, news, cal, disc, positions, now, log=print):
    """prev: 지난 wrap.json. 16:30 에 1차, 18시 이후 실행(19:00)에서 한 번 더 최종으로 만든다 (17시대 리서치 정리 글 반영)."""
    wraps = (prev or {}).get("wraps", [])
    today = now.strftime("%Y-%m-%d")
    old = next((w for w in wraps if w["date"] == today), None)
    if old and (old.get("final") or now.hour < 18):
        log("  장 마감 시황: 오늘 것은 이미 만들어 둠 → 유지")
        return prev
    w = build(kis, mk, news, cal, disc, positions, now, log)
    return {"wraps": [w] + [x for x in wraps if x["date"] != today][:KEEP - 1]}
