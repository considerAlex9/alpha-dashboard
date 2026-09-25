"""텔레그램 공개 채널 → 매크로 뉴스 브리핑 (무료 규칙 기반 정리).

- 하루 두 번 발행: 장전 08:00 · 장마감 후 16:00
  각 브리핑은 '직전 브리핑 기준 시각 ~ 이번 기준 시각' 사이의 글만 다뤄서 서로 겹치지 않는다.
  (월요일 장전 브리핑은 금요일 16:00 이후 주말 글 전체)
- 채널 구분 없이 글 내용의 핵심어로 '주제'를 정해 묶는다
- 출처는 채널명이 아니라 글 안에 적힌 원 출처(블룸버그, 씨티 등)를 찾아 표시한다
  AI 요약이 아니므로 문장을 새로 쓰지 않고, 각 글의 핵심 첫 문장을 보여준다.
"""
import html
import re
import urllib.request
from datetime import datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

CHANNELS = ["hedgecat0301", "ehdwl", "insidertracking", "hanwhastrategy", "lim_econ", "shmstory", "aetherjapanresearch"]
EDITIONS = [(time(8, 0), "장전 브리핑"), (time(16, 0), "장마감 브리핑")]
KEEP = 10                  # 보관할 지난 브리핑 수
MAX_CHARS = 3000

# 주제 — (이름, 핵심어들). 글마다 핵심어가 가장 많이 나온 주제 하나에 넣는다.
TOPICS = [
    ("미국 금리·연준", ["연준", "fed", "fomc", "파월", "기준금리", "금리 인하", "금리인하", "금리 인상", "국채", "10년물", "2년물", "treasury", "yield", "점도표", "워시"]),
    ("물가·경제지표", ["cpi", "pce", "ppi", "물가", "인플레", "고용", "실업", "비농업", "소매판매", "내구재", "gdp", "pmi", "ism", "주택", "소비자심리", "경제지표", "durable goods"]),
    ("미중·관세·무역", ["관세", "무역", "시진핑", "ustr", "수출통제", "무역협상", "tariff", "trade deal", "미중", "희토류"]),
    ("중동·지정학", ["이란", "이스라엘", "후티", "중동", "호르무즈", "우크라이나", "러시아", "전쟁", "휴전", "iran", "israel", "houthi"]),
    ("유가·원자재", ["유가", "원유", "wti", "브렌트", "opec", "금값", "금 가격", "구리", "천연가스", "oil", "crude"]),
    ("환율·달러", ["환율", "달러", "원/달러", "원달러", "엔화", "위안", "dxy", "원화", "dollar", "엔/달러"]),
    ("반도체·AI", ["반도체", "메모리", "hbm", "d램", "dram", "낸드", "엔비디아", "nvidia", "마이크론", "tsmc", "하이닉스", "삼성전자", "데이터센터", " ai ", "ai ", "인공지능", "오라클", "openai"]),
    ("미국 증시", ["나스닥", "s&p", "다우", "뉴욕증시", "미 증시", "미국 증시", "nasdaq", "러셀", "필라델피아"]),
    ("한국 증시·수급", ["코스피", "코스닥", "외국인", "기관", "순매수", "순매도", "국내 증시", "한국 증시", "kospi", "kosdaq", "공매도", "밸류업"]),
    ("일본·중국·아시아", ["일본", "닛케이", "일본은행", "boj", "다카이치", "중국 증시", "상해", "항셍", "홍콩", "대만"]),
]

# 원 출처 — 언론·통신사는 이름만 나오면 출처로 본다.
MEDIA = [
    ("블룸버그", ["블룸버그", "bloomberg"]), ("로이터", ["로이터", "reuters"]), ("WSJ", ["wsj", "월스트리트저널", "wall street journal"]),
    ("FT", ["파이낸셜타임스", "financial times", "(ft)"]), ("CNBC", ["cnbc"]), ("닛케이신문", ["닛케이신문", "니혼게이자이", "nikkei asia"]),
    ("뉴욕타임스", ["뉴욕타임스", "뉴욕타임즈", "nyt", "new york times"]), ("워싱턴포스트", ["워싱턴포스트", "washington post"]),
    ("가디언", ["가디언", "guardian"]), ("블룸버그", ["블룸버그통신"]), ("악시오스", ["악시오스", "axios"]), ("폴리티코", ["폴리티코", "politico"]),
    ("AP", ["ap통신", "associated press"]), ("AFP", ["afp"]), ("CNN", ["cnn"]), ("이코노미스트", ["the economist", "이코노미스트지"]),
    ("배런스", ["배런스", "barron"]), ("마켓워치", ["마켓워치", "marketwatch"]), ("연합뉴스", ["연합뉴스", "연합인포맥스"]),
    ("한국경제", ["한국경제", "한경"]), ("매일경제", ["매일경제", "매경"]), ("SCMP", ["scmp", "사우스차이나모닝포스트"]),
    ("트루스소셜", ["트루스소셜", "truth social"]),
]
# 투자은행·리서치 — 'JP모건 -3.4%' 같은 주가 언급과 구분하려고, 뒤에 인용·분석 표현이 올 때만 출처로 본다.
BANKS = [
    ("골드만삭스", ["골드만삭스", "골드만", "goldman"]), ("모건스탠리", ["모건스탠리", "morgan stanley"]), ("JP모건", ["jp모건", "jpmorgan", "jp morgan"]),
    ("씨티", ["씨티", "citi"]), ("BofA", ["bofa", "뱅크오브아메리카", "bank of america", "메릴린치"]), ("UBS", ["ubs"]),
    ("도이체방크", ["도이체", "deutsche"]), ("바클레이즈", ["바클레이즈", "barclays"]), ("노무라", ["노무라", "nomura"]),
    ("웰스파고", ["웰스파고", "wells fargo"]), ("HSBC", ["hsbc"]), ("맥쿼리", ["맥쿼리", "macquarie"]), ("번스타인", ["번스타인", "bernstein"]),
    ("제프리스", ["제프리스", "jefferies"]), ("에버코어", ["에버코어", "evercore"]), ("캐피털이코노믹스", ["캐피털이코노믹스", "capital economics"]),
]
CITE = r"\s*(은|는|이|가|의|측|에 따르면|에따르면|:|리포트|보고서|분석|전망|추정|예상|이코노미스트|애널리스트|전략가|says|said|expects|sees|note)"


# ---------------- 텔레그램 수집 ----------------
def _clean(fragment):
    t = re.sub(r"<br\s*/?>", "\n", fragment)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


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
        posts.append({"id": pid.group(1), "url": f"https://t.me/{pid.group(1)}",
                      "time": datetime.fromisoformat(tm.group(1)).astimezone(KST),
                      "text": _clean(body.group(1))[:MAX_CHARS]})
    return posts


def fetch_posts(start, end, log=print):
    """start < 글 시각 <= end 인 글. 주말을 넘기는 경우를 위해 필요한 만큼 이전 페이지를 더 읽는다(최대 6쪽)."""
    out, errors = [], []
    for ch in CHANNELS:
        try:
            posts = _page(ch)
            for _ in range(5):
                if not posts or posts[0]["time"] <= start:
                    break
                posts = _page(ch, before=posts[0]["id"].split("/")[-1]) + posts
            out += [p for p in posts if start < p["time"] <= end and len(p["text"]) >= 20]
        except Exception as e:  # noqa: BLE001 — 채널 하나가 실패해도 나머지는 계속
            errors.append(f"{ch}: {type(e).__name__} {e}"[:150])
    out.sort(key=lambda p: p["time"], reverse=True)
    log(f"  텔레그램: {len(CHANNELS) - len(errors)}/{len(CHANNELS)}개 채널 · {start:%m/%d %H:%M}~{end:%m/%d %H:%M} 글 {len(out)}개")
    return out, errors


# ---------------- 규칙 기반 정리 ----------------
def headline(text):
    """이모지·인사말만 있는 줄은 건너뛰고, 내용이 있는 첫 문장"""
    for line in text.split("\n"):
        s = re.sub(r"^[\s\-–•*★☆▶▷■□◆◇●○☀-⟿\U0001F000-\U0001FAFF️]+", "", line).strip()
        if len(re.findall(r"[가-힣A-Za-z0-9]", s)) < 8 or re.match(r"(안녕하세요|구독자|좋은 아침|오늘도)", s):
            continue
        return s[:140] + ("…" if len(s) > 140 else "")
    return None


def topic_of(text):
    t = " " + text.lower() + " "
    best, score = "기타", 0
    for name, words in TOPICS:
        n = sum(t.count(w) for w in words)
        if n > score:
            best, score = name, n
    return best


# 'OO에 따르면', 'OO가 보도했다' 형태로 적힌 출처. 너무 일반적인 말은 뺀다.
ACCORDING = re.compile(r"([가-힣A-Za-z&]{2,12}(?: [가-힣A-Za-z&]{2,8})?)(?:에 따르면|에따르면|(?:이|가|는|은) 보도했다|(?:이|가) 전했다)")
GENERIC = {"보도", "소식통", "관계자", "발표", "자료", "조사", "이에", "통계", "외신", "현지", "매체", "당국", "정부", "업계", "시장",
           "회사", "기업", "해당", "이들", "그는", "그", "이", "현지 매체", "언론"}


def sources_of(text):
    t = text.lower()
    found = [name for name, words in MEDIA if any(w in t for w in words)]
    for name, words in BANKS:
        if any(re.search(re.escape(w) + CITE, t) for w in words):
            found.append(name)
    for m in ACCORDING.findall(text):
        words = m.split()
        while words and words[0] in GENERIC:          # '매체 유타르니' → '유타르니'
            words = words[1:]
        name = " ".join(words)
        if name and name not in GENERIC and words[-1] not in GENERIC                 and not any(name.lower() in w for _, ws in MEDIA + BANKS for w in ws):
            found.append(name)
    return list(dict.fromkeys(found))[:4]


def organize(posts):
    seen, topics = set(), {}
    for p in posts:
        h = headline(p["text"])
        if not h or h in seen:            # 여러 채널이 같은 뉴스를 옮긴 경우는 한 번만
            continue
        seen.add(h)
        topics.setdefault(topic_of(p["text"]), []).append(
            {"headline": h, "sources": sources_of(p["text"]), "time": p["time"].isoformat(), "url": p["url"]})
    out = [{"title": n, "items": v} for n, v in topics.items()]
    out.sort(key=lambda t: (t["title"] == "기타", -len(t["items"])))      # 글 많은 주제 위로, 기타는 맨 아래
    for t in out:
        cnt = {}
        for it in t["items"]:
            for s in it["sources"]:
                cnt[s] = cnt.get(s, 0) + 1
        t["sources"] = sorted(cnt, key=lambda s: -cnt[s])[:6]
    return out


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
    posts, errors = fetch_posts(start, cut, log=log)
    topics = organize(posts)
    log(f"  매크로 뉴스: {name}({cut:%m/%d %H:%M}) 발행 · 주제 {len([t for t in topics if t['title'] != '기타'])}개 · 제목 {sum(len(t['items']) for t in topics)}개")
    return {"id": eid, "name": name, "start": start.isoformat(), "end": cut.isoformat(), "created_at": now.isoformat(),
            "post_count": len(posts), "topics": topics, "errors": errors}


def collect(prev, log=print):
    """prev: 지난번 news.json (없으면 None). 발행 시각이 지났으면 새 브리핑을 앞에 추가한다."""
    now = datetime.now(KST)
    editions = (prev or {}).get("editions", [])
    due = due_edition(now, {e["id"] for e in editions})
    if not due:
        log("  매크로 뉴스: 새 브리핑 발행 시각 아님 → 기존 유지")
        return {"editions": editions, "checked_at": now.isoformat()}
    cut, name, eid = due
    if not editions:
        # 처음 실행: 바로 앞 브리핑도 함께 만들어 두 개로 시작
        p_cut = prev_slot(cut)
        p_name = dict((t, n) for t, n in EDITIONS)[p_cut.time()]
        editions = [build(p_cut, p_name, f"{p_cut:%Y-%m-%d %H%M}", prev_slot(p_cut), now, log)]
    # 시작 = 직전 브리핑의 기준 시각 → 브리핑끼리 겹치지 않고, 발행을 놓친 시간도 빠지지 않음
    start = max(datetime.fromisoformat(e["end"]) for e in editions)
    return {"editions": ([build(cut, name, eid, start, now, log)] + editions)[:KEEP], "checked_at": now.isoformat()}
