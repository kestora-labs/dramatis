"""Reading a folder, asking, and being asked only once.

**4.2** completes the structure map: a model proposes what `propose_structure` refuses to
guess, a person confirms or corrects it, and the answer is saved so the next ingest of the
same folder does not ask again.

The bullet's constraint is the one worth guarding — *no convention is hardcoded, and in
particular nothing anywhere defines what a preface is*. So there is a test below that reads
the shipped prompt looking for a definition, and several that check the machinery cannot
invent a boundary the document does not support.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dramatis.providers.scripted import ScriptedProvider
from dramatis.store import Store
from dramatis.structure import (
    NARRATIVE,
    REFERENCE,
    UNKNOWN,
    StructureError,
    confirm,
    prompt_text,
    propose_structure,
    propose_with_model,
    save,
    structure_for,
)

PREFACE = (
    "PREFACE BY THE EDITOR\n\n"
    "This edition follows the text of 1813, with the printer's errors silently amended. "
    "The reader will forgive a word on the circumstances of its composition.\n\n"
)
NOVEL = (
    "It is a truth universally acknowledged, that a single man in possession of a good "
    "fortune, must be in want of a wife.\n\n"
    '"My dear Mr Bennet," said his lady to him one day, "have you heard that Netherfield '
    'Park is let at last?"\n\n'
    "Mr Bennet replied that he had not.\n\n"
    "THE END\n"
)
APPENDIX = "\nAPPENDIX: a note on the transcription, prepared by a later hand.\n"


def a_folder(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    return root


def texts_of(root: Path, structure) -> dict[str, str]:
    return {
        plan.path: (root / plan.path).read_text(encoding="utf-8") for plan in structure.documents
    }


def one_book(tmp_path: Path, text: str = PREFACE + NOVEL + APPENDIX) -> Path:
    return a_folder(tmp_path / "corpus", {"book.md": text, "notes.md": "Ada is Bram's sister.\n"})


def answering(**by_path: dict) -> ScriptedProvider:
    return ScriptedProvider(
        [{"documents": [{"path": path, **entry} for path, entry in by_path.items()]}],
        model="scripted/reader",
    )


class TestTheModelAnswersWhatTheFolderCannot:
    def test_a_role_arrives_where_the_folder_offered_none(self, tmp_path: Path) -> None:
        root = one_book(tmp_path)
        structure = propose_structure(root)
        assert structure.plan_for("notes.md").role.value == UNKNOWN

        texts = texts_of(root, structure)
        read = propose_with_model(
            structure,
            texts,
            answering(
                **{
                    "book.md": {"role": NARRATIVE, "reason": "characters speak on the page"},
                    "notes.md": {
                        "role": REFERENCE,
                        "reason": "it states a relation rather than showing it",
                    },
                }
            ),
        )

        assert read.plan_for("book.md").role.value == NARRATIVE
        assert read.plan_for("notes.md").role.value == REFERENCE

    def test_the_model_reason_is_carried_into_the_basis(self, tmp_path: Path) -> None:
        # Somebody is about to be asked to agree with this. "reference" alone is not a
        # question anyone can answer.
        root = one_book(tmp_path)
        structure = propose_structure(root)
        texts = texts_of(root, structure)

        read = propose_with_model(
            structure,
            texts,
            answering(
                **{
                    "book.md": {"role": NARRATIVE, "reason": "characters speak on the page"},
                    "notes.md": {"role": REFERENCE, "reason": "it states a relation"},
                }
            ),
        )

        assert "it states a relation" in read.plan_for("notes.md").role.basis
        assert "scripted/reader" in read.plan_for("notes.md").role.basis

    def test_unsure_stays_unknown_rather_than_being_rounded(self, tmp_path: Path) -> None:
        """The whole reason `unsure` is in the schema. A model forced to choose between two
        roles will choose one, and the coin flip is recorded as a classification on exactly
        the documents a person most needs to look at."""
        root = one_book(tmp_path)
        structure = propose_structure(root)
        texts = texts_of(root, structure)

        read = propose_with_model(
            structure,
            texts,
            answering(
                **{
                    "book.md": {"role": NARRATIVE, "reason": "it is a novel"},
                    "notes.md": {"role": "unsure", "reason": "it could be either"},
                }
            ),
        )

        assert read.plan_for("notes.md").role.value == UNKNOWN
        assert read.plan_for("notes.md").role.settled is False

    def test_unsure_is_offered_by_the_schema(self) -> None:
        from dramatis.structure import RESPONSE_SCHEMA

        roles = RESPONSE_SCHEMA["properties"]["documents"]["items"]["properties"]["role"]["enum"]
        assert "unsure" in roles

    def test_a_document_the_model_skipped_is_named_rather_than_left_looking_answered(
        self, tmp_path: Path
    ) -> None:
        root = one_book(tmp_path)
        structure = propose_structure(root)
        texts = texts_of(root, structure)

        read = propose_with_model(
            structure, texts, answering(**{"book.md": {"role": NARRATIVE, "reason": "a novel"}})
        )

        assert read.plan_for("notes.md").role.value == UNKNOWN
        assert any("notes.md" in note and "did not answer" in note for note in read.notes)

    def test_a_refusal_is_not_an_empty_classification(self, tmp_path: Path) -> None:
        from dramatis.providers import ModelResponse

        root = one_book(tmp_path)
        structure = propose_structure(root)
        texts = texts_of(root, structure)
        provider = ScriptedProvider(
            [ModelResponse(text="", model="m", provider="p", stop_reason="refusal")]
        )

        with pytest.raises(StructureError, match="declined"):
            propose_with_model(structure, texts, provider)

    def test_only_documents_the_map_knows_about_are_sent(self, tmp_path: Path) -> None:
        # A caller cannot widen what leaves the machine by passing extra texts (Invariant 7
        # is about where text goes; this is about how much of it goes there).
        root = one_book(tmp_path)
        structure = propose_structure(root)
        texts = texts_of(root, structure)
        texts["secret-diary.md"] = "Nobody asked for this to be read."

        provider = answering(
            **{
                "book.md": {"role": NARRATIVE, "reason": "a novel"},
                "notes.md": {"role": REFERENCE, "reason": "a list"},
            }
        )
        propose_with_model(structure, texts, provider)

        assert "secret-diary.md" not in provider.calls[0].prompt
        assert "Nobody asked" not in provider.calls[0].prompt


class TestBoundariesAreFoundInTheText:
    def _read(self, tmp_path: Path, **boundaries: str):
        root = one_book(tmp_path)
        structure = propose_structure(root)
        texts = texts_of(root, structure)
        return propose_with_model(
            structure,
            texts,
            answering(
                **{
                    "book.md": {
                        "role": NARRATIVE,
                        "reason": "a novel with front matter",
                        **boundaries,
                    },
                    "notes.md": {"role": REFERENCE, "reason": "a list"},
                }
            ),
        )

    def test_a_preface_becomes_its_own_region_and_the_novel_another(self, tmp_path: Path) -> None:
        read = self._read(
            tmp_path,
            narrative_begins_with="It is a truth universally acknowledged",
            narrative_ends_with="THE END",
        )
        labels = [region.label for region in read.plan_for("book.md").regions]

        assert labels == ["before the narrative", "narrative", "after the narrative"]

    def test_the_material_around_the_narrative_is_reference(self, tmp_path: Path) -> None:
        read = self._read(
            tmp_path,
            narrative_begins_with="It is a truth universally acknowledged",
            narrative_ends_with="THE END",
        )
        regions = {region.label: region for region in read.plan_for("book.md").regions}

        assert regions["before the narrative"].role.value == REFERENCE
        assert regions["narrative"].role.value == NARRATIVE
        assert regions["after the narrative"].role.value == REFERENCE

    def test_the_boundary_lands_where_the_preface_actually_ends(self, tmp_path: Path) -> None:
        from dramatis.text import normalise_whitespace

        root = one_book(tmp_path)
        normalised = normalise_whitespace((root / "book.md").read_text(encoding="utf-8"))
        read = self._read(
            tmp_path,
            narrative_begins_with="It is a truth universally acknowledged",
            narrative_ends_with="THE END",
        )
        narrative = next(r for r in read.plan_for("book.md").regions if r.label == "narrative")

        assert normalised[narrative.starts_at :].startswith("It is a truth")
        assert normalised[: narrative.starts_at].strip().endswith("composition.")
        assert normalised[narrative.starts_at : narrative.ends_at].endswith("THE END")

    def test_a_quotation_reflowed_by_the_model_still_anchors(self, tmp_path: Path) -> None:
        # A model asked for verbatim text returns it re-wrapped often enough that refusing
        # would mean refusing correct answers. `reanchor` forgives whitespace and nothing else.
        read = self._read(
            tmp_path,
            narrative_begins_with="It is a truth\n   universally     acknowledged",
            narrative_ends_with="THE END",
        )
        labels = [region.label for region in read.plan_for("book.md").regions]

        assert "narrative" in labels
        assert read.notes == propose_structure(tmp_path / "corpus").notes

    def test_a_boundary_not_in_the_document_is_refused_and_reported(self, tmp_path: Path) -> None:
        """The failure that must never be silent. A boundary in the wrong place removes text
        from the analysis and nothing on screen would say why."""
        read = self._read(
            tmp_path,
            narrative_begins_with="Call me Ishmael, some years ago, never mind how long",
            narrative_ends_with="THE END",
        )
        plan = read.plan_for("book.md")

        assert [region.label for region in plan.regions] == ["whole document"]
        assert any("not in the document" in note and "book.md" in note for note in read.notes)

    def test_a_narrative_said_to_end_before_it_begins_divides_nothing(self, tmp_path: Path) -> None:
        read = self._read(
            tmp_path,
            narrative_begins_with="THE END",
            narrative_ends_with="It is a truth universally acknowledged",
        )
        plan = read.plan_for("book.md")

        assert [region.label for region in plan.regions] == ["whole document"]
        assert any("before it begins" in note for note in read.notes)

    def test_no_boundaries_leaves_the_document_whole(self, tmp_path: Path) -> None:
        read = self._read(tmp_path)
        plan = read.plan_for("book.md")

        assert [region.label for region in plan.regions] == ["whole document"]
        assert plan.regions[0].ends_at == plan.characters


class TestNothingDefinesAPreface:
    """**4.2**'s constraint, checked against the artefacts that could break it."""

    def test_the_prompt_asks_what_this_document_holds_not_what_a_preface_is(self) -> None:
        assert "not being asked what a preface is" in prompt_text()

    def test_the_prompt_names_no_boundary_text_to_look_for(self, tmp_path: Path) -> None:
        # A prompt listing "PREFACE", "INTRODUCTION", "FOREWORD" would be a hardcoded
        # convention wearing a prompt's clothes.
        lowered = prompt_text().lower()
        for heading in ("foreword", "prologue", "chapter one", "*** start of"):
            assert heading not in lowered

    def test_no_string_the_module_computes_with_names_a_kind_of_front_matter(self) -> None:
        """Prose may discuss a preface; code may not match on one.

        Checked over the syntax tree rather than the source text, because the two are not the
        same claim: the module's docstrings talk about prefaces at length and should, while a
        single string literal compared against a heading would be the hardcoded convention
        **4.2** forbids. Docstrings are what an AST can tell apart from the rest.
        """
        import ast

        import dramatis.structure as module

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        # Attribute docstrings — the bare string after a field — are prose too.
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list):  # IfExp and f-strings carry a `body` that is not one
                continue
            for previous, following in zip(body, body[1:], strict=False):
                if (
                    isinstance(previous, ast.AnnAssign | ast.Assign)
                    and isinstance(following, ast.Expr)
                    and isinstance(following.value, ast.Constant)
                ):
                    docstrings.add(id(following.value))

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                for word in ("preface", "foreword", "prologue", "introduction", "appendix"):
                    assert word not in node.value.lower(), f"line {node.lineno}: {node.value!r}"

    def test_a_document_whose_front_matter_is_not_called_a_preface_divides_the_same(
        self, tmp_path: Path
    ) -> None:
        root = a_folder(
            tmp_path / "corpus",
            {"book.md": "A NOTE FROM THE TRANSLATOR\n\nI have kept the idiom.\n\n" + NOVEL},
        )
        structure = propose_structure(root)
        texts = {"book.md": (root / "book.md").read_text(encoding="utf-8")}
        read = propose_with_model(
            structure,
            texts,
            answering(
                **{
                    "book.md": {
                        "role": NARRATIVE,
                        "reason": "the story starts after the translator's note",
                        "narrative_begins_with": "It is a truth universally acknowledged",
                    }
                }
            ),
        )

        labels = [region.label for region in read.plan_for("book.md").regions]
        assert labels[0] == "before the narrative"


class TestConfirming:
    def _read(self, tmp_path: Path, notes_role: str = REFERENCE):
        root = one_book(tmp_path)
        structure = propose_structure(root)
        texts = texts_of(root, structure)
        return root, propose_with_model(
            structure,
            texts,
            answering(
                **{
                    "book.md": {
                        "role": NARRATIVE,
                        "reason": "characters speak",
                        "narrative_begins_with": "It is a truth universally acknowledged",
                        "narrative_ends_with": "THE END",
                    },
                    "notes.md": {"role": notes_role, "reason": "it states a relation"},
                }
            ),
        )

    def test_confirming_settles_every_role(self, tmp_path: Path) -> None:
        _, read = self._read(tmp_path)
        settled = confirm(read)

        assert all(plan.role.settled for plan in settled.documents)

    def test_a_confirmation_keeps_what_it_agreed_with(self, tmp_path: Path) -> None:
        _, read = self._read(tmp_path)
        settled = confirm(read)

        assert "confirmed by you" in settled.plan_for("notes.md").role.basis
        assert "it states a relation" in settled.plan_for("notes.md").role.basis

    def test_a_correction_replaces_the_value_and_records_what_it_overrode(
        self, tmp_path: Path
    ) -> None:
        _, read = self._read(tmp_path)
        corrected = confirm(read, {"notes.md": NARRATIVE})
        role = corrected.plan_for("notes.md").role

        assert role.value == NARRATIVE
        assert "corrected by you" in role.basis
        assert "it states a relation" in role.basis

    def test_a_correction_reaches_the_regions_that_shared_the_old_role(
        self, tmp_path: Path
    ) -> None:
        # Otherwise a document says "reference" while the region covering all of it still
        # says "narrative", and which one the analysis reads is a coin toss.
        _, read = self._read(tmp_path)
        corrected = confirm(read, {"book.md": REFERENCE})
        narrative = next(r for r in corrected.plan_for("book.md").regions if r.label == "narrative")

        assert narrative.role.value == REFERENCE

    def test_an_unknown_document_cannot_be_confirmed(self, tmp_path: Path) -> None:
        _, read = self._read(tmp_path, notes_role="unsure")

        with pytest.raises(StructureError, match="no role yet"):
            confirm(read)

    def test_the_refusal_says_how_to_answer_it(self, tmp_path: Path) -> None:
        _, read = self._read(tmp_path, notes_role="unsure")

        with pytest.raises(StructureError, match="notes.md"):
            confirm(read)

    def test_an_unknown_document_can_be_confirmed_once_corrected(self, tmp_path: Path) -> None:
        _, read = self._read(tmp_path, notes_role="unsure")
        settled = confirm(read, {"notes.md": REFERENCE})

        assert settled.plan_for("notes.md").role.value == REFERENCE

    def test_correcting_a_document_that_is_not_there_is_an_error(self, tmp_path: Path) -> None:
        _, read = self._read(tmp_path)

        with pytest.raises(StructureError, match="nothing to correct"):
            confirm(read, {"absent.md": NARRATIVE})

    def test_a_role_that_is_not_a_role_is_refused(self, tmp_path: Path) -> None:
        _, read = self._read(tmp_path)

        with pytest.raises(StructureError, match="is not a role"):
            confirm(read, {"notes.md": "front matter"})


class TestSavedAndReused:
    def _confirmed(self, tmp_path: Path):
        root = one_book(tmp_path)
        structure = propose_structure(root)
        texts = texts_of(root, structure)
        read = propose_with_model(
            structure,
            texts,
            answering(
                **{
                    "book.md": {
                        "role": NARRATIVE,
                        "reason": "characters speak",
                        "narrative_begins_with": "It is a truth universally acknowledged",
                        "narrative_ends_with": "THE END",
                    },
                    "notes.md": {"role": REFERENCE, "reason": "it states a relation"},
                }
            ),
        )
        return root, confirm(read)

    def test_the_next_reading_of_the_folder_does_not_ask_again(self, tmp_path: Path) -> None:
        """The bullet, in one test: saved and reused on subsequent ingests."""
        root, settled = self._confirmed(tmp_path)

        with Store(tmp_path / "project.sqlite") as store:
            save(settled, store)
            again = structure_for(root, store)

        assert again.plan_for("book.md").role.value == NARRATIVE
        assert again.plan_for("notes.md").role.value == REFERENCE
        assert all(plan.role.settled for plan in again.documents)

    def test_the_regions_come_back_too(self, tmp_path: Path) -> None:
        root, settled = self._confirmed(tmp_path)

        with Store(tmp_path / "project.sqlite") as store:
            save(settled, store)
            again = structure_for(root, store)

        assert [r.label for r in again.plan_for("book.md").regions] == [
            "before the narrative",
            "narrative",
            "after the narrative",
        ]

    def test_a_document_edited_since_has_its_boundary_found_again(self, tmp_path: Path) -> None:
        """Why the boundary is stored as a quotation and not as a number. The author adds a
        paragraph to the preface; every offset in the file moves; the map still divides it in
        the right place."""
        root, settled = self._confirmed(tmp_path)
        with Store(tmp_path / "project.sqlite") as store:
            save(settled, store)

            longer = PREFACE + "A further word, added later, on the printer.\n\n" + NOVEL + APPENDIX
            (root / "book.md").write_text(longer, encoding="utf-8", newline="")
            again = structure_for(root, store)

        from dramatis.text import normalise_whitespace

        normalised = normalise_whitespace(longer)
        narrative = next(r for r in again.plan_for("book.md").regions if r.label == "narrative")

        assert normalised[narrative.starts_at :].startswith("It is a truth")
        assert not any("not in the document" in note for note in again.notes)

    def test_a_boundary_the_author_deleted_is_reported_rather_than_guessed(
        self, tmp_path: Path
    ) -> None:
        root, settled = self._confirmed(tmp_path)
        with Store(tmp_path / "project.sqlite") as store:
            save(settled, store)
            (root / "book.md").write_text(
                PREFACE + "The opening was rewritten entirely and says something else now.\n",
                encoding="utf-8",
                newline="",
            )
            again = structure_for(root, store)

        plan = again.plan_for("book.md")
        assert [region.label for region in plan.regions] == ["whole document"]
        assert any("earlier version" in note for note in again.notes)
        assert plan.role.value == NARRATIVE, "the role was confirmed; only the division lapsed"

    def test_a_document_added_since_is_still_unanswered(self, tmp_path: Path) -> None:
        root, settled = self._confirmed(tmp_path)

        with Store(tmp_path / "project.sqlite") as store:
            save(settled, store)
            (root / "letters.md").write_text("A new file nobody has seen.\n", encoding="utf-8")
            again = structure_for(root, store)

        assert again.plan_for("letters.md").role.value == UNKNOWN
        assert again.plan_for("letters.md").role.settled is False

    def test_what_the_folder_measures_is_taken_fresh_not_restored(self, tmp_path: Path) -> None:
        # The folder is what it is now. Only the answers a person gave are restored.
        root, settled = self._confirmed(tmp_path)

        with Store(tmp_path / "project.sqlite") as store:
            save(settled, store)
            (root / "notes.md").write_text(
                "Ada is Bram's sister, and also his rival.\n", encoding="utf-8"
            )
            again = structure_for(root, store)

        assert again.plan_for("notes.md").characters > settled.plan_for("notes.md").characters

    def test_an_unconfirmed_map_is_not_saved(self, tmp_path: Path) -> None:
        root = one_book(tmp_path)
        structure = propose_structure(root)

        with (
            Store(tmp_path / "project.sqlite") as store,
            pytest.raises(StructureError, match="not been confirmed"),
        ):
            save(structure, store)

    def test_a_folder_nobody_has_answered_for_reads_as_a_plain_proposal(
        self, tmp_path: Path
    ) -> None:
        root = one_book(tmp_path)

        with Store(tmp_path / "project.sqlite") as store:
            structure = structure_for(root, store)

        assert all(plan.role.value == UNKNOWN for plan in structure.documents)

    def test_a_correction_overwrites_only_the_document_corrected(self, tmp_path: Path) -> None:
        root, settled = self._confirmed(tmp_path)

        with Store(tmp_path / "project.sqlite") as store:
            save(settled, store)
            save(confirm(structure_for(root, store), {"notes.md": NARRATIVE}), store)
            again = structure_for(root, store)

        assert again.plan_for("notes.md").role.value == NARRATIVE
        assert again.plan_for("book.md").role.value == NARRATIVE
        assert [r.label for r in again.plan_for("book.md").regions][0] == "before the narrative"

    def test_forgetting_a_folder_makes_it_ask_again(self, tmp_path: Path) -> None:
        root, settled = self._confirmed(tmp_path)

        with Store(tmp_path / "project.sqlite") as store:
            save(settled, store)
            assert store.forget_structure_map(str(root)) == 2
            again = structure_for(root, store)

        assert all(plan.role.value == UNKNOWN for plan in again.documents)

    def test_two_folders_in_one_store_do_not_answer_for_each_other(self, tmp_path: Path) -> None:
        root, settled = self._confirmed(tmp_path)
        other = a_folder(tmp_path / "other", {"book.md": NOVEL})

        with Store(tmp_path / "project.sqlite") as store:
            save(settled, store)
            elsewhere = structure_for(other, store)

        assert elsewhere.plan_for("book.md").role.value == UNKNOWN

    def test_restoring_says_how_many_answers_it_kept(self, tmp_path: Path) -> None:
        root, settled = self._confirmed(tmp_path)

        with Store(tmp_path / "project.sqlite") as store:
            save(settled, store)
            again = structure_for(root, store)

        assert any("you confirmed earlier" in note for note in again.notes)


class TestEverythingAUserReadsIsAscii:
    def test_every_message_survives_a_legacy_console(self, tmp_path: Path) -> None:
        root = one_book(tmp_path)
        structure = propose_structure(root)
        texts = texts_of(root, structure)
        read = propose_with_model(
            structure,
            texts,
            answering(
                **{
                    "book.md": {
                        "role": NARRATIVE,
                        "reason": "characters speak",
                        "narrative_begins_with": "not in this document at all, deliberately",
                    },
                    "notes.md": {"role": "unsure", "reason": "hard to say"},
                }
            ),
        )
        for note in read.notes:
            note.encode("ascii")

        with pytest.raises(StructureError) as raised:
            confirm(read)
        str(raised.value).encode("ascii")

    # The prompt is deliberately not covered here. The convention is about a Windows console
    # under a legacy code page, and a prompt goes to a model rather than to a console; every
    # prompt this project ships uses typographic punctuation already.


class TestTheConfirmedMapReachesTheIngest:
    """What *reused on subsequent ingests* buys, concretely.

    `ingest_folder` takes one `role` for a whole folder, which cannot describe fixture **C**:
    its reference material and its narrative sit side by side, and no single flag separates
    them. A confirmed map does, and this is where it is spent.
    """

    def _confirmed(self, tmp_path: Path, store):
        root = one_book(tmp_path)
        structure = propose_structure(root)
        read = propose_with_model(
            structure,
            texts_of(root, structure),
            answering(
                **{
                    "book.md": {"role": NARRATIVE, "reason": "characters speak"},
                    "notes.md": {"role": REFERENCE, "reason": "it states a relation"},
                }
            ),
        )
        save(confirm(read), store)
        return root

    def test_each_document_takes_the_role_it_was_given(self, tmp_path: Path) -> None:
        from dramatis.ingest import ingest_folder

        with Store(tmp_path / "project.sqlite") as store:
            root = self._confirmed(tmp_path, store)
            result = ingest_folder(store, root)
            roles = {
                store.get_document(outcome.document_id).path: store.get_document(
                    outcome.document_id
                ).role
                for outcome in result.documents
            }

        assert roles == {"book.md": NARRATIVE, "notes.md": REFERENCE}

    def test_a_folder_nobody_confirmed_still_takes_the_flag(self, tmp_path: Path) -> None:
        from dramatis.ingest import ingest_folder

        root = one_book(tmp_path)
        with Store(tmp_path / "project.sqlite") as store:
            result = ingest_folder(store, root, role=REFERENCE)
            roles = {store.get_document(o.document_id).role for o in result.documents}

        assert roles == {REFERENCE}
        assert result.confirmed == ()

    def test_a_document_added_since_falls_back_to_the_flag(self, tmp_path: Path) -> None:
        from dramatis.ingest import ingest_folder

        with Store(tmp_path / "project.sqlite") as store:
            root = self._confirmed(tmp_path, store)
            (root / "letters.md").write_text("Nobody has classified this.\n", encoding="utf-8")
            result = ingest_folder(store, root, role=REFERENCE)
            roles = {
                store.get_document(o.document_id).path: store.get_document(o.document_id).role
                for o in result.documents
            }

        assert roles["book.md"] == NARRATIVE, "the confirmed answer still holds"
        assert roles["letters.md"] == REFERENCE, "the unanswered one takes the flag"
        assert result.confirmed == ("book.md", "notes.md")

    def test_the_ingest_says_which_documents_it_did_not_have_to_guess_about(
        self, tmp_path: Path
    ) -> None:
        from dramatis.ingest import ingest_folder

        with Store(tmp_path / "project.sqlite") as store:
            root = self._confirmed(tmp_path, store)
            summary = ingest_folder(store, root).summary

        assert "2 took the role you confirmed" in summary
        summary.encode("ascii")
