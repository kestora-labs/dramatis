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
import { DEFAULT_SCALING, buildGraph, type Scaling, type SnapshotDocument } from "./graph.js";
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
import {
  changeIndex,
  changeList,
  classFor,
  describeAttribution,
  pairKey,
  unionDocument,
  type ChangeEntry,
  type DiffResponse,
} from "./overlay.js";
import {
  applyMarks,
  compareProvenance,
  describeComparison,
  findingList,
  type Comparison,
  type FindingEntry,
} from "./declared.js";
import {
  describePlan,
  initialChoices,
  isReady,
  plansFor,
  type Choice,
  type ProposedStructure,
  type Role,
} from "./create.js";
import {
  appliedIn,
  canSubmit,
  characterKinds,
  conflictsFor,
  correctionBody,
  correctionsFor,
  correctionsUrl,
  currentText,
  describe as describeValue,
  fieldsFor,
  withCorrection,
  type CorrectionEntry,
  type CorrectionsPayload,
} from "./correction.js";
import {
  STATUSES as REVIEW_STATUSES,
  canRecord,
  decisionBody,
  indexReviews,
  reviewsUrl,
  statusFor,
  tally,
  withSubject,
  type ReviewOverlay,
  type ReviewStatus,
  type ReviewSubject,
} from "./review.js";
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

  // The overlay. Colour carries the direction of the change and opacity carries whether the
  // element is still there: a removed edge is drawn, because a diff that omitted what went
  // would hide the half a reader is least able to reconstruct.
  { selector: "node.added", style: { "background-color": "#2f855a" } },
  { selector: "node.removed", style: { "background-color": "#c53030", opacity: 0.45 } },
  { selector: "node.merged", style: { "background-color": "#b7791f", opacity: 0.45 } },
  { selector: "node.split", style: { "background-color": "#2b6cb0" } },
  { selector: "edge.added", style: { "line-color": "#2f855a", opacity: 1 } },
  {
    selector: "edge.removed",
    style: { "line-color": "#c53030", opacity: 0.5, "line-style": "dashed" },
  },
  { selector: "edge.strengthened", style: { "line-color": "#2b6cb0", opacity: 1 } },
  { selector: "edge.weakened", style: { "line-color": "#b7791f", opacity: 1 } },
  { selector: "edge.retyped", style: { "line-color": "#805ad5", opacity: 1 } },

  // 4.4, comparing what a corpus declares against what it enacts. A separate palette from
  // the diff marks above, and never drawn at the same time: two mark systems answering
  // different questions in one picture is a picture answering neither.
  //
  // The two disagreements are the loud ones and the agreement recedes, because a pair that
  // is both declared and enacted is the case needing no attention. Declared-only is dashed
  // as well as coloured: it is the relationship that exists in the plan and not on the page,
  // and a dashed edge is the one convention a reader already reads as "not really there".
  {
    selector: "edge.declared-only",
    style: { "line-color": "#805ad5", opacity: 1, "line-style": "dashed" },
  },
  { selector: "edge.enacted-only", style: { "line-color": "#c05621", opacity: 1 } },
  { selector: "edge.agreed", style: { "line-color": "#a0aec0", opacity: 0.35 } },
];

/**
 * Where review of the selected node or edge stands, and how to move it.
 *
 * A control rather than a field, because review is the one thing on this panel a person
 * *does* rather than reads. It sits above the claim it is about: the question "is this
 * right?" is asked of the whole thing, not of the last row of it.
 *
 * The reason box is always offered and only required for `corrected` — the rule `canRecord`
 * holds and the server enforces. A correction that does not say what it corrects is
 * indistinguishable from a rejection somebody softened. Why that button is unavailable is
 * said in text rather than in a `title`: a disabled control does not reliably receive the
 * hover that would show a tooltip, so the explanation would be invisible exactly when it is
 * needed.
 */
function ReviewControl({
  subject,
  viewing,
  busy,
  onRecord,
}: {
  subject: ReviewSubject;
  /** The snapshot on screen, so a ruling taken in another one can say so. */
  viewing: string | null;
  busy: boolean;
  onRecord: (status: ReviewStatus, note: string) => void;
}) {
  const [note, setNote] = useState("");

  // The standing reason, until the reviewer starts writing their own. Clearing it when the
  // selection changes is the caller's job — the key on this component does it.
  const reason = note || subject.note || "";

  return (
    <section className="review" aria-label="Review">
      <div className="review-head">
        <h3 className="field-label">Review</h3>
        <span className={`review-status is-${subject.status}`}>{subject.status}</span>
      </div>

      <div className="review-buttons">
        {REVIEW_STATUSES.map((status) => (
          <button
            key={status}
            type="button"
            className={status === subject.status ? "review-choice current" : "review-choice"}
            aria-pressed={status === subject.status}
            disabled={busy || !canRecord(status, reason)}
            onClick={() => onRecord(status, reason)}
          >
            {status}
          </button>
        ))}
      </div>

      <input
        type="text"
        className="review-note"
        placeholder="Why (required to correct)"
        value={reason}
        onChange={(event) => setNote(event.target.value)}
      />

      {!canRecord("corrected", reason) && (
        <p className="review-hint">
          A correction has to say what it corrects: give a reason first.
        </p>
      )}

      {subject.reviewed && (
        // What is on the record, as against what is in the box above it. A reviewer coming
        // back to a claim needs to see that somebody already ruled, and when.
        <p className="review-decided">
          {subject.status} on {subject.decided_at?.slice(0, 10)}
          {/* Named only when it is not the reading on screen. A decision taken while
              looking at a different snapshot is worth knowing about; one taken here is
              already obvious. */}
          {subject.decided_in && subject.decided_in !== viewing ? (
            <>
              {" · decided in "}
              <code>{subject.decided_in}</code>
            </>
          ) : null}
        </p>
      )}
    </section>
  );
}

/**
 * Putting right what this reading got wrong.
 *
 * The browser half of **5.2**. Three things are on screen at once and they are three
 * different facts: what a person has already corrected, where this reading argued with one of
 * those corrections, and a box for making a new one.
 *
 * The snapshot itself does not change and the panel says so out loud. A correction is
 * recorded against the reading it was made on and written into the graph by the next
 * analysis, because snapshots are immutable — so somebody who expected the node to rename
 * itself needs telling, in the place they would look.
 */
function CorrectionControl({
  selection,
  document_,
  payload,
  busy,
  onRecord,
}: {
  selection: Selection;
  document_: SnapshotDocument | null;
  payload: CorrectionsPayload | null;
  busy: boolean;
  onRecord: (field: string, value: string, note: string) => void;
}) {
  const fields = fieldsFor(payload, selection.kind);
  const [field, setField] = useState(fields[0] ?? "");
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const [open, setOpen] = useState(false);

  const standing = correctionsFor(payload, selection);
  const pending = standing.filter((entry) => !appliedIn(document_, selection, entry));
  const conflicts = conflictsFor(payload, selection);
  const kinds = characterKinds(payload);

  // The box starts at what the reading says, so a correction is an edit rather than a
  // retyping — and so that what is being replaced is visible while replacing it.
  const start = (next: string) => {
    setField(next);
    setValue(currentText(document_, selection, next));
  };

  const begin = () => {
    setOpen(true);
    start(fields[0] ?? "");
  };

  if (fields.length === 0) return null;

  return (
    <section className="correction" aria-label="Corrections">
      {standing.length > 0 && (
        <>
          <h3 className="field-label">Corrected</h3>
          <ul className="corrections">
            {standing.map((entry) => (
              <li key={entry.field}>
                <code>{entry.field}</code> {describeValue(entry.was) || "nothing"} →{" "}
                <strong>{describeValue(entry.value) || "nothing"}</strong>
                {entry.note && <span className="correction-note">{entry.note}</span>}
              </li>
            ))}
          </ul>
          {/* Only where it is true. A correction is written in when a snapshot is built, so
              the reading it was made against never carries it and every one since does —
              and telling somebody their correction is pending while showing them the graph
              that already has it is the confusion this line exists to avoid. */}
          {pending.length > 0 && (
            <p className="correction-pending">
              {pending.length === standing.length ? "Applied" : "The rest applied"} when this work
              is next analysed. This snapshot is unchanged.
            </p>
          )}
        </>
      )}

      {conflicts.length > 0 && (
        <>
          <h3 className="field-label">This reading disagreed</h3>
          <ul className="conflicts">
            {conflicts.map((entry) => (
              <li key={`${entry.field} ${entry.noticed_at}`}>
                <code>{entry.field}</code> — it proposed{" "}
                {describeValue(entry.proposed) || "nothing"}; your{" "}
                {describeValue(entry.held) || "nothing"} stood.
              </li>
            ))}
          </ul>
        </>
      )}

      {open ? (
        <div className="correction-form">
          <label>
            Field
            <select value={field} onChange={(event) => start(event.target.value)}>
              {fields.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>

          <label>
            Value
            {field === "kind" ? (
              <select value={value} onChange={(event) => setValue(event.target.value)}>
                {kinds.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            ) : field === "directed" ? (
              <select value={value} onChange={(event) => setValue(event.target.value)}>
                <option value="false">false</option>
                <option value="true">true</option>
              </select>
            ) : (
              <input
                type="text"
                value={value}
                placeholder={
                  field === "aliases" || field === "types" ? "comma separated" : undefined
                }
                onChange={(event) => setValue(event.target.value)}
              />
            )}
          </label>

          <label>
            Why
            <input
              type="text"
              value={note}
              placeholder="optional"
              onChange={(event) => setNote(event.target.value)}
            />
          </label>

          <div className="correction-buttons">
            <button
              type="button"
              disabled={busy || !canSubmit(payload, field, value)}
              onClick={() => {
                onRecord(field, value, note);
                setOpen(false);
                setNote("");
              }}
            >
              Record correction
            </button>
            <button type="button" className="quiet" onClick={() => setOpen(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button type="button" className="correction-open" onClick={begin}>
          Correct this…
        </button>
      )}
    </section>
  );
}

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
  selection,
  document_,
  review,
  reviewing,
  reviewBusy,
  onReview,
  corrections,
  correctionBusy,
  onCorrect,
  onClear,
  onOpen,
  openAt,
}: {
  detail: Detail;
  /** What is selected, which the correction form needs and the fields alone cannot say. */
  selection: Selection | null;
  document_: SnapshotDocument | null;
  review: ReviewSubject | null;
  /** The snapshot on screen, which review decisions are shown against. */
  reviewing: string | null;
  reviewBusy: boolean;
  onReview: (status: ReviewStatus, note: string) => void;
  corrections: CorrectionsPayload | null;
  correctionBusy: boolean;
  onCorrect: (field: string, value: string, note: string) => void;
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

      {review && (
        // Keyed on the subject so switching selection starts with a fresh reason box rather
        // than carrying one claim's note across to the next.
        <ReviewControl
          key={`${review.kind} ${review.id}`}
          subject={review}
          viewing={reviewing}
          busy={reviewBusy}
          onRecord={onReview}
        />
      )}

      {selection && (
        <CorrectionControl
          // Keyed on the subject, so switching selection starts a fresh form rather than
          // carrying one claim's half-typed value across to the next.
          key={`${selection.kind} ${selection.id}`}
          selection={selection}
          document_={document_}
          payload={corrections}
          busy={correctionBusy}
          onRecord={onCorrect}
        />
      )}

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
  comparedWith,
  onSelect,
  onCompare,
}: {
  lineage: Lineage;
  selected: string | null;
  comparedWith: string | null;
  onSelect: (snapshotId: string) => void;
  onCompare: (snapshotId: string | null) => void;
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
                          className={
                            snapshot.id === selected
                              ? "cell chosen"
                              : snapshot.id === comparedWith
                                ? "cell compared"
                                : "cell"
                          }
                          aria-pressed={snapshot.id === selected}
                          title={
                            snapshot.id === selected
                              ? snapshot.id
                              : `${snapshot.id} — shift-click to compare with the selected snapshot`
                          }
                          // Shift-click compares rather than selects. A diff is a second
                          // choice about a graph already on screen, not a way of opening one.
                          onClick={(event) => {
                            if (event.shiftKey && snapshot.id !== selected) {
                              onCompare(snapshot.id === comparedWith ? null : snapshot.id);
                            } else {
                              onCompare(null);
                              onSelect(snapshot.id);
                            }
                          }}
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

/**
 * The diff as a list somebody can read down and check.
 *
 * Sits beside the overlay rather than instead of it: a graph cannot say "25 to 4", and a
 * list cannot show that both edges that moved meet at the same character.
 */
function DiffPanel({
  diff,
  entries,
  onClose,
}: {
  diff: DiffResponse;
  entries: ChangeEntry[];
  onClose: () => void;
}) {
  return (
    <section className="detail">
      <div className="detail-head">
        <h2>What changed</h2>
        <button type="button" className="clear" onClick={onClose} aria-label="Stop comparing">
          ×
        </button>
      </div>

      {/* Said for every attribution, not only the bad ones: this is the sentence that
          decides what the rest of the panel is worth. */}
      <p className={diff.attribution === "both" ? "caveat" : "tally"}>
        {describeAttribution(diff)}
      </p>

      {diff.warnings.map((warning) => (
        <p className="caveat" key={warning}>
          {warning}
        </p>
      ))}

      {entries.length === 0 ? (
        <p className="tally">No characters and no relations differ.</p>
      ) : (
        <ul className="changes">
          {entries.map((entry, index) => (
            <li key={index} className={entry.kind}>
              <span className="change-kind">{entry.kind}</span>
              <span className="change-subject">{entry.subject}</span>
              {entry.detail && <span className="change-detail">{entry.detail}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * What the corpus declares, against what it enacts.
 *
 * Two findings, and the panel is arranged so the first is unmissable: a relationship the
 * reference material gives a section to and the narrative never shows is the thing an author
 * opened this view to find. Agreements are listed last and drawn faint, because they are the
 * case needing no attention.
 *
 * No weights appear anywhere here. The two classes count different things, and a column of
 * numbers side by side would invite exactly the comparison `require_comparable` refuses.
 */
function DeclaredPanel({
  comparison,
  entries,
  onClose,
}: {
  comparison: Comparison;
  entries: FindingEntry[];
  onClose: () => void;
}) {
  return (
    <section className="detail">
      <div className="detail-head">
        <h2>Declared and enacted</h2>
        <button type="button" className="clear" onClick={onClose} aria-label="Stop comparing">
          ×
        </button>
      </div>

      <p className="tally">{describeComparison(comparison)}</p>

      {comparison.other.length > 0 && (
        // Neither declared nor enacted. Said out loud so the totals here agree with the
        // totals everywhere else, rather than quietly excluding a third of the graph.
        <p className="note">
          {comparison.other.length} relation(s) were entered by hand and are neither declared nor
          enacted, so they are not compared here.
        </p>
      )}

      <ul className="changes">
        {entries.map((entry, index) => (
          <li key={index} className={entry.agreement}>
            <span className="change-kind">
              {entry.agreement === "declared-only"
                ? "declared only"
                : entry.agreement === "enacted-only"
                  ? "enacted only"
                  : "agreed"}
            </span>
            <span className="change-subject">{entry.subject}</span>
            {entry.detail && <span className="change-detail">{entry.detail}</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Creating a project: choose a source, say what each document is, and ingest.
 *
 * The whole of 4.9's browser half. It calls no model — proposing reads the folder, and
 * analysing stays a separate act — so opening this costs nothing and can be abandoned
 * freely.
 *
 * The one screen that matters is the document list: a role for each, and, where a critical
 * preface is bound into the same file, the line the narrative begins at. That line is what
 * lets the preface be dropped before a token is spent on it, which is the finding D31
 * measured and the reason this flow exists at all.
 */
function CreateProject({ onCreated }: { onCreated: () => void }) {
  const [source, setSource] = useState("");
  const [title, setTitle] = useState("");
  const [collectives, setCollectives] = useState(false);
  const [structure, setStructure] = useState<ProposedStructure | null>(null);
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const look = async () => {
    setProblem(null);
    setDone(null);
    setBusy(true);
    try {
      const response = await fetch(`/api/structure/propose?source=${encodeURIComponent(source)}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail ?? "could not read that path");
      setStructure(payload);
      setChoices(initialChoices(payload));
    } catch (reason) {
      setStructure(null);
      setProblem(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const create = async () => {
    if (!structure) return;
    setProblem(null);
    setBusy(true);
    try {
      // The store first: everything after it writes into a project that must exist.
      const created = await fetch("/api/store", { method: "POST" });
      if (!created.ok) throw new Error("could not create the project file");

      await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ collectives_are_actors: collectives }),
      });

      // The confirmed map before the ingest, because the ingest is what acts on it: a role
      // decides how each document is read, and an excluded region is dropped as it is stored.
      const saved = await fetch("/api/structure", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ root: structure.root, plans: plansFor(structure, choices) }),
      });
      if (!saved.ok) throw new Error("could not save the structure map");

      const ingested = await fetch("/api/ingest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: source, work_title: title || null }),
      });
      const result = await ingested.json();
      if (!ingested.ok) throw new Error(result.detail ?? "could not ingest");

      setDone(result.summary);
      onCreated();
    } catch (reason) {
      setProblem(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const setChoice = (path: string, patch: Partial<Choice>) =>
    setChoices((current) => ({ ...current, [path]: { ...current[path], ...patch } }));

  return (
    <section className="detail">
      <div className="detail-head">
        <h2>New project</h2>
      </div>

      <label htmlFor="source">A file, a folder, or a folder tree</label>
      <input
        id="source"
        type="text"
        value={source}
        placeholder="/path/to/novel.txt"
        onChange={(event) => setSource(event.target.value)}
      />
      <button type="button" className="clear" disabled={!source || busy} onClick={look}>
        {busy ? "Reading..." : "Read it"}
      </button>

      {problem && <p className="error">{problem}</p>}
      {done && <p className="tally">{done}</p>}

      {structure && (
        <>
          <label htmlFor="work-title">Work title</label>
          <input
            id="work-title"
            type="text"
            value={title}
            placeholder="taken from the filename"
            onChange={(event) => setTitle(event.target.value)}
          />

          <label>
            <input
              type="checkbox"
              checked={collectives}
              onChange={(event) => setCollectives(event.target.checked)}
            />{" "}
            Count collectives as actors
          </label>
          {/* Said here because it is a term the whole study is conducted under, not a view
              option: snapshots either side of a change answer different questions. */}
          <p className="note">
            A faction reported beside its own members counts their contacts twice. Turn this on only
            for corpora where a group really acts.
          </p>

          <h3>What is in it</h3>
          <ul className="changes">
            {structure.documents.map((document) => (
              <li key={document.path}>
                <span className="change-subject">{document.path}</span>
                <span className="change-detail">
                  {(["narrative", "reference"] as Role[]).map((role) => (
                    <button
                      key={role}
                      type="button"
                      className={choices[document.path]?.role === role ? "cell compared" : "clear"}
                      onClick={() => setChoice(document.path, { role })}
                    >
                      {role}
                    </button>
                  ))}
                  <input
                    type="text"
                    value={choices[document.path]?.excludeBefore ?? ""}
                    placeholder="drop everything before this line (optional)"
                    onChange={(event) =>
                      setChoice(document.path, { excludeBefore: event.target.value })
                    }
                  />
                </span>
              </li>
            ))}
          </ul>

          {structure.skipped.length > 0 && (
            <p className="note">{structure.skipped.length} file(s) skipped as not text.</p>
          )}

          <p className="tally">{describePlan(structure, choices)}</p>
          <button
            type="button"
            className="pin"
            disabled={!isReady(structure, choices) || busy}
            onClick={create}
          >
            {busy ? "Creating..." : "Create the project"}
          </button>
          <p className="note">
            Creating reads the text into the project and records the settings. It calls no model;
            analysing is a separate step.
          </p>
        </>
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
  scaling,
  measuredAgainst,
  bases,
  onChoose,
  onPin,
  onUnpin,
  onScaling,
}: {
  algorithm: LayoutName;
  pinned: PinnedLayout | null;
  scaling: Scaling;
  measuredAgainst: number;
  bases: Record<string, number>;
  onChoose: (name: LayoutName) => void;
  onPin: () => void;
  onUnpin: () => void;
  onScaling: (scaling: Scaling) => void;
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

      <h4 className="field-label">Thickness</h4>
      <ul className="chips choices">
        {(["absolute", "relative"] as Scaling[]).map((choice) => (
          <li key={choice}>
            <button
              type="button"
              className={scaling === choice ? "chosen" : ""}
              aria-pressed={scaling === choice}
              onClick={() => onScaling(choice)}
            >
              {choice}
            </button>
          </li>
        ))}
      </ul>
      {/* Named per basis when there is more than one, because since 4.3 that is the ordinary
          state of a corpus with reference material, and a single number would be untrue of
          every edge not on the basis it came from. */}
      <p className="tally">
        {Object.keys(bases).length > 1
          ? `Measured within each basis, since they count different things: ${Object.entries(bases)
              .map(([basis, most]) => `${basis} against ${most}`)
              .join(", ")}.`
          : scaling === "absolute"
            ? `Measured against the heaviest relation in the snapshot (${measuredAgainst}), so narrowing the graph does not thicken what it leaves.`
            : `Measured against the heaviest relation on screen (${measuredAgainst}), so this view uses its full range — and changes when the view does.`}
      </p>

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
  const [scaling, setScaling] = useState<Scaling>(DEFAULT_SCALING);
  const [pin, setPin] = useState<PinnedLayout | null>(null);
  const [lineage, setLineage] = useState<Lineage | null>(null);
  const [comparedWith, setComparedWith] = useState<string | null>(null);
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  // Both documents, in diff order. Keeping the pair rather than 'the other one' means
  // the union is always built the right way round, whichever cell was shift-clicked.
  const [pair, setPair] = useState<{ before: SnapshotDocument; after: SnapshotDocument } | null>(
    null,
  );
  const [declared, setDeclared] = useState(false);
  const [creating, setCreating] = useState(false);
  const [reviews, setReviews] = useState<ReviewOverlay | null>(null);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [corrections, setCorrections] = useState<CorrectionsPayload | null>(null);
  const [correctionBusy, setCorrectionBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Held in a ref as well as in state: the pin button and the drag handler need the live
  // instance, and they are not part of what makes the graph rebuild.
  const graph = useRef<cytoscape.Core | null>(null);

  /**
   * What the project holds, tolerant of it holding nothing yet.
   *
   * `/api/snapshots` answers 404 before the project file exists, and that is a normal state
   * now that a project can be created from here (4.9). Reading the body as an array
   * regardless crashed the whole client to a blank page on a fresh install — the exact case
   * this flow is for.
   */
  const loadSnapshots = ({ openCreationWhenEmpty = false } = {}) =>
    fetch("/api/snapshots")
      .then(async (response) => (response.ok ? await response.json() : []))
      .then((found: unknown) => {
        const summaries = Array.isArray(found) ? (found as SnapshotSummary[]) : [];
        setSnapshots(summaries);
        if (summaries.length > 0) setSelected(summaries[0].id);
        else if (openCreationWhenEmpty) setCreating(true);
      })
      .catch(() => setError("could not reach the Dramatis server"));

  useEffect(() => {
    loadSnapshots({ openCreationWhenEmpty: true });
    // Run once, on mount. `loadSnapshots` is stable enough for this purpose and pulling it
    // into the dependency list would re-fetch on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

    // A second request, and deliberately: the snapshot endpoint serves the archived
    // document unchanged, and a decision taken since is not part of it (5.1). A project
    // nobody has reviewed answers with every subject proposed, so there is no empty case.
    setReviews(null);
    fetch(reviewsUrl(selected))
      .then((response) => (response.ok ? response.json() : null))
      .then(setReviews)
      .catch(() => setReviews(null));

    // And the corrections, which are the work's rather than this snapshot's — except the
    // conflicts, which are exactly this snapshot's (5.2).
    setCorrections(null);
    fetch(correctionsUrl(selected))
      .then((response) => (response.ok ? response.json() : null))
      .then(setCorrections)
      .catch(() => setCorrections(null));
  }, [selected]);

  useEffect(() => {
    if (!selected || !comparedWith) {
      setDiff(null);
      setPair(null);
      return;
    }
    // The earlier snapshot is the one to compare *from*, so the diff reads forwards
    // regardless of which cell was shift-clicked.
    const order = lineage?.snapshots.map((snapshot) => snapshot.id) ?? [];
    const [before, after] =
      order.indexOf(comparedWith) < order.indexOf(selected)
        ? [comparedWith, selected]
        : [selected, comparedWith];

    Promise.all([
      fetch(`/api/diff?before=${encodeURIComponent(before)}&after=${encodeURIComponent(after)}`),
      fetch(`/api/snapshots/${encodeURIComponent(before)}`),
      fetch(`/api/snapshots/${encodeURIComponent(after)}`),
    ])
      .then(async ([diffed, first, second]) => {
        if (!diffed.ok) throw new Error((await diffed.json()).detail ?? "could not diff");
        setDiff(await diffed.json());
        setPair({ before: await first.json(), after: await second.json() });
      })
      .catch((reason: Error) => {
        setError(reason.message);
        setComparedWith(null);
      });
  }, [selected, comparedWith, lineage]);

  // The overlay is drawn over everything either snapshot had, so what was removed is still
  // on screen. Without a comparison this is just the snapshot.
  const shown = pair ? unionDocument(pair.before, pair.after) : document_;
  const built = shown ? buildGraph(shown, filters, scaling) : null;
  const options = document_ ? optionsFor(document_) : null;
  // Derived rather than state: it is a reading of the loaded snapshot, and a stale copy of
  // it would mark the graph for a snapshot the reader has already navigated away from.
  const comparison = document_ ? compareProvenance(document_) : null;

  useEffect(() => {
    if (!container.current || !document_) return;

    const { elements, weightBasis } = buildGraph(shown ?? document_, filters, scaling);

    // The overlay: each element carries the change that moved it, so the stylesheet can
    // mark it. Done here rather than inside buildGraph because a diff is a second reading
    // of a graph, not a property of one.
    if (diff) {
      const index = changeIndex(diff);
      for (const element of elements) {
        const data = element.data;
        const change =
          "source" in data
            ? classFor(index.relations.get(pairKey(String(data.source), String(data.target))))
            : (index.characters.get(String(data.id)) ?? null);
        if (change) element.classes = `${element.classes ?? ""} ${change}`.trim();
      }
    } else if (declared) {
      // Only when no diff is drawn. A comparison between snapshots and a comparison between
      // provenances are two different questions, and one picture carrying both palettes at
      // once would answer neither (4.4).
      applyMarks(elements, compareProvenance(document_));
    }
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

    // A snapshot mixing weight bases used to raise an error here. Since 4.3 it is the
    // ordinary state of any corpus with reference material — statements and passages of
    // contact are counted differently — and widths are measured within each basis, so an
    // edge is comparable with the edges it can honestly be compared with. `FilterControls`
    // already withholds the weight filter and names the basis, which is where a reader
    // should learn this; a red banner for the expected case teaches them to ignore banners.
    void weightBasis;
    return () => {
      instance.destroy();
      graph.current = null;
    };
    // `pin` is deliberately absent: pinning captures where the graph already is, so
    // rebuilding on it would throw away the arrangement being captured.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    // `diff` and `pair` are here because the overlay is drawn into the elements: without
    // them the marks arrive after the graph is built and are never applied. They are state
    // rather than derived values, so their identity is stable between renders — `shown` is
    // rebuilt every render and would loop.
  }, [document_, filters, algorithm, selected, diff, pair, scaling, declared]);

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

  function recordReview(status: ReviewStatus, note: string) {
    if (!selected || !selection) return;

    setReviewBusy(true);
    fetch(reviewsUrl(selected), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(decisionBody(selection, status, note)),
    })
      .then(async (response) => {
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          throw new Error(body.detail ?? "the server would not record that decision");
        }
        return response.json() as Promise<ReviewSubject>;
      })
      // The subject as the server now holds it, folded in rather than guessed at: the note
      // it kept and the time it stamped are its answers, not the browser's.
      .then((subject) => setReviews((current) => withSubject(current, subject)))
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setReviewBusy(false));
  }

  function recordCorrection(field: string, value: string, note: string) {
    if (!selected || !selection) return;

    setCorrectionBusy(true);
    fetch(correctionsUrl(selected), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(correctionBody(selection, field, value, note)),
    })
      .then(async (response) => {
        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          // The server's message names the rule that refused it — that a weight is a count
          // rather than an opinion, say — and that is the useful half of the answer.
          throw new Error(body.detail ?? "the server would not record that correction");
        }
        return response.json() as Promise<CorrectionEntry>;
      })
      .then((entry) => {
        setCorrections((current) => withCorrection(current, entry));
        // A correction is also a review decision, so the status beside it has moved.
        return fetch(reviewsUrl(selected))
          .then((response) => (response.ok ? response.json() : null))
          .then(setReviews);
      })
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setCorrectionBusy(false));
  }

  const reviewIndex = indexReviews(reviews);
  const reviewOfSelection = statusFor(reviewIndex, document_, selection);

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

        {/* Offered always, and opened by default when the project holds nothing: a fresh
            install lands on an empty graph, and the first thing to do there is make one. */}
        <button type="button" className="clear" onClick={() => setCreating(!creating)}>
          {creating ? "Close" : "New project"}
        </button>
        {creating && (
          <CreateProject
            onCreated={() => {
              // Re-read what the project holds rather than guessing: the ingest may have
              // added a revision to a work already there.
              loadSnapshots();
            }}
          />
        )}

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

        {lineage && (
          <LineagePanel
            lineage={lineage}
            selected={selected}
            comparedWith={comparedWith}
            onSelect={setSelected}
            onCompare={setComparedWith}
          />
        )}

        {document_ && (
          <LayoutControls
            algorithm={algorithm}
            pinned={pin}
            scaling={scaling}
            measuredAgainst={built?.maxWeight ?? 0}
            bases={built?.maxWeightByBasis ?? {}}
            onChoose={chooseLayout}
            onPin={pinLayout}
            onUnpin={unpinLayout}
            onScaling={setScaling}
          />
        )}

        {comparison && (comparison.available || declared) && (
          <button
            type="button"
            className="clear"
            aria-pressed={declared}
            onClick={() => setDeclared(!declared)}
          >
            {declared ? "Stop comparing provenance" : "Compare declared with enacted"}
          </button>
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

        {diff && shown ? (
          <DiffPanel
            diff={diff}
            entries={changeList(shown, diff)}
            onClose={() => setComparedWith(null)}
          />
        ) : declared && comparison && document_ ? (
          <DeclaredPanel
            comparison={comparison}
            entries={findingList(document_, comparison)}
            onClose={() => setDeclared(false)}
          />
        ) : detail ? (
          <DetailPanel
            detail={detail}
            selection={selection}
            document_={document_}
            review={reviewOfSelection}
            reviewing={selected}
            reviewBusy={reviewBusy}
            onReview={recordReview}
            corrections={corrections}
            correctionBusy={correctionBusy}
            onCorrect={recordCorrection}
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
            {reviews && (
              <>
                <dt>Review</dt>
                {/* Only the statuses actually reached. A row of three zeroes on a project
                    nobody has reviewed is noise; "everything proposed" is the whole fact. */}
                <dd>
                  {tally(reviews)
                    .filter((entry) => entry.count > 0)
                    .map((entry) => `${entry.count} ${entry.status}`)
                    .join(", ")}
                </dd>
              </>
            )}
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
