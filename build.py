"""data/ 에 쌓인 값으로 dashboard.html 을 만든다. (collect.py 다음에 실행)"""
import json
from collections import defaultdict
from pathlib import Path

import days

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def read_opt(name, default, folder=DATA):
    p = folder / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def attribution(by_day, trades, kospi_bars):
    """거래일마다 '전 거래일 마지막 상태 → 그날 마지막 상태' 손익을 종목별로 나눈다.
    종목 손익 = 평가손익 변화 + 그날 청산으로 확정된 손익. 수수료 등 나머지는 '기타'."""
    kc = {b[0]: b[4] for b in kospi_bars}
    real = defaultdict(float)                       # (거래일, 종목, 롱/숏) → 실현손익
    names = {}
    for t in trades:
        side = "long" if t["side"] in ("buy", "sell") else "short"
        real[(days.kst_date(t["created_at"]), t["company_symbol"], side)] += t.get("realized_pnl") or 0
        names.setdefault(t["company_symbol"], t["company_name"])
    for s in by_day.values():                       # 한글 종목명 우선
        names.update({p["company_symbol"]: p.get("company_alias") or p["company_name"] for p in s["portfolio"]["positions"]})
    keys = list(by_day)
    daily, rows = [], []
    for a, b in zip(keys, keys[1:]):
        pa, pb = by_day[a]["portfolio"], by_day[b]["portfolio"]
        ua = {(p["company_symbol"], p["side"]): p for p in pa["positions"]}
        ub = {(p["company_symbol"], p["side"]): p for p in pb["positions"]}
        rows = []
        traded = {(c, s) for (d, c, s) in real if a < d <= b}      # 그날 사고팔아 양쪽 스냅샷에 없는 종목도 포함
        for k in set(ua) | set(ub) | traded:
            code, side = k
            r = sum(v for (d, c, s), v in real.items() if c == code and s == side and a < d <= b)
            pnl = (ub[k]["unrealized_pnl"] if k in ub else 0) - (ua[k]["unrealized_pnl"] if k in ua else 0) + r
            if k not in ua and k not in ub and not r:
                continue
            p = ub.get(k) or ua.get(k) or {}
            base = ua[k]["market_value"] if k in ua else (ub[k]["market_value"] if k in ub else 0)
            rows.append({"code": code, "name": names.get(code, code), "side": side,
                         "sector": p.get("sector"), "pnl": round(pnl), "realized": round(r), "base": base,
                         "status": "신규" if k not in ua and k in ub else ("청산" if k not in ub else "")})
        lp = sum(x["pnl"] for x in rows if x["side"] == "long")
        sp = sum(x["pnl"] for x in rows if x["side"] == "short")
        daily.append({"date": b, "prev": a, "nav": pb["nav"], "pnl": pb["nav"] - pa["nav"], "long": lp, "short": sp,
                      "other": pb["nav"] - pa["nav"] - lp - sp, "long_base": pa["long_mv"], "short_base": pa["short_mv"],
                      "kospi": kc[b] / kc[a] - 1 if kc.get(a) and kc.get(b) else None})
    last = daily[-1] if daily else None
    attr = {**last, "captured_at": by_day[last["date"]]["captured_at"],
            "rows": sorted(rows, key=lambda x: -abs(x["pnl"]))} if last else None
    return daily, attr


def main():
    snaps = days.load_snaps(DATA / "snapshots")
    if not snaps:
        raise SystemExit("[오류] 스냅샷이 없습니다. 먼저 collect.py 를 실행하세요.")
    latest = snaps[-1][1]
    market = read_opt("market.json", None)
    trades = read_opt("trades_all.json", [])
    kospi = next((d["bars"] for d in (market or {}).get("domestic", []) if d["symbol"] == "KOSPI"), [])
    by_day = days.by_trade_day(snaps, [b[0] for b in kospi])
    daily, attr = attribution(by_day, trades, kospi)
    payload = {
        "captured_at": latest["captured_at"],
        "trade_date": latest.get("trade_date"),
        "portfolio": latest["portfolio"],
        "orders": latest["orders"],
        "me": latest.get("me"),                         # 내 순위 요약 (공개)
        "contest_lock": latest.get("contest_lock"),     # 대회 동향 — 비밀번호로 암호화된 상태로만 들어감
        "history": read_opt("history.json", []),
        "daily": daily,                                 # 거래일별 롱·숏·기타 손익
        "attr": attr,                                   # 가장 최근 거래일의 종목별 손익
        "trades": trades[:300],
        "market": market,
        "flows_history": read_opt("flows_history.json", []),
        "disclosures": read_opt("disclosures.json", None),
        "news": read_opt("news.json", None),
        "calendar": read_opt("calendar.json", None),
        "intraday": read_opt("intraday.json", None),         # 오늘 장중 흐름 (10분마다)
        "wrap": read_opt("wrap.json", None),                 # 장 마감 시황
        "themes": read_opt("sectors.json", {}, ROOT),
        "limits": read_opt("limits.json", {}, ROOT),
    }
    # </script> 가 데이터에 섞여도 페이지가 깨지지 않게
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = (ROOT / "template.html").read_text(encoding="utf-8").replace("/*__DATA__*/null", blob)
    # index.html 은 웹(GitHub Pages) 첫 화면용, dashboard.html 은 로컬에서 여는 용
    for name in ("index.html", "dashboard.html"):
        (ROOT / name).write_text(html, encoding="utf-8")
    # 미국 패턴 터미널 (us.html) — data/us.json 이 있을 때만
    us_p, us_t = DATA / "us.json", ROOT / "us_template.html"
    if us_t.exists():
        blob_us = us_p.read_text(encoding="utf-8").replace("</", "<\\/") if us_p.exists() else "null"
        (ROOT / "us.html").write_text(us_t.read_text(encoding="utf-8").replace("/*__US__*/null", blob_us), encoding="utf-8")
    print(f"index.html / dashboard.html 생성 완료 ({snaps[-1][0]} 기준 · 거래일 기록 {len(by_day)}일)")


if __name__ == "__main__":
    main()
