import { Fragment, useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";

import { describeSelection, type Detail, type Selection } from "./detail.js";
import {
  NO_FILTERS,
  isNarrowed,
  optionsFor,
  toggle,
  type FilterOptions,
  type Filters,
} from "./filters.js";
import { buildGraph, type SnapshotDocument } from "./graph.js";
import {
  DEFAULT_LAYOUT,
  LAYOUTS,
  clearPin,
  loadPin,
  planLayout,
  savePin,
  type LayoutName,
  type PinnedLayout,
  type Positions,
} from "./layout.js";
import { formatPath } from "./evidence.js";
import {
  buildGrid,
  hasHistory,
  readingLabels,
  revisionName,
  runName,
  type Lineage,
} from "./lineage.js";
import { describeAnchor, passageUrl, splitPassage, type PassageResponse } from "./passage.js";

interface SnapshotSummary {
  id: string;
  work_id: string;
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
 * A work's snapshots on the grid its two time axes describe.
 *
 * A row per text revision, a column per analysis run. Reading across a row holds the text
 * still and varies the analysis; reading down a column does the reverse. That is what
 * Invariant 4 asks for — the reader must be able to tell whether a graph changed because
 * the work changed or because the analysis did — and a list down the side of the screen
 * cannot answer it.
 *
 * An empty cell is drawn as such rather than skipped: a pairing nobody has analysed is a
 * different fact from one that produced nothing new.
 */
function LineagePanel({
  lineage,
  selected,
  onSelect,
}: {
  lineage: Lineage;
  selected: string | null;
  onSelect: (snapshotId: string) => void;
}) {
  const grid = buildGrid(lineage);
  const labels = readingLabels(grid.readings);
  const chosen = lineage.snapshots.find((snapshot) => snapshot.id === selected) ?? null;

  if (grid.revisions.length === 0) return null;

  return (
    <section className="lineage">
      <h3 className="field-label">
        {grid.revisions.length} text {grid.revisions.length === 1 ? "revision" : "revisions"} ·{" "}
        {grid.readings.length} {grid.readings.length === 1 ? "reading" : "readings"}
      </h3>

      {grid.readings.length === 0 ? (
        <p className="tally">This work has been ingested but never analysed.</p>
      ) : (
        <table className="grid">
          <thead>
            <tr>
              <th />
              {grid.readings.map((reading) => (
                <th
                  key={reading.configuration}
                  scope="col"
                  title={reading.runs.map((run) => run.id).join(", ")}
                >
                  {labels.get(reading.configuration)}
                  {reading.runs.length > 1 && (
                    <span className="documents">{reading.runs.length} runs</span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.rows.map((row, index) => (
              <tr key={grid.revisions[index].id}>
                <th scope="row" title={grid.revisions[index].id}>
                  {revisionName(grid.revisions[index])}
                  <span className="documents">
                    {grid.revisions[index].documents}{" "}
                    {grid.revisions[index].documents === 1 ? "file" : "files"}
                  </span>
                </th>
                {row.map((cell) => (
                  <td key={cell.reading.configuration}>
                    {cell.snapshots.length === 0 ? (
                      // Not analysed, which is not the same as analysed and unchanged.
                      <span className="never" aria-label="not analysed">
                        ·
                      </span>
                    ) : (
                      cell.snapshots.map((snapshot) => (
                        <button
                          key={snapshot.id}
                          type="button"
                          className={snapshot.id === selected ? "cell chosen" : "cell"}
                          aria-pressed={snapshot.id === selected}
                          onClick={() => onSelect(snapshot.id)}
                        >
                          {snapshot.characters}c · {snapshot.relations}r
                        </button>
                      ))
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {chosen && hasHistory(grid) && (
        <p className="tally">
          Showing {revisionName(grid.revisions.find((r) => r.id === chosen.text_revision_id)!)} read
          by{" "}
          {runName(
            grid.readings
              .flatMap((reading) => reading.runs)
              .find((run) => run.id === chosen.analysis_run_id)!,
          )}
          .
        </p>
      )}

      {grid.orphaned.length > 0 && (
        <p className="error">
          {grid.orphaned.length} snapshot{grid.orphaned.length === 1 ? "" : "s"} name a revision or
          run this work does not list, and cannot be placed.
        </p>
      )}
    </section>
  );
}

/** Every node's position, as the graph currently has them. */
function positionsOf(instance: cytoscape.Core): Positions {
  const positions: Positions = {};
  instance.nodes().forEach((node) => {
    const point = node.position();
    positions[node.id()] = { x: point.x, y: point.y };
  });
  return positions;
}

/**
 * Choosing how the graph is arranged, and keeping an arrangement worth keeping.
 *
 * The pin is the point of this control. A force layout has no memory, so without one every
 * redraw scatters the characters a reader has just finished learning the shape of.
 */
function LayoutControls({
  algorithm,
  pinned,
  onChoose,
  onPin,
  onUnpin,
}: {
  algorithm: LayoutName;
  pinned: PinnedLayout | null;
  onChoose: (name: LayoutName) => void;
  onPin: () => void;
  onUnpin: () => void;
}) {
  const chosen = LAYOUTS.find((choice) => choice.name === algorithm);

  return (
    <section className="layout-controls">
      <label htmlFor="layout">Layout</label>
      <select
        id="layout"
        value={algorithm}
        onChange={(event) => onChoose(event.target.value as LayoutName)}
      >
        {LAYOUTS.map((choice) => (
          <option key={choice.name} value={choice.name}>
            {choice.label}
          </option>
        ))}
      </select>
      {chosen && <p className="tally">{chosen.note}</p>}

      {pinned ? (
        <>
          <button type="button" className="pin pinned" onClick={onUnpin}>
            Pinned — release
          </button>
          <p className="tally">
            This arrangement is kept, and the graph reopens in it. Drag a character and the new
            position is kept too.
          </p>
        </>
      ) : (
        <>
          <button type="button" className="pin" onClick={onPin}>
            Pin this arrangement
          </button>
          <p className="tally">
            Unpinned, the layout is recomputed on every redraw and everyone moves.
          </p>
        </>
      )}
    </section>
  );
}

/**
 * Controls for narrowing the graph.
 *
 * Each control appears only when the snapshot gives it something to distinguish — see
 * `filters.ts`. A snapshot with no relation types gets no type control rather than an empty
 * one, and where nothing at all can be filtered the whole section is absent instead of
 * standing there inert.
 */
function FilterControls({
  options,
  filters,
  onChange,
  shown,
  total,
  hidden,
}: {
  options: FilterOptions;
  filters: Filters;
  onChange: (filters: Filters) => void;
  shown: number;
  total: number;
  hidden: number;
}) {
  // The slider's own value, which moves with the thumb. Applying the filter on every input
  // event rebuilds the graph and re-runs the force layout on each one — on a 102-character
  // novel a single drag queues dozens of those and the page stops responding. So the label
  // follows the thumb and the graph is rebuilt once, when the thumb is let go.
  const [draft, setDraft] = useState(filters.minimumWeight);
  useEffect(() => setDraft(filters.minimumWeight), [filters.minimumWeight]);

  const anything =
    options.weightUsable || options.types.length > 0 || options.provenance.length > 0;
  if (!anything) return null;

  const applyWeight = () => {
    if (draft !== filters.minimumWeight) onChange({ ...filters, minimumWeight: draft });
  };

  return (
    <section className="filters">
      <div className="detail-head">
        <h3 className="field-label">Filters</h3>
        {isNarrowed(filters) && (
          <button type="button" className="clear" onClick={() => onChange(NO_FILTERS)}>
            reset
          </button>
        )}
      </div>

      {options.weightUsable && (
        <>
          <label htmlFor="minimum-weight">
            Minimum weight — {draft} <code>{options.weightBasis}</code>
          </label>
          <input
            id="minimum-weight"
            type="range"
            min={0}
            max={options.maxWeight}
            value={draft}
            onChange={(event) => setDraft(Number(event.target.value))}
            onPointerUp={applyWeight}
            onKeyUp={applyWeight}
            onBlur={applyWeight}
          />
        </>
      )}

      {options.types.length > 0 && (
        <>
          <h4 className="field-label">Relation type</h4>
          <ul className="chips choices">
            {options.types.map((type) => (
              <li key={type}>
                <button
                  type="button"
                  className={filters.types.includes(type) ? "chosen" : ""}
                  aria-pressed={filters.types.includes(type)}
                  onClick={() => onChange({ ...filters, types: toggle(filters.types, type) })}
                >
                  {type}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      {options.provenance.length > 0 && (
        <>
          <h4 className="field-label">Provenance</h4>
          <ul className="chips choices">
            {options.provenance.map((value) => (
              <li key={value}>
                <button
                  type="button"
                  className={filters.provenance.includes(value) ? "chosen" : ""}
                  aria-pressed={filters.provenance.includes(value)}
                  onClick={() =>
                    onChange({ ...filters, provenance: toggle(filters.provenance, value) })
                  }
                >
                  {value}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      {isNarrowed(filters) && (
        <p className="tally">
          Showing {shown} of {total} relations
          {hidden > 0 &&
            (hidden === 1
              ? ", and 1 character has none left"
              : `, and ${hidden} characters have none left`)}
          .
        </p>
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
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [algorithm, setAlgorithm] = useState<LayoutName>(DEFAULT_LAYOUT);
  const [pin, setPin] = useState<PinnedLayout | null>(null);
  const [lineage, setLineage] = useState<Lineage | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Held in a ref as well as in state: the pin button and the drag handler need the live
  // instance, and they are not part of what makes the graph rebuild.
  const graph = useRef<cytoscape.Core | null>(null);

  useEffect(() => {
    fetch("/api/snapshots")
      .then((response) => response.json())
      .then((found: SnapshotSummary[]) => {
        setSnapshots(found);
        if (found.length > 0) setSelected(found[0].id);
      })
      .catch(() => setError("could not reach the Dramatis server"));
  }, []);

  // The lineage follows the work, not the snapshot: switching between two snapshots of one
  // work must not re-fetch the history they share.
  const work = snapshots.find((snapshot) => snapshot.id === selected)?.work_id ?? null;

  useEffect(() => {
    if (!work) return;
    fetch(`/api/works/${encodeURIComponent(work)}/lineage`)
      .then((response) => (response.ok ? response.json() : null))
      .then(setLineage)
      .catch(() => setLineage(null));
  }, [work]);

  useEffect(() => {
    if (!selected) return;
    setSelection(null);
    closePassage();
    // Filters describe one snapshot's vocabulary. Carrying "kinship" across to a snapshot
    // that has never heard of it would silently empty the graph.
    setFilters(NO_FILTERS);
    // A pin belongs to one snapshot, so this is where it is picked up.
    const found = loadPin(window.localStorage, selected);
    setPin(found);
    setAlgorithm(found?.layout ?? DEFAULT_LAYOUT);
    fetch(`/api/snapshots/${selected}`)
      .then((response) => response.json())
      .then(setDocument)
      .catch(() => setError(`could not load snapshot ${selected}`));
  }, [selected]);

  const built = document_ ? buildGraph(document_, filters) : null;
  const options = document_ ? optionsFor(document_) : null;

  useEffect(() => {
    if (!container.current || !document_) return;

    const { elements, weightBasis } = buildGraph(document_, filters);
    const plan = planLayout(
      pin,
      elements
        .filter((element) => !("source" in element.data))
        .map((element) => String(element.data.id)),
      algorithm,
    );

    const instance = cytoscape({
      container: container.current,
      elements,
      style: STYLE,
      // Nothing is laid out at construction. What runs depends on how much the pin covers,
      // and deciding that here rather than passing an algorithm blindly is the difference
      // between a filter change costing nothing and costing a full force simulation.
      layout: { name: "preset", positions: plan.preset, fit: true },
    });

    if (!plan.complete) {
      // Pinned nodes are locked rather than left out of the layout. A layout run over the
      // loose nodes alone would carry none of the edges joining them to the rest, so a
      // force simulation would have nothing to pull against and would scatter the
      // newcomers as though the graph had no structure. Locked, they act as the fixed
      // points the new characters are arranged around, and they do not move.
      const anchored = instance.nodes().filter((node) => plan.preset[node.id()] !== undefined);
      anchored.lock();
      instance.layout({ name: plan.algorithm, animate: false, nodeRepulsion: 8000 }).run();
      anchored.unlock();
      instance.fit(undefined, 30);
    }

    graph.current = instance;

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

    // Moving a character by hand is an edit to a pinned arrangement, so it is kept. Without
    // this the drag survives until the next redraw and then silently reverts.
    instance.on("dragfree", "node", () => {
      if (!selected) return;
      setPin((current) => {
        if (!current) return current;
        const updated = {
          ...current,
          positions: { ...current.positions, ...positionsOf(instance) },
          savedAt: new Date().toISOString(),
        };
        savePin(window.localStorage, selected, updated);
        return updated;
      });
    });

    if (weightBasis === null) {
      setError("this snapshot mixes weight bases; edge widths are not comparable");
    }
    return () => {
      instance.destroy();
      graph.current = null;
    };
    // `pin` is deliberately absent: pinning captures where the graph already is, so
    // rebuilding on it would throw away the arrangement being captured.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [document_, filters, algorithm, selected]);

  // A filter can remove the thing being inspected. Leaving its panel open would describe a
  // node or edge that is no longer on screen.
  useEffect(() => {
    if (!selection || !built) return;
    const drawn = built.elements.some((element) => element.data.id === selection.id);
    if (!drawn) {
      setSelection(null);
      closePassage();
    }
  }, [built, selection]);

  function pinLayout() {
    const instance = graph.current;
    if (!instance || !selected) return;

    const pinned: PinnedLayout = {
      layout: algorithm,
      positions: positionsOf(instance),
      savedAt: new Date().toISOString(),
    };
    savePin(window.localStorage, selected, pinned);
    setPin(pinned);
  }

  function unpinLayout() {
    if (!selected) return;
    clearPin(window.localStorage, selected);
    setPin(null);
  }

  function chooseLayout(name: LayoutName) {
    // Choosing a layout while pinned means asking for a different arrangement, which is a
    // request to drop the pinned one rather than to pin it under a new name.
    if (pin) unpinLayout();
    setAlgorithm(name);
  }

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

        {lineage && <LineagePanel lineage={lineage} selected={selected} onSelect={setSelected} />}

        {document_ && (
          <LayoutControls
            algorithm={algorithm}
            pinned={pin}
            onChoose={chooseLayout}
            onPin={pinLayout}
            onUnpin={unpinLayout}
          />
        )}

        {options && built && (
          <FilterControls
            options={options}
            filters={filters}
            onChange={setFilters}
            shown={built.relationsShown}
            total={built.relationsTotal}
            hidden={built.charactersHidden}
          />
        )}

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
