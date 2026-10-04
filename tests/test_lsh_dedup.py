import logging

import pytest
from simhash import Simhash

import lsh_dedup
from lsh_dedup import compute_simhash, hamming_distance


def _signed(val):
    return val - (1 << 64) if val >= (1 << 63) else val


def test_token_repeated_300_times_does_not_raise():
    assert isinstance(compute_simhash("the " * 300 + "cat sat on the mat"), int)


def test_token_repeated_1000_times_does_not_raise():
    assert isinstance(compute_simhash("overflow " * 1000 + "tail words here"), int)


def test_long_text_is_deterministic():
    text = "word " * 470 + "other things"
    assert compute_simhash(text) == compute_simhash(text)


def test_under_cap_hash_matches_plain_simhash():
    text = "alpha beta gamma " * 100 + "delta epsilon"
    assert compute_simhash(text) == _signed(Simhash(text).value)


def test_cap_boundary_255_matches_plain_simhash():
    text = "the " * 255 + "cat sat"
    assert compute_simhash(text) == _signed(Simhash(text).value)


def test_near_duplicate_long_texts_stay_similar():
    base = "the " * 400 + " ".join(f"w{i}" for i in range(200))
    other = base + " extra"
    assert hamming_distance(compute_simhash(base), compute_simhash(other)) <= 3


def test_fingerprint_failure_returns_zero_and_logs(monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(lsh_dedup, "Simhash", boom)
    with caplog.at_level(logging.WARNING):
        assert compute_simhash("some text", label="My Title https://x.test/a") == 0
    assert "My Title https://x.test/a" in caplog.text
    assert "kaboom" in caplog.text
