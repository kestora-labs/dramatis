"""Proves the Python toolchain is wired up: the package imports and is versioned."""

import dramatis


def test_package_imports_and_reports_a_version() -> None:
    assert isinstance(dramatis.__version__, str)
    assert dramatis.__version__


def test_version_is_pep440_dev_release() -> None:
    # Phase 0 is pre-alpha; guard against an accidental release-looking version.
    assert dramatis.__version__.startswith("0.1.0")
