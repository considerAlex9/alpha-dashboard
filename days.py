"""거래일 판단과 '거래일별 마지막 상태' 고르기.

- 거래일 = 코스피 일봉에 그 날짜가 있는 날 (주말·공휴일·대체공휴일은 자동으로 빠짐)
- 기록은 거래일 09:00 이후에 받은 값만 그 날짜로 남긴다.
  대회 쪽은 휴장일에도 가격을 다시 매기는 경우가 있어서, 휴장일 값은 기록에 넣지 않는다.
"""
import json
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def kospi_dates(market):
    for d in (market or {}).get("domestic", []):
        if d["symbol"] == "KOSPI":
            return [b[0] for b in d["bars"]]
    return []


def trade_date_now(now, market, fresh):
    """지금 받은 값이 어느 거래일의 값인지. 휴장일이거나 장 시작 전이면 None.
    fresh: 이번 실행에서 시장 데이터를 새로 받았는지 (못 받았으면 평일 여부로만 판단)"""
    today = now.astimezone(KST).date()
    if now.astimezone(KST).hour < 9:
        return None
    if fresh:
        return today.isoformat() if today.isoformat() in kospi_dates(market) else None
    return today.isoformat() if today.weekday() < 5 else None


def by_trade_day(snaps, dates):
    """스냅샷 목록 [(파일 날짜, 내용)] → {거래일: 그 거래일의 마지막 상태}.
    휴장일·장 시작 전 스냅샷은 바로 앞 거래일의 값이 하나도 없을 때만 그 거래일 값으로 쓴다."""
    dset, out, later = set(dates), {}, []
    for d, s in sorted(snaps, key=lambda x: x[0]):
        if "trade_date" in s:
            td = s["trade_date"]
        else:                                   # 예전 스냅샷: 날짜와 받은 시각으로 판단
            cap = datetime.fromisoformat(s["captured_at"]).astimezone(KST)
            td = d if d in dset and cap.hour >= 9 else None
        if td:
            out[td] = s
        else:
            later.append((d, s))
    for d, s in later:
        prev = [x for x in dates if x < d]
        if prev and prev[-1] not in out:
            out[prev[-1]] = s
    return dict(sorted(out.items()))


def load_snaps(folder):
    return [(f.stem, json.loads(f.read_text(encoding="utf-8"))) for f in sorted(folder.glob("*.json"))]


def kst_date(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(KST).date().isoformat()
