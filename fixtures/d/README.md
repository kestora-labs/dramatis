# Fixture D — scholarly corpus

Reference corpus **D**: one work in more than one edition, with third-party critical
annotation, under citation requirements.

Skeleton only — no analysis. Phase 6 develops multi-edition support and the export and
citation work against it.

## Layout

```
d/
├─ corpus.json
├─ editions/
│  ├─ 1889-first/       the first edition
│  └─ 1903-revised/     the author's revised edition
└─ apparatus/
   └─ commentary.md     third-party annotation, separately licensed
```

## What this fixture is for

**Editions are not revisions.** Fixture B's two drafts are one work moving forward in time,
and the later supersedes the earlier. Fixture D's two editions are both authoritative, both
citable, and both current. A reader may legitimately want the graph of the 1889 text
specifically. A model that treats the 1903 edition as simply the newer revision is wrong in
a way that matters to the people this shape exists for.

The two editions differ in one consequential way: in 1889 the confidante is named Hesper and
in 1903 she is renamed Perdita. Same character, same function, different surface form across
editions. Resolution must happen *within* an edition and be mapped *across* editions, not
merged into a single node that belongs to neither.

**Annotation is a third provenance source.** `apparatus/commentary.md` is not by the author
and is not the narrative. It asserts relationships, and it is separately licensed, so it must
be attributable and separable on export.

**Citation.** Every claim drawn from this corpus needs to say which edition it came from.
That is what the locator's `document_id` and the work's `edition` field are for.
