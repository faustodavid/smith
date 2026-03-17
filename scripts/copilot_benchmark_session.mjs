#!/usr/bin/env node

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const ENV_PLACEHOLDER_PREFIX = "__ENV__:";

async function readStdin() {
  return await new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => {
      data += chunk;
    });
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

function resolveEnvPlaceholders(value) {
  if (typeof value === "string") {
    if (!value.startsWith(ENV_PLACEHOLDER_PREFIX)) {
      return value;
    }
    const envVar = value.slice(ENV_PLACEHOLDER_PREFIX.length);
    return process.env[envVar];
  }

  if (Array.isArray(value)) {
    return value
      .map((item) => resolveEnvPlaceholders(item))
      .filter((item) => item !== undefined);
  }

  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .map(([key, item]) => [key, resolveEnvPlaceholders(item)])
        .filter(([, item]) => item !== undefined),
    );
  }

  return value;
}

function findLatestCopilotSdkPath() {
  const pkgRoot = path.join(os.homedir(), ".copilot", "pkg");
  if (!fs.existsSync(pkgRoot)) {
    throw new Error("Could not locate ~/.copilot/pkg to load the Copilot SDK.");
  }

  const candidates = [];
  for (const platformEntry of fs.readdirSync(pkgRoot, { withFileTypes: true })) {
    if (!platformEntry.isDirectory()) {
      continue;
    }
    const platformRoot = path.join(pkgRoot, platformEntry.name);
    for (const versionEntry of fs.readdirSync(platformRoot, { withFileTypes: true })) {
      if (!versionEntry.isDirectory()) {
        continue;
      }
      const candidate = path.join(platformRoot, versionEntry.name, "copilot-sdk", "index.js");
      if (fs.existsSync(candidate)) {
        candidates.push(candidate);
      }
    }
  }

  if (candidates.length === 0) {
    throw new Error("Could not find a copilot-sdk/index.js installation under ~/.copilot/pkg.");
  }

  candidates.sort((left, right) => fs.statSync(right).mtimeMs - fs.statSync(left).mtimeMs);
  return candidates[0];
}

function extractUsage(events) {
  let inputTokens = 0;
  let outputTokens = 0;
  let cacheReadTokens = 0;
  let cacheWriteTokens = 0;
  let apiDurationMs = 0;

  for (const event of events) {
    if (event?.type !== "assistant.usage") {
      continue;
    }
    const data = event.data ?? {};
    inputTokens += Number(data.inputTokens ?? 0);
    outputTokens += Number(data.outputTokens ?? 0);
    cacheReadTokens += Number(data.cacheReadTokens ?? 0);
    cacheWriteTokens += Number(data.cacheWriteTokens ?? 0);
    apiDurationMs += Number(data.duration ?? 0);
  }

  return {
    inputTokens,
    outputTokens,
    totalTokens: inputTokens + outputTokens,
    cacheReadTokens,
    cacheWriteTokens,
    apiDurationMs,
  };
}

async function main() {
  const rawPayload = await readStdin();
  const payload = JSON.parse(rawPayload);
  const sdkPath = payload.sdkPath || process.env.COPILOT_SDK_PATH || findLatestCopilotSdkPath();
  const { CopilotClient } = await import(sdkPath);

  const events = [];
  const startedAt = Date.now();

  const client = new CopilotClient({
    cliArgs: payload.cliArgs ?? [],
    cliPath: payload.cliPath,
    cwd: payload.workingDirectory,
    logLevel: "error",
    useStdio: true,
  });

  const approveAll = async () => ({ kind: "approved" });

  let session;
  try {
    session = await client.createSession({
      availableTools: payload.availableTools,
      mcpServers: resolveEnvPlaceholders(payload.mcpServers ?? {}),
      model: payload.model,
      onPermissionRequest: approveAll,
      reasoningEffort: payload.reasoningEffort,
      streaming: false,
      systemMessage: { mode: "append", content: payload.systemMessage },
      workingDirectory: payload.workingDirectory,
    });

    session.on((event) => {
      events.push(event);
    });

    const response = await session.sendAndWait(
      { prompt: payload.prompt },
      Number(payload.timeoutMs ?? 600000),
    );
    const finishedAt = Date.now();

    process.stdout.write(
      JSON.stringify({
        events,
        finalAnswer: response?.data?.content ?? "",
        finishedAt,
        sessionId: session.sessionId,
        startedAt,
        usage: extractUsage(events),
        wallClockDurationMs: finishedAt - startedAt,
      }),
    );
  } finally {
    if (session) {
      await session.disconnect();
    }
    await client.stop();
  }
}

main().catch((error) => {
  const message = error instanceof Error ? error.stack || error.message : String(error);
  process.stderr.write(message + "\n");
  process.exit(1);
});
