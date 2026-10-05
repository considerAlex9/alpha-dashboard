"""Alpha Lenz 대회 API에서 현재 상태를 받아 data/ 에 누적 저장한다.

API는 '지금 이 순간'만 알려주므로, 자동 업데이트가 돌 때마다 기록이 쌓인다.
일별 기록(history.json)은 거래일 09:00 이후 값만 그 거래일 날짜로 남기고, 같은 날은 마지막 값으로 덮어쓴다.
(주말·공휴일·장 시작 전 실행은 일별 기록에 넣지 않는다)
외부 패키지 없이 표준 라이브러리만 사용.
"""
import json
import os
import days
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
    env.update({k: v for k, v in os.environ.items() if k in ("ALPHA_API_KEY", "COMPETITION_ID", "KIS_APP_KEY", "KIS_APP_SECRET", "DART_API_KEY", "DASH_PASSWORD",
                                                          "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") and v})
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


def hist_entry(date, captured_at, portfolio, me, participants):
    """일별 기록 한 줄"""
    return {
        "date": date,
        "captured_at": captured_at,
        "price_as_of": portfolio.get("price_as_of"),
        **{k: portfolio[k] for k in ("nav", "total_pnl", "total_pnl_pct", "realized_pnl", "unrealized_pnl", "max_drawdown_pct",
                                     "sharpe_ratio", "long_mv", "short_mv", "net_exposure_pct", "gross_exposure")},
        "rank": me["rank"] if me else None,
        "score": me.get("composite_score") if me else None,
        "participants": participants,
    }


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

    # 1) 체결 내역 아카이브 — API는 최근 200건만 주므로 id 기준으로 계속 합쳐 둔다
    tr_p = DATA / "trades_all.json"
    merged = {t["id"]: t for t in read_json(tr_p, [])}
    merged.update({t["id"]: t for t in trades})
    write_json(tr_p, sorted(merged.values(), key=lambda t: t["created_at"], reverse=True))

    # 2) 시장 데이터 — 실패해도 포트폴리오 수집 결과는 그대로 둔다
    mk, fresh = read_json(DATA / "market.json", None), False
    try:
        import market
        mk = market.collect(portfolio["positions"], (env.get("KIS_APP_KEY"), env.get("KIS_APP_SECRET")), prev=mk)
        write_json(DATA / "market.json", mk)
        fresh = bool(days.kospi_dates(mk))
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

    # 3) 원본 스냅샷 (그날 마지막 상태) — trade_date: 이 값이 어느 거래일 값인지 (휴장일·장 시작 전이면 None)
    #    대회 동향(리더보드·참가자 업종 포지션)은 비밀번호로 암호화한 것만 저장한다. 비밀번호가 없으면 저장하지 않음.
    trade_date = days.trade_date_now(now, mk, fresh)
    me_info = {k: me[k] for k in ("rank", "nav", "total_return_pct", "sharpe_ratio", "max_drawdown_pct", "composite_score")} if me else None
    snap = {"captured_at": now.isoformat(), "trade_date": trade_date, "portfolio": portfolio, "orders": orders,
            "me": {**(me_info or {}), "participants": len(leaderboard)}}
    if env.get("DASH_PASSWORD"):
        import lock
        snap["contest_lock"] = lock.encrypt({"leaderboard": leaderboard, "sector_sentiment": sector_sentiment}, env["DASH_PASSWORD"])
    write_json(DATA / "snapshots" / f"{today}.json", snap)

    # 4) 일별 기록 — 거래일 값만
    hist_p = DATA / "history.json"
    hist = read_json(hist_p, [])
    if trade_date:
        hist = [h for h in hist if h["date"] != trade_date]
        hist.append(hist_entry(trade_date, now.isoformat(), portfolio, me, len(leaderboard)))
        hist.sort(key=lambda h: h["date"])
        write_json(hist_p, hist)
    else:
        print("  일별 기록: 휴장일이거나 장 시작 전이라 이번 값은 기록하지 않음")

    # 5) 다트 공시 — 키가 있을 때만, 실패해도 나머지는 그대로
    if env.get("DART_API_KEY"):
        try:
            import dart
            held = [(p["company_symbol"], p.get("company_alias") or p["company_name"], p["side"]) for p in portfolio["positions"]]
            write_json(DATA / "disclosures.json", dart.collect(env["DART_API_KEY"], held))
        except Exception as e:  # noqa: BLE001
            print(f"  [경고] 다트 공시 수집 실패: {e}")

    # 6) 텔레그램 매크로 뉴스 — 장전·장마감 브리핑 (업데이트 시각이 지났을 때만 새로 만듦)
    try:
        import news
        write_json(DATA / "news.json", news.collect(read_json(DATA / "news.json", None)))
    except Exception as e:  # noqa: BLE001
        print(f"  [경고] 매크로 뉴스 수집 실패: {e}")

    # 7) 이번 주 경제 일정
    try:
        import econ
        write_json(DATA / "calendar.json", econ.collect(read_json(DATA / "calendar.json", None)))
    except Exception as e:  # noqa: BLE001
        print(f"  [경고] 경제 일정 수집 실패: {e}")

    # 8) 내 텔레그램으로 알림 — 봇 토큰과 대화방 번호가 있을 때만
    if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        try:
            import alerts
            alerts.run(env["TELEGRAM_BOT_TOKEN"], env["TELEGRAM_CHAT_ID"])
        except Exception as e:  # noqa: BLE001
            print(f"  [경고] 텔레그램 알림 실패: {e}")

    rank = f"{me['rank']}/{len(leaderboard)}위" if me else "순위 미확인"
    print(f"[{now:%Y-%m-%d %H:%M}] NAV {portfolio['nav']:,.0f}  수익률 {portfolio['total_pnl_pct']*100:+.2f}%  "
          f"{rank}  기록 {len(hist)}거래일{'' if trade_date else ' (이번 값은 기록 안 함)'}  체결 누적 {len(merged)}건")


if __name__ == "__main__":
    main()
