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

> **Status: pre-alpha.** Phase 0 of the build. There is no application yet — only the
> schema and its validator. See [`AI/ROADMAP.md`](AI/ROADMAP.md).

## Privacy

Nothing leaves your machine except the text you send to the model provider you configure,
and with a local model nothing leaves at all. There is no telemetry, no analytics, and no
phone-home — not now and not later. Reading, rendering, diffing, and exporting an existing
analysis never require a network connection or an API key.

## Licence

Code is Apache-2.0. Documentation and the schema specification are CC BY 4.0.
