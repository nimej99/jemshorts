#!/usr/bin/env node
// promo-shorts M0: 로컬 CLI(claude/codex) 브리지 프로토타입.
// Node 22 stdlib만 사용 (node:http, node:child_process, node:os, node:fs, node:url).
// - GET  /health              : 브리지/CLI/네트워크 상태
// - POST /v1/chat/completions : OpenAI 호환 최소 구현 (messages -> CLI 헤드리스 호출)

import http from "node:http";
import os from "node:os";
import { execFile } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

export const BRIDGE_VERSION = "0.1.0";
export const PROTOCOL_VERSION = 1;
export const DEFAULT_TIMEOUT_MS = 120_000; // 호출당 120초
const MAX_BODY_BYTES = 1_000_000;

const BIND = process.env.BRIDGE_BIND || "127.0.0.1";
const PORT = Number(process.env.BRIDGE_PORT || 8765);

// ---------------------------------------------------------------------------
// CLI 탐지
// ---------------------------------------------------------------------------

function execFileP(cmd, args, opts = {}) {
  return new Promise((resolve) => {
    const child = execFile(
      cmd,
      args,
      { timeout: DEFAULT_TIMEOUT_MS, maxBuffer: 16 * 1024 * 1024, ...opts },
      (error, stdout, stderr) => {
        resolve({ error, stdout: String(stdout || ""), stderr: String(stderr || "") });
      },
    );
    child.on("error", () => {}); // ENOENT 등은 callback error로 수렴
    // codex exec는 stdin이 pipe로 열려 있으면 EOF까지 대기하므로 즉시 닫는다.
    child.stdin?.end();
  });
}

let cliInfoPromise = null;

/** 사용 가능한 CLI 탐지: claude 우선, 없으면 codex, 둘 다 없으면 none. */
export function detectCli() {
  if (!cliInfoPromise) {
    cliInfoPromise = (async () => {
      for (const name of ["claude", "codex"]) {
        const { error, stdout } = await execFileP(name, ["--version"], { timeout: 15_000 });
        if (!error && stdout.trim()) {
          return { name, version: stdout.trim().split("\n")[0] };
        }
      }
      return { name: "none", version: null };
    })();
  }
  return cliInfoPromise;
}

// ---------------------------------------------------------------------------
// 네트워크 정보
// ---------------------------------------------------------------------------

/** IPv4 non-internal 주소 수집. */
export function collectInterfaces() {
  const out = [];
  for (const [name, addrs] of Object.entries(os.networkInterfaces())) {
    for (const a of addrs || []) {
      if (a.family === "IPv4" && !a.internal) {
        out.push({ name, address: a.address });
      }
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// CLI 헤드리스 호출
// ---------------------------------------------------------------------------

/**
 * 단일 CLI 헤드리스 호출.
 * @returns {Promise<{ok: boolean, text: string, usage: object|null, error: string|null}>}
 */
export async function runCli(cliName, prompt, { timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  if (cliName === "claude") {
    const { error, stdout, stderr } = await execFileP(
      "claude",
      ["-p", prompt, "--output-format", "json"],
      { timeout: timeoutMs },
    );
    if (error) {
      return { ok: false, text: "", usage: null, error: `claude exit: ${error.code ?? error.message}; ${stderr.slice(0, 500)}` };
    }
    try {
      const parsed = JSON.parse(stdout);
      const text = String(parsed.result ?? "").trim();
      if (parsed.is_error || !text) {
        return { ok: false, text: "", usage: null, error: `claude result error/empty: ${stdout.slice(0, 300)}` };
      }
      return {
        ok: true,
        text,
        usage: parsed.usage
          ? {
              prompt_tokens: parsed.usage.input_tokens ?? 0,
              completion_tokens: parsed.usage.output_tokens ?? 0,
              total_tokens: (parsed.usage.input_tokens ?? 0) + (parsed.usage.output_tokens ?? 0),
            }
          : null,
        error: null,
      };
    } catch {
      return { ok: false, text: "", usage: null, error: `claude json parse fail: ${stdout.slice(0, 300)}` };
    }
  }

  if (cliName === "codex") {
    // codex exec는 stdout에 사람이 읽는 스트림을 출력하므로
    // --output-last-message(temp file)로 최종 메시지만 회수한다.
    const dir = mkdtempSync(join(os.tmpdir(), "promo-bridge-"));
    const lastMsg = join(dir, "last.txt");
    try {
      const { error, stderr } = await execFileP(
        "codex",
        ["exec", "--skip-git-repo-check", "--output-last-message", lastMsg, prompt],
        { timeout: timeoutMs },
      );
      if (error) {
        return { ok: false, text: "", usage: null, error: `codex exit: ${error.code ?? error.message}; ${stderr.slice(0, 500)}` };
      }
      let text = "";
      try {
        text = readFileSync(lastMsg, "utf8").trim();
      } catch {
        /* 파일 미생성 = 실패 */
      }
      if (!text) return { ok: false, text: "", usage: null, error: "codex empty last message" };
      return { ok: true, text, usage: null, error: null };
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  }

  return { ok: false, text: "", usage: null, error: `unknown cli: ${cliName}` };
}

/** 120초 타임아웃 + 실패 시 1회 재시도. */
export async function runCliWithRetry(cliName, prompt, opts = {}) {
  const first = await runCli(cliName, prompt, opts);
  if (first.ok) return { ...first, retried: false };
  const second = await runCli(cliName, prompt, opts);
  return { ...second, retried: true, firstError: first.error };
}

// ---------------------------------------------------------------------------
// HTTP 서버
// ---------------------------------------------------------------------------

function sendJson(res, status, body) {
  const data = JSON.stringify(body);
  res.writeHead(status, { "content-type": "application/json" });
  res.end(data);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (c) => {
      size += c.length;
      if (size > MAX_BODY_BYTES) {
        reject(new Error("body too large"));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

/** messages 배열을 단일 프롬프트 텍스트로 결합. */
export function messagesToPrompt(messages) {
  return messages
    .map((m) => {
      const content = typeof m.content === "string" ? m.content : JSON.stringify(m.content);
      return m.role === "user" ? content : `[${m.role}]\n${content}`;
    })
    .join("\n\n");
}

async function handleHealth(res) {
  const cli = await detectCli();
  const interfaces = collectInterfaces();
  sendJson(res, 200, {
    bridge_version: BRIDGE_VERSION,
    protocol_version: PROTOCOL_VERSION,
    cli,
    host_lan_ip: interfaces[0]?.address ?? null,
    interfaces,
  });
}

async function handleChatCompletions(req, res) {
  let body;
  try {
    body = JSON.parse(await readBody(req));
  } catch (e) {
    sendJson(res, 400, { error: { message: `invalid json body: ${e.message}`, type: "invalid_request_error" } });
    return;
  }
  const messages = body?.messages;
  if (!Array.isArray(messages) || messages.length === 0) {
    sendJson(res, 400, { error: { message: "messages must be a non-empty array", type: "invalid_request_error" } });
    return;
  }

  // model에 codex/claude가 명시되면 그 CLI를, 아니면 탐지된 기본 CLI 사용
  const detected = await detectCli();
  const model = String(body.model || "");
  let cliName = detected.name;
  if (/codex/i.test(model)) cliName = "codex";
  else if (/claude/i.test(model)) cliName = "claude";
  if (cliName === "none") {
    sendJson(res, 503, { error: { message: "no supported cli (claude/codex) found on host", type: "bridge_error" } });
    return;
  }

  const prompt = messagesToPrompt(messages);
  const result = await runCliWithRetry(cliName, prompt);
  if (!result.ok) {
    sendJson(res, 502, {
      error: { message: `cli call failed after retry: ${result.error}`, type: "bridge_upstream_error", first_error: result.firstError ?? null },
    });
    return;
  }

  sendJson(res, 200, {
    id: `chatcmpl-bridge-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`,
    object: "chat.completion",
    created: Math.floor(Date.now() / 1000),
    model: model || `${cliName}-bridge`,
    choices: [
      {
        index: 0,
        message: { role: "assistant", content: result.text },
        finish_reason: "stop",
      },
    ],
    usage: result.usage ?? { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
  });
}

export function createServer() {
  return http.createServer((req, res) => {
    const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
    if (req.method === "GET" && url.pathname === "/health") {
      handleHealth(res).catch((e) => sendJson(res, 500, { error: { message: e.message, type: "bridge_error" } }));
      return;
    }
    if (req.method === "POST" && url.pathname === "/v1/chat/completions") {
      handleChatCompletions(req, res).catch((e) => sendJson(res, 500, { error: { message: e.message, type: "bridge_error" } }));
      return;
    }
    sendJson(res, 404, { error: { message: `no route: ${req.method} ${url.pathname}`, type: "not_found" } });
  });
}

// 직접 실행 시에만 서버 기동 (spike 스크립트에서 import해도 부작용 없음)
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const server = createServer();
  server.listen(PORT, BIND, () => {
    console.log(`[promo-bridge] v${BRIDGE_VERSION} listening on http://${BIND}:${PORT}`);
  });
}
