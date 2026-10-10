"""장중 트리거 감시 — 기술적 분석의 '돌파 대기' 종목 현재가를 10분마다 확인해서
트리거 가격을 넘으면(숏은 아래로 뚫으면) data/trig.json 에 적고, 텔레그램이 연결돼 있으면 알린다 (종목당 하루 한 번).
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
MAX = 60


def run(kis, now, token=None, chat=None, log=print):
    p = DATA / "kr_ta.json"
    if not p.exists():
        return
    watch = (json.loads(p.read_text(encoding="utf-8")).get("watch") or [])[:MAX]
    if not watch:
        return
    today = now.strftime("%Y-%m-%d")
    sp = DATA / "trig.json"
    st = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    if st.get("date") != today:
        st = {"date": today, "hits": {}, "px": {}}
    new = []
    for code, name, d, trig, stop, target, why, dist, rr in watch:
        try:
            px = kis.price(code)["price"]
        except Exception:  # noqa: BLE001
            continue
        if not px:
            continue
        st["px"][code] = px
        hit = px >= trig if d == "long" else px <= trig
        if hit and code not in st["hits"]:
            st["hits"][code] = {"t": now.strftime("%H:%M"), "p": px}
            new.append((code, name, d, trig, stop, target, why, px))
    st["updated_at"] = now.isoformat()
    sp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  트리거 감시: {len(watch)}종목 확인 · 오늘 돌파 {len(st['hits'])} (새로 {len(new)})")
    if new and token and chat:
        import alerts
        e = alerts.e
        lines = [f"🎯 <b>트리거 돌파</b> · {now:%H:%M}"]
        for code, name, d, trig, stop, target, why, px in new:
            ko = "롱" if d == "long" else "숏"
            lines.append(f"• <b>{e(name)}</b> {ko} — {e(why)} {trig:,} {'돌파' if d == 'long' else '이탈'} (현재 {px:,.0f})\n"
                         f"   {ko} 손절 {stop:,} · {ko} 목표 {target:,}")
        lines.append(f'<a href="{alerts.SITE}#ta">대시보드에서 차트 보기</a>')
        alerts.send(token, chat, "\n".join(lines))
