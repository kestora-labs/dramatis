import { Fragment, useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";

import { describeSelection, type Detail, type Selection } from "./detail.js";
import { buildGraph, type SnapshotDocument } from "./graph.js";

interface SnapshotSummary {
  id: string;
  label: string | null;
  created_at: string;
  characters: number;
  relations: number;
}

const STYLE: cytoscape.StylesheetJson = [
  {
    selector: "node",
    style: {
      "background-color": "#4a5568",
      label: "data(label)",
      width: "data(size)",
      height: "data(size)",
      "font-size": 11,
      color: "#1a202c",
      "text-valign": "bottom",
      "text-margin-y": 4,
      "text-outline-color": "#ffffff",
      "text-outline-width": 2,
    },
  },
  { selector: "node.isolated", style: { "background-color": "#a0aec0" } },
  {
    selector: "edge",
    style: {
      width: "data(width)",
      "line-color": "#a0aec0",
      "curve-style": "haystack",
      opacity: 0.75,
    },
  },
  { selector: ":selected", style: { "background-color": "#c05621", "line-color": "#c05621" } },
];

/**
 * What the snapshot claims about the selected node or edge.
 *
 * The fields themselves are decided in `detail.ts`; this only sets them. Aliases and
 * relation types are set apart from the rest because they are lists of short free-text
 * terms rather than single values, and run together in a definition list they read as one
 * run-on string.
 */
function DetailPanel({ detail, onClear }: { detail: Detail; onClear: () => void }) {
  const list = detail.kind === "character" ? detail.aliases : detail.types;
  const listLabel = detail.kind === "character" ? "Also known as" : "Types";

  return (
    <section className="detail">
      <div className="detail-head">
        <h2>{detail.title}</h2>
        <button type="button" className="clear" onClick={onClear} aria-label="Clear selection">
          ×
        </button>
      </div>

      {list.length > 0 && (
        <>
          <h3 className="field-label">{listLabel}</h3>
          <ul className="chips">
            {list.map((term) => (
              <li key={term}>{term}</li>
            ))}
          </ul>
        </>
      )}

      <dl>
        {detail.fields.map((field) => (
          <Fragment key={field.label}>
            <dt>{field.label}</dt>
            <dd>{field.code ? <code>{field.value}</code> : field.value}</dd>
          </Fragment>
        ))}
      </dl>

      {detail.notes && <p className="detail-notes">{detail.notes}</p>}
    </section>
  );
}

export function App() {
  const container = useRef<HTMLDivElement>(null);
  const [snapshots, setSnapshots] = useState<SnapshotSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [document_, setDocument] = useState<SnapshotDocument | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/snapshots")
      .then((response) => response.json())
      .then((found: SnapshotSummary[]) => {
        setSnapshots(found);
        if (found.length > 0) setSelected(found[0].id);
      })
      .catch(() => setError("could not reach the Dramatis server"));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setSelection(null);
    fetch(`/api/snapshots/${selected}`)
      .then((response) => response.json())
      .then(setDocument)
      .catch(() => setError(`could not load snapshot ${selected}`));
  }, [selected]);

  useEffect(() => {
    if (!container.current || !document_) return;

    const { elements, weightBasis } = buildGraph(document_);
    const instance = cytoscape({
      container: container.current,
      elements,
      style: STYLE,
      layout: { name: "cose", animate: false, nodeRepulsion: 8000 },
    });

    // `tap` rather than Cytoscape's own select/unselect pair: moving from one node to
    // another fires both, and the panel should not depend on which arrives last.
    instance.on("tap", "node", (event) =>
      setSelection({ kind: "character", id: event.target.id() }),
    );
    instance.on("tap", "edge", (event) =>
      setSelection({ kind: "relation", id: event.target.id() }),
    );
    instance.on("tap", (event) => {
      if (event.target === instance) setSelection(null);
    });

    if (weightBasis === null) {
      setError("this snapshot mixes weight bases; edge widths are not comparable");
    }
    return () => instance.destroy();
  }, [document_]);

  const run = document_?.analysis_runs?.[0];
  const detail = document_ ? describeSelection(document_, selection) : null;

  return (
    <div className="layout">
      <aside>
        <h1>Dramatis</h1>
        {error && <p className="error">{error}</p>}

        <label htmlFor="snapshot">Snapshot</label>
        <select
          id="snapshot"
          value={selected ?? ""}
          onChange={(event) => setSelected(event.target.value)}
        >
          {snapshots.map((snapshot) => (
            <option key={snapshot.id} value={snapshot.id}>
              {snapshot.label ?? snapshot.id} — {snapshot.characters} characters,{" "}
              {snapshot.relations} relations
            </option>
          ))}
        </select>

        {detail ? (
          <DetailPanel detail={detail} onClear={() => setSelection(null)} />
        ) : (
          document_ && (
            <p className="hint">Select a node or an edge for what the snapshot claims about it.</p>
          )
        )}

        {document_ && (
          <dl>
            <dt>Work</dt>
            <dd>{document_.works[0]?.title}</dd>
            <dt>Text revision</dt>
            <dd>
              <code>{document_.snapshot.text_revision_id}</code>
            </dd>
            <dt>Analysis run</dt>
            <dd>
              <code>{document_.snapshot.analysis_run_id}</code>
            </dd>
            <dt>Model</dt>
            <dd>{run?.model}</dd>
            <dt>Prompt</dt>
            <dd>{run?.prompt_version}</dd>
          </dl>
        )}

        <p className="note">
          Edge width and node size are on a square-root scale: interaction counts are heavily
          skewed, and a linear scale renders the leads as ropes and everyone else as
          indistinguishable hairlines.
        </p>
      </aside>

      <main ref={container} className="graph" />
    </div>
  );
}
