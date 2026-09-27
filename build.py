"""data/ 에 쌓인 값으로 dashboard.html 을 만든다. (collect.py 다음에 실행)"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def read_opt(name, default):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def main():
    snaps = sorted((DATA / "snapshots").glob("*.json"))
    if not snaps:
        raise SystemExit("[오류] 스냅샷이 없습니다. 먼저 collect.py 를 실행하세요.")
    latest = json.loads(snaps[-1].read_text(encoding="utf-8"))
    payload = {
        "captured_at": latest["captured_at"],
        "portfolio": latest["portfolio"],
        "orders": latest["orders"],
        "leaderboard": latest["leaderboard"],
        "history": json.loads((DATA / "history.json").read_text(encoding="utf-8")),
        "trades": json.loads((DATA / "trades_all.json").read_text(encoding="utf-8"))[:300],
        "sector_sentiment": latest.get("sector_sentiment", []),
        "market": read_opt("market.json", None),
        "flows_history": read_opt("flows_history.json", []),
        "disclosures": read_opt("disclosures.json", None),
        "news": read_opt("news.json", None),
        "themes": json.loads((ROOT / "sectors.json").read_text(encoding="utf-8")) if (ROOT / "sectors.json").exists() else {},
    }
    # </script> 가 데이터에 섞여도 페이지가 깨지지 않게
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = (ROOT / "template.html").read_text(encoding="utf-8").replace("/*__DATA__*/null", blob)
    # index.html 은 웹(GitHub Pages) 첫 화면용, dashboard.html 은 로컬에서 여는 용
    # site/index.html 은 클라우드플레어 사이트용 — 이 폴더만 공개되므로 코드·데이터 파일은 올라가지 않는다
    (ROOT / "site").mkdir(exist_ok=True)
    for path in (ROOT / "index.html", ROOT / "dashboard.html", ROOT / "site" / "index.html"):
        path.write_text(html, encoding="utf-8")
    print(f"index.html / dashboard.html 생성 완료 ({snaps[-1].stem} 기준)")


if __name__ == "__main__":
    main()
