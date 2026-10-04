import subprocess
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import ai_engine
from ai_engine import AIEngine, SummarizationError


def make_engine(backend='claude_cli', concurrency=3):
    eng = AIEngine.__new__(AIEngine)
    eng.backend = backend
    eng.model = 'haiku'
    eng.claude_bin = 'claude'
    eng.oauth_token = 'x'
    eng.ai_timeout = 90
    eng.max_input_chars = 1000
    eng.prompts_dir = '.'
    eng._prompt_cache = {}
    eng._cli_sem = threading.BoundedSemaphore(concurrency)
    eng._breaker_lock = threading.Lock()
    eng._consecutive_cli_failures = 0
    eng.sleeps = []
    eng._sleep = eng.sleeps.append
    return eng


def proc(rc, out='', err=''):
    return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


def script(monkeypatch, results):
    """Make subprocess.run return/raise each item of `results` in turn; returns the list of calls."""
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        r = results[len(calls) - 1]
        if isinstance(r, BaseException):
            raise r
        return r

    monkeypatch.setattr(subprocess, 'run', fake_run)
    return calls


TIMEOUT = subprocess.TimeoutExpired(cmd=['claude', '-p', '--system-prompt', 'SECRET PROMPT'], timeout=90)


def test_crash_then_success_waits_before_retry(monkeypatch):
    eng = make_engine()
    calls = script(monkeypatch, [proc(-11), proc(0, '{"a": 1}')])
    assert eng._claude_cli('s', 'u', 90) == '{"a": 1}'
    assert len(calls) == 2
    assert len(eng.sleeps) == 1 and 3.75 <= eng.sleeps[0] <= 6.25


def test_two_crashes_then_success_uses_backoff(monkeypatch):
    eng = make_engine()
    script(monkeypatch, [proc(-11), proc(-9), proc(0, 'ok')])
    assert eng._claude_cli('s', 'u', 90) == 'ok'
    assert 3.75 <= eng.sleeps[0] <= 6.25 and 15 <= eng.sleeps[1] <= 25


def test_all_attempts_crash_raises_same_error(monkeypatch):
    eng = make_engine()
    calls = script(monkeypatch, [proc(-11, err='boom')] * 3)
    with pytest.raises(RuntimeError, match=r'claude CLI exited -11: boom'):
        eng._claude_cli('s', 'u', 90)
    assert len(calls) == 3 and len(eng.sleeps) == 2


def test_ordinary_error_not_retried(monkeypatch):
    eng = make_engine()
    calls = script(monkeypatch, [proc(1, err='bad auth')])
    with pytest.raises(RuntimeError, match='exited 1: bad auth'):
        eng._claude_cli('s', 'u', 90)
    assert len(calls) == 1 and eng.sleeps == []
    assert eng._consecutive_cli_failures == 0


def test_timeout_retried_and_message_is_clean(monkeypatch):
    eng = make_engine()
    script(monkeypatch, [TIMEOUT, proc(0, 'ok')])
    assert eng._claude_cli('s', 'u', 90) == 'ok'

    eng = make_engine()
    script(monkeypatch, [TIMEOUT] * 3)
    with pytest.raises(RuntimeError) as ei:
        eng._claude_cli('s', 'u', 90)
    assert str(ei.value) == 'claude CLI timed out after 90s'
    assert 'SECRET PROMPT' not in str(ei.value)


def test_breaker_pauses_after_consecutive_failures_and_resets(monkeypatch):
    eng = make_engine()
    script(monkeypatch, [proc(-11)] * 3 + [proc(-11), proc(0, 'ok')])
    with pytest.raises(RuntimeError):
        eng._claude_cli('s', 'u', 90)  # 3 consecutive failures trips the breaker
    assert eng._consecutive_cli_failures == 3
    assert ai_engine.CLI_BREAKER_COOLDOWN not in eng.sleeps
    # next call: cooldown first, then one crash + success
    assert eng._claude_cli('s', 'u', 90) == 'ok'
    assert ai_engine.CLI_BREAKER_COOLDOWN in eng.sleeps
    assert eng._consecutive_cli_failures == 0


def test_semaphore_limits_concurrent_cli_calls(monkeypatch):
    eng = make_engine(concurrency=1)
    active, peak, lock = [0], [0], threading.Lock()

    def fake_run(cmd, **kw):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1
        return proc(0, 'ok')

    monkeypatch.setattr(subprocess, 'run', fake_run)
    threads = [threading.Thread(target=eng._claude_cli, args=('s', 'u', 5)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1


GOOD = {'summary_en': 'a', 'summary_zh': 'b', 'long_summary_en': 'c', 'long_summary_zh': 'd', 'importance_score': 5}


def stub_completions(eng, monkeypatch, results):
    calls = []

    def fake(*a, **k):
        calls.append(1)
        r = results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(eng, '_complete_json', fake)
    monkeypatch.setattr(eng, '_get_prompt_for_category', lambda c: '')
    return calls


def test_summarize_raises_on_cli_failure_without_instant_retry(monkeypatch):
    eng = make_engine()
    calls = stub_completions(eng, monkeypatch, [RuntimeError('claude CLI exited -11: x')] * 2)
    with pytest.raises(SummarizationError, match='-11'):
        eng.summarize_content('text', title='t')
    assert len(calls) == 1


def test_summarize_reasks_once_on_malformed_json(monkeypatch):
    eng = make_engine()
    stub_completions(eng, monkeypatch, [ValueError('bad json'), GOOD])
    assert eng.summarize_content('text', title='t')[0] == 'a'


def test_summarize_openai_backend_still_retries_transport_errors(monkeypatch):
    eng = make_engine(backend='openai')
    stub_completions(eng, monkeypatch, [ConnectionError('net'), GOOD])
    assert eng.summarize_content('text', title='t')[0] == 'a'


def test_pipeline_requeues_and_counts_summarize_failure():
    import pipeline_runner

    recorded, failures = [], []
    user_db = SimpleNamespace(
        record_fetched_url=lambda url, sid, next_fetch_at=None: recorded.append((url, sid, next_fetch_at)))
    stats = SimpleNamespace(record_failure=lambda *a: failures.append(a))
    pipeline_runner._handle_summarize_failure(user_db, stats, 'rss', 'desc', 'http://u', 7, 'err')
    assert failures == [('rss', 'desc', 'summarize_failed')]
    url, sid, when = recorded[0]
    assert (url, sid) == ('http://u', 7)
    assert 29 * 60 < (when - datetime.now(timezone.utc)).total_seconds() <= 30 * 60
