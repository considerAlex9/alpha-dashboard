"""한국투자증권 오픈 API — 시세 '조회'만 사용한다. 주문 기능은 넣지 않는다.

- 접근토큰은 24시간 유효하고, 유효한 동안 다시 요청하면 같은 토큰을 돌려준다. 발급 요청은 1분에 1회까지라
  겹치면 1분 기다렸다가 다시 받는다. 내 컴퓨터에서는 .kis_token.json 에 저장해 재사용한다 (.gitignore 처리).
- 키는 환경변수 KIS_APP_KEY / KIS_APP_SECRET (.env 또는 깃허브 비밀 보관함)
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

BASE = "https://openapi.koreainvestment.com:9443"   # 실전 서버 (시세 조회용)
TOKEN_CACHE = Path(__file__).resolve().parent / ".kis_token.json"
MIN_INTERVAL = 0.1    # 초당 10건 — 실전 계좌 한도(약 20건)보다 넉넉하게 (여러 스레드가 함께 씀)


class KIS:
    def __init__(self, app_key, app_secret):
        self.key, self.secret = app_key, app_secret
        self.token = self._token()
        self._last = 0.0
        self._lock = threading.Lock()      # 여러 스레드가 동시에 불러도 초당 호출 한도를 지키도록

    # ---------- 인증 ----------
    def _token(self):
        if TOKEN_CACHE.exists():
            c = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
            if c.get("app_key_tail") == self.key[-6:] and c["expires"] > (datetime.now() + timedelta(minutes=30)).isoformat():
                return c["token"]
        body = json.dumps({"grant_type": "client_credentials", "appkey": self.key, "appsecret": self.secret}).encode()
        for i in range(3):
            req = urllib.request.Request(f"{BASE}/oauth2/tokenP", data=body, headers={"content-type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    d = json.load(r)
                break
            except urllib.error.HTTPError as e:
                d = json.loads(e.read() or b"{}")
                if d.get("error_code") != "EGW00133" or i == 2:      # 1분에 1회 제한 → 기다렸다 다시
                    raise RuntimeError(f"토큰 발급 실패 {d.get('error_code')} {d.get('error_description')}")
                time.sleep(62)
        exp = datetime.strptime(d["access_token_token_expired"], "%Y-%m-%d %H:%M:%S")
        try:
            TOKEN_CACHE.write_text(json.dumps({"token": d["access_token"], "expires": exp.isoformat(),
                                               "app_key_tail": self.key[-6:]}), encoding="utf-8")
        except OSError:
            pass
        return d["access_token"]

    def get(self, path, tr_id, params, tries=6):
        for i in range(tries):
            with self._lock:
                wait = MIN_INTERVAL - (time.time() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.time()
            req = urllib.request.Request(f"{BASE}{path}?{urllib.parse.urlencode(params)}", headers={
                "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {self.token}",
                "appkey": self.key, "appsecret": self.secret, "tr_id": tr_id, "custtype": "P"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    d = json.load(r)
            except urllib.error.HTTPError as e:
                d = json.loads(e.read() or b"{}")
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:     # 연결 끊김 → 잠깐 쉬고 다시
                if i == tries - 1:
                    raise RuntimeError(f"{tr_id} 연결 실패: {e}")
                time.sleep(1.5 * (i + 1))
                continue
            if d.get("rt_cd") == "0":
                return d
            if d.get("msg_cd") == "EGW00201":        # 초당 호출 초과 → 잠깐 쉬고 재시도
                time.sleep(1.0 * (i + 1))
                continue
            raise RuntimeError(f"{tr_id} {d.get('msg_cd')} {d.get('msg1')}")
        raise RuntimeError(f"{tr_id} 호출 초과로 실패")

    # ---------- 일봉 ----------
    def _daily(self, path, tr_id, market, code, days, fields):
        """100개씩 끊어서 과거로 거슬러 받는다 → [[YYYY-MM-DD, 시가, 고가, 저가, 종가, 거래량], ...]"""
        end = datetime.now()
        rows = {}
        for _ in range(6):
            start = end - timedelta(days=140)
            d = self.get(path, tr_id, {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code,
                                       "FID_INPUT_DATE_1": start.strftime("%Y%m%d"), "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                                       "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
            got = [r for r in d.get("output2", []) if r.get("stck_bsop_date")]
            for r in got:
                dt = r["stck_bsop_date"]
                rows[dt] = [f"{dt[:4]}-{dt[4:6]}-{dt[6:]}", *(float(r[f] or 0) for f in fields)]
            if len(rows) >= days or not got:
                break
            end = datetime.strptime(min(r["stck_bsop_date"] for r in got), "%Y%m%d") - timedelta(days=1)
        return [rows[k] for k in sorted(rows)][-days:]

    def stock_daily(self, code, days=260):
        return self._daily("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100", "J", code, days,
                           ("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_clpr", "acml_vol", "acml_tr_pbmn"))

    def index_daily(self, code, days=260):
        """업종코드: 0001 코스피, 1001 코스닥, 2001 코스피200"""
        return self._daily("/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice", "FHKUP03500100", "U", code, days,
                           ("bstp_nmix_oprc", "bstp_nmix_hgpr", "bstp_nmix_lwpr", "bstp_nmix_prpr", "acml_vol"))

    # ---------- 수급 ----------
    def stock_investor(self, code):
        """종목별 투자자 매매동향(최근 약 30영업일). 금액 단위는 백만원"""
        return self.get("/uapi/domestic-stock/v1/quotations/inquire-investor", "FHKST01010900",
                        {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}).get("output", [])

    def market_investor(self, market, date):
        """시장별 투자자 매매동향(일별). market: KSP 코스피 / KSQ 코스닥"""
        idx = {"KSP": "0001", "KSQ": "1001"}[market]
        return self.get("/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market", "FHPTJ04040000",
                        {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": idx, "FID_INPUT_DATE_1": date,
                         "FID_INPUT_ISCD_1": market, "FID_INPUT_DATE_2": date, "FID_INPUT_ISCD_2": idx}).get("output", [])

    # ---------- 현재가·순위 ----------
    def price(self, code):
        """현재가 요약: 업종명, 시가총액(억), 등락률, 시장경고(00 없음 01 주의 02 경고 03 위험)"""
        d = self.get("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})["output"]
        return {"sector": d.get("bstp_kor_isnm") or "", "mcap": float(d.get("hts_avls") or 0) * 1e8,
                "price": float(d.get("stck_prpr") or 0), "chg1": float(d.get("prdy_ctrt") or 0) / 100,
                "tv": float(d.get("acml_tr_pbmn") or 0), "warn": d.get("mrkt_warn_cls_code") or "00",
                "market": d.get("rprs_mrkt_kor_name") or ""}

    def market_cap_top(self, index_code):
        """시가총액 상위 (한 번에 30종목). index_code: 0001 코스피, 1001 코스닥"""
        d = self.get("/uapi/domestic-stock/v1/ranking/market-cap", "FHPST01740000",
                     {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20174", "FID_DIV_CLS_CODE": "1",
                      "FID_INPUT_ISCD": index_code, "FID_TRGT_CLS_CODE": "0", "FID_TRGT_EXLS_CLS_CODE": "0",
                      "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "", "FID_VOL_CNT": ""})
        return [(r["mksc_shrn_iscd"], r["hts_kor_isnm"]) for r in d.get("output", []) if r.get("mksc_shrn_iscd")]

    # ---------- 장 마감 시황용 ----------
    def index_price(self, code):
        """업종 현재지수: 지수, 등락률, 거래대금(원), 상승·하락·보합·상한·하한 종목 수. code: 0001 코스피, 1001 코스닥"""
        o = self.get("/uapi/domestic-stock/v1/quotations/inquire-index-price", "FHPUP02100000",
                     {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code})["output"]
        n = lambda k: float(o.get(k) or 0)
        return {"close": n("bstp_nmix_prpr"), "chg1": n("bstp_nmix_prdy_ctrt") / 100, "value": n("acml_tr_pbmn") * 1e6,
                "up": int(n("ascn_issu_cnt")), "down": int(n("down_issu_cnt")), "flat": int(n("stnr_issu_cnt")),
                "limit_up": int(n("uplm_issu_cnt")), "limit_down": int(n("lslm_issu_cnt"))}

    def index_minutes(self, code, step=300):
        """업종 분봉 (step 초 단위, 300 = 5분) → 가장 최근 거래일 것만 [(HHMM, 지수)] 시간순"""
        return [(t, v) for t, v, _ in self.index_minutes_full(code, step)[1]]

    def index_minutes_full(self, code, step=300):
        """업종 분봉 → (거래일 YYYYMMDD, [(HHMM, 지수, 누적 거래대금 원)])"""
        d = self.get("/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice", "FHKUP03500200",
                     {"FID_COND_MRKT_DIV_CODE": "U", "FID_ETC_CLS_CODE": "0", "FID_INPUT_ISCD": code,
                      "FID_INPUT_HOUR_1": str(step), "FID_PW_DATA_INCU_YN": "Y"})
        rows = [r for r in d.get("output2", []) if r.get("stck_cntg_hour", "").isdigit() and r["stck_cntg_hour"] != "888888"]
        if not rows:
            return None, []
        day = max(r["stck_bsop_date"] for r in rows)
        return day, sorted((r["stck_cntg_hour"][:4], float(r["bstp_nmix_prpr"]), float(r.get("acml_tr_pbmn") or 0) * 1e6)
                           for r in rows if r["stck_bsop_date"] == day)

    def value_rank(self, index_code):
        """거래대금 상위 30종목 (ETF·ETN 포함). index_code: 0001 코스피, 1001 코스닥"""
        d = self.get("/uapi/domestic-stock/v1/quotations/volume-rank", "FHPST01710000",
                     {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171", "FID_INPUT_ISCD": index_code,
                      "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": "3", "FID_TRGT_CLS_CODE": "111111111",
                      "FID_TRGT_EXLS_CLS_CODE": "0000000000", "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
                      "FID_VOL_CNT": "", "FID_INPUT_DATE_1": ""})
        return [{"code": r["mksc_shrn_iscd"], "name": r["hts_kor_isnm"], "price": float(r["stck_prpr"]),
                 "chg1": float(r["prdy_ctrt"]) / 100, "value": float(r["acml_tr_pbmn"])}
                for r in d.get("output", []) if r.get("mksc_shrn_iscd")]

    def rates(self):
        """금리 종합: 국고채 등 국내 금리(2)와 해외 금리(1) → {이름: (금리, 전일 대비 %p)}"""
        out = {}
        for div in ("2", "1"):
            d = self.get("/uapi/domestic-stock/v1/quotations/comp-interest", "FHPST07020000",
                         {"FID_COND_MRKT_DIV_CODE": "I", "FID_COND_SCR_DIV_CODE": "20702", "FID_DIV_CLS_CODE": div,
                          "FID_DIV_CLS_CODE1": ""})
            for r in d.get("output1") or []:
                try:
                    out[r["hts_kor_isnm"].strip()] = (float(r["bond_mnrt_prpr"]), float(r["bond_mnrt_prdy_vrss"]))
                except (KeyError, ValueError):
                    pass
        return out

    def investor_rank(self, market, who):
        """외국인·기관 순매수 금액 상위 30 (장중 가집계). market: 0001 코스피, 1001 코스닥 · who: 1 외국인, 2 기관
        → [{code, name, price, chg1, net(원)}]"""
        d = self.get("/uapi/domestic-stock/v1/quotations/foreign-institution-total", "FHPTJ04400000",
                     {"FID_COND_MRKT_DIV_CODE": "V", "FID_COND_SCR_DIV_CODE": "16449", "FID_INPUT_ISCD": market,
                      "FID_DIV_CLS_CODE": "1", "FID_RANK_SORT_CLS_CODE": "0", "FID_ETC_CLS_CODE": who})
        key = "frgn_ntby_tr_pbmn" if who == "1" else "orgn_ntby_tr_pbmn"
        return [{"code": r["mksc_shrn_iscd"], "name": r["hts_kor_isnm"], "price": float(r["stck_prpr"] or 0),
                 "chg1": float(r["prdy_ctrt"] or 0) / 100, "net": float(r[key] or 0) * 1e6}
                for r in d.get("output", []) if r.get("mksc_shrn_iscd")]

    # ---------- 공매도 ----------
    def short_sale(self, code, days=30):
        end = datetime.now()
        d = self.get("/uapi/domestic-stock/v1/quotations/daily-short-sale", "FHPST04830000",
                     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                      "FID_INPUT_DATE_1": (end - timedelta(days=days * 2)).strftime("%Y%m%d"),
                      "FID_INPUT_DATE_2": end.strftime("%Y%m%d")})
        return d.get("output2", [])
