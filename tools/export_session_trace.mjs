#!/usr/bin/env node
/**
 * Export the current DSH session log as an agent JSONL trace.
 *
 * DSH writes one zstd *frame per event* into
 *   ~/.dsh/sessions/<workspace>/session-<id>/session.v4.jsonl.zstd
 * Node's zstd decompressor stops after the first frame, so this script splits
 * the file on the zstd magic number, inflates every frame, and then maps the
 * events onto the Claude-Code-style JSONL shape that `playground trace` reads:
 *   assistant message  -> {type:"assistant", message:{content:[reasoning,text,tool_use]}}
 *   tool result        -> {type:"user", message:{content:[tool_result]}}
 *   user message       -> {type:"user", message:{content:[text]}}
 *
 * Secrets are redacted on the way out; the raw session file is never modified.
 *
 *   node tools/export_session_trace.mjs --out trace/session-trace.jsonl
 */

import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import zlib from "node:zlib";

const MAGIC = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);

function latestSessionFile() {
  const base = path.join(os.homedir(), ".dsh", "sessions");
  let best = null;
  for (const workspace of fs.readdirSync(base)) {
    const dir = path.join(base, workspace);
    if (!fs.statSync(dir).isDirectory()) continue;
    for (const entry of fs.readdirSync(dir)) {
      const candidate = path.join(dir, entry);
      if (!fs.statSync(candidate).isDirectory()) continue;
      const log = path.join(candidate, "session.v4.jsonl.zstd");
      if (!fs.existsSync(log)) continue;
      const mtime = fs.statSync(log).mtimeMs;
      if (!best || mtime > best.mtime) best = { file: log, mtime };
    }
  }
  if (!best) throw new Error("no session.v4.jsonl.zstd found under ~/.dsh/sessions");
  return best.file;
}

function decompressAll(buffer) {
  const offsets = [];
  let cursor = 0;
  while ((cursor = buffer.indexOf(MAGIC, cursor)) !== -1) {
    offsets.push(cursor);
    cursor += 4;
  }
  const parts = [];
  for (let i = 0; i < offsets.length; i += 1) {
    const end = i + 1 < offsets.length ? offsets[i + 1] : buffer.length;
    parts.push(zlib.zstdDecompressSync(buffer.subarray(offsets[i], end)));
  }
  return Buffer.concat(parts);
}

function redact(text) {
  if (typeof text !== "string") return text;
  return text
    .replace(/asp_[A-Za-z0-9]{16,}/g, "asp_REDACTED")
    .replace(/github_pat_[A-Za-z0-9_]{20,}/g, "github_pat_REDACTED");
}

function iso(ms) {
  const date = new Date(typeof ms === "number" ? ms : Date.now());
  return date.toISOString();
}

function main() {
  const args = process.argv.slice(2);
  const outIndex = args.indexOf("--out");
  const outPath = path.resolve(outIndex >= 0 ? args[outIndex + 1] : "trace/session-trace.jsonl");

  const sessionFile = latestSessionFile();
  const raw = decompressAll(fs.readFileSync(sessionFile));
  const events = raw
    .toString("utf8")
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line));

  const header = events.find((event) => event.type === "request/header");
  const model = header?.data?.header?.config?.model || "unknown-model";

  const rows = [];
  for (const event of events) {
    if (event.type === "assistant/message") {
      const blocks = [];
      for (const block of event.data?.message?.content || []) {
        if (block.type === "reasoning" && block.text) {
          blocks.push({ type: "reasoning", text: redact(block.text) });
        } else if (block.type === "text" && block.text) {
          blocks.push({ type: "text", text: redact(block.text) });
        } else if (block.type === "tool-call") {
          let input = {};
          try {
            input = JSON.parse(block.arguments || "{}");
          } catch {
            input = { raw: String(block.arguments || "") };
          }
          blocks.push({ type: "tool_use", id: block.id, name: block.name, input });
        }
      }
      if (!blocks.length) continue;
      const usage = event.data?.usage || {};
      rows.push({
        type: "assistant",
        timestamp: iso(event.time),
        message: {
          role: "assistant",
          model,
          content: blocks,
          usage: {
            input_tokens: Number(usage.inputTokens || 0),
            output_tokens: Number(usage.outputTokens || 0),
            cache_read_input_tokens: Number(usage.cacheReadTokens || 0),
          },
        },
      });
    } else if (event.type === "tool/result") {
      const message = event.data?.message || {};
      let body = "";
      for (const block of message.content || []) {
        if (block.type === "text") body += block.text || "";
      }
      rows.push({
        type: "user",
        timestamp: iso(event.time),
        message: {
          role: "user",
          content: [
            {
              type: "tool_result",
              tool_use_id: event.data?.toolCallId || message.toolCallId || "",
              content: [{ type: "text", text: redact(body) }],
            },
          ],
        },
      });
    } else if (event.type === "user/message") {
      const texts = [];
      for (const block of event.data?.content || []) {
        if (block.type === "text") texts.push(block.text || "");
      }
      if (!texts.length) continue;
      rows.push({
        type: "user",
        timestamp: iso(event.time),
        message: { role: "user", content: [{ type: "text", text: redact(texts.join("\n")) }] },
      });
    }
  }

  const resultIds = new Set(
    rows
      .filter((row) => row.message?.content?.[0]?.type === "tool_result")
      .map((row) => row.message.content[0].tool_use_id)
      .filter(Boolean),
  );
  while (rows.length) {
    const last = rows[rows.length - 1];
    const calls = (last.message?.content || []).filter((block) => block.type === "tool_use");
    if (last.type === "assistant" && calls.length && calls.every((call) => !resultIds.has(call.id))) {
      rows.pop(); // the export command itself is still in flight; keep the trace complete
      continue;
    }
    break;
  }

  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, rows.map((row) => JSON.stringify(row)).join("\n") + "\n");
  console.log(JSON.stringify({
    status: "exported",
    session_file: sessionFile,
    model,
    events: rows.length,
    assistants: rows.filter((r) => r.type === "assistant").length,
    tool_results: rows.filter((r) => r.type === "user" &&
      (r.message.content?.[0]?.type === "tool_result")).length,
    out: outPath,
  }, null, 2));
}

main();
