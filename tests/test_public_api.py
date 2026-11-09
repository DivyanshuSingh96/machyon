"""Every public name in `machyon.__all__` is importable and called directly by at least one test.
(The rest are exercised end to end elsewhere; this file covers the four only used indirectly.)"""
import importlib.metadata
import logging

import pytest

import machyon


def test_every_public_name_exists():
    missing = [n for n in machyon.__all__ if not hasattr(machyon, n)]
    assert not missing, missing


def test_version_matches_the_installed_package():
    assert machyon.__version__ == importlib.metadata.version("machyon")


def test_errors_share_one_base_class():
    assert issubclass(machyon.FatalDiskError, machyon.MachyonError)
    with pytest.raises(machyon.MachyonError):
        raise machyon.FatalDiskError("disk full")


def test_setup_logging_is_idempotent_and_writes_a_file(tmp_path):
    log = machyon.setup_logging(tmp_path)
    handlers = len(log.handlers)
    assert machyon.setup_logging(tmp_path) is log and len(log.handlers) == handlers   # safe to call twice
    log.warning("hello from the test")
    for h in log.handlers:
        h.flush()
    assert "hello from the test" in (tmp_path / "machyon.log").read_text(encoding="utf-8")
    for h in [h for h in log.handlers if isinstance(h, logging.FileHandler)]:   # release the file (Windows)
        log.removeHandler(h)
        h.close()


def test_config_reads_defaults_and_ini_files(tmp_path):
    assert machyon.Config().get_int("SETTINGS", "MAX_RETRIES") == 5            # defaults without a file
    ini = tmp_path / "machyon.ini"
    ini.write_text("[SETTINGS]\nMAX_RETRIES = 9\nADAPTIVE = false\nBACKOFF_FACTOR = 1.5\n", encoding="utf-8")
    cfg = machyon.Config(ini)
    assert (cfg.get_int("SETTINGS", "MAX_RETRIES"), cfg.get_bool("SETTINGS", "ADAPTIVE"),
            cfg.get_float("SETTINGS", "BACKOFF_FACTOR")) == (9, False, 1.5)
    cfg.save(tmp_path / "copy" / "machyon.ini")                                 # round-trips through save()
    assert machyon.Config(tmp_path / "copy" / "machyon.ini").get_int("SETTINGS", "MAX_RETRIES") == 9
