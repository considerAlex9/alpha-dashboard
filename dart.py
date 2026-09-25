"""금융감독원 오픈다트(DART) 공시 수집 — 보유 종목의 최근 공시 목록.

- 종목코드 → 다트 고유번호 변환표(corpCode.xml)는 하루 한 번만 받아 .dart_corp.json 에 저장한다
  (깃허브 자동 실행에서는 매번 새로 받는다. 이 파일은 .gitignore 로 올라가지 않음)
- 키: 환경변수 DART_API_KEY (.env 또는 깃허브 비밀 보관함)
"""
import io
import json
import re
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
BASE = "https://opendart.fss.or.kr/api"
CACHE = Path(__file__).resolve().parent / ".dart_corp.json"

# (분류, 제목에 들어가는 말들) — 위에서부터 먼저 맞는 것으로 분류
CATEGORIES = [
    ("거래정지·관리", ["매매거래정지", "거래정지", "영업정지", "관리종목", "상장폐지", "상장적격성", "불성실공시", "감사의견"]),
    ("자금조달·희석", ["유상증자", "전환사채", "신주인수권부사채", "교환사채", "무상증자", "감자", "주식관련사채"]),
    ("실적", ["잠정", "영업실적", "매출액또는손익구조", "손익구조"]),
    ("계약·수주", ["공급계약", "판매ㆍ공급", "수주"]),
    ("최대주주·경영권", ["최대주주변경", "경영권", "공개매수", "대표이사"]),
    ("조회공시·해명", ["조회공시", "풍문또는보도"]),
    ("자사주·배당", ["자기주식", "현금ㆍ현물배당", "배당", "주식소각"]),
    ("합병·분할", ["합병", "분할", "영업양수", "영업양도", "자산양수", "자산양도", "타법인주식"]),
    ("소송·제재", ["소송", "횡령", "배임", "제재", "벌금"]),
    ("시장조치", ["투자주의", "투자경고", "투자위험", "단기과열", "공매도"]),
    ("지분 변동", ["최대주주등소유주식변동", "대량보유상황", "임원ㆍ주요주주", "특정증권등소유"]),
    ("정기보고서", ["사업보고서", "반기보고서", "분기보고서"]),
    ("증권 발행 서류", ["투자설명서", "일괄신고", "증권발행실적", "증권신고서", "파생결합"]),
]
IMPORTANT = {"거래정지·관리", "자금조달·희석", "실적", "계약·수주", "최대주주·경영권", "조회공시·해명", "합병·분할", "소송·제재", "시장조치"}


def _get(path, key, params, raw=False):
    q = urllib.parse.urlencode({"crtfc_key": key, **params})
    with urllib.request.urlopen(f"{BASE}/{path}?{q}", timeout=60) as r:
        body = r.read()
    return body if raw else json.loads(body)


def corp_codes(key):
    """종목코드(6자리) → 다트 고유번호(8자리)"""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if CACHE.exists():
        c = json.loads(CACHE.read_text(encoding="utf-8"))
        if c.get("date") == today:
            return c["map"]
    z = zipfile.ZipFile(io.BytesIO(_get("corpCode.xml", key, {}, raw=True)))
    xml = z.read(z.namelist()[0]).decode("utf-8")
    m = {}
    for block in re.findall(r"<list>(.*?)</list>", xml, re.S):
        sc = re.search(r"<stock_code>\s*(\d{6})\s*</stock_code>", block)
        cc = re.search(r"<corp_code>(\d{8})</corp_code>", block)
        if sc and cc:
            m[sc.group(1)] = cc.group(1)
    try:
        CACHE.write_text(json.dumps({"date": today, "map": m}), encoding="utf-8")
    except OSError:
        pass
    return m


def classify(title):
    t = title.replace(" ", "")
    for cat, words in CATEGORIES:
        if any(w in t for w in words):
            return cat
    return "기타"


def collect(key, holdings, days=60, log=print):
    """holdings: [(종목코드, 이름, 'long'|'short'), ...] → 최근 공시 목록 (최신순)"""
    cmap = corp_codes(key)
    end = datetime.now(KST)
    bgn = (end - timedelta(days=days)).strftime("%Y%m%d")
    out, missing = [], []
    for code, name, side in holdings:
        cc = cmap.get(code) or cmap.get(code[:5] + "0")      # 우선주는 보통주 회사의 공시를 쓴다
        if not cc:
            missing.append(name)
            continue
        d = _get("list.json", key, {"corp_code": cc, "bgn_de": bgn, "end_de": end.strftime("%Y%m%d"), "page_count": 100})
        if d.get("status") not in ("000", "013"):          # 013 = 조회된 데이터 없음
            raise RuntimeError(f"다트 {d.get('status')} {d.get('message')}")
        for r in d.get("list", []):
            title = re.sub(r"\s+", " ", r["report_nm"]).strip()
            cat = classify(title)
            out.append({"code": code, "name": name, "side": side, "title": title, "category": cat,
                        "important": cat in IMPORTANT, "correction": "정정]" in title,
                        "date": f"{r['rcept_dt'][:4]}-{r['rcept_dt'][4:6]}-{r['rcept_dt'][6:]}",
                        "filer": r.get("flr_nm", ""), "rcept_no": r["rcept_no"]})
        time.sleep(0.1)
    out.sort(key=lambda x: x["rcept_no"], reverse=True)
    log(f"  다트 공시: {len(holdings) - len(missing)}종목 · {len(out)}건 (최근 {days}일)"
        + (f" · 고유번호 없음 {missing}" if missing else ""))
    return {"captured_at": end.isoformat(), "days": days, "items": out}
