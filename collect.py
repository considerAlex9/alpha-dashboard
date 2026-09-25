"""Alpha Lenz 대회 API에서 현재 상태를 받아 data/ 에 누적 저장한다.

API는 '지금 이 순간'만 알려주므로, 이 스크립트를 매일(장 마감 후) 돌려야
NAV 히스토리가 쌓인다. 같은 날 여러 번 돌리면 그날 값은 마지막 것으로 덮어쓴다.
외부 패키지 없이 표준 라이브러리만 사용.
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
KST = timezone(timedelta(hours=9))
BASE = "https://api.alpha-lenz.com/api/v1/alpha-capture"  # 문서의 alpha-lenz.com 은 웹페이지로 가서 404


def load_env():
    env = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env.update({k: v for k, v in os.environ.items() if k in ("ALPHA_API_KEY", "COMPETITION_ID", "KIS_APP_KEY", "KIS_APP_SECRET", "DART_API_KEY") and v})
    return env


def get(path, key):
    req = urllib.request.Request(f"{BASE}{path}", headers={"X-API-Key": key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"[오류] {path} -> HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}")


def read_json(p, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def write_json(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    env = load_env()
    key, cid = env.get("ALPHA_API_KEY"), env.get("COMPETITION_ID", "9")
    if not key:
        sys.exit("[오류] .env 에 ALPHA_API_KEY 가 없습니다.")

    comp = f"/competitions/{cid}"
    portfolio = get(f"{comp}/portfolio", key)
    orders = get(f"{comp}/orders", key)
    trades = get(f"{comp}/trades", key)
    leaderboard = get(f"{comp}/leaderboard", key)
    sector_sentiment = get(f"{comp}/sector-sentiment", key)  # 전체 참가자의 섹터별 롱·숏 합계

    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")

    # 리더보드에는 참가자 ID가 없어서 NAV가 일치하는 행으로 내 순위를 찾는다
    me = next((r for r in leaderboard if abs(r["nav"] - portfolio["nav"]) < 1), None)

    # 1) 원본 스냅샷 (그날 마지막 상태)
    write_json(DATA / "snapshots" / f"{today}.json", {
        "captured_at": now.isoformat(), "portfolio": portfolio, "orders": orders, "leaderboard": leaderboard,
        "sector_sentiment": sector_sentiment,
    })

    # 2) 일별 요약 히스토리 — 차트용
    hist_p = DATA / "history.json"
    hist = [h for h in read_json(hist_p, []) if h["date"] != today]
    hist.append({
        "date": today,
        "captured_at": now.isoformat(),
        "price_as_of": portfolio.get("price_as_of"),
        "nav": portfolio["nav"],
        "total_pnl": portfolio["total_pnl"],
        "total_pnl_pct": portfolio["total_pnl_pct"],
        "realized_pnl": portfolio["realized_pnl"],
        "unrealized_pnl": portfolio["unrealized_pnl"],
        "max_drawdown_pct": portfolio["max_drawdown_pct"],
        "sharpe_ratio": portfolio["sharpe_ratio"],
        "long_mv": portfolio["long_mv"],
        "short_mv": portfolio["short_mv"],
        "net_exposure_pct": portfolio["net_exposure_pct"],
        "gross_exposure": portfolio["gross_exposure"],
        "rank": me["rank"] if me else None,
        "participants": len(leaderboard),
    })
    hist.sort(key=lambda h: h["date"])
    write_json(hist_p, hist)

    # 3) 체결 내역 아카이브 — API는 최근 200건만 주므로 id 기준으로 계속 합쳐 둔다
    tr_p = DATA / "trades_all.json"
    merged = {t["id"]: t for t in read_json(tr_p, [])}
    merged.update({t["id"]: t for t in trades})
    write_json(tr_p, sorted(merged.values(), key=lambda t: t["created_at"], reverse=True))

    # 4) 시장 데이터 — 실패해도 포트폴리오 수집 결과는 그대로 둔다
    try:
        import market
        mk = market.collect(portfolio["positions"], (env.get("KIS_APP_KEY"), env.get("KIS_APP_SECRET")))
        write_json(DATA / "market.json", mk)
        # 투자자별 순매수는 네이버가 당일 1건만 주므로 영업일별로 누적
        fl_p = DATA / "flows_history.json"
        fl = {f["bizdate"]: f for f in read_json(fl_p, [])}
        for d in mk.get("flows_days", []):          # 한국투자증권: 최근 영업일 여러 날
            fl[d["bizdate"]] = d
        if "KOSPI" in mk["flows"]:                   # 네이버 예비: 당일 1건
            b = mk["flows"]["KOSPI"]["bizdate"]
            fl.setdefault(b, {"bizdate": b, **{k: {x: v[x] for x in ("개인", "외국인", "기관")} for k, v in mk["flows"].items()}})
        write_json(fl_p, sorted(fl.values(), key=lambda f: f["bizdate"]))
    except Exception as e:  # noqa: BLE001
        print(f"  [경고] 시장 데이터 수집 실패: {e}")

    # 5) 다트 공시 — 키가 있을 때만, 실패해도 나머지는 그대로
    if env.get("DART_API_KEY"):
        try:
            import dart
            held = [(p["company_symbol"], p.get("company_alias") or p["company_name"], p["side"]) for p in portfolio["positions"]]
            write_json(DATA / "disclosures.json", dart.collect(env["DART_API_KEY"], held))
        except Exception as e:  # noqa: BLE001
            print(f"  [경고] 다트 공시 수집 실패: {e}")

    rank = f"{me['rank']}/{len(leaderboard)}위" if me else "순위 미확인"
    print(f"[{now:%Y-%m-%d %H:%M}] NAV {portfolio['nav']:,.0f}  수익률 {portfolio['total_pnl_pct']*100:+.2f}%  "
          f"{rank}  히스토리 {len(hist)}일  체결 누적 {len(merged)}건")


if __name__ == "__main__":
    main()
