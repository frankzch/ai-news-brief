"""One-command dev loop: starts the read-only API and the Vite frontend together.

    python tools/dev.py

API on http://127.0.0.1:8000, frontend on http://localhost:5173. Ctrl-C stops both.
"""
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@dataclass
class Command:
    name: str
    argv: list
    cwd: str


def npm_executable(os_name=os.name):
    return "npm.cmd" if os_name == "nt" else "npm"


def build_commands(root, python=None, npm=None):
    python = python or sys.executable
    npm = npm or npm_executable()
    return [
        Command("api", [python, "-m", "api"], root),
        Command("web", [npm, "run", "dev"], os.path.join(root, "web")),
    ]


def check_prereqs(which=shutil.which):
    problems = []
    for tool in ("node", "npm"):
        if which(tool) is None:
            problems.append(f"{tool} not found on PATH (install Node.js 20+ from https://nodejs.org)")
    return problems


def port_in_use(port, host="127.0.0.1"):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def needs_install(root):
    return not os.path.isdir(os.path.join(root, "web", "node_modules"))


def _pump(name, stream):
    for line in stream:
        print(f"[{name}] {line.rstrip()}", flush=True)


def _spawn(cmd):
    proc = subprocess.Popen(
        cmd.argv,
        cwd=cmd.cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        # Own process group so Ctrl-C handling and teardown cover npm's child (vite).
        start_new_session=(os.name != "nt"),
    )
    threading.Thread(target=_pump, args=(cmd.name, proc.stdout), daemon=True).start()
    return proc


def _stop(proc):
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(proc.pid, 15)
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            proc.kill()
        else:
            os.killpg(proc.pid, 9)
    except ProcessLookupError:
        pass


def main():
    problems = check_prereqs()
    if problems:
        print("Cannot start the dev loop:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1

    for port, what in ((8000, "API"), (5173, "Vite dev server (strictPort; the API's CORS only allows 5173)")):
        if port_in_use(port):
            print(f"Port {port} is already in use, needed by the {what}. Stop the other process and retry.", file=sys.stderr)
            return 1

    if needs_install(ROOT):
        print("[dev] web/node_modules missing; running npm install ...", flush=True)
        if subprocess.call([npm_executable(), "install"], cwd=os.path.join(ROOT, "web")) != 0:
            print("npm install failed", file=sys.stderr)
            return 1

    procs = {c.name: _spawn(c) for c in build_commands(ROOT)}
    print("[dev] api: http://127.0.0.1:8000/api/health   web: http://localhost:5173  (Ctrl-C to stop)", flush=True)
    code = 0
    try:
        while True:
            for name, proc in procs.items():
                rc = proc.poll()
                if rc is not None:
                    print(f"[dev] {name} exited with code {rc}; stopping the other process", flush=True)
                    code = rc or 1
                    raise SystemExit
            time.sleep(0.3)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        for proc in procs.values():
            _stop(proc)
    return code


if __name__ == "__main__":
    sys.exit(main())
