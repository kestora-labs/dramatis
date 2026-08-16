import { useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";

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

export function App() {
  const container = useRef<HTMLDivElement>(null);
  const [snapshots, setSnapshots] = useState<SnapshotSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [document_, setDocument] = useState<SnapshotDocument | null>(null);
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
    if (weightBasis === null) {
      setError("this snapshot mixes weight bases; edge widths are not comparable");
    }
    return () => instance.destroy();
  }, [document_]);

  const run = document_?.analysis_runs?.[0];

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
