"""한국투자증권 오픈 API — 시세 '조회'만 사용한다. 주문 기능은 넣지 않는다.

- 접근토큰은 1분에 1회만 발급되므로, 내 컴퓨터에서는 .kis_token.json 에 저장해 재사용한다
  (이 파일은 .gitignore 로 깃허브에 올라가지 않음). 깃허브 자동 실행은 매번 새로 발급한다.
- 키는 환경변수 KIS_APP_KEY / KIS_APP_SECRET (.env 또는 깃허브 비밀 보관함)
"""
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

BASE = "https://openapi.koreainvestment.com:9443"   # 실전 서버 (시세 조회용)
TOKEN_CACHE = Path(__file__).resolve().parent / ".kis_token.json"
MIN_INTERVAL = 0.07   # 실전 계좌 초당 호출 제한(약 20건)보다 여유 있게


class KIS:
    def __init__(self, app_key, app_secret):
        self.key, self.secret = app_key, app_secret
        self.token = self._token()
        self._last = 0.0

    # ---------- 인증 ----------
    def _token(self):
        if TOKEN_CACHE.exists():
            c = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
            if c.get("app_key_tail") == self.key[-6:] and c["expires"] > (datetime.now() + timedelta(minutes=30)).isoformat():
                return c["token"]
        body = json.dumps({"grant_type": "client_credentials", "appkey": self.key, "appsecret": self.secret}).encode()
        req = urllib.request.Request(f"{BASE}/oauth2/tokenP", data=body, headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
        exp = datetime.strptime(d["access_token_token_expired"], "%Y-%m-%d %H:%M:%S")
        try:
            TOKEN_CACHE.write_text(json.dumps({"token": d["access_token"], "expires": exp.isoformat(),
                                               "app_key_tail": self.key[-6:]}), encoding="utf-8")
        except OSError:
            pass
        return d["access_token"]

    def get(self, path, tr_id, params, tries=3):
        for i in range(tries):
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
            if d.get("rt_cd") == "0":
                return d
            if d.get("msg_cd") == "EGW00201":        # 초당 호출 초과 → 잠깐 쉬고 재시도
                time.sleep(1 + i)
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
                           ("stck_oprc", "stck_hgpr", "stck_lwpr", "stck_clpr", "acml_vol"))

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

    # ---------- 공매도 ----------
    def short_sale(self, code, days=30):
        end = datetime.now()
        d = self.get("/uapi/domestic-stock/v1/quotations/daily-short-sale", "FHPST04830000",
                     {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                      "FID_INPUT_DATE_1": (end - timedelta(days=days * 2)).strftime("%Y%m%d"),
                      "FID_INPUT_DATE_2": end.strftime("%Y%m%d")})
        return d.get("output2", [])
