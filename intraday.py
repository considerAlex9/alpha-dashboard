"""장중 흐름 — 거래일 장중에 10분마다 코스피·코스닥 흐름, 거래대금, 내 포트폴리오 하루 수익률을 쌓는다.

- 지수·거래대금: 한국투자증권 5분 단위 분봉 (매번 오늘 전체를 다시 받음)
- 내 포트폴리오: 실행할 때마다 한 점 (전 거래일 마지막 기록 순자산 대비 하루 수익률)
"""
from datetime import datetime


def _slot(now):
    """포트폴리오 점의 시각: 5분 단위로 내리고, 장 마감(15:30) 뒤는 15:30 으로"""
    m = min(now.hour * 60 + now.minute, 15 * 60 + 30)
    m -= m % 5
    return f"{m // 60:02d}:{m % 60:02d}"


def update(kis, prev, portfolio, hist, now, log=print):
    """→ (intraday.json 내용, 오늘이 거래일인지)"""
    today = now.strftime("%Y-%m-%d")
    out = {"date": today, "updated_at": now.isoformat(), "portfolio": (prev or {}).get("portfolio", []) if (prev or {}).get("date") == today else []}
    trading = False
    for key, code in (("KOSPI", "0001"), ("KOSDAQ", "1001")):
        day, mins = kis.index_minutes_full(code)
        if day != now.strftime("%Y%m%d"):
            continue                                   # 오늘 분봉이 없음 → 휴장일이거나 장 시작 전
        trading = True
        p = kis.index_price(code)
        base = p["close"] / (1 + p["chg1"])
        out[key] = {**p, "prev": base,
                    "series": [[f"{t[:2]}:{t[2:]}", round(v / base - 1, 5), round(val)] for t, v, val in mins]}
    if not trading:
        return prev, False
    # 내 포트폴리오: 전 거래일 마지막 기록 대비
    base = next((h for h in reversed(hist) if h["date"] < today), None)
    if base:
        r = portfolio["nav"] / base["nav"] - 1
        slot = _slot(now)
        out["portfolio"] = [x for x in out["portfolio"] if x[0] != slot] + [[slot, round(r, 6), round(portfolio["nav"])]]
        out["portfolio"].sort()
        out["base_date"] = base["date"]
    k = out.get("KOSPI", {})
    log(f"  장중 흐름: 코스피 {k.get('chg1', 0) * 100:+.2f}% · 거래대금 {k.get('value', 0) / 1e12:.1f}조 · 포트 점 {len(out['portfolio'])}개")
    return out, True


if __name__ == "__main__":
    print(_slot(datetime(2026, 10, 7, 10, 3)), _slot(datetime(2026, 10, 7, 16, 40)))
