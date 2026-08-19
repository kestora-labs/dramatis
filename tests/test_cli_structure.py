"""The ``dramatis structure`` command.

Exit codes are the contract, as everywhere else in this CLI. Two things beyond that are
worth holding still: the command reaches no network unless `--ask` is given, and it writes
nothing unless `--confirm` is given. Both are properties somebody decides to trust before
running it against a corpus they care about, and neither is visible from the output.

There is no interactive prompt here on purpose. **4.2** could have been a question-and-answer
session on stdin; an earlier version of the ingest command was, and it raised EOFError
wherever stdin was not a terminal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dramatis.cli import main
from dramatis.providers import ModelResponse
from dramatis.providers.scripted import ScriptedProvider
from dramatis.store import Store

PREFACE = "PREFACE BY THE EDITOR\n\nThis edition follows the text of 1813.\n\n"
NOVEL = (
    "It is a truth universally acknowledged, that a single man in possession of a good "
    "fortune, must be in want of a wife.\n\nMr Bennet replied that he had not.\n\nTHE END\n"
)


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "book.md").write_text(PREFACE + NOVEL, encoding="utf-8", newline="")
    (root / "notes.md").write_text("Ada is Bram's sister.\n", encoding="utf-8", newline="")
    return root


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    path = tmp_path / "project.sqlite"
    with Store(path):
        pass
    return path


def a_reader(monkeypatch: pytest.MonkeyPatch, **by_path: dict) -> list:
    """Stand in for the real provider, and record that it was called at all."""
    calls: list = []
    provider = ScriptedProvider(
        [{"documents": [{"path": path, **entry} for path, entry in by_path.items()]}],
        model="scripted/reader",
    )

    def build(*_: object, **__: object):
        calls.append(True)
        return provider

    monkeypatch.setattr("dramatis.providers.anthropic_provider.AnthropicProvider", build)
    return calls


class TestLookingCostsNothing:
    def test_a_plain_run_reports_the_folder(self, corpus: Path, capsys) -> None:
        assert main(["structure", str(corpus)]) == 0
        assert "book.md" in capsys.readouterr().out

    def test_a_plain_run_calls_no_provider(
        self, corpus: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called = a_reader(monkeypatch, **{"book.md": {"role": "narrative", "reason": "a novel"}})
        main(["structure", str(corpus)])

        assert called == []

    def test_a_plain_run_leaves_nothing_saved(self, corpus: Path, store_path: Path) -> None:
        main(["structure", str(corpus), "--store", str(store_path)])

        with Store(store_path) as store:
            assert store.structure_map(str(corpus.resolve())) == {}

    def test_asking_without_confirming_saves_nothing(
        self, corpus: Path, store_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a_reader(
            monkeypatch,
            **{
                "book.md": {"role": "narrative", "reason": "a novel"},
                "notes.md": {"role": "reference", "reason": "a list"},
            },
        )
        assert main(["structure", str(corpus), "--store", str(store_path), "--ask"]) == 0

        with Store(store_path) as store:
            assert store.structure_map(str(corpus.resolve())) == {}

    def test_a_missing_path_exits_one(self, tmp_path: Path, capsys) -> None:
        assert main(["structure", str(tmp_path / "absent")]) == 1
        assert "no such file or folder" in capsys.readouterr().err

    def test_a_single_file_is_a_corpus_of_one(self, corpus: Path, capsys) -> None:
        """The shape 4.11 made a structure-map root, which this command could not read.

        `propose_structure` names a single file's one document after the file, and the command
        then rebuilt a path for it by joining the root to it — looking for `book.md/book.md`
        and exiting 1 on every single-file corpus. Reading the texts off the source instead
        removes the join, and with it the only place that had to know which shape it had.
        """
        assert main(["structure", str(corpus / "book.md"), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)

        assert [entry["path"] for entry in payload["documents"]] == ["book.md"]
        assert payload["root"] == str((corpus / "book.md").resolve())

    def test_json_stays_parseable_while_notes_go_to_stderr(self, corpus: Path, capsys) -> None:
        assert main(["structure", str(corpus), "--json"]) == 0
        captured = capsys.readouterr()

        payload = json.loads(captured.out)
        assert [entry["path"] for entry in payload["documents"]] == ["book.md", "notes.md"]


class TestAskingAndConfirming:
    def _ask(self, monkeypatch: pytest.MonkeyPatch) -> list:
        return a_reader(
            monkeypatch,
            **{
                "book.md": {
                    "role": "narrative",
                    "reason": "characters speak on the page",
                    "narrative_begins_with": "It is a truth universally acknowledged",
                },
                "notes.md": {"role": "reference", "reason": "it states a relation"},
            },
        )

    def test_asking_fills_the_roles_the_folder_could_not(
        self, corpus: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        self._ask(monkeypatch)
        assert main(["structure", str(corpus), "--ask", "--json"]) == 0

        payload = json.loads(capsys.readouterr().out)
        roles = {entry["path"]: entry["role"]["value"] for entry in payload["documents"]}
        assert roles == {"book.md": "narrative", "notes.md": "reference"}

    def test_the_division_is_shown_when_there_is_one(
        self, corpus: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        self._ask(monkeypatch)
        main(["structure", str(corpus), "--ask"])

        out = capsys.readouterr().out
        assert "before the narrative" in out
        assert "narrative" in out

    def test_confirming_saves_and_the_next_run_does_not_ask(
        self, corpus: Path, store_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        """The bullet, through the command somebody actually runs."""
        self._ask(monkeypatch)
        assert (
            main(["structure", str(corpus), "--store", str(store_path), "--ask", "--confirm"]) == 0
        )
        capsys.readouterr()

        called = a_reader(monkeypatch, **{"book.md": {"role": "reference", "reason": "changed"}})
        assert main(["structure", str(corpus), "--store", str(store_path), "--json"]) == 0

        payload = json.loads(capsys.readouterr().out)
        roles = {entry["path"]: entry["role"]["value"] for entry in payload["documents"]}
        assert roles == {"book.md": "narrative", "notes.md": "reference"}
        assert called == [], "the saved answer was reused, so nothing was asked again"

    def test_a_confirmed_role_is_marked_as_confirmed_on_screen(
        self, corpus: Path, store_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        self._ask(monkeypatch)
        main(["structure", str(corpus), "--store", str(store_path), "--ask", "--confirm"])
        capsys.readouterr()
        main(["structure", str(corpus), "--store", str(store_path)])

        assert "(confirmed)" in capsys.readouterr().out

    def test_correcting_beats_what_the_model_said(
        self, corpus: Path, store_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        self._ask(monkeypatch)
        code = main(
            [
                "structure",
                str(corpus),
                "--store",
                str(store_path),
                "--ask",
                "--set",
                "notes.md=narrative",
                "--confirm",
                "--json",
            ]
        )
        assert code == 0

        payload = json.loads(capsys.readouterr().out)
        notes = next(e for e in payload["documents"] if e["path"] == "notes.md")
        assert notes["role"]["value"] == "narrative"
        assert "corrected by you" in notes["role"]["basis"]

    def test_setting_without_confirming_says_nothing_was_saved(
        self, corpus: Path, store_path: Path, capsys
    ) -> None:
        code = main(
            [
                "structure",
                str(corpus),
                "--store",
                str(store_path),
                "--set",
                "book.md=narrative",
                "--set",
                "notes.md=reference",
            ]
        )
        assert code == 0
        assert "nothing was saved" in capsys.readouterr().err

        with Store(store_path) as store:
            assert store.structure_map(str(corpus.resolve())) == {}

    def test_confirming_an_unread_folder_is_refused_rather_than_guessed(
        self, corpus: Path, store_path: Path, capsys
    ) -> None:
        # Nothing has classified these documents, so there is nothing to confirm. Saving
        # would record "unknown" as an answer and never ask again.
        code = main(["structure", str(corpus), "--store", str(store_path), "--confirm"])

        assert code == 1
        assert "no role yet" in capsys.readouterr().err

    def test_a_folder_can_be_confirmed_by_hand_with_no_model_at_all(
        self, corpus: Path, store_path: Path, capsys
    ) -> None:
        """A person who already knows what their folder holds should not have to pay a model
        to be asked. `--set` alone is a complete path through this command."""
        code = main(
            [
                "structure",
                str(corpus),
                "--store",
                str(store_path),
                "--set",
                "book.md=narrative",
                "--set",
                "notes.md=reference",
                "--confirm",
            ]
        )
        assert code == 0

        with Store(store_path) as store:
            saved = store.structure_map(str(corpus.resolve()))
        assert saved["book.md"]["role"]["value"] == "narrative"

    def test_a_malformed_correction_is_refused(
        self, corpus: Path, store_path: Path, capsys
    ) -> None:
        code = main(
            ["structure", str(corpus), "--store", str(store_path), "--set", "book.md", "--confirm"]
        )

        assert code == 1
        assert "PATH=ROLE" in capsys.readouterr().err

    def test_a_role_that_is_not_a_role_is_refused(
        self, corpus: Path, store_path: Path, capsys
    ) -> None:
        code = main(
            [
                "structure",
                str(corpus),
                "--store",
                str(store_path),
                "--set",
                "book.md=front-matter",
                "--confirm",
            ]
        )

        assert code == 1
        assert "is not a role" in capsys.readouterr().err

    def test_forgetting_makes_the_folder_ask_again(
        self, corpus: Path, store_path: Path, capsys
    ) -> None:
        main(
            [
                "structure",
                str(corpus),
                "--store",
                str(store_path),
                "--set",
                "book.md=narrative",
                "--set",
                "notes.md=reference",
                "--confirm",
            ]
        )
        capsys.readouterr()

        assert main(["structure", str(corpus), "--store", str(store_path), "--forget"]) == 0
        assert "forgot 2" in capsys.readouterr().out

        main(["structure", str(corpus), "--store", str(store_path), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert all(entry["role"]["value"] == "unknown" for entry in payload["documents"])

    def test_a_provider_refusal_exits_one_rather_than_saving_an_empty_reading(
        self, corpus: Path, store_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        provider = ScriptedProvider(
            [ModelResponse(text="", model="m", provider="p", stop_reason="refusal")]
        )
        monkeypatch.setattr(
            "dramatis.providers.anthropic_provider.AnthropicProvider",
            lambda *_, **__: provider,
        )

        code = main(["structure", str(corpus), "--store", str(store_path), "--ask", "--confirm"])

        assert code == 1
        assert "declined" in capsys.readouterr().err
        with Store(store_path) as store:
            assert store.structure_map(str(corpus.resolve())) == {}


class TestEverythingPrintedIsAscii:
    """The convention `IngestResult.summary` states: a Windows console under a legacy code
    page renders typographic punctuation as replacement characters."""

    def test_the_output_of_every_path_through_the_command_survives_it(
        self, corpus: Path, store_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        a_reader(
            monkeypatch,
            **{
                "book.md": {
                    "role": "narrative",
                    "reason": "characters speak",
                    "narrative_begins_with": "a quotation that is not in this document",
                },
                "notes.md": {"role": "unsure", "reason": "hard to say"},
            },
        )
        main(["structure", str(corpus), "--store", str(store_path), "--ask"])
        captured = capsys.readouterr()
        captured.out.encode("ascii")
        captured.err.encode("ascii")

        main(["structure", str(corpus), "--store", str(store_path), "--confirm"])
        captured = capsys.readouterr()
        captured.out.encode("ascii")
        captured.err.encode("ascii")
