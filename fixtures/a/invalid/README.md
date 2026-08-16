# Deliberately invalid documents

Each file here fails validation in exactly one way, and the expected failure is named in
the filename. They exist so the negative case is tested as deliberately as the positive
one: a validator that accepts everything passes a suite made only of valid fixtures.

| File | Expected failure |
|---|---|
| `malformed-json.json` | Not parseable as JSON. |
| `missing-analysis-run-id.json` | Snapshot omits one of its two required axes. |
| `dangling-relation-endpoint.json` | An edge names a character that was never emitted. |
| `duplicate-character-id.json` | One identifier used by two characters. |
| `weight-without-basis.json` | A weight with no declared basis, so it means nothing. |
| `self-relation.json` | An edge joining a character to itself. |
