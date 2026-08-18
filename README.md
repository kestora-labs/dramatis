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

cd ~/writing/my-novel
dramatis ingest draft.txt --work "My Novel"    # creates dramatis.sqlite here
dramatis status                                # what is this project, and what is in it
dramatis analyse rev:abc123

npm ci --prefix web && npm --prefix web run build
dramatis serve                                 # http://127.0.0.1:7373
```

To keep the text on your own machine entirely, run a model locally with
[Ollama](https://ollama.com) and point `analyse` at it. No key, no account, no egress:

```bash
ollama pull llama3.1
dramatis analyse rev:abc123 --provider ollama
```

Or start with nothing and build the project in the browser — point `serve` at a file that
does not exist yet, open it, and choose a source, a role for each document, and any front
matter to leave out:

```bash
dramatis serve --store my-novel.sqlite     # creates nothing until you ask it to
```

Excluding a preface there means its characters never reach the model, so it costs nothing to
analyse a book without the critic who introduced it.

`analyse` calls a model, and `structure --ask` does when you ask it to. `ingest`, `status`,
`validate`, `characters`, and `serve` never do, and work with no credential and no network.

### In a container

The image builds the client and bundles it with the API. Mount a directory to hold the
project store, and publish the port to `127.0.0.1` so the server stays on your machine —
inside the container it binds `0.0.0.0`, because a container's loopback is unreachable from
the host, so the published address is where you decide who can reach it.

```bash
docker build -t dramatis .
docker run --rm -p 127.0.0.1:7373:7373 -v "$PWD/project:/data" dramatis
# the store lives at ./project/dramatis.sqlite; open http://127.0.0.1:7373
```

Analysis still runs wherever you point it: pass `-e ANTHROPIC_API_KEY=...` for a hosted
model, or reach an Ollama on the host for a fully local one.

## The project file

A project is one SQLite file — texts, character registry, and every snapshot. Copy it and
you have copied the project.

For a deployment a single file is wrong for — several people reading one corpus, a container
with no persistent disk — point `--store` at a Postgres URL instead. Nothing else changes:

```bash
pip install "dramatis[postgres]"
dramatis ingest draft.txt --store postgresql://user:pass@localhost/dramatis --work "My Novel"
```

Commands look for `dramatis.sqlite` in the current directory and every directory above it,
so they work anywhere inside a project. Only `ingest` creates one; everything else says so
rather than starting an empty project you did not ask for. `--store` names a file directly
and is never searched for.

**A project holds one collection**, and the character registry is scoped to it. Two works in
the same project share one cast, which is what a series or a shared universe wants:

```bash
dramatis ingest meteor-girl.txt --work "Meteor Girl" --collection "Golden Age"
dramatis ingest the-spark.txt   --work "The Spark"     # joins the same collection
```

An unrelated property belongs in its own file. Ingesting one into an existing project is
refused, because merging two casts into one namespace is not something you can undo.

Edge width and node size are drawn on a square-root scale. Interaction counts are heavily
skewed — two leads share dozens of passages while most pairs share one or two — and a
linear scale renders the leads as ropes and everyone else as indistinguishable hairlines.

## Privacy

Nothing leaves your machine except the text you send to the model provider you configure,
and with `--provider ollama` nothing leaves at all — Dramatis tells you if the Ollama you
have configured is on another machine. There is no telemetry, no analytics, and no
phone-home — not now and not later. Reading, rendering, diffing, and exporting an existing
analysis never require a network connection or an API key.

## Licence

Code is Apache-2.0. Documentation and the schema specification are CC BY 4.0.
