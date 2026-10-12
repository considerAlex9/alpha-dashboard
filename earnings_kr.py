"""국내 실적 시즌 캘린더 — 다트(전자공시)에서 이번 분기 실적 발표 일정과 이미 나온 잠정실적을 모은다.

- 기업설명회(IR) 개최 공시: 목적·주요내용에 '실적'이 들어간 것만 (증권사 행사·사업 설명회는 제외)
- 결산실적공시 예고: 결산 기간이 이번 분기 말로 끝나는 것
- 영업(잠정)실적 공시: 실적 기간이 이번 분기(3개월)인 것 → 매출액·영업이익·당기순이익, 전년 동기·직전 분기 대비
한 번 읽은 공시는 data/earnings_kr.json 에 저장해 두고 다시 받지 않는다.
"""
import html
import io
import json
import re
import time
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
KST = timezone(timedelta(hours=9))
API = "https://opendart.fss.or.kr/api"
EARN = re.compile(r"3분기|3Q|3 분기|분기\s*(경영)?\s*실적|경영\s*실적|실적\s*(발표|설명)|영업\s*실적|잠정\s*실적|결산\s*실적|컨퍼런스\s*콜|어닝")


EVENT = re.compile(r"Corporate\s*Day|Conference|컨퍼런스\s*참|NDR|Non-Deal|Roadshow|로드쇼|Forum|포럼|투자자\s*방문|1:1|소그룹|Investor\s*Day", re.I)
Q3 = re.compile(r"3\s*분기|3Q|3rd\s*Quarter", re.I)


def is_earnings_ir(v, qe):
    """실적 발표 IR 인지: 분기 말 1주 뒤 이후 일정이면서 '3분기'가 적혀 있거나, 실적 발표·컨퍼런스콜 같은 분명한 표현이 있는 것"""
    if v["date"] < (qe + timedelta(days=7)).isoformat():
        return False
    if Q3.search(v["desc"]):
        return True
    strong = re.compile(r"실적\s*발표|경영\s*실적\s*(발표|설명)|실적\s*설명회|컨퍼런스\s*콜|Conference\s*Call|어닝\s*콜", re.I)
    return bool(strong.search(v["desc"]) or strong.search(v.get("how", ""))) and not EVENT.search(v["desc"])


def quarter_of(today):
    """가장 최근에 끝난 분기 → (연도, 분기, 시작일, 종료일)"""
    q_end_month = ((today.month - 1) // 3) * 3
    y = today.year
    if q_end_month == 0:
        y, q_end_month = y - 1, 12
    q = q_end_month // 3
    start = date(y, q_end_month - 2, 1)
    end = (date(y + (q_end_month == 12), q_end_month % 12 + 1, 1) - timedelta(days=1))
    return y, q, start, end


def _get_json(path, key, params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    with urllib.request.urlopen(f"{API}/{path}?crtfc_key={key}&{q}", timeout=30) as r:
        return json.load(r)


def _doc(key, rcept_no):
    with urllib.request.urlopen(f"{API}/document.xml?crtfc_key={key}&rcept_no={rcept_no}", timeout=30) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    t = z.read(z.namelist()[0]).decode("utf-8", "replace")
    t = re.sub(r"<style.*?</style>", " ", t, flags=re.S | re.I)
    t = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)))
    j = t.rfind("} ", 0, 2000)                      # 앞부분 서식 글자 제거
    return t[j + 2:] if j > 0 else t


def _num(s):
    s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse(kind, text, qs, qe):
    """공시 본문 → 필요한 값 (해당 없으면 None)"""
    if kind == "ir":
        m = re.search(r"1\.\s*일시(.{0,200})", text)         # 코스닥 양식 '1. 일시 … 시작일' · 코스피 양식 '1. 일시 및 장소 일시 …'
        seg = m.group(1) if m else text[:400]
        d = re.search(r"(20\d{2}-\d{2}-\d{2})", seg)
        tm = re.search(r"(\d{1,2}:\d{2})", seg)
        purpose = re.search(r"(?:실시목적|개최목적)\s*(.{0,150}?)\s*(?:\d\.\s|$)", text)
        content = re.search(r"(?:주요내용|주요\s*설명회\s*내용\s*\(요약\))\s*(.{0,250}?)\s*(?:\d\.\s|$)", text)
        desc = " ".join(x.group(1).strip() for x in (purpose, content) if x)
        if not d or not EARN.search(desc):
            return None
        if re.search(r"[12]\s*분기|[12]Q|상반기", desc) and not re.search(r"3\s*분기|3Q", desc):
            return None                             # 지난 분기 실적 설명회
        how = re.search(r"(?:실시방법|개최방법)\s*(.{0,60}?)\s*\d\.\s", text)
        return {"date": d.group(1), "time": tm.group(1) if tm else "", "desc": desc[:160], "how": how.group(1).strip()[:60] if how else ""}
    if kind == "notice":
        end = re.search(r"종료일\s*(20\d{2}-\d{2}-\d{2})", text)
        d = re.search(r"공시\s*예정일\s*(20\d{2}-\d{2}-\d{2})", text)
        if not d or (end and end.group(1) != qe.isoformat()):
            return None
        return {"date": d.group(1), "time": "", "desc": "결산실적 공시 예정"}
    if kind == "result":
        per = re.search(r"당기실적\s*(20\d{2}-\d{2}-\d{2})\s*~\s*(20\d{2}-\d{2}-\d{2})", text)
        if not per or per.group(1) != qs.isoformat() or per.group(2) != qe.isoformat():
            return None                             # 월별 실적 등은 제외
        um = re.search(r"단위\s*:\s*(조원|십억원|억원|백만원|천원|원)", text)
        unit = {"조원": 1e12, "십억원": 1e9, "억원": 1e8, "백만원": 1e6, "천원": 1e3, "원": 1}[um.group(1)] if um else 1e6
        out = {"consolidated": "연결" in text[:300]}
        for key, label in (("sales", "매출액"), ("op", "영업이익"), ("net", "당기순이익")):
            m = re.search(label + r"\s*당해실적\s*([-\d,\.]+)\s+([-\d,\.]+)\s+([-\d\.]+)\s+(\S+)\s+([-\d,\.]+)\s+([-\d\.]+)", text)
            if m:
                cur, prv, qoq, _, yoy_v, yoy = (_num(x) for x in m.groups())
                out[key] = {"cur": cur * unit / 1e8 if cur is not None else None, "prev_q": prv * unit / 1e8 if prv is not None else None,
                            "yoy_v": yoy_v * unit / 1e8 if yoy_v is not None else None, "qoq": qoq, "yoy": yoy}
        return out
    return None


def collect(key, log=print):
    now = datetime.now(KST)
    y, q, qs, qe = quarter_of(now.date())
    p = DATA / "earnings_kr.json"
    old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    cache = old.get("cache", {}) if old.get("quarter") == f"{y}-{q}" else {}
    bgn = qe - timedelta(days=35)
    end = min(now.date(), qe + timedelta(days=80))
    rows, page = [], 1
    while True:
        d = _get_json("list.json", key, {"bgn_de": bgn.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"), "pblntf_ty": "I",
                                         "page_no": page, "page_count": 100})
        if d.get("status") != "000":
            break
        for r in d.get("list", []):
            nm = r["report_nm"].replace(" ", "")
            kind = "ir" if "기업설명회" in nm and "결과" not in nm else "notice" if "결산실적공시예고" in nm else \
                "result" if "잠정)실적" in nm else None
            if kind and r.get("stock_code"):
                rows.append((kind, r))
        if page >= d.get("total_page", 1):
            break
        page += 1
        time.sleep(0.15)
    new = 0
    for kind, r in rows:
        rn = r["rcept_no"]
        if rn in cache:
            continue
        try:
            cache[rn] = {"kind": kind, "v": parse(kind, _doc(key, rn), qs, qe)}
            new += 1
        except Exception as e:  # noqa: BLE001
            log(f"  [경고] 다트 문서 {rn} 실패: {e}")
        time.sleep(0.12)
    # 기업별로 묶기
    cos = {}
    for kind, r in rows:
        v = (cache.get(r["rcept_no"]) or {}).get("v")
        if not v or (kind == "ir" and not is_earnings_ir(v, qe)):
            continue
        c = cos.setdefault(r["stock_code"], {"code": r["stock_code"], "name": r["corp_name"], "mkt": {"Y": "코스피", "K": "코스닥"}.get(r["corp_cls"], "기타"),
                                              "sched": [], "result": None})
        if kind == "result":
            if not c["result"] or r["rcept_no"] > c["result"]["rcept_no"]:
                c["result"] = {**v, "date": f"{r['rcept_dt'][:4]}-{r['rcept_dt'][4:6]}-{r['rcept_dt'][6:]}", "rcept_no": r["rcept_no"]}
        else:
            c["sched"] = [x for x in c["sched"] if not (x["kind"] == kind and x["date"] == v["date"])]
            c["sched"].append({**v, "kind": kind, "rcept_no": r["rcept_no"]})
    # 시가총액·내 보유 표시 (기술적 분석 자료·최근 스냅샷)
    ta = DATA / "kr_ta.json"
    mc = {s["s"]: s["mc"] for s in json.loads(ta.read_text(encoding="utf-8"))["stocks"]} if ta.exists() else {}
    for c in cos.values():
        c["mc"] = mc.get(c["code"])
        c["sched"].sort(key=lambda x: (x["date"], x["time"]))
    out = {"quarter": f"{y}-{q}", "label": f"{y}년 {q}분기", "period": [qs.isoformat(), qe.isoformat()], "captured_at": now.isoformat(),
           "companies": sorted(cos.values(), key=lambda c: -(c["mc"] or 0)), "cache": cache}
    p.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"  실적 캘린더 ({out['label']}): 공시 {len(rows)}건(새로 읽음 {new}) → 기업 {len(cos)}곳 · 일정 {sum(1 for c in cos.values() if c['sched'])} · 발표 완료 {sum(1 for c in cos.values() if c['result'])}")
    return out


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    import collect as col
    collect(col.load_env()["DART_API_KEY"])
