#!/usr/bin/env node
/**
 * Run a Python command with the project's own interpreter and import path.
 *
 *     node scripts/py.mjs -m app.jobs.backfill_kase_stocks --status
 *
 * The npm scripts route Python through here so that `npm run backfill`,
 * `npm test` and `npm run migrate` behave identically whether or not the
 * caller has activated the virtualenv: the .venv interpreter is preferred over
 * whatever `python` happens to be on PATH, and `backend/` is on PYTHONPATH so
 * `app.*` imports resolve from the repository root.
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join, dirname, delimiter } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const isWindows = process.platform === "win32";
const venvPython = join(root, ".venv", isWindows ? "Scripts/python.exe" : "bin/python");
const python = existsSync(venvPython) ? venvPython : isWindows ? "python" : "python3";

const backend = join(root, "backend");
const pythonPath = process.env.PYTHONPATH
  ? `${backend}${delimiter}${process.env.PYTHONPATH}`
  : backend;

const child = spawn(python, process.argv.slice(2), {
  cwd: root,
  stdio: "inherit",
  shell: isWindows,
  env: { ...process.env, PYTHONPATH: pythonPath },
});
child.on("exit", (code, signal) => process.exit(signal ? 1 : code ?? 0));
