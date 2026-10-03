import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

import dev  # noqa: E402


def test_build_commands_runs_api_module_from_repo_root_and_vite_from_web(tmp_path):
    cmds = dev.build_commands(str(tmp_path), python="py", npm="npm")
    by_name = {c.name: c for c in cmds}

    assert by_name["api"].argv == ["py", "-m", "api"]
    assert by_name["api"].cwd == str(tmp_path)
    assert by_name["web"].argv == ["npm", "run", "dev"]
    assert by_name["web"].cwd == os.path.join(str(tmp_path), "web")


def test_check_prereqs_passes_when_node_and_npm_found():
    assert dev.check_prereqs(lambda name: "/usr/bin/" + name) == []


def test_check_prereqs_reports_each_missing_tool():
    problems = dev.check_prereqs(lambda name: None)
    assert any("node" in p for p in problems)
    assert any("npm" in p for p in problems)


def test_needs_install_when_node_modules_missing(tmp_path):
    (tmp_path / "web").mkdir()
    assert dev.needs_install(str(tmp_path)) is True


def test_no_install_when_node_modules_present(tmp_path):
    (tmp_path / "web" / "node_modules").mkdir(parents=True)
    assert dev.needs_install(str(tmp_path)) is False


def test_npm_executable_is_cmd_shim_on_windows():
    assert dev.npm_executable("nt") == "npm.cmd"
    assert dev.npm_executable("posix") == "npm"


def test_port_in_use_detects_a_listening_socket():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    try:
        assert dev.port_in_use(s.getsockname()[1]) is True
    finally:
        s.close()


def test_port_in_use_false_for_free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert dev.port_in_use(port) is False


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX-only")
def test_stop_kills_process_and_its_children(tmp_path):
    import subprocess
    import time
    pidfile = tmp_path / "child.pid"
    script = (
        "import subprocess,sys,time;"
        "c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"open(r'{pidfile}','w').write(str(c.pid));time.sleep(60)"
    )
    proc = dev._spawn(dev.Command("t", [sys.executable, "-c", script], str(tmp_path)))
    for _ in range(50):
        if pidfile.exists() and pidfile.read_text():
            break
        time.sleep(0.1)
    child = int(pidfile.read_text())

    dev._stop(proc)

    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)
