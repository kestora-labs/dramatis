"""Several editions of one work in one collection (**6.4**), corpus shape **D**.

Fixture **D** states the requirement this bullet answers, and states it as a refusal:

> **Editions are not revisions.** Fixture B's two drafts are one work moving forward in time,
> and the later supersedes the earlier. Fixture D's two editions are both authoritative, both
> citable, and both current. A reader may legitimately want the graph of the 1889 text
> specifically.

and a second, sharper one about the confidante who is called Hesper in 1889 and Perdita in
1903:

> Resolve within an edition, map across editions. Merging the two into one node that belongs
> to neither loses the ability to answer *"who is in the 1889 text?"*, which is the question
> this shape exists to serve.

Almost every test here is a way of asking whether one of those two sentences still holds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dramatis import ids
from dramatis.diff import (
    ADDED,
    BOTH,
    EDITION,
    REMOVED,
    RENAMED,
    TEXT,
    DiffError,
    attribution_of,
    diff_snapshots,
    editions_of,
)
from dramatis.identity import (
    IdentityError,
    correspond,
    correspondents,
    describe_correspondences,
    withdraw,
)
from dramatis.ingest import ingest_file
from dramatis.store import Store

COLLECTION = "col:salt-road"


# -- identifiers ----------------------------------------------------------------------


class TestAnEditionIsPartOfAWorksIdentity:
    def test_two_editions_are_two_works(self) -> None:
        first = ids.work_id("The Salt Road", "1889-first")
        second = ids.work_id("The Salt Road", "1903-revised")

        assert first != second

    def test_a_work_with_no_edition_keeps_the_bare_form(self) -> None:
        """Like `relation_id` leaving `observed` unsuffixed: every identifier already written
        down stays where it is, and a project that never had an edition never grows one."""
        assert ids.work_id("The Salt Road") == "work:the-salt-road"
        assert ids.work_id("The Salt Road", None) == "work:the-salt-road"
        assert ids.work_id("The Salt Road", "") == "work:the-salt-road"

    def test_the_edition_is_slugged_like_everything_else(self) -> None:
        assert ids.work_id("The Salt Road", "1903 Revised") == "work:the-salt-road@1903-revised"

    def test_it_reads_back_into_the_work_and_the_edition(self) -> None:
        assert ids.work_edition("work:the-salt-road@1889-first") == (
            "work:the-salt-road",
            "1889-first",
        )

    def test_a_work_with_no_edition_reads_back_as_itself(self) -> None:
        assert ids.work_edition("work:the-salt-road") == ("work:the-salt-road", None)


# -- ingest ---------------------------------------------------------------------------


def an_edition(tmp_path: Path, store_path: Path, edition: str, confidante: str) -> str:
    source = tmp_path / f"{edition}.md"
    source.write_text(
        f"Corin Ashe found {confidante} waiting.\n\n"
        f'"You came," she said.\n\n'
        "Marlow received them in the counting-house.\n",
        encoding="utf-8",
        newline="",
    )
    with Store(store_path) as store:
        return ingest_file(
            store,
            source,
            work_title="The Salt Road",
            collection_name="Salt Road",
            edition=edition,
        ).work_id


class TestTwoEditionsInOneCollection:
    def test_they_do_not_collapse_into_revisions_of_each_other(self, tmp_path: Path) -> None:
        """The whole bullet. Without the edition in the identity, the second ingest is a new
        revision of the first and the 1889 graph stops being addressable."""
        store_path = tmp_path / "d.sqlite"
        first = an_edition(tmp_path, store_path, "1889-first", "Hesper")
        second = an_edition(tmp_path, store_path, "1903-revised", "Perdita")

        assert first != second
        with Store(store_path) as store:
            works = store.list_works()
            assert len(works) == 2
            assert {w["edition"] for w in works} == {"1889-first", "1903-revised"}
            for work in works:
                assert len(store.list_text_revisions(work["id"])) == 1

    def test_they_share_one_collection_and_therefore_one_registry(self, tmp_path: Path) -> None:
        """Which is what *in a single collection* buys: a character whose name did not change
        is the same character in both editions, with nothing to declare."""
        store_path = tmp_path / "d.sqlite"
        an_edition(tmp_path, store_path, "1889-first", "Hesper")
        an_edition(tmp_path, store_path, "1903-revised", "Perdita")

        with Store(store_path) as store:
            assert {w["collection_id"] for w in store.list_works()} == {COLLECTION}

    def test_ingesting_without_an_edition_is_unchanged(self, tmp_path: Path) -> None:
        source = tmp_path / "plain.md"
        source.write_text("Ada met Bram.\n", encoding="utf-8", newline="")
        store_path = tmp_path / "plain.sqlite"

        with Store(store_path) as store:
            result = ingest_file(store, source, work_title="A Work")

        assert result.work_id == "work:a-work"


# -- the correspondence ---------------------------------------------------------------


def a_registry(tmp_path: Path) -> Path:
    """A project with two characters in it, standing in for two editions' casts."""
    store_path = tmp_path / "d.sqlite"
    with Store(store_path) as store:
        from dramatis.store import RegisteredCharacter

        store.upsert_collection(COLLECTION, "Salt Road")
        for identifier, name in (("char:hesper", "Hesper"), ("char:perdita", "Perdita")):
            store.upsert_character(
                RegisteredCharacter(id=identifier, collection_id=COLLECTION, name=name)
            )
    return store_path


class TestCorrespondenceIsNotAMerge:
    def test_neither_character_is_changed(self, tmp_path: Path) -> None:
        """The point of the whole operation. After a merge the 1903 graph would show a node
        captioned Hesper — a name that occurs nowhere in the 1903 text."""
        store_path = a_registry(tmp_path)

        with Store(store_path) as store:
            correspond(store, COLLECTION, "char:hesper", "char:perdita")

            hesper = store.get_character("char:hesper")
            perdita = store.get_character("char:perdita")

        assert hesper is not None and perdita is not None
        assert hesper.merged_into is None and perdita.merged_into is None
        assert hesper.name == "Hesper" and perdita.name == "Perdita"

    def test_each_keeps_its_own_surface_forms(self, tmp_path: Path) -> None:
        store_path = a_registry(tmp_path)

        with Store(store_path) as store:
            correspond(store, COLLECTION, "char:hesper", "char:perdita")

            assert store.find_character_by_form(COLLECTION, "Hesper").id == "char:hesper"
            assert store.find_character_by_form(COLLECTION, "Perdita").id == "char:perdita"

    def test_it_reads_back_both_ways_round(self, tmp_path: Path) -> None:
        """A diff has to be able to look up either side."""
        store_path = a_registry(tmp_path)

        with Store(store_path) as store:
            correspond(store, COLLECTION, "char:perdita", "char:hesper")
            found = correspondents(store, COLLECTION)

        assert found == {"char:hesper": "char:perdita", "char:perdita": "char:hesper"}

    def test_declaring_it_twice_either_way_round_is_one_record(self, tmp_path: Path) -> None:
        store_path = a_registry(tmp_path)

        with Store(store_path) as store:
            correspond(store, COLLECTION, "char:hesper", "char:perdita")
            correspond(store, COLLECTION, "char:perdita", "char:hesper", note="second thoughts")

            recorded = store.list_correspondences(COLLECTION)

        assert len(recorded) == 1
        assert recorded[0].note == "second thoughts"

    def test_it_can_be_withdrawn(self, tmp_path: Path) -> None:
        store_path = a_registry(tmp_path)

        with Store(store_path) as store:
            correspond(store, COLLECTION, "char:hesper", "char:perdita")

            assert withdraw(store, COLLECTION, "char:perdita", "char:hesper") is True
            assert withdraw(store, COLLECTION, "char:perdita", "char:hesper") is False
            assert correspondents(store, COLLECTION) == {}

    def test_it_is_described_for_a_console(self, tmp_path: Path) -> None:
        store_path = a_registry(tmp_path)

        with Store(store_path) as store:
            correspond(store, COLLECTION, "char:hesper", "char:perdita", note="renamed in 1903")
            lines = describe_correspondences(store, COLLECTION)

        assert lines == ["Hesper = Perdita across editions (renamed in 1903)"]


class TestWhatCorrespondenceRefuses:
    def test_a_character_that_is_not_there(self, tmp_path: Path) -> None:
        store_path = a_registry(tmp_path)

        with Store(store_path) as store, pytest.raises(IdentityError):
            correspond(store, COLLECTION, "char:hesper", "char:nobody")

    def test_a_character_with_itself(self, tmp_path: Path) -> None:
        store_path = a_registry(tmp_path)

        with Store(store_path) as store, pytest.raises(IdentityError) as raised:
            correspond(store, COLLECTION, "char:hesper", "char:hesper")

        assert "already itself" in str(raised.value)

    def test_two_characters_a_reading_found_in_one_edition(self, tmp_path: Path) -> None:
        """The distinction the operation exists for. Two people in one text who are really one
        person is a merge — which moves the surface forms and retires one of them."""
        store_path = tmp_path / "d.sqlite"
        an_edition(tmp_path, store_path, "1889-first", "Hesper")

        from dramatis.pipeline import analyse
        from dramatis.providers.scripted import ScriptedProvider

        payload = json.dumps(
            {
                "characters": [
                    {"name": n, "aliases": [], "kind": "person"} for n in ("Corin Ashe", "Hesper")
                ],
                "interactions": [
                    {
                        "participants": ["Corin Ashe", "Hesper"],
                        "quotation": '"You came," she said.',
                        "note": "",
                    }
                ],
                "groups": [
                    {"canonical_name": n, "forms": [n], "kind": "person", "same_as_registered": ""}
                    for n in ("Corin Ashe", "Hesper")
                ],
            }
        )

        with Store(store_path) as store:
            work = store.list_works()[0]
            revision = store.list_text_revisions(work["id"])[-1]
            analyse(store, revision.id, ScriptedProvider(lambda _r: payload))

            with pytest.raises(IdentityError) as raised:
                correspond(store, COLLECTION, "char:corin-ashe", "char:hesper")

        assert "both appear in" in str(raised.value)
        assert "merge" in str(raised.value)


# -- the diff across editions ---------------------------------------------------------


def a_snapshot(
    work: str, revision: str, characters: list[dict[str, Any]], relations: list[dict[str, Any]]
) -> dict[str, Any]:
    edition = ids.work_edition(work)[1]
    entry: dict[str, Any] = {"id": work, "title": "The Salt Road"}
    if edition:
        entry["edition"] = edition
    return {
        "schema_version": "0.1.0",
        "collection": {"id": COLLECTION, "name": "Salt Road"},
        "works": [entry],
        "snapshot": {
            "id": f"snap:{revision}",
            "work_id": work,
            "text_revision_id": revision,
            "analysis_run_id": "run:1",
        },
        "analysis_runs": [{"id": "run:1", "model": "m", "prompt_version": "extract-v2"}],
        "characters": characters,
        "relations": relations,
    }


def a_character(identifier: str, name: str) -> dict[str, Any]:
    return {"id": identifier, "name": name, "provenance": "observed"}


def a_relation(source: str, target: str) -> dict[str, Any]:
    return {
        "id": f"rel:{source.removeprefix('char:')}--{target.removeprefix('char:')}",
        "source": source,
        "target": target,
        "weight": 4,
        "weight_basis": "interaction_passages",
        "provenance": "observed",
    }


def the_editions() -> tuple[dict[str, Any], dict[str, Any]]:
    first = a_snapshot(
        "work:the-salt-road@1889-first",
        "rev:1889",
        [a_character("char:corin-ashe", "Corin Ashe"), a_character("char:hesper", "Hesper")],
        [a_relation("char:corin-ashe", "char:hesper")],
    )
    second = a_snapshot(
        "work:the-salt-road@1903-revised",
        "rev:1903",
        [a_character("char:corin-ashe", "Corin Ashe"), a_character("char:perdita", "Perdita")],
        [a_relation("char:corin-ashe", "char:perdita")],
    )
    return first, second


class TestComparingTwoEditions:
    def test_it_is_not_refused_as_two_different_works(self) -> None:
        """Two editions *are* two works by identifier, and the refusal's own argument — that
        two novels share no characters — is false of them: they share a registry."""
        before, after = the_editions()

        diff_snapshots(before, after)

    def test_two_genuinely_different_works_are_still_refused(self) -> None:
        before, _ = the_editions()
        other = a_snapshot("work:another", "rev:9", [], [])

        with pytest.raises(DiffError):
            diff_snapshots(before, other)

    def test_the_attribution_is_edition_and_never_text(self) -> None:
        """Fixture D's central complaint. `text` means the work was rewritten and the later
        state supersedes the earlier; both these editions are current."""
        before, after = the_editions()

        assert attribution_of(before, after) == EDITION
        assert attribution_of(before, after) != TEXT

    def test_which_editions_were_compared_is_carried(self) -> None:
        before, after = the_editions()

        assert editions_of(before, after) == ("1889-first", "1903-revised")
        assert diff_snapshots(before, after).editions == ("1889-first", "1903-revised")

    def test_a_changed_analysis_across_editions_is_still_both(self) -> None:
        """Nothing can be credited to either, which is what `both` has always meant. The
        edition pair is still carried, because it is part of the citation."""
        before, after = the_editions()
        after["analysis_runs"] = [{"id": "run:1", "model": "m", "prompt_version": "extract-v3"}]

        result = diff_snapshots(before, after)

        assert result.attribution == BOTH
        assert result.editions == ("1889-first", "1903-revised")

    def test_it_says_out_loud_that_nothing_below_is_a_change_to_the_work(self) -> None:
        before, after = the_editions()

        warnings = " ".join(diff_snapshots(before, after).warnings)

        assert "two editions of one work" in warnings

    def test_a_character_whose_name_did_not_change_is_not_reported_at_all(self) -> None:
        """The shared registry doing its work: Corin Ashe is literally the same character."""
        before, after = the_editions()

        names = {change.name for change in diff_snapshots(before, after).characters}

        assert "Corin Ashe" not in names


class TestARenamedCharacterAcrossEditions:
    def test_without_a_correspondence_she_is_a_departure_and_an_arrival(self) -> None:
        """Two false statements where the truth is that nothing about her changed but her
        name. This is the state the correspondence exists to fix."""
        before, after = the_editions()

        result = diff_snapshots(before, after)

        assert {(c.kind, c.name) for c in result.characters} == {
            (REMOVED, "Hesper"),
            (ADDED, "Perdita"),
        }

    def test_the_diff_points_at_the_command_that_would_fix_it(self) -> None:
        before, after = the_editions()

        warnings = " ".join(diff_snapshots(before, after).warnings)

        assert "dramatis correspond" in warnings

    def test_with_a_correspondence_she_is_one_figure_renamed(self) -> None:
        before, after = the_editions()
        corresponding = {"char:hesper": "char:perdita", "char:perdita": "char:hesper"}

        result = diff_snapshots(before, after, corresponding=corresponding)

        assert [(c.kind, c.name, c.counterparts) for c in result.characters] == [
            (RENAMED, "Hesper", ("char:perdita",))
        ]

    def test_her_relations_are_compared_through_the_correspondence(self) -> None:
        """Otherwise every edge she touched is reported removed and an identical one added —
        the same noise a merge would make, for the same reason."""
        before, after = the_editions()
        corresponding = {"char:hesper": "char:perdita", "char:perdita": "char:hesper"}

        result = diff_snapshots(before, after, corresponding=corresponding)

        assert result.relations == ()

    def test_a_correspondence_naming_somebody_absent_changes_nothing(self) -> None:
        before, after = the_editions()

        result = diff_snapshots(before, after, corresponding={"char:hesper": "char:nobody"})

        assert {c.kind for c in result.characters} == {REMOVED, ADDED}
