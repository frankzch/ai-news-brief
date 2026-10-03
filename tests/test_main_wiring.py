import main


def _patch_jobs(monkeypatch, calls, archive_error=None):
    monkeypatch.setattr(main, 'run_pipeline', lambda: calls.append('pipeline'))
    monkeypatch.setattr(main, 'cleanup_old_articles', lambda: calls.append('cleanup'))

    def fake_archive():
        calls.append('archive')
        if archive_error:
            raise archive_error

    monkeypatch.setattr(main, 'archive_today', fake_archive)


def test_run_all_jobs_archives_between_pipeline_and_cleanup(monkeypatch):
    calls = []
    _patch_jobs(monkeypatch, calls)

    main.run_all_jobs()

    assert calls == ['pipeline', 'archive', 'cleanup']


def test_archive_failure_does_not_block_cleanup(monkeypatch):
    calls = []
    _patch_jobs(monkeypatch, calls, archive_error=RuntimeError('db down'))

    main.run_all_jobs()

    assert calls == ['pipeline', 'archive', 'cleanup']
