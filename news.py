"""텔레그램 공개 채널 → 매크로 뉴스 브리핑 (무료, 규칙 기반).

- 하루 두 번 발행: 장전 08:00 · 장마감 후 16:00
  각 브리핑은 '직전 브리핑 기준 시각 ~ 이번 기준 시각' 사이의 글만 다뤄서 서로 겹치지 않는다.
  (월요일 장전 브리핑은 금요일 16:00 이후 주말 글 전체)
- 시황 채널: 애널리스트가 이미 '제목 + 핵심 항목'으로 정리해 올리므로, 그 제목과 핵심 항목만 뽑아 카드로 보여준다.
  주가·환율·금리 숫자가 적힌 시황 글에서는 숫자를 따로 뽑아 맨 위에 보여준다.
- 참고 채널: 글 제목만 짧게.
- 출처는 채널명이 아니라 글 안에 적힌 원 출처(블룸버그, 씨티 등). 링크는 넣지 않는다.
"""
import html
import re
import urllib.request
from datetime import datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

MARKET_CHANNELS = ["lim_econ", "hanwhastrategy", "ehdwl", "yieldnspread"]           # 시황 채널
REFERENCE_CHANNELS = ["aetherjapanresearch", "free_life59", "Jstockclass"]          # 그 외 참고 채널
EDITIONS = [(time(8, 0), "장전 브리핑"), (time(16, 0), "장마감 브리핑")]
KEEP = 10                  # 보관할 지난 브리핑 수
MAX_CHARS = 4000

# 카드에 붙는 작은 주제 표시 — 글마다 핵심어가 가장 많이 나온 주제 하나
TOPICS = [
    ("금리·연준", ["연준", "fed", "fomc", "파월", "기준금리", "금리 인하", "금리인하", "금리 인상", "국채", "10년물", "2년물", "treasury", "점도표", "연은", "긴축"]),
    ("경제지표", ["cpi", "pce", "ppi", "물가", "인플레", "고용", "실업", "비농업", "소매판매", "내구재", "gdp", "pmi", "ism", "소비자심리", "경제지표"]),
    ("관세·무역", ["관세", "무역", "시진핑", "ustr", "수출통제", "tariff", "미중", "희토류"]),
    ("지정학", ["이란", "이스라엘", "후티", "중동", "호르무즈", "우크라이나", "러시아", "전쟁", "휴전"]),
    ("유가·원자재", ["유가", "원유", "wti", "브렌트", "opec", "금값", "구리", "천연가스", "송유관"]),
    ("환율", ["환율", "달러인덱스", "원/달러", "달러/원", "엔화", "위안", "dxy", "원화"]),
    ("반도체·AI", ["반도체", "메모리", "hbm", "dram", "낸드", "엔비디아", "마이크론", "tsmc", "하이닉스", "삼성전자", "데이터센터", "인공지능", "오라클"]),
    ("미국 증시", ["나스닥", "s&p", "다우", "뉴욕증시", "미 증시", "러셀", "필라델피아", "특징 종목"]),
    ("국내 증시", ["코스피", "kospi", "코스닥", "kosdaq", "외국인", "순매수", "국내 증시", "한국증시"]),
    ("채권", ["국고채", "채권", "크레딧", "회사채"]),
    ("일본·아시아", ["일본", "닛케이", "일본은행", "boj", "다카이치", "항셍", "대만"]),
]

# 원 출처 — 언론·통신사는 이름만 나오면 출처로 본다.
MEDIA = [
    ("블룸버그", ["블룸버그", "bloomberg"]), ("로이터", ["로이터", "reuters"]), ("WSJ", ["wsj", "월스트리트저널", "wall street journal"]),
    ("FT", ["파이낸셜타임스", "financial times", "(ft)"]), ("CNBC", ["cnbc"]), ("닛케이신문", ["닛케이신문", "니혼게이자이", "nikkei asia"]),
    ("뉴욕타임스", ["뉴욕타임스", "뉴욕타임즈", "nyt", "new york times"]), ("워싱턴포스트", ["워싱턴포스트", "washington post"]),
    ("가디언", ["가디언", "guardian"]), ("이코노미스트", ["the economist", "이코노미스트지"]), ("악시오스", ["악시오스", "axios"]),
    ("폴리티코", ["폴리티코", "politico"]), ("CNN", ["cnn"]), ("배런스", ["배런스", "barron"]), ("연합뉴스", ["연합뉴스", "연합인포맥스"]),
    ("마켓워치", ["마켓워치", "marketwatch"]), ("SCMP", ["scmp"]),
]
# 투자은행 — 'JP모건 -3.4%' 같은 주가 언급과 구분하려고, 뒤에 인용·분석 표현이 올 때만 출처로 본다.
BANKS = [
    ("골드만삭스", ["골드만삭스", "골드만", "goldman"]), ("모건스탠리", ["모건스탠리", "morgan stanley"]), ("JP모건", ["jp모건", "jpmorgan"]),
    ("씨티", ["씨티", "citi"]), ("BofA", ["bofa", "뱅크오브아메리카", "메릴린치"]), ("UBS", ["ubs"]), ("도이체방크", ["도이체", "deutsche"]),
    ("바클레이즈", ["바클레이즈", "barclays"]), ("노무라", ["노무라", "nomura"]), ("웰스파고", ["웰스파고"]), ("HSBC", ["hsbc"]),
    ("맥쿼리", ["맥쿼리", "macquarie"]), ("번스타인", ["번스타인", "bernstein"]), ("제프리스", ["제프리스", "jefferies"]),
]
CITE = r"\s*(은|는|이|가|의|측|에 따르면|에따르면|:|리포트|보고서|분석|전망|추정|예상|이코노미스트|애널리스트|전략가)"
ACCORDING = re.compile(r"([가-힣A-Za-z&]{2,12}(?: [가-힣A-Za-z&]{2,8})?)(?:에 따르면|에따르면|(?:이|가|는|은) 보도했다|(?:이|가) 전했다)")
GENERIC = {"보도", "소식통", "관계자", "발표", "자료", "조사", "이에", "통계", "외신", "현지", "매체", "당국", "정부", "업계", "시장",
           "회사", "기업", "해당", "이들", "그는", "그", "이", "언론"}

EMOJI = r"☀-⟿\U0001F000-\U0001FAFF️‍"
LEAD = re.compile(rf"^[\s\-–—•·*★☆▶▷►■□◆◇●○※>#{EMOJI}]+")
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
BULLET = re.compile(rf"^\s*([\-–•·▶►■◆●※]|[{CIRCLED}]|\d{{1,2}}[\.\)]\s)")
URL = re.compile(r"https?://\S+")
METRIC = re.compile(r"^([A-Za-z가-힣/ ]*?[A-Za-z가-힣](?:\s?\d+년물)?)\s+([\d,]+(?:\.\d+)?)\s*(pt|%|원|bp)?\s+([+\-−][\d,]+(?:\.\d+)?)\s*(%|bp|원|pt)?\s*$")


# ---------------- 텔레그램 수집 ----------------
def _clean(fragment):
    t = re.sub(r"<br\s*/?>", "\n", fragment)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


def _page(channel, before=None):
    url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        doc = r.read().decode("utf-8", "replace")
    posts = []
    for block in re.split(r'(?=<div class="tgme_widget_message_wrap)', doc)[1:]:
        pid = re.search(r'data-post="([^"]+)"', block)
        tm = re.search(r'<time datetime="([^"]+)"', block)
        body = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
        if not (pid and tm and body):
            continue
        posts.append({"id": pid.group(1), "time": datetime.fromisoformat(tm.group(1)).astimezone(KST),
                      "text": _clean(body.group(1))[:MAX_CHARS]})
    return posts


def fetch_posts(channels, start, end, errors):
    """start < 글 시각 <= end 인 글. 주말을 넘기는 경우를 위해 필요한 만큼 이전 페이지를 더 읽는다(최대 6쪽)."""
    out = []
    for ch in channels:
        try:
            posts = _page(ch)
            for _ in range(5):
                if not posts or posts[0]["time"] <= start:
                    break
                posts = _page(ch, before=posts[0]["id"].split("/")[-1]) + posts
            out += [p for p in posts if start < p["time"] <= end and len(p["text"]) >= 15]
        except Exception as e:  # noqa: BLE001 — 채널 하나가 실패해도 나머지는 계속
            errors.append(f"{ch}: {type(e).__name__} {e}"[:150])
    return sorted(out, key=lambda p: p["time"])


# ---------------- 글 → 제목 · 핵심 항목 · 숫자 ----------------
def _tidy(line, limit=110):
    s = LEAD.sub("", URL.sub("", line)).strip()
    s = re.sub(r"^<(.*)>$", r"\1", s).strip()                       # <제목> → 제목
    s = re.sub(r"\s*\((?=[A-Za-z])[^()가-힣]*\)?", "", s)                # 영어 원문 괄호는 빼고 한국어만
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[:limit].rstrip() + "…"


def _meaningful(s):
    return len(re.findall(r"[가-힣A-Za-z0-9]", s)) >= 6 and not re.match(r"(안녕하세요|구독자|좋은 아침|오늘도|오늘 새벽 시황도 영상)", s)


def _first_sentence(s):
    m = re.match(r"(.+?[가-힣]\.)(\s|$)", s)          # 한글 뒤 마침표에서 자름 (Vs. 같은 영어 약어는 유지)
    return m.group(1) if m and len(m.group(1)) >= 15 else s


def parse(text):
    lines = [l for l in (x.strip() for x in text.split("\n")) if l and not URL.fullmatch(l)]
    title, body = None, []
    for i, l in enumerate(lines):
        t = _tidy(l, 80)
        if _meaningful(t):
            title, body = t, lines[i + 1:]
            break
    if not title:
        return None
    title = re.sub(r"\s*\(\d{1,2}/\d{1,2}\)$", "", title)            # '주식 마감 시황 (9/23)' → '주식 마감 시황'
    title = re.sub(r"_\d{1,2}/\d{1,2}.*$", "", title)                 # '…5가지_9/23 Bloomberg' → '…5가지'
    lead_src = None
    m = re.match(r"^([A-Za-z가-힣&\. ]{2,20})\)\s*(.+)", title)            # '블룸버그) 오라클…' → 출처 + 제목
    if m:
        lead_src, title = m.group(1).strip(), m.group(2)
    metrics, bullets = [], []
    for l in body:
        m = METRIC.match(LEAD.sub("", l).strip())
        if m:
            metrics.append({"label": m.group(1).strip(), "value": m.group(2) + (m.group(3) or ""),
                            "chg": m.group(4).replace("−", "-") + (m.group(5) or "")})
    circled = [l for l in body if l[:1] in CIRCLED]
    if circled:                                                        # ① ② ③ 로 번호 붙인 글은 그 소제목만
        bullets = [_tidy(l, 70) for l in circled]
    else:
        marked = [l for l in body if BULLET.match(l) and not METRIC.match(LEAD.sub("", l).strip())]
        if marked:                                                     # '-', '•' 항목은 첫 문장만
            bullets = [_tidy(_first_sentence(LEAD.sub("", l).strip()), 95) for l in marked]
        else:                                                          # 문단 글은 '소제목: …' 줄 또는 첫 문장들
            heads = [l for l in body if re.match(r"^\*?[^:：]{2,30}[:：]\s*\S", l) and len(l) < 200]
            bullets = [_tidy(l, 95) for l in heads] if heads else [_tidy(_first_sentence(l), 95) for l in body if len(l) > 30]
    bullets = [b for b in bullets if _meaningful(b)]
    return {"title": title, "bullets": list(dict.fromkeys(bullets))[:5], "metrics": metrics, "lead_src": lead_src}


def topic_of(text):
    """핵심어가 3번 이상 나온 주제만 (애매하면 표시하지 않음)"""
    t = " " + text.lower() + " "
    best, score = None, 2
    for name, words in TOPICS:
        n = sum(t.count(w) for w in words)
        if n > score:
            best, score = name, n
    return best


def sources_of(text):
    t = text.lower()
    found = [name for name, words in MEDIA if any(w in t for w in words)]
    for name, words in BANKS:
        if any(re.search(re.escape(w) + CITE, t) for w in words):
            found.append(name)
    for m in ACCORDING.findall(text):
        words = m.split()
        while words and words[0] in GENERIC:
            words = words[1:]
        name = " ".join(words)
        if name and name not in GENERIC and words[-1] not in GENERIC \
                and not any(name.lower() in w for _, ws in MEDIA + BANKS for w in ws):
            found.append(name)
    return list(dict.fromkeys(found))[:3]


WRAP = re.compile(r"시황|증시|마감|개장|알아야|브리핑|마켓|모닝|이브닝")     # 하루 시장을 정리한 글은 위로


def _srcs(r, text):
    found = sources_of(text)
    if r.get("lead_src"):
        canon = next((n for n, ws in MEDIA + BANKS if r["lead_src"].lower() in ws or r["lead_src"] == n), r["lead_src"])
        found = [canon] + [f for f in found if f != canon]
    return found[:3]


def organize(market_posts, ref_posts):
    metrics, cards, seen = {}, [], set()
    for p in market_posts:
        r = parse(p["text"])
        if not r or r["title"] in seen:
            continue
        seen.add(r["title"])
        for m in r["metrics"]:
            metrics[m["label"]] = m                                    # 같은 지표는 가장 최근 값
        if not r["bullets"] and not r["metrics"]:
            continue
        cards.append({"title": r["title"], "bullets": r["bullets"], "numbers": r["metrics"][:4],
                      "topic": topic_of(p["text"]), "sources": _srcs(r, p["text"]), "time": p["time"].isoformat(),
                      "rank": 0 if r["metrics"] else 1 if WRAP.search(r["title"]) else (2 if len(r["bullets"]) >= 3 else 3)})
    # 숫자가 있는 시황 글 → 핵심 항목이 많은 글 → 나머지, 같은 순위 안에서는 최신 글 먼저
    cards.sort(key=lambda c: (c["rank"], -datetime.fromisoformat(c["time"]).timestamp()))
    refs, seen = [], set()
    for p in reversed(ref_posts):
        r = parse(p["text"])
        if r and r["title"][:30] not in seen:
            seen.add(r["title"][:30])
            refs.append({"title": r["title"], "topic": topic_of(p["text"]), "sources": _srcs(r, p["text"]), "time": p["time"].isoformat()})
    return list(metrics.values())[:10], cards, refs[:15]


# ---------------- 발행 시각 관리 ----------------
def due_edition(now, done_ids):
    """지금 발행해야 할 가장 최근 브리핑(기준 시각, 이름, id). 평일만, 이미 만든 것은 건너뜀."""
    for back in range(0, 4):
        day = (now - timedelta(days=back)).date()
        if day.weekday() >= 5:
            continue
        for at, name in reversed(EDITIONS):
            cut = datetime.combine(day, at, KST)
            eid = f"{cut:%Y-%m-%d %H%M}"
            if cut <= now:
                return (cut, name, eid) if eid not in done_ids else None
    return None


def prev_slot(cut):
    """바로 앞 브리핑 기준 시각: 16:00 → 같은 날 08:00, 08:00 → 직전 평일 16:00"""
    if cut.time() == EDITIONS[1][0]:
        return datetime.combine(cut.date(), EDITIONS[0][0], KST)
    day = cut.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return datetime.combine(day, EDITIONS[1][0], KST)


def build(cut, name, eid, start, now, log):
    errors = []
    mp = fetch_posts(MARKET_CHANNELS, start, cut, errors)
    rp = fetch_posts(REFERENCE_CHANNELS, start, cut, errors)
    metrics, cards, refs = organize(mp, rp)
    log(f"  매크로 뉴스: {name}({cut:%m/%d %H:%M}) · 시황 글 {len(mp)}개 → 카드 {len(cards)}개 · 숫자 {len(metrics)}개 · 참고 {len(refs)}개"
        + (f" · 실패 {errors}" if errors else ""))
    return {"id": eid, "name": name, "start": start.isoformat(), "end": cut.isoformat(), "created_at": now.isoformat(),
            "post_count": len(mp) + len(rp), "metrics": metrics, "cards": cards, "refs": refs, "errors": errors}


def collect(prev, log=print):
    """prev: 지난번 news.json (없으면 None). 발행 시각이 지났으면 새 브리핑을 앞에 추가한다."""
    now = datetime.now(KST)
    editions = [e for e in (prev or {}).get("editions", []) if "cards" in e]      # 예전 형식 브리핑은 버림
    due = due_edition(now, {e["id"] for e in editions})
    if not due:
        log("  매크로 뉴스: 새 브리핑 발행 시각 아님 → 기존 유지")
        return {"editions": editions, "checked_at": now.isoformat()}
    cut, name, eid = due
    if not editions:
        # 처음 실행: 바로 앞 브리핑도 함께 만들어 두 개로 시작
        p_cut = prev_slot(cut)
        p_name = dict(EDITIONS)[p_cut.time()]
        editions = [build(p_cut, p_name, f"{p_cut:%Y-%m-%d %H%M}", prev_slot(p_cut), now, log)]
    # 시작 = 직전 브리핑의 기준 시각 → 브리핑끼리 겹치지 않고, 발행을 놓친 시간도 빠지지 않음
    start = max(datetime.fromisoformat(e["end"]) for e in editions)
    return {"editions": ([build(cut, name, eid, start, now, log)] + editions)[:KEEP], "checked_at": now.isoformat()}
