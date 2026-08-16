# API tests

A [Bruno](https://usebruno.com) collection for the Dramatis HTTP API. Useful for poking at
a running server by hand, and runnable headless from the terminal.

## Running

Start a server against a project that has at least one snapshot in it:

```bash
dramatis serve                       # http://127.0.0.1:7373
```

Then, from this directory:

```bash
bru run --env local
```

Install the runner with `npm install -g @usebruno/cli` if `bru` is missing. The graphical
Bruno app opens this folder directly — **Open Collection**, then pick `api-tests`.

Point it somewhere else by editing `environments/local.bru`, or by adding another
environment file beside it.

## What it tests

The suite discovers what to ask for rather than hard-coding identifiers: it reads the work
list and the snapshot list, captures the first of each, and uses those for the requests that
follow. It therefore runs against any project, not only the fixture.

Most of the value is in `04-snapshot-document`, which checks the invariants a reader would
rely on rather than merely the shape:

| Assertion | Why it matters |
|---|---|
| Both axes resolve inside the document | A document naming a revision or run it does not carry cannot be interpreted away from the store that made it. |
| Every relation endpoint is a character present in the document | An edge against an absent character renders as a phantom node. |
| Every weight declares its basis, and all relations share one | Weights are comparable only within a basis; one travelling without it is a number that looks meaningful and is not. |
| Every character and relation records its provenance | Observed, asserted, and human claims are different kinds of thing. |
| Every piece of evidence has a quotation and a locator | Evidence that cannot be resolved back to a passage is not evidence. |
| Every locator segment names a type the work declares | Structural position is a path of typed segments supplied per work; an undeclared type resolves to nothing. |
| The snapshot list carries no evidence or weights | The document is served whole from its own endpoint. A list repeating it would be a second representation, free to drift. |

## Relationship to the Python tests

`tests/test_server.py` covers the same endpoints through an in-process client. It is faster,
runs in CI, and does not need a server. This collection exists for the things that one
cannot do: exercising a real process over a real socket, and giving somebody a collection
they can open, edit, and poke at without reading Python.

Neither replaces the other. The Python tests found none of the two faults that shipping
`serve` uncovered, because they never crossed the process boundary.
