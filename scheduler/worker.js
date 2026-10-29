// 롱숏 보드 자동 업데이트 시계 — 클라우드플레어 워커 (크론 트리거 "*/10 * * * *")
// 깃허브 예약 실행이 자주 늦거나 빠져서, 정해진 시각에 깃허브에 "지금 실행" 요청을 보낸다.
// 필요한 비밀값: GH_TOKEN (깃허브 토큰 — 이 저장소의 Actions 읽기·쓰기 권한만)
const REPO = "considerAlex9/alpha-dashboard";

// 한국 시간 기준으로 지금 돌릴 작업 고르기
function jobsAt(now) {
  const k = new Date(now.getTime() + 9 * 3600e3);
  const wd = k.getUTCDay(), t = k.getUTCHours() * 60 + k.getUTCMinutes();
  const at = (h, m) => t >= h * 60 + m && t < h * 60 + m + 10;   // 10분 칸 안에 들면
  const weekday = wd >= 1 && wd <= 5;
  const jobs = [];
  if (wd >= 2 && wd <= 6 && at(7, 0)) jobs.push("us.yml");        // 미국장 마감 다음 날 아침 07:00
  if (!weekday) return jobs;
  if (at(8, 0)) jobs.push("update.yml");                            // 장전 브리핑
  if (t >= 9 * 60 && t < 16 * 60) jobs.push(at(Math.floor(t / 60), 10) ? "update.yml" : "quick.yml");   // 장중: 매시 10분 전체, 나머지 10분 빠른
  if (at(16, 0) || at(16, 10) || at(16, 30) || at(19, 0)) jobs.push("update.yml");   // 장마감 브리핑·마감 확정·시황·최종
  if (at(16, 40)) jobs.push("kr.yml");                              // 기술적 분석·실적 캘린더
  return jobs;
}

async function dispatch(env, file) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${file}/dispatches`, {
    method: "POST",
    headers: { Authorization: `Bearer ${env.GH_TOKEN}`, Accept: "application/vnd.github+json", "User-Agent": "ak-board-scheduler",
               "X-GitHub-Api-Version": "2022-11-28" },
    body: JSON.stringify({ ref: "main" }),
  });
  return `${file}: ${r.status}`;
}

export default {
  async scheduled(event, env, ctx) {
    const jobs = jobsAt(new Date(event.scheduledTime));
    ctx.waitUntil(Promise.all(jobs.map(j => dispatch(env, j))).then(r => console.log(r.join(", ") || "실행할 작업 없음")));
  },
  // 주소로 열면 지금 시각에 무엇을 돌릴지 보여 줌 (실행은 안 함)
  async fetch(req, env) {
    const jobs = jobsAt(new Date());
    return new Response(`지금(한국 시간) 돌릴 작업: ${jobs.join(", ") || "없음"}\n토큰 설정: ${env.GH_TOKEN ? "있음" : "없음"}`,
      { headers: { "content-type": "text/plain; charset=utf-8" } });
  },
};
