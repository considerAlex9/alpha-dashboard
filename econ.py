"""이번 주 주요 경제 일정 — 무료 공개 일정표(Forex Factory 주간 자료)를 받아 한국 시간·한국어로 정리한다.

- 중요도 '높음'은 미국·중국·일본·유로존·영국, '보통'은 미국·중국만 남긴다.
- 지난 일정도 같은 주 안이면 남겨 둔다 (주간 자료가 일요일에 바뀌기 때문에 이전 값과 합친다).
"""
import json
import re
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
KEEP_DAYS = 10

COUNTRY = {"USD": "미국", "CNY": "중국", "JPY": "일본", "EUR": "유로존", "GBP": "영국", "CAD": "캐나다",
           "AUD": "호주", "NZD": "뉴질랜드", "CHF": "스위스", "All": "전체"}
HIGH = {"USD", "CNY", "JPY", "EUR", "GBP"}
MEDIUM = {"USD", "CNY"}

# 제목 전체를 바로 바꾸는 표 (자주 나오는 것)
TITLE = {
    "Non-Farm Employment Change": "비농업 고용",
    "ADP Non-Farm Employment Change": "ADP 민간 고용",
    "Unemployment Rate": "실업률",
    "Unemployment Claims": "주간 신규 실업수당 청구",
    "Average Hourly Earnings m/m": "시간당 임금 (전월 대비)",
    "CPI m/m": "소비자물가 (전월 대비)",
    "CPI y/y": "소비자물가 (전년 대비)",
    "Core CPI m/m": "근원 소비자물가 (전월 대비)",
    "Core CPI y/y": "근원 소비자물가 (전년 대비)",
    "PPI m/m": "생산자물가 (전월 대비)",
    "PPI y/y": "생산자물가 (전년 대비)",
    "Core PPI m/m": "근원 생산자물가 (전월 대비)",
    "Core PCE Price Index m/m": "근원 PCE 물가 (전월 대비)",
    "PCE Price Index m/m": "PCE 물가 (전월 대비)",
    "Retail Sales m/m": "소매판매 (전월 대비)",
    "Core Retail Sales m/m": "근원 소매판매 (전월 대비)",
    "Advance GDP q/q": "GDP 속보치 (전분기 대비)",
    "Prelim GDP q/q": "GDP 잠정치 (전분기 대비)",
    "Final GDP q/q": "GDP 확정치 (전분기 대비)",
    "GDP q/q": "GDP (전분기 대비)",
    "GDP y/y": "GDP (전년 대비)",
    "ISM Manufacturing PMI": "ISM 제조업 지수",
    "ISM Services PMI": "ISM 서비스업 지수",
    "Flash Manufacturing PMI": "제조업 PMI 속보치",
    "Flash Services PMI": "서비스업 PMI 속보치",
    "Manufacturing PMI": "제조업 PMI",
    "Services PMI": "서비스업 PMI",
    "Caixin Manufacturing PMI": "차이신 제조업 PMI",
    "Caixin Services PMI": "차이신 서비스업 PMI",
    "JOLTS Job Openings": "JOLTS 구인 건수",
    "Prelim UoM Consumer Sentiment": "미시간대 소비자심리 (예비)",
    "Revised UoM Consumer Sentiment": "미시간대 소비자심리 (확정)",
    "Prelim UoM Inflation Expectations": "미시간대 기대인플레이션 (예비)",
    "CB Consumer Confidence": "콘퍼런스보드 소비자신뢰",
    "Federal Funds Rate": "미국 기준금리 결정",
    "FOMC Statement": "FOMC 성명서",
    "FOMC Press Conference": "FOMC 기자회견",
    "FOMC Meeting Minutes": "FOMC 의사록 공개",
    "FOMC Economic Projections": "FOMC 경제 전망",
    "Main Refinancing Rate": "ECB 기준금리 결정",
    "Monetary Policy Statement": "통화정책 성명",
    "ECB Press Conference": "ECB 기자회견",
    "BOJ Policy Rate": "일본은행 기준금리 결정",
    "BOJ Press Conference": "일본은행 기자회견",
    "Official Bank Rate": "영국 기준금리 결정",
    "Trade Balance": "무역수지",
    "Industrial Production m/m": "산업생산 (전월 대비)",
    "Industrial Production y/y": "산업생산 (전년 대비)",
    "Durable Goods Orders m/m": "내구재 주문 (전월 대비)",
    "Core Durable Goods Orders m/m": "근원 내구재 주문 (전월 대비)",
    "Empire State Manufacturing Index": "뉴욕 제조업 지수",
    "Philly Fed Manufacturing Index": "필라델피아 제조업 지수",
    "Building Permits": "건축 허가",
    "Housing Starts": "주택 착공",
    "Existing Home Sales": "기존 주택 판매",
    "New Home Sales": "신규 주택 판매",
    "Crude Oil Inventories": "원유 재고",
    "Natural Gas Storage": "천연가스 재고",
    "Treasury Currency Report": "미 재무부 환율 보고서",
    "Bank Holiday": "휴장 (공휴일)",
    "New Loans": "신규 위안화 대출",
    "M2 Money Supply y/y": "M2 통화량 (전년 대비)",
    "Tankan Manufacturing Index": "단칸 제조업 지수",
    "Household Spending y/y": "가계 지출 (전년 대비)",
    "German Prelim CPI m/m": "독일 소비자물가 예비치 (전월 대비)",
    "CPI Flash Estimate y/y": "소비자물가 속보치 (전년 대비)",
    "Core CPI Flash Estimate y/y": "근원 소비자물가 속보치 (전년 대비)",
    "Claimant Count Change": "실업수당 청구 변화",
    "Employment Change": "고용 변화",
    "10-y Bond Auction": "10년물 국채 입찰",
    "30-y Bond Auction": "30년물 국채 입찰",
}
# 발언자 이름
NAMES = {"Powell": "파월", "Ueda": "우에다", "Lagarde": "라가르드", "Bailey": "베일리", "Waller": "월러",
         "Jefferson": "제퍼슨", "Williams": "윌리엄스", "Bowman": "보먼", "Cook": "쿡", "Barr": "바", "Logan": "로건",
         "Goolsbee": "굴스비", "Hammack": "해맥", "Musalem": "무살렘", "Kashkari": "카시카리", "Daly": "데일리",
         "Bostic": "보스틱", "Schmid": "슈미드", "Miran": "미란", "Bessent": "베선트", "Trump": "트럼프", "Hassett": "해싯",
         "Warsh": "워시", "Pan": "판궁성", "Himino": "히미노", "Uchida": "우치다"}
ROLE = [("Fed Chair", "연준 의장"), ("FOMC Member", "FOMC 위원"), ("BOJ Gov", "일본은행 총재"), ("ECB President", "ECB 총재"),
        ("BOE Gov", "영란은행 총재"), ("PBOC Gov", "인민은행 총재"), ("Treasury Secretary", "재무장관"),
        ("President", "대통령"), ("Fed Vice Chair", "연준 부의장"), ("BOJ Deputy Gov", "일본은행 부총재")]
WORD = [("Prelim ", "예비 "), ("Flash ", "속보 "), ("Final ", "확정 "), ("Revised ", "수정 "), ("Core ", "근원 "),
        (" m/m", " (전월 대비)"), (" y/y", " (전년 대비)"), (" q/q", " (전분기 대비)"), ("German ", "독일 "),
        ("French ", "프랑스 "), ("Italian ", "이탈리아 "), ("Spanish ", "스페인 ")]


def ko_title(t):
    if t in TITLE:
        return TITLE[t]
    m = re.match(r"(.+?) Speaks$", t)
    if m:                                       # 'BOJ Gov Ueda Speaks' → '일본은행 총재 우에다 발언'
        who = m.group(1)
        for en, ko in sorted(ROLE, key=lambda r: -len(r[0])):
            who = who.replace(en, ko)
        for en, ko in NAMES.items():
            who = re.sub(rf"\b{en}\b", ko, who)
        return f"{who} 발언"
    base = t
    for en, ko in WORD:
        base = base.replace(en, ko)
    for en, ko in TITLE.items():                # 앞뒤 수식어를 뗀 본 이름이 표에 있으면 바꿈
        if en in base:
            base = base.replace(en, ko)
            break
    return base


def collect(prev=None, log=print):
    now = datetime.now(KST)
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        rows = json.load(r)
    events = {}
    for e in (prev or {}).get("events", []):        # 지난번에 받아 둔 일정 (이번 주 자료에서 빠진 지난 일정 보존)
        if datetime.fromisoformat(e["time"]) >= now - timedelta(days=KEEP_DAYS):
            events[e["key"]] = e
    for e in rows:
        cur, imp = e.get("country"), e.get("impact")
        if not ((imp == "High" and cur in HIGH) or (imp == "Medium" and cur in MEDIUM) or (imp == "Holiday" and cur in HIGH)):
            continue
        t = datetime.fromisoformat(e["date"]).astimezone(KST)
        key = f"{cur}|{e['title']}|{t:%Y-%m-%d}"
        events[key] = {"key": key, "time": t.isoformat(), "country": COUNTRY.get(cur, cur), "title": ko_title(e["title"]),
                       "title_en": e["title"], "impact": {"High": "높음", "Medium": "보통", "Holiday": "휴일"}[imp],
                       "forecast": e.get("forecast") or "", "previous": e.get("previous") or "",
                       "actual": e.get("actual") or (events.get(key) or {}).get("actual", "")}
    out = sorted(events.values(), key=lambda e: e["time"])
    log(f"  경제 일정: {len(out)}건 (중요도 높음 {sum(e['impact'] == '높음' for e in out)}건)")
    return {"captured_at": now.isoformat(), "source": "Forex Factory 주간 일정", "events": out}


if __name__ == "__main__":
    d = collect()
    for e in d["events"]:
        print(e["time"][5:16], e["country"], e["impact"], e["title"], "|", e["title_en"], e["forecast"], e["previous"])
