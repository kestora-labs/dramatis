"""Relations the reference material declares, kept apart from the ones the narrative enacts.

Fixture **C** states the requirement this file exists to enforce, in its own README:

> A pipeline that merges the two provenance classes into one graph loses both findings.

The two findings are a relationship the bible gives a whole section to and the transmissions
never show, and a pair who carry more page time than anyone while the bible does not mention
them. Both survive only if `asserted` and `observed` remain separate edges with separate
weights, all the way into the snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dramatis.aggregation import INTERACTION_PASSAGES, ComparabilityError, require_comparable
from dramatis.assertion import (
    ASSERTED_STATEMENTS,
    AssertionFailure,
    aggregate_assertions,
    extract_assertions,
    system_prompt,
)
from dramatis.ingest import ingest_folder
from dramatis.pipeline import analyse
from dramatis.providers import ModelResponse
from dramatis.providers.scripted import ScriptedProvider
from dramatis.resolution import resolve_mentions
from dramatis.segmentation import segment_text
from dramatis.store import NARRATIVE, REFERENCE, Store

BIBLE = (
    "# Ada Mbeki\n\n"
    "Relay chief at Kanto Station. Eleven years on the same rotation.\n\n"
    "## Tomas Reiner - brother, estranged\n\n"
    "They have not spoken since the Berthold decision.\n"
)
TRANSMISSION = (
    "Ada raised Sister Yeong on the open channel.\n\n"
    "Sister Yeong told Ada the relay was clear, and Ada thanked her.\n"
)


def _of(result, provenance: str) -> list[dict]:
    """The snapshot's relations of one provenance."""
    return [r for r in result.snapshot.document["relations"] if r["provenance"] == provenance]


def a_bible_reading(**overrides) -> str:
    payload = {
        "characters": [
            {"name": "Ada Mbeki", "aliases": ["Ada"], "kind": "person"},
            {"name": "Tomas Reiner", "aliases": ["Tomas"], "kind": "person"},
        ],
        "relationships": [
            {
                "participants": ["Ada Mbeki", "Tomas Reiner"],
                "quotation": "They have not spoken since the Berthold decision.",
                "types": ["kinship", "estrangement"],
                "note": "",
            }
        ],
    }
    payload.update(overrides)
    return json.dumps(payload)


def a_narrative_reading() -> str:
    return json.dumps(
        {
            "characters": [
                {"name": "Ada", "aliases": [], "kind": "person"},
                {"name": "Sister Yeong", "aliases": [], "kind": "person"},
            ],
            "interactions": [
                {
                    "participants": ["Ada", "Sister Yeong"],
                    "quotation": "Ada raised Sister Yeong on the open channel.",
                    "note": "",
                }
            ],
        }
    )


def a_grouping(*names: str) -> str:
    return json.dumps(
        {
            "groups": [
                {
                    "canonical_name": name,
                    "forms": [name],
                    "kind": "person",
                    "same_as_registered": "",
                }
                for name in names
            ]
        }
    )


def a_corpus(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "series-bible").mkdir(parents=True)
    (root / "transmissions").mkdir(parents=True)
    (root / "series-bible" / "ada.md").write_text(BIBLE, encoding="utf-8", newline="")
    (root / "transmissions" / "t01.md").write_text(TRANSMISSION, encoding="utf-8", newline="")
    return root


def analysed(tmp_path: Path, store: Store, *, replies: list[str] | None = None):
    """Ingest the corpus with both roles confirmed, then analyse it."""
    from dramatis.structure import confirm, propose_structure, save

    root = a_corpus(tmp_path)
    structure = propose_structure(root)
    save(
        confirm(
            structure,
            {"series-bible/ada.md": REFERENCE, "transmissions/t01.md": NARRATIVE},
        ),
        store,
    )
    ingested = ingest_folder(store, root, work_title="A Serial")
    provider = ScriptedProvider(
        replies
        or [
            a_narrative_reading(),
            a_bible_reading(),
            a_grouping("Ada", "Sister Yeong", "Ada Mbeki", "Tomas Reiner"),
        ]
    )
    return analyse(store, ingested.revision_id, provider), provider


class TestTheTwoPassesAreDifferentReadings:
    def test_reference_material_is_read_under_its_own_prompt(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            _, provider = analysed(tmp_path, store)

        steps = [call.metadata.get("step") for call in provider.calls]
        assert "extract" in steps
        assert "assert" in steps

    def test_the_bible_is_never_handed_to_the_narrative_prompt(self, tmp_path: Path) -> None:
        """The failure this whole split prevents. A bible read as narrative yields relations
        marked `observed`, which claims the story enacted something only written down."""
        with Store(tmp_path / "p.sqlite") as store:
            _, provider = analysed(tmp_path, store)

        for call in provider.calls:
            if call.metadata.get("step") == "extract":
                assert "brother, estranged" not in call.prompt

    def test_the_narrative_is_never_handed_to_the_assertion_prompt(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            _, provider = analysed(tmp_path, store)

        for call in provider.calls:
            if call.metadata.get("step") == "assert":
                assert "open channel" not in call.prompt

    def test_a_corpus_with_no_reference_material_reads_exactly_as_before(
        self, tmp_path: Path
    ) -> None:
        # Fixtures A and B are entirely narrative. Nothing about 4.3 should cost them a call.
        root = tmp_path / "plain"
        root.mkdir()
        (root / "t01.md").write_text(TRANSMISSION, encoding="utf-8", newline="")

        with Store(tmp_path / "p.sqlite") as store:
            ingested = ingest_folder(store, root, work_title="A Serial")
            provider = ScriptedProvider([a_narrative_reading(), a_grouping("Ada", "Sister Yeong")])
            result = analyse(store, ingested.revision_id, provider)

        assert [call.metadata.get("step") for call in provider.calls] == ["extract", "resolve"]
        assert result.asserted.relations == ()

    def test_a_narrative_only_run_records_no_assertion_parameters(self, tmp_path: Path) -> None:
        """A run's identity is hashed from its parameters. A key added unconditionally would
        give every existing corpus a new run identifier for a question never asked of it."""
        root = tmp_path / "plain"
        root.mkdir()
        (root / "t01.md").write_text(TRANSMISSION, encoding="utf-8", newline="")

        with Store(tmp_path / "p.sqlite") as store:
            ingested = ingest_folder(store, root, work_title="A Serial")
            result = analyse(
                store,
                ingested.revision_id,
                ScriptedProvider([a_narrative_reading(), a_grouping("Ada", "Sister Yeong")]),
            )
            run = result.snapshot.document["analysis_runs"][0]

        assert "assertion_prompt_version" not in run["parameters"]

    def test_a_run_that_read_reference_material_records_that_it_did(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            result, _ = analysed(tmp_path, store)
            run = result.snapshot.document["analysis_runs"][0]

        assert run["parameters"]["assertion_prompt_version"] == "assert-v1"
        assert run["parameters"]["asserted_weight_basis"] == ASSERTED_STATEMENTS


class TestTheTwoClassesStaySeparate:
    def test_a_declared_pair_and_an_enacted_pair_are_different_edges(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            result, _ = analysed(tmp_path, store)

        provenances = {r["provenance"] for r in result.snapshot.document["relations"]}
        assert provenances == {"observed", "asserted"}

    def test_one_pair_declared_and_enacted_yields_two_edges_not_one(self, tmp_path: Path) -> None:
        """The merge fixture C forbids. The bible and the narrative both cover Ada and Tomas;
        a single edge would report the declaration as though the story had shown it."""
        with Store(tmp_path / "p.sqlite") as store:
            root = a_corpus(tmp_path)
            from dramatis.structure import confirm, propose_structure, save

            save(
                confirm(
                    propose_structure(root),
                    {"series-bible/ada.md": REFERENCE, "transmissions/t01.md": NARRATIVE},
                ),
                store,
            )
            (root / "transmissions" / "t01.md").write_text(
                "Ada met Tomas at the gate and neither of them spoke.\n",
                encoding="utf-8",
                newline="",
            )
            ingested = ingest_folder(store, root, work_title="A Serial")
            result = analyse(
                store,
                ingested.revision_id,
                ScriptedProvider(
                    [
                        json.dumps(
                            {
                                "characters": [
                                    {"name": "Ada", "aliases": [], "kind": "person"},
                                    {"name": "Tomas", "aliases": [], "kind": "person"},
                                ],
                                "interactions": [
                                    {
                                        "participants": ["Ada", "Tomas"],
                                        "quotation": "Ada met Tomas at the gate",
                                        "note": "",
                                    }
                                ],
                            }
                        ),
                        a_bible_reading(),
                        json.dumps(
                            {
                                "groups": [
                                    {
                                        "canonical_name": "Ada Mbeki",
                                        "forms": ["Ada", "Ada Mbeki"],
                                        "kind": "person",
                                        "same_as_registered": "",
                                    },
                                    {
                                        "canonical_name": "Tomas Reiner",
                                        "forms": ["Tomas", "Tomas Reiner"],
                                        "kind": "person",
                                        "same_as_registered": "",
                                    },
                                ]
                            }
                        ),
                    ]
                ),
            )

        relations = result.snapshot.document["relations"]
        assert len(relations) == 2, "one edge would have merged the declaration with the scene"
        assert {r["provenance"] for r in relations} == {"observed", "asserted"}
        assert len({r["id"] for r in relations}) == 2, "and they must not collide on identity"

    def test_the_asserted_identifier_says_so(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            result, _ = analysed(tmp_path, store)

        asserted = _of(result, "asserted")
        assert asserted[0]["id"].endswith("@asserted")

    def test_an_observed_identifier_is_unchanged(self, tmp_path: Path) -> None:
        # Identifiers already written down must not move because 4.3 landed.
        with Store(tmp_path / "p.sqlite") as store:
            result, _ = analysed(tmp_path, store)

        observed = _of(result, "observed")
        assert "@" not in observed[0]["id"]

    def test_the_two_weights_are_on_declared_and_different_scales(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            result, _ = analysed(tmp_path, store)

        bases = {r["provenance"]: r["weight_basis"] for r in result.snapshot.document["relations"]}
        assert bases == {"observed": INTERACTION_PASSAGES, "asserted": ASSERTED_STATEMENTS}

    def test_nothing_can_rank_the_two_together(self, tmp_path: Path) -> None:
        """`require_comparable` is the guard, and it must fire here. A statement and a scene
        are different quantities; a chart ranking them together looks right and means nothing.
        """
        with Store(tmp_path / "p.sqlite") as store:
            result, _ = analysed(tmp_path, store)

        with pytest.raises(ComparabilityError):
            require_comparable(result.aggregation, result.asserted)

    def test_the_snapshot_keeps_them_apart_in_the_result_too(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            result, _ = analysed(tmp_path, store)

        assert all(r.provenance == "observed" for r in result.aggregation.relations)
        assert all(r.provenance == "asserted" for r in result.asserted.relations)


class TestWhatTheDeclarationSaid:
    def test_the_type_of_the_claim_survives_onto_the_edge(self, tmp_path: Path) -> None:
        # The bible does not say two characters interacted; it says they are estranged
        # siblings. 4.4 compares declaration against enactment and needs the declaration.
        with Store(tmp_path / "p.sqlite") as store:
            result, _ = analysed(tmp_path, store)

        asserted = next(r for r in result.asserted.relations)
        assert asserted.types == ("estrangement", "kinship")

    def test_types_from_several_statements_are_gathered_onto_one_edge(self) -> None:
        from dramatis.assertion import AssertedRelation

        segmentation = segment_text(BIBLE)
        claims = [
            AssertedRelation(("Ada", "Tomas"), "They have not spoken", ("kinship",), None, 0),
            AssertedRelation(
                ("Ada", "Tomas"), "Relay chief at Kanto Station.", ("rivalry",), None, 1
            ),
        ]
        resolution = _identity_resolution("Ada", "Tomas")
        aggregated = aggregate_assertions(claims, resolution, segmentation)

        assert aggregated.relations[0].types == ("kinship", "rivalry")

    def test_an_observed_relation_carries_no_types(self, tmp_path: Path) -> None:
        # Counting contact is not naming it. An empty list would imply the question was asked.
        with Store(tmp_path / "p.sqlite") as store:
            result, _ = analysed(tmp_path, store)

        observed = _of(result, "observed")
        assert "types" not in observed[0]

    def test_the_weight_counts_statements(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            result, _ = analysed(tmp_path, store)

        asserted = next(r for r in result.asserted.relations)
        assert asserted.weight == 1
        assert asserted.weight_basis == ASSERTED_STATEMENTS


class TestCharactersResolveOnce:
    def test_a_name_in_the_bible_and_a_name_on_the_page_become_one_character(
        self, tmp_path: Path
    ) -> None:
        """Without this, every relation reads as both undeclared and unenacted: the overlay
        would compare a declaration against an enactment that never meets it."""
        with Store(tmp_path / "p.sqlite") as store:
            root = a_corpus(tmp_path)
            from dramatis.structure import confirm, propose_structure, save

            save(
                confirm(
                    propose_structure(root),
                    {"series-bible/ada.md": REFERENCE, "transmissions/t01.md": NARRATIVE},
                ),
                store,
            )
            ingested = ingest_folder(store, root, work_title="A Serial")
            result = analyse(
                store,
                ingested.revision_id,
                ScriptedProvider(
                    [
                        a_narrative_reading(),
                        a_bible_reading(),
                        json.dumps(
                            {
                                "groups": [
                                    {
                                        "canonical_name": "Ada Mbeki",
                                        "forms": ["Ada", "Ada Mbeki"],
                                        "kind": "person",
                                        "same_as_registered": "",
                                    },
                                    {
                                        "canonical_name": "Sister Yeong",
                                        "forms": ["Sister Yeong"],
                                        "kind": "person",
                                        "same_as_registered": "",
                                    },
                                    {
                                        "canonical_name": "Tomas Reiner",
                                        "forms": ["Tomas Reiner"],
                                        "kind": "person",
                                        "same_as_registered": "",
                                    },
                                ]
                            }
                        ),
                    ]
                ),
            )

        observed = next(r for r in result.aggregation.relations)
        asserted = next(r for r in result.asserted.relations)
        assert observed.source in (asserted.source, asserted.target) or observed.target in (
            asserted.source,
            asserted.target,
        ), "Ada must be the same character in both edges"

    def test_both_passes_are_offered_to_resolution_in_one_call(self, tmp_path: Path) -> None:
        with Store(tmp_path / "p.sqlite") as store:
            _, provider = analysed(tmp_path, store)

        resolving = [call for call in provider.calls if call.metadata.get("step") == "resolve"]
        assert len(resolving) == 1
        assert "Sister Yeong" in resolving[0].prompt
        assert "Tomas Reiner" in resolving[0].prompt


class TestAQuotationTheDocumentDoesNotContain:
    def test_an_unverifiable_declaration_is_dropped(self, tmp_path: Path) -> None:
        """Invariant 3 does not soften because a relation was declared rather than enacted.
        A bible quotation the bible does not contain is as unusable as invented dialogue."""
        with Store(tmp_path / "p.sqlite") as store:
            _, _ = None, None
            root = a_corpus(tmp_path)
            from dramatis.structure import confirm, propose_structure, save

            save(
                confirm(
                    propose_structure(root),
                    {"series-bible/ada.md": REFERENCE, "transmissions/t01.md": NARRATIVE},
                ),
                store,
            )
            ingested = ingest_folder(store, root, work_title="A Serial")
            result = analyse(
                store,
                ingested.revision_id,
                ScriptedProvider(
                    [
                        a_narrative_reading(),
                        a_bible_reading(
                            relationships=[
                                {
                                    "participants": ["Ada Mbeki", "Tomas Reiner"],
                                    "quotation": "They were reconciled at the end of the war.",
                                    "types": ["kinship"],
                                    "note": "",
                                }
                            ]
                        ),
                        a_grouping("Ada", "Sister Yeong", "Ada Mbeki", "Tomas Reiner"),
                    ]
                ),
            )

        assert result.asserted.relations == ()

    def test_a_declaration_with_no_quotation_at_all_is_discarded_and_named(self) -> None:
        segmentation = segment_text(BIBLE)
        provider = ScriptedProvider(
            [
                a_bible_reading(
                    relationships=[
                        {
                            "participants": ["Ada Mbeki", "Tomas Reiner"],
                            "quotation": "",
                            "types": ["kinship"],
                            "note": "",
                        }
                    ]
                )
            ]
        )
        assertions = extract_assertions(segmentation, provider)

        assert assertions.relationships == []
        assert any("no quotation" in warning for warning in assertions.warnings)

    def test_a_refusal_is_not_an_empty_bible(self) -> None:
        segmentation = segment_text(BIBLE)
        provider = ScriptedProvider(
            [ModelResponse(text="", model="m", provider="p", stop_reason="refusal")]
        )

        with pytest.raises(AssertionFailure, match="declined"):
            extract_assertions(segmentation, provider)

    def test_a_relationship_with_one_participant_is_discarded(self) -> None:
        segmentation = segment_text(BIBLE)
        provider = ScriptedProvider(
            [
                a_bible_reading(
                    relationships=[
                        {
                            "participants": ["Ada Mbeki"],
                            "quotation": "Relay chief at Kanto Station.",
                            "types": [],
                            "note": "",
                        }
                    ]
                )
            ]
        )
        assertions = extract_assertions(segmentation, provider)

        assert assertions.relationships == []
        assert any("joins exactly two" in warning for warning in assertions.warnings)


class TestThePromptDoesNotInvent:
    def test_it_says_a_document_may_decline_to_commit_to_a_name(self) -> None:
        # Fixture C's bible names Berthold and says outright it is unresolved whether
        # Berthold is a person, a station, or a ruling. Nothing may be built on that.
        prompt = system_prompt().lower()
        assert "mentioned only" in prompt or "does not commit" in prompt

    def test_it_says_an_invented_relationship_is_worse_than_a_missed_one(self) -> None:
        assert "worse than one missed" in system_prompt()

    def test_it_refuses_relations_with_things_rather_than_people(self) -> None:
        assert "not a person or a group of people" in system_prompt()

    def test_it_demands_a_verbatim_quotation(self) -> None:
        prompt = system_prompt()
        assert "verbatim" in prompt
        assert "searched for literally" in prompt

    def test_it_does_not_hand_the_model_a_closed_vocabulary_of_types(self) -> None:
        # The schema calls relation types "deliberately not enumerated: no closed vocabulary
        # survives contact with real narrative". The prompt must not quietly close it.
        assert "not a fixed vocabulary" in system_prompt()


def _identity_resolution(*names: str):
    """A resolution in which every name is its own character, with no model involved."""
    from dramatis.extraction import MentionedCharacter
    from dramatis.store import Store as _Store

    class _Registry:
        def list_characters(self, _collection_id):
            return []

        def upsert_character(self, *_args, **_kwargs):
            return None

    assert _Store  # the real store is not needed; resolution's baseline touches only these
    return resolve_mentions(
        [MentionedCharacter(name=name, aliases=(), kind="person") for name in names],
        _Registry(),
        "col:test",
    )
