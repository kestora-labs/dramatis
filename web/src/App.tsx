import { Fragment, useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";

import { describeSelection, type Detail, type Selection } from "./detail.js";
import { buildGraph, type SnapshotDocument } from "./graph.js";
import { formatPath } from "./evidence.js";
import { describeAnchor, passageUrl, splitPassage, type PassageResponse } from "./passage.js";

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
function DetailPanel({
  detail,
  onClear,
  onOpen,
  openAt,
}: {
  detail: Detail;
  onClear: () => void;
  onOpen: (position: number) => void;
  openAt: number | null;
}) {
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

      {detail.kind === "relation" && detail.evidence.length > 0 && (
        <>
          <h3 className="field-label">Evidence — {detail.evidence.length} passages</h3>
          <ol className="evidence">
            {detail.evidence.map((piece, position) => (
              // The list key is the place in this list; the server is addressed by
              // `piece.position`, which is where the piece sits in the stored array.
              // Evidence carries no required id, and the same sentence may be quoted twice.
              <li key={position}>
                {/* A button, not a clickable div: this is reachable by keyboard and
                    announces itself, and the passage it opens is the point of the panel. */}
                <button
                  type="button"
                  className={piece.position === openAt ? "passage-link open" : "passage-link"}
                  onClick={() => onOpen(piece.position)}
                  aria-expanded={piece.position === openAt}
                >
                  <span className="locator">
                    {piece.document ? `${piece.document} · ${piece.locator}` : piece.locator}
                  </span>
                  <blockquote>{piece.quotation}</blockquote>
                </button>
                {piece.note && <p className="evidence-note">{piece.note}</p>}
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

/**
 * The source text, opened at the passage a piece of evidence names.
 *
 * The quotation is marked by the offsets the server measured, never by searching the text
 * here — see `passage.ts`. The highlight is scrolled into view on open, because a passage
 * from the middle of a long chapter is otherwise opened somewhere above the fold, which is
 * the same as not opening it at the position at all.
 */
function Reader({
  passage,
  locator,
  onClose,
}: {
  passage: PassageResponse;
  locator: string;
  onClose: () => void;
}) {
  const highlight = useRef<HTMLElement>(null);
  const { before, quoted, after } = splitPassage(passage.text, passage.quotation);
  const caveat = describeAnchor(
    passage.anchor,
    passage.anchor.stored_path ? formatPath(passage.anchor.stored_path) : null,
  );

  useEffect(() => {
    highlight.current?.scrollIntoView({ block: "center" });
  }, [passage]);

  return (
    <aside className="reader">
      <div className="detail-head">
        <h2>{locator}</h2>
        <button type="button" className="clear" onClick={onClose} aria-label="Close the source">
          ×
        </button>
      </div>

      {passage.quotation === null && (
        <p className="error">
          This quotation is not in this revision of the text, in these words or close to them. The
          passage it named is shown unhighlighted.
        </p>
      )}

      {caveat && <p className="caveat">{caveat}</p>}

      {passage.widened && (
        <p className="hint">
          The quotation runs past the end of this passage, so the following ones are shown with it.
        </p>
      )}

      <p className="source">
        {before}
        {quoted && (
          <mark ref={highlight} className={passage.anchor.method === "fuzzy" ? "approximate" : ""}>
            {quoted}
          </mark>
        )}
        {after}
      </p>
    </aside>
  );
}

export function App() {
  const container = useRef<HTMLDivElement>(null);
  const [snapshots, setSnapshots] = useState<SnapshotSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [document_, setDocument] = useState<SnapshotDocument | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [openAt, setOpenAt] = useState<number | null>(null);
  const [passage, setPassage] = useState<PassageResponse | null>(null);
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
    closePassage();
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

  function closePassage() {
    setOpenAt(null);
    setPassage(null);
  }

  function openPassage(position: number) {
    // Clicking the open passage again closes it, so the same control both opens and
    // dismisses rather than needing the reader's own × to be found first.
    if (position === openAt) {
      closePassage();
      return;
    }
    if (!selected || selection?.kind !== "relation") return;

    setOpenAt(position);
    setPassage(null);
    fetch(passageUrl(selected, selection.id, position))
      .then(async (response) => {
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          throw new Error(body.detail ?? `the server could not open that passage`);
        }
        return response.json();
      })
      .then(setPassage)
      .catch((reason: Error) => {
        setError(reason.message);
        closePassage();
      });
  }

  // The reader is headed by the locator the panel showed, so the two name the same place.
  const openLocator =
    detail?.kind === "relation"
      ? (detail.evidence.find((piece) => piece.position === openAt)?.locator ?? "")
      : "";

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
          <DetailPanel
            detail={detail}
            onClear={() => {
              setSelection(null);
              closePassage();
            }}
            onOpen={openPassage}
            openAt={openAt}
          />
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

      <main className="stage">
        <div ref={container} className="graph" />
        {passage && <Reader passage={passage} locator={openLocator} onClose={closePassage} />}
      </main>
    </div>
  );
}
