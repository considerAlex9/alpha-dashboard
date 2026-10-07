"""내 텔레그램으로 알림 보내기 (텔레그램 봇, 무료).

자동 업데이트가 돌 때마다 새로 생긴 일만 한 번씩 보낸다. 이미 보낸 것은 data/alerts_state.json 에 적어 두고 다시 보내지 않는다.
  - 매크로 뉴스 브리핑 (장전·장마감, 처음 만들어질 때 한 번)
  - 장 마감 시황 (거래일 16:30)
  - 손절가 근접·도달
  - 보유 종목 급등락 (오늘 등락률이 기준 이상)
  - 보유 종목 새 중요 공시
  - 보유 종목 시장경보 지정
  - 비중·노출·베타 한도 초과

처음 연결할 때:  python alerts.py 연결   (봇에게 아무 말이나 보낸 뒤 실행 → 대화방 번호를 .env 에 적어 줌)
시험 메시지:     python alerts.py 시험
"""
import html
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
KST = timezone(timedelta(hours=9))
SITE = "https://consideralex9.github.io/alpha-dashboard/"
STATE = DATA / "alerts_state.json"
KEEP_DAYS = 14
SEC = {"Technology": "기술", "Industrials": "산업재", "Healthcare": "헬스케어", "Financial Services": "금융",
       "Consumer Cyclical": "경기소비재", "Consumer Defensive": "필수소비재", "Communication Services": "커뮤니케이션",
       "Basic Materials": "소재", "Energy": "에너지", "Utilities": "유틸리티", "Real Estate": "부동산"}
WARN = {"caution": "투자주의", "warning": "투자경고", "risk": "투자위험"}
WARN_CODE = {"01": "caution", "02": "warning", "03": "risk"}


def _read(p, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _api(token, method, params):
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data), timeout=20) as r:
        return json.load(r)


def send(token, chat, text):
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    return _api(token, "sendMessage", {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true"})


def e(s):
    return html.escape(str(s), quote=False)


side_ko = {"long": "롱", "short": "숏"}


def _pct(v, d=1):
    return f"{v * 100:+.{d}f}%"


def gather(now):
    """지금 상태에서 알릴 거리 → [(키, 묶음 이름, 문장)]"""
    snaps = sorted((DATA / "snapshots").glob("*.json"))
    if not snaps:
        return []
    P = json.loads(snaps[-1].read_text(encoding="utf-8"))["portfolio"]
    M = _read(DATA / "market.json", {}) or {}
    L = _read(ROOT / "limits.json", {})
    today = now.strftime("%Y-%m-%d")
    out = []
    name = lambda p: p.get("company_alias") or p["company_name"]

    # 손절가
    near = L.get("손절가 근접 기준", 0.05)
    for p in P["positions"]:
        st, px = p.get("stop_loss_price"), p.get("current_price")
        if not st or not px:
            continue
        dist = px / st - 1 if p["side"] == "long" else st / px - 1
        if dist <= near:
            hit = dist <= 0
            out.append((f"stop:{today}:{p['company_symbol']}:{'hit' if hit else 'near'}", "손절가",
                        f"<b>{e(name(p))}</b> ({side_ko[p['side']]}) 현재 {px:,.0f} · 손절 {st:,.0f} · "
                        + ("<b>손절가 도달</b>" if hit else f"남은 거리 {dist * 100:.1f}%")))

    # 급등락 (오늘 거래분만)
    big = L.get("급등락 알림 기준", 0.05)
    for h in M.get("holdings", []):
        c = h.get("chg1")
        if h.get("date") != today or c is None or abs(c) < big:
            continue
        bad = (h["side"] == "long") == (c < 0)
        out.append((f"move:{today}:{h['symbol']}:{'u' if c > 0 else 'd'}", "급등락",
                    f"<b>{e(h['name'])}</b> ({side_ko[h['side']]}) 오늘 {_pct(c)} {'급등' if c > 0 else '급락'} → "
                    + ("내게 불리" if bad else "내게 유리")))

    # 시장경보
    held = {p["company_symbol"]: p for p in P["positions"]}
    found = {}
    for kind in ("risk", "warning", "caution"):
        for r in (M.get("warnings") or {}).get(kind, []):
            if r["code"] in held and r["code"] not in found:
                found[r["code"]] = kind
    for h in M.get("holdings", []):
        if h["symbol"] not in found and WARN_CODE.get(h.get("warn")):
            found[h["symbol"]] = WARN_CODE[h["warn"]]
    for code, kind in found.items():
        p = held[code]
        out.append((f"warn:{code}:{kind}", "시장경보", f"<b>{e(name(p))}</b> ({side_ko[p['side']]}) 한국거래소 <b>{WARN[kind]}</b> 지정"))

    # 중요 공시 (최근 2일)
    since = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    for d in (_read(DATA / "disclosures.json", {}) or {}).get("items", []):
        if d.get("important") and d["date"] >= since:
            url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={d['rcept_no']}"
            out.append((f"disc:{d['rcept_no']}", "공시",
                        f"<b>{e(d['name'])}</b> ({side_ko.get(d['side'], '')}) <a href=\"{url}\">{e(d['title'])}</a> · {e(d['category'])}"))

    # 한도
    nav = P["nav"]
    mx = L.get("한 종목 최대 비중", 0.05)
    for p in P["positions"]:
        w = p["market_value"] / nav
        if w > mx:
            out.append((f"limit:{today}:w:{p['company_symbol']}", "한도",
                        f"<b>{e(name(p))}</b> 비중 {w * 100:.1f}% (기준 {mx * 100:.0f}%)"))
    net = (P["long_mv"] - P["short_mv"]) / nav
    if abs(net) > L.get("순노출 허용 범위", 0.10):
        out.append((f"limit:{today}:net", "한도", f"순노출 {_pct(net)} (허용 ±{L['순노출 허용 범위'] * 100:.0f}%)"))
    gross = (P["long_mv"] + P["short_mv"]) / nav
    if gross > L.get("총노출 최대", 1.0):
        out.append((f"limit:{today}:gross", "한도", f"총노출 {gross * 100:.0f}% (최대 {L['총노출 최대'] * 100:.0f}%)"))
    hb = [h for h in M.get("holdings", []) if h.get("beta60") is not None]
    if hb:
        beta = sum((1 if h["side"] == "long" else -1) * h["market_value"] / nav * h["beta60"] for h in hb)
        if abs(beta) > L.get("포트 베타 허용 범위", 0.20):
            out.append((f"limit:{today}:beta", "한도", f"포트 베타(60일) {beta:+.2f} (허용 ±{L['포트 베타 허용 범위']:.2f})"))
    T = _read(ROOT / "sectors.json", {})
    by = {c: t for t, cs in T.items() if isinstance(cs, list) for c in cs}
    ps = (M.get("pairs") or {}).get("stocks") or {}
    tnet = {}
    for p in P["positions"]:
        t = by.get(p["company_symbol"]) or (ps.get(p["company_symbol"]) or {}).get("sector") or SEC.get(p["sector"], p["sector"])
        tnet[t] = tnet.get(t, 0) + (1 if p["side"] == "long" else -1) * p["market_value"] / nav
    tmax = L.get("한 테마 최대 순노출", 0.10)
    for t, v in tnet.items():
        if abs(v) > tmax:
            out.append((f"limit:{today}:t:{t}", "한도", f"테마 <b>{e(t)}</b> 순노출 {_pct(v)} (허용 ±{tmax * 100:.0f}%)"))
    return out


def briefs():
    """아직 안 보낸 최신 브리핑 → [(키, 메시지)]"""
    out = []
    for ed in ((_read(DATA / "news.json", {}) or {}).get("editions") or [])[:2]:
        lines = [f"📰 <b>{e(ed['name'])}</b> · {ed['start'][5:7]}/{ed['start'][8:10]} {ed['start'][11:16]} ~ {ed['end'][11:16]}"]
        if ed.get("metrics"):
            lines.append(" · ".join(f"{e(m['label'])} {e(m['value'])} {e(m.get('chg') or '')}".strip() for m in ed["metrics"][:6]))
        for c in (ed.get("cards") or [])[:6]:
            src = f" — {e(', '.join(c['sources'][:2]))}" if c.get("sources") else ""
            lines.append(f"• {e(c['title'])}{src}")
        if not ed.get("cards"):
            lines.append("이 시간대에 올라온 시황 글이 없습니다.")
        lines.append(f'<a href="{SITE}">대시보드에서 자세히 보기</a>')
        out.append((f"news:{ed['id']}", "\n".join(lines)))
    w = ((_read(DATA / "wrap.json", {}) or {}).get("wraps") or [None])[0]
    if w:                                          # 장 마감 시황 (거래일 16:30)
        lines = [f"📊 <b>장 마감 시황</b> · {w['date'][5:7]}/{w['date'][8:10]}", f"<b>{e(w['headline'])}</b>"]
        lines += [f"• {e(x)}" for x in w["glance"][:5]]
        lines.append(f'<a href="{SITE}#wrap">대시보드에서 자세히 보기</a>')
        out.append((f"wrap:{w['date']}", "\n".join(lines)))
    return out


ICON = {"손절가": "🛑", "급등락": "⚡", "시장경보": "🚨", "공시": "📄", "한도": "⚖️"}


def run(token, chat, log=print):
    now = datetime.now(KST)
    first = not STATE.exists()
    st = _read(STATE, {"sent": {}})
    sent = st["sent"]
    events, news = gather(now), briefs()
    new_ev = [x for x in events if x[0] not in sent]
    new_news = [x for x in news if x[0] not in sent]
    if first:
        # 처음 연결: 지금까지 쌓인 것을 한꺼번에 쏟아내지 않고, 현재 상태 요약 한 번만
        send(token, chat, "✅ <b>롱숏 포트폴리오 보드 알림이 연결됐어요.</b>\n"
             f"지금 확인할 것 {len(events)}건 · 앞으로 새로 생기는 일만 알려 드려요.\n<a href=\"{SITE}\">대시보드 열기</a>")
        new_ev, new_news = [], []
        for k, *_ in events + news:
            sent[k] = now.isoformat()
    for k, msg in reversed(new_news):
        send(token, chat, msg)
        sent[k] = now.isoformat()
    if new_ev:
        groups = {}
        for k, g, txt in new_ev:
            groups.setdefault(g, []).append(txt)
        body = [f"🔔 <b>포트폴리오 알림</b> · {now:%m/%d %H:%M}"]
        for g, items in groups.items():
            body.append(f"\n{ICON.get(g, '•')} <b>{g}</b>")
            body += [f"• {t}" for t in items]
        body.append(f'\n<a href="{SITE}">대시보드 열기</a>')
        send(token, chat, "\n".join(body))
        for k, *_ in new_ev:
            sent[k] = now.isoformat()
    cut = (now - timedelta(days=KEEP_DAYS)).isoformat()
    st["sent"] = {k: v for k, v in sent.items() if v >= cut}
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"  텔레그램 알림: 브리핑 {len(new_news)}건 · 알림 {len(new_ev)}건 보냄{' (첫 연결)' if first else ''}")


def _env():
    sys.path.insert(0, str(ROOT))
    import collect
    return collect.load_env()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    env = _env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("[안내] .env 에 TELEGRAM_BOT_TOKEN=... 줄을 먼저 넣어 주세요.")
    if cmd == "연결":
        ups = _api(token, "getUpdates", {}).get("result", [])
        chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name", "") for u in ups if "message" in u}
        if not chats:
            sys.exit("[안내] 봇과의 대화방에서 아무 말이나 한 번 보낸 뒤 다시 실행해 주세요.")
        cid = list(chats)[-1]
        f = ROOT / ".env"
        lines = [x for x in f.read_text(encoding="utf-8-sig").splitlines() if not x.startswith("TELEGRAM_CHAT_ID=")]
        f.write_text("\n".join(lines + [f"TELEGRAM_CHAT_ID={cid}"]) + "\n", encoding="utf-8")
        send(token, cid, "✅ 대시보드 알림 봇이 연결됐어요.")
        print(f"연결 완료: 대화방 번호 {cid} 를 .env 에 적었어요. 깃허브 비밀 보관함에도 같은 값을 넣어 주세요.")
    elif cmd == "시험":
        send(token, env["TELEGRAM_CHAT_ID"], "🔔 시험 메시지입니다. 알림이 잘 도착했어요.")
        print("보냈어요.")
    else:
        for k, g, t in gather(datetime.now(KST)):
            print(g, "|", k, "|", t)
