#!/usr/bin/env node
/**
 * One-command development start.
 *
 *     npm run dev
 *
 * Starts the Next.js frontend and the FastAPI backend together. The backend's
 * lifespan owns the scheduler, so the ten-minute monitoring loop and the
 * historical backfill pass come up with it - there is no separate worker to
 * remember to start.
 *
 * Output from both processes is prefixed and interleaved; Ctrl-C stops both.
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const isWindows = process.platform === "win32";
const venvPython = join(root, ".venv", isWindows ? "Scripts/python.exe" : "bin/python");

if (!existsSync(venvPython)) {
  process.stderr.write(
    "No .venv found. Run `npm run setup` first - it creates the virtualenv, " +
      "installs both dependency sets and migrates the database.\n"
  );
  process.exit(1);
}
if (!existsSync(join(root, "frontend/node_modules"))) {
  process.stderr.write("frontend/node_modules is missing. Run `npm run setup` first.\n");
  process.exit(1);
}

const RESET = "[0m";
const children = [];
let shuttingDown = false;

function start(name, color, command, args, options = {}) {
  const child = spawn(command, args, {
    cwd: root,
    shell: isWindows,
    stdio: ["ignore", "pipe", "pipe"],
    ...options,
  });
  const tag = `[${color}m[${name}]${RESET}`;
  const forward = (stream, sink) => {
    let buffered = "";
    stream.on("data", (chunk) => {
      buffered += chunk.toString();
      const lines = buffered.split("\n");
      buffered = lines.pop() ?? "";
      for (const line of lines) sink.write(`${tag} ${line}\n`);
    });
  };
  forward(child.stdout, process.stdout);
  forward(child.stderr, process.stderr);
  child.on("exit", (code) => {
    if (shuttingDown) return;
    process.stdout.write(`${tag} exited with ${code}\n`);
    shutdown(code ?? 1);
  });
  children.push(child);
  return child;
}

function shutdown(code) {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const child of children) {
    if (child.exitCode === null) {
      try {
        child.kill(isWindows ? undefined : "SIGINT");
      } catch {
        /* already gone */
      }
    }
  }
  setTimeout(() => process.exit(code), 500);
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

// uvicorn's --reload runs the server in a subprocess, and on Windows that path
// selects a SelectorEventLoop, which cannot spawn Playwright's browser process:
// the KASE browser collector then fails with an empty NotImplementedError while
// everything else still looks healthy. Reload is therefore off by default on
// Windows and on elsewhere; `npm run dev -- --reload` forces it back on.
const wantsReload =
  process.argv.includes("--reload") ||
  (!isWindows && !process.argv.includes("--no-reload"));
const backendArgs = [
  "-m", "uvicorn", "app.main:app",
  "--host", "127.0.0.1", "--port", "8000",
  "--app-dir", "backend",
];
if (wantsReload) backendArgs.push("--reload");

start("backend", "36", venvPython, backendArgs);
start("frontend", "35", "npm", ["run", "dev", "--prefix", "frontend"]);

process.stdout.write(
  "\n  frontend  http://localhost:3000\n" +
    "  backend   http://localhost:8000/docs\n" +
    "  health    http://localhost:8000/api/v1/health\n" +
    "  scheduler runs inside the backend process\n" +
    (wantsReload
      ? "  backend auto-reload: on\n\n"
      : isWindows
        ? "  backend auto-reload: off - it would disable the KASE browser\n" +
          "                       collector on Windows. Pass\n" +
          "                       `npm run dev -- --reload` if you do not\n" +
          "                       need the collector.\n\n"
        : "  backend auto-reload: off\n\n")
);
