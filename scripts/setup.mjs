#!/usr/bin/env node
/**
 * One-command project setup.
 *
 *     npm run setup
 *
 * Creates the Python virtualenv, installs both dependency sets, seeds .env
 * from .env.example when it is missing, and brings the database schema up to
 * head. Every step is idempotent, so re-running it after a pull is the normal
 * way to catch up rather than something to avoid.
 *
 * No KASE API key is required: the default KASE_DATA_MODE works from public
 * pages alone.
 */
import { spawnSync } from "node:child_process";
import { existsSync, copyFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const isWindows = process.platform === "win32";
const venv = join(root, ".venv");
const venvPython = join(venv, isWindows ? "Scripts/python.exe" : "bin/python");

let failed = 0;

function step(label, fn) {
  process.stdout.write(`\n→ ${label}\n`);
  try {
    fn();
  } catch (error) {
    failed += 1;
    process.stdout.write(`  FAILED: ${error.message}\n`);
  }
}

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    stdio: "inherit",
    shell: isWindows,
    ...options,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} exited with ${result.status}`);
  }
}

function findSystemPython() {
  for (const candidate of isWindows ? ["py", "python"] : ["python3", "python"]) {
    const probe = spawnSync(candidate, ["--version"], { shell: isWindows });
    if (probe.status === 0) return candidate;
  }
  throw new Error("no Python interpreter found on PATH (3.11+ required)");
}

step("environment file", () => {
  const env = join(root, ".env");
  if (existsSync(env)) {
    process.stdout.write("  .env already exists, left untouched\n");
    return;
  }
  copyFileSync(join(root, ".env.example"), env);
  process.stdout.write("  created .env from .env.example (no KASE API key needed)\n");
});

step("python virtualenv", () => {
  if (existsSync(venvPython)) {
    process.stdout.write("  .venv already exists\n");
    return;
  }
  run(findSystemPython(), ["-m", "venv", ".venv"]);
});

step("backend dependencies", () => {
  run(venvPython, ["-m", "pip", "install", "--upgrade", "pip", "--quiet"]);
  run(venvPython, ["-m", "pip", "install", "-r", "backend/requirements.txt", "--quiet"]);
});

step("browser engine for the KASE collector", () => {
  // The public-web collector drives a real Chromium. Without this step
  // KASE_DATA_MODE=browser starts but every fetch fails, and
  // /health/kase-browser reports the missing engine rather than data.
  run(venvPython, ["-m", "playwright", "install", "chromium"]);
});

step("frontend dependencies", () => {
  run("npm", ["install", "--prefix", "frontend", "--no-fund", "--no-audit"]);
});

step("database schema", () => {
  run(venvPython, ["-m", "alembic", "upgrade", "head"]);
});

if (failed) {
  process.stdout.write(
    `\nsetup finished with ${failed} failed step(s) above. ` +
      "Fix those and re-run `npm run setup` - completed steps are skipped.\n"
  );
  process.exit(1);
}

process.stdout.write(
  "\nsetup complete. Start everything with:\n\n    npm run dev\n\n" +
    "  frontend  http://localhost:3000\n" +
    "  backend   http://localhost:8000/docs\n" +
    "  health    http://localhost:8000/api/v1/health\n"
);
