"""텔레그램 글 여러 개에서 중요한 문장만 골라 다듬어 요약한다 (규칙 기반, 원문 링크 없이).

- 숫자만 나열한 줄(지수·금리)은 따로 모으고, 이유·흐름이 담긴 문장을 우선 고른다
- 여러 채널이 같은 말을 하면 하나만 남긴다
"""
import re

import news

NUMLINE = re.compile(r"^(KOSPI|KOSDAQ|코스피|코스닥|국고채|국채|회사채|통안|원/달러|달러/원|미 ?국채|미국채|美)[^가-힣]*\d")
REASON = re.compile(r"영향|우려|기대|관망|경계|부담|강세|약세|상승|하락|매수|매도|반등|급등|급락|마감|출발|확대|축소|전환|발표|지속|되돌림|유입|이탈|주도|견인|부진|호조")
US_KW = re.compile(r"미국|美|뉴욕|나스닥|S&P|다우|엔비디아|NVIDIA|Nvidia|애플|테슬라|마이크로소프트|아마존|메타|알파벳|구글|브로드컴|AMD|인텔|마이크론|"
                   r"퀄컴|오라클|팔란티어|OpenAI|오픈AI|연준|Fed|FOMC|파월|트럼프|백악관|월가|Citi|씨티|골드만|모건스탠리|JP ?모건|BofA|"
                   r"뱅크오브아메리카|\$[A-Z]{1,5}\b")
KR_WRAP = re.compile(r"마감\s?시황|시장\s?정리|장\s?마감")
CHANNEL = {"hanwhastrategy": "한화투자증권 리서치센터", "strategy_kis": "한국투자증권 리서치"}     # 그 밖의 채널은 채널 이름 그대로


def channel_of(p):
    return p.get("post_url", "").split("/")[3] if p.get("post_url", "").count("/") >= 3 else ""


def _clean(s):
    s = news.URL.sub("", s)
    s = re.sub(r"^\s*(\[[^\]]{1,30}\]|【[^】]{1,30}】)\s*", "", s)
    s = re.sub(r"[★☆■□◆◇●○▶▷►※]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -·.")
    return s


def split(text):
    """→ (숫자 줄들, 문장들)"""
    nums, sents = [], []
    for raw in text.split("\n"):
        line = _clean(news.LEAD.sub("", raw.strip()))
        if not line:
            continue
        if NUMLINE.match(line) and len(line) < 90:
            nums.append(line)
            continue
        for s in re.split(r"(?<=[가-힣A-Za-z\)%])\.(?=\s|$)", line):
            s = _clean(s)
            if s:
                sents.append(s)
    return nums, sents


def _ok(s):
    if len(s) < 18 or not re.search(r"[가-힣]{2}", s):
        return False
    if re.search(r"마감\s?시황|시장\s?정리|^\(?\d{1,2}/\d{1,2}\)?$", s) and len(s) < 30:     # 제목 줄
        return False
    return True


ENDINGS = [(r"(했|하였)(습니다|네요|고요|어요|다)$", "함"), (r"(합니다|해요|한다|하고요)$", "함"),
           (r"(되었|됐)(습니다|네요|고요|다)$", "됨"), (r"(됩니다|돼요|된다)$", "됨"), (r"(입니다|이에요|예요|이고요)$", "임"),
           (r"왔(습니다|네요|고요|다)$", "옴"), (r"(습니다|네요|고요|어요)$", "음"), (r"겠음$", "겠음")]


def tone(s):
    """원문 말투(~했습니다, ~네요)를 요약체(~함, ~음)로"""
    s = s.rstrip(" .")
    if s.count("(") > s.count(")"):                         # 'est.' 처럼 잘린 괄호는 떼어 냄
        s = s[:s.rfind("(")].rstrip()
    m = re.match(r"^(.*?)(\s*\([^()]*\))$", s)               # 끝의 '(10/6 발표)' 는 잠시 떼었다 붙임
    body, tail = (m.group(1), m.group(2)) if m else (s, "")
    for pat, rep in ENDINGS:
        if re.search(pat, body):
            return re.sub(pat, rep, body) + tail
    return s


def _short(s, n=130):
    if len(s) <= n:
        return s
    cut = max(s.rfind(", ", 0, n), s.rfind(" 등 ", 0, n), s.rfind("며 ", 0, n), s.rfind("고 ", 0, n))
    return (s[:cut + 1].rstrip(", ") if cut > 50 else s[:n].rstrip()) + "…"


def _words(s):
    return set(re.findall(r"[가-힣A-Za-z0-9]{2,}", s))


def similar(a, b):
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) > 0.6


def digest(posts, n=6, need_reason=True):
    out = []
    for p in posts:
        for s in split(p["text"])[1]:
            if not _ok(s) or (need_reason and not REASON.search(s)):
                continue
            s = _short(tone(s))
            if any(similar(s, t) for t in out):
                continue
            out.append(s)
    return out[:n]


def title(text, n=70):
    """글 첫 문장을 짧은 제목으로"""
    r = news.parse(text)
    t = r["title"] if r else ""
    t = re.split(r"(?<=[가-힣A-Za-z\)%])\.(?=\s|$)", t)[0]
    return _short(tone(_clean(t)), n)


def merge_runs(posts, minutes=10):
    """같은 채널이 짧은 간격으로 이어 올린 글은 한 글로 합친다"""
    out = []
    for p in sorted(posts, key=lambda p: p["time"]):
        last = out[-1] if out else None
        if last and channel_of(last) == channel_of(p) and (p["time"] - last["time"]).total_seconds() <= minutes * 60:
            last["text"] += "\n" + p["text"]
        else:
            out.append(dict(p))
    return out


def numbers(posts, pat):
    out = []
    for p in posts:
        for l in split(p["text"])[0]:
            if re.search(pat, l) and l not in out:
                out.append(l)
    return out
