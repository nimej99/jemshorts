#!/usr/bin/env node
// promo-shorts M0: CLI 브리지 스파이크.
// claude / codex 각각 짧은 한국어 프롬프트로 헤드리스 호출 n회 실행,
// 성공(비어있지 않은 텍스트 + exit 0) 횟수와 평균 레이턴시를 측정한다.
// 계정 미로그인/CLI 오류는 실패로 기록하고 계속 진행한다.
//
// 사용법: node scripts/bridge_spike.mjs [runs]   (기본 runs=10)

import { runCli } from "../bridge/promo_bridge.mjs";

const RUNS = Number(process.argv[2] || 10);
const PROMPT = "옷을 파는 가게의 쇼츠 훅 한 문장만 써줘.";
const CLIS = ["claude", "codex"];

async function spike(cliName) {
  const results = [];
  for (let i = 1; i <= RUNS; i += 1) {
    const t0 = Date.now();
    let r;
    try {
      r = await runCli(cliName, PROMPT); // 재시도 없이 1회 = 1시도
    } catch (e) {
      r = { ok: false, text: "", error: `unexpected: ${e.message}` };
    }
    const ms = Date.now() - t0;
    results.push({ run: i, ok: r.ok, ms, error: r.error ? r.error.slice(0, 200) : null });
    console.log(
      `[${cliName} ${i}/${RUNS}] ${r.ok ? "OK" : "FAIL"} ${ms}ms` +
        (r.ok ? ` :: ${r.text.replace(/\s+/g, " ").slice(0, 80)}` : ` :: ${results.at(-1).error}`),
    );
  }
  const okRuns = results.filter((r) => r.ok);
  return {
    cli: cliName,
    runs: RUNS,
    success: okRuns.length,
    success_rate: okRuns.length / RUNS,
    avg_latency_ms: okRuns.length ? Math.round(okRuns.reduce((s, r) => s + r.ms, 0) / okRuns.length) : null,
    min_latency_ms: okRuns.length ? Math.min(...okRuns.map((r) => r.ms)) : null,
    max_latency_ms: okRuns.length ? Math.max(...okRuns.map((r) => r.ms)) : null,
    failures: results.filter((r) => !r.ok).map((r) => ({ run: r.run, error: r.error })),
  };
}

const summaries = [];
for (const cli of CLIS) {
  console.log(`\n=== spike: ${cli} (n=${RUNS}) ===`);
  summaries.push(await spike(cli));
}

console.log("\n=== SUMMARY(JSON) ===");
console.log(JSON.stringify(summaries, null, 2));

for (const s of summaries) {
  const rate = (s.success_rate * 100).toFixed(0);
  console.log(
    `${s.cli}: ${s.success}/${s.runs} (${rate}%), avg ${s.avg_latency_ms ?? "-"}ms, gate(>=90%): ${s.success_rate >= 0.9 ? "PASS" : "FAIL"}`,
  );
}
