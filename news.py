"""텔레그램 공개 채널 → 매크로 뉴스 브리핑 (무료, 규칙 기반).

- 장전 브리핑: 전 평일 19:00 ~ 당일 09:00 글 (08:00 에 1차, 09:00 에 최종 업데이트)
  장마감 브리핑: 당일 09:00 ~ 19:00 글 (16:00 에 1차, 19:00 에 최종 업데이트)
  (월요일 장전 브리핑은 금요일 19:00 이후 주말 글 전체)
- 시황 채널: 애널리스트가 이미 '제목 + 핵심 항목'으로 정리해 올리므로, 그 제목과 핵심 항목만 뽑아 카드로 보여준다.
  주가·환율·금리 숫자가 적힌 시황 글에서는 숫자를 따로 뽑아 맨 위에 보여준다.
- 참고 채널: 글 제목만 짧게.
- 출처는 채널명이 아니라 글 안에 적힌 원 출처(블룸버그, 씨티 등). 링크는 넣지 않는다.
"""
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

MARKET_CHANNELS = ["cahier_de_market", "ehdwl", "hedgecat0301", "lim_econ", "strategy_kis", "hanwhastrategy", "Jstockclass"]
REFERENCE_CHANNELS = []          # 제목만 짧게 보여줄 참고 채널 (지금은 없음)
# 브리핑 종류: (id 꼬리, 이름, 시작 시각, 업데이트 시각들 — 마지막이 최종)
#   장전   = 전 평일 19:00 ~ 당일 09:00  (08:00 1차 → 09:00 최종)
#   장마감 = 당일 09:00 ~ 19:00         (16:00 1차 → 19:00 최종)
#   경계가 09:00 · 19:00 으로 딱 맞물려서 서로 겹치지도, 빠지지도 않는다.
EDITIONS = [
    ("pre", "장전 브리핑", "prev19", [time(8, 0), time(9, 0)]),
    ("post", "장마감 브리핑", time(9, 0), [time(16, 0), time(19, 0)]),
]
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
KNOWN = {  # 글에 나오는 이름 → 화면에 쓸 이름
    "KOSPI": "코스피", "코스피": "코스피", "KOSDAQ": "코스닥", "코스닥": "코스닥", "다우": "다우", "S&P500": "S&P500", "S&P 500": "S&P500",
    "나스닥": "나스닥", "필라델피아 반도체": "필라델피아 반도체", "러셀2000": "러셀2000", "미 10년물": "미 10년물", "10년물": "미 10년물",
    "미 2년물": "미 2년물", "30년물": "미 30년물", "WTI": "WTI", "브렌트": "브렌트유", "원/달러": "원/달러", "달러/원": "원/달러",
    "달러인덱스": "달러인덱스", "DXY": "달러인덱스", "비트코인": "비트코인", "VIX": "VIX", "닛케이": "닛케이", "엔/달러": "엔/달러",
}
INDEX_LIKE = {"코스피", "코스닥", "다우", "S&P500", "나스닥", "필라델피아 반도체", "러셀2000", "닛케이"}
INLINE = re.compile(
    r"(?<![가-힣A-Za-z])(?P<n>" + "|".join(re.escape(k) for k in sorted(KNOWN, key=len, reverse=True)) + r")"
    r"(?:\s*(?:지수|금리|환율|유가|선물))?\s*(?P<v>\d[\d,]*(?:\.\d+)?(?![\d,.]*\s*(?:조|억|만|천|개|명|배|건|주|위|년|월|일|분기|종목)))?\s*(?P<u>pt|p|%|원|bp|달러)?"
    r"\s*\(?\s*(?P<c>[+\-−]\d[\d,]*(?:\.\d+)?\s?(?:%|bp|원|pt|p)?)?\)?")


# 실제 시세와 비교해서 말이 안 되는 숫자는 버린다 (예: '코스피 1330조원' 영업이익을 지수로 읽는 실수)
REF_SYM = {"코스피": "KOSPI", "코스닥": "KOSDAQ", "다우": "^DJI", "S&P500": "^GSPC", "나스닥": "^IXIC", "필라델피아 반도체": "^SOX",
           "닛케이": "^N225", "VIX": "^VIX", "WTI": "CL=F", "원/달러": "KRW=X", "달러인덱스": "DX-Y.NYB", "비트코인": "BTC-USD",
           "미 10년물": "^TNX"}
_REF = None


def _ref():
    global _REF
    if _REF is None:
        _REF = {}
        try:
            mk = json.loads((Path(__file__).resolve().parent / "data" / "market.json").read_text(encoding="utf-8"))
            _REF.update({d["symbol"]: d["bars"][-1][4] for d in mk.get("domestic", [])})
            _REF.update({g["symbol"]: g["price"] for g in mk.get("global", [])})
        except Exception:  # noqa: BLE001
            pass
    return _REF


def plausible(label, value, chg):
    """→ (value, chg) — 실제 최근 값과 30% 넘게 다르면 값을, 지수 하루 등락이 15% 넘으면 등락을 버림"""
    ref = _ref().get(REF_SYM.get(label))
    num = lambda t: float(re.sub(r"[^\d.]", "", t) or "nan")
    if value and ref:
        v = num(value)
        if v == v and not (0.7 <= v / ref <= 1.3):
            value = ""
    if chg and label in INDEX_LIKE and chg.endswith("%"):
        c = num(chg)
        if c == c and c > 15:
            chg = ""
    return value, chg


def inline_metrics(line):
    out = []
    for m in INLINE.finditer(line):
        name, v, u, c = KNOWN[m.group("n")], m.group("v"), m.group("u") or "", m.group("c")
        if not v and not c:
            continue
        if v and not c and u == "%" and name in INDEX_LIKE:      # '다우 0.0%' 처럼 등락률만 적힌 경우
            v, u, c = None, "", v + "%"
        val, chg = plausible(name, (v + ("pt" if u in ("p", "pt") else u)) if v else "", (c or "").replace("−", "-").replace(" ", ""))
        if val or chg:
            out.append({"label": name, "value": val, "chg": chg})
    return out
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
        # 글 안의 원문 기사 주소 (텔레그램 자체 주소는 제외)
        hrefs = [html.unescape(h) for h in re.findall(r'href="(https?://[^"]+)"', body.group(1))
                 if not re.match(r"https?://(t\.me|telegram\.me)/", h)]
        posts.append({"id": pid.group(1), "time": datetime.fromisoformat(tm.group(1)).astimezone(KST),
                      "text": _clean(body.group(1))[:MAX_CHARS], "post_url": f"https://t.me/{pid.group(1)}",
                      "links": list(dict.fromkeys(hrefs))[:3]})
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
    s = re.sub(r"\s*\((?=[A-Za-z])[^()가-힣]*\)", "", s)            # 영어 원문 괄호는 빼고 한국어만 (한글이 섞인 괄호는 유지)
    s = re.sub(r"\s*\((?=[A-Za-z])[^()가-힣]*$", "", s)             # 잘려서 닫는 괄호가 없는 영어 괄호
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
    title = re.sub(r"^\[(.*)\]$", r"\1", title).strip()             # [제목] → 제목
    title = re.sub(r"^(\d{4}년\s*)?\d{1,2}월\s*\d{1,2}일\s*|^\d{1,2}/\d{1,2},?\s*", "", title)   # 앞의 날짜
    title = re.sub(r",\s*[가-힣]{2,5}\s[가-힣]{2,4}$", "", title)      # '…, 키움 한지영' 작성자
    lead_src = None
    m = re.match(r"^([A-Za-z가-힣&\. ]{2,20})\)\s*(.+)", title)            # '블룸버그) 오라클…' → 출처 + 제목
    if m:
        lead_src, title = m.group(1).strip(), m.group(2)
    metrics, bullets = [], []
    for l in body:
        m = METRIC.match(LEAD.sub("", l).strip())
        if m:
            label = re.sub(r"\s*(환율|지수)$", "", m.group(1).strip())
            lab = KNOWN.get(label, label)
            val, chg = plausible(lab, m.group(2) + (m.group(3) or ""), m.group(4).replace("−", "-") + (m.group(5) or ""))
            if val or chg:
                metrics.append({"label": lab, "value": val, "chg": chg})
        elif len(l) < 120:
            metrics += inline_metrics(l)
    metrics = list({m["label"]: m for m in metrics}.values())
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
    bullets = [b for b in bullets if _meaningful(b) and len(inline_metrics(b)) < 2]    # 숫자만 나열한 줄은 숫자 칩으로 보여주므로 뺌
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


WRAP = re.compile(r"시황|증시|마감|개장|장 시작|장전|시장 정리|알아야|브리핑|마켓|모닝|이브닝")     # 하루 시장을 정리한 글은 위로


def _srcs(r, text):
    found = sources_of(text)
    if r.get("lead_src"):
        canon = next((n for n, ws in MEDIA + BANKS if r["lead_src"].lower() in ws or r["lead_src"] == n), r["lead_src"])
        found = [canon] + [f for f in found if f != canon]
    return found[:3]


def _link(p):
    """출처 링크: 글에 원문 기사 주소가 있으면 그 기사, 없으면 텔레그램 원문 글"""
    if p.get("links"):
        host = re.sub(r"^www\.", "", urllib.parse.urlsplit(p["links"][0]).netloc)
        return {"link": p["links"][0], "link_label": host}
    return {"link": p.get("post_url"), "link_label": "텔레그램 원문"}


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
        cards.append({"title": r["title"], "bullets": r["bullets"], "numbers": r["metrics"][:4], **_link(p),
                      "topic": topic_of(p["text"]), "sources": _srcs(r, p["text"]), "time": p["time"].isoformat(),
                      "rank": 0 if r["metrics"] else 1 if WRAP.search(r["title"]) else (2 if len(r["bullets"]) >= 3 else 3)})
    # 숫자가 있는 시황 글 → 핵심 항목이 많은 글 → 나머지, 같은 순위 안에서는 최신 글 먼저
    cards.sort(key=lambda c: (c["rank"], -datetime.fromisoformat(c["time"]).timestamp()))
    refs, seen = [], set()
    for p in reversed(ref_posts):
        r = parse(p["text"])
        if r and r["title"][:30] not in seen:
            seen.add(r["title"][:30])
            refs.append({"title": r["title"], "topic": topic_of(p["text"]), "sources": _srcs(r, p["text"]), "time": p["time"].isoformat(), **_link(p)})
    return list(metrics.values())[:10], cards, refs[:15]


# ---------------- 발행 시각 관리 ----------------
def _prev_weekday(day):
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def due_editions(now, days_back=4):
    """최근 평일들의 브리핑 중, 지금까지 지난 업데이트 시각이 있는 것 → (id, 이름, 시작, 이번 기준 시각, 최종 여부)"""
    out = []
    for back in range(days_back, -1, -1):
        day = (now - timedelta(days=back)).date()
        if day.weekday() >= 5:
            continue
        for tail, name, start_at, stages in EDITIONS:
            start = (datetime.combine(_prev_weekday(day), time(19, 0), KST) if start_at == "prev19"
                     else datetime.combine(day, start_at, KST))
            passed = [datetime.combine(day, t, KST) for t in stages if datetime.combine(day, t, KST) <= now]
            if passed:
                out.append((f"{day:%Y-%m-%d}-{tail}", name, start, passed[-1], len(passed) == len(stages)))
    return out


def build(eid, name, start, cut, final, now, log):
    errors = []
    mp = fetch_posts(MARKET_CHANNELS, start, cut, errors)
    rp = fetch_posts(REFERENCE_CHANNELS, start, cut, errors)
    metrics, cards, refs = organize(mp, rp)
    log(f"  매크로 뉴스: {name} {start:%m/%d %H:%M}~{cut:%m/%d %H:%M}{' (최종)' if final else ''} · 시황 글 {len(mp)}개 → 카드 {len(cards)}개 · 숫자 {len(metrics)}개"
        + (f" · 실패 {errors}" if errors else ""))
    return {"id": eid, "name": name, "start": start.isoformat(), "end": cut.isoformat(), "final": final,
            "created_at": now.isoformat(), "post_count": len(mp) + len(rp), "metrics": metrics, "cards": cards,
            "refs": refs, "errors": errors}


def collect(prev, log=print):
    """prev: 지난번 news.json (없으면 None). 업데이트 시각이 지난 브리핑만 새로 만들거나 덧붙여 다시 만든다."""
    now = datetime.now(KST)
    have = {e["id"]: e for e in (prev or {}).get("editions", []) if "final" in e}   # 예전 형식 브리핑은 버림
    first = not have
    due = due_editions(now)
    if first:
        due = due[-4:]                     # 처음에는 최근 브리핑 4개만 만든다
    changed = 0
    for eid, name, start, cut, final in due:
        old = have.get(eid)
        if old and datetime.fromisoformat(old["end"]) >= cut:
            continue                       # 이미 이 기준 시각까지 반영됨
        if not old and not first and start < min(datetime.fromisoformat(e["start"]) for e in have.values()):
            continue                       # 보관 중인 것보다 오래된 브리핑은 새로 만들지 않음
        have[eid] = build(eid, name, start, cut, final, now, log)
        changed += 1
    if not changed:
        log("  매크로 뉴스: 새로 반영할 업데이트 시각 아님 → 기존 유지")
    editions = sorted(have.values(), key=lambda e: e["end"], reverse=True)[:KEEP]
    return {"editions": editions, "checked_at": now.isoformat()}
