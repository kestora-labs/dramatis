"""Guards the CI workflow against drifting away from the checks run locally.

Working rule 4 requires every commit to carry an automated check. For CI configuration
that check is this: the workflow parses, it triggers on the events we expect, and the
commands it runs are the same ones a contributor runs on their own machine. A workflow
that silently stops running the test suite is worse than no workflow at all.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_workflow_exists_and_parses(workflow: dict) -> None:
    assert workflow["name"] == "CI"


def test_triggers_on_push_and_pull_request(workflow: dict) -> None:
    # PyYAML resolves the bare key `on` to the boolean True (YAML 1.1 truthiness).
    triggers = workflow.get("on", workflow.get(True))
    assert triggers is not None, "workflow declares no triggers"
    assert "push" in triggers
    assert "pull_request" in triggers


def _commands(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_python_job_lints_formats_and_tests(workflow: dict) -> None:
    commands = _commands(workflow["jobs"]["python"])
    assert "ruff check" in commands
    assert "ruff format --check" in commands
    assert "pytest" in commands


def test_web_job_typechecks_formats_tests_and_builds(workflow: dict) -> None:
    commands = _commands(workflow["jobs"]["web"])
    assert "run typecheck" in commands
    assert "run format:check" in commands
    assert "npm --prefix web test" in commands
    # A client that no longer builds is broken even when its unit tests pass.
    assert "run build" in commands


def test_python_matrix_covers_the_supported_floor(workflow: dict) -> None:
    versions = workflow["jobs"]["python"]["strategy"]["matrix"]["python-version"]
    # pyproject declares requires-python >=3.11; CI must actually prove that floor.
    assert "3.11" in versions
