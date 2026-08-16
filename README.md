# Dramatis

[![CI](https://github.com/kestora-labs/dramatis/actions/workflows/ci.yml/badge.svg)](https://github.com/kestora-labs/dramatis/actions/workflows/ci.yml)
[![Licence: Apache-2.0](https://img.shields.io/badge/licence-Apache--2.0-blue.svg)](LICENSE)

*"the persons of the drama"*

Dramatis analyses a narrative body of work and produces a graph of its characters and
their relationships. Nodes are characters. Edges are relationships, weighted by closeness
and decorated with notes and pointers back into the source. Every analysis is saved as an
immutable snapshot, so the graph can be compared across drafts and revisions to show how
the relationships evolved as the work was written.

A [Kestora Labs](https://github.com/kestora-labs) product.

> **Status: pre-alpha.** Phase 1 of the build — the walking skeleton. A single text can be
> ingested, analysed, and viewed as a graph. See [`AI/ROADMAP.md`](AI/ROADMAP.md).

## Trying it

```bash
pip install -e ".[anthropic,serve]"
export ANTHROPIC_API_KEY=...          # or run `ant auth login`

dramatis ingest my-novel.txt --store project.sqlite --work "My Novel"
dramatis analyse rev:abc123 --store project.sqlite

npm ci --prefix web && npm --prefix web run build
dramatis serve --store project.sqlite                 # http://127.0.0.1:7373
```

`analyse` is the only command that calls a model. `ingest`, `validate`, and `serve` never
do, and work with no credential and no network.

Edge width and node size are drawn on a square-root scale. Interaction counts are heavily
skewed — two leads share dozens of passages while most pairs share one or two — and a
linear scale renders the leads as ropes and everyone else as indistinguishable hairlines.

## Privacy

Nothing leaves your machine except the text you send to the model provider you configure,
and with a local model nothing leaves at all. There is no telemetry, no analytics, and no
phone-home — not now and not later. Reading, rendering, diffing, and exporting an existing
analysis never require a network connection or an API key.

## Licence

Code is Apache-2.0. Documentation and the schema specification are CC BY 4.0.
