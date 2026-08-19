/**
 * Correcting what a reading got wrong, in the browser.
 *
 * The browser half of **5.2**. **5.1** put a status on a claim; this replaces the claim's
 * content, and the replacement is written into every snapshot built afterwards. The logic
 * lives here rather than in the component for the reason **4.4** learned the hard way — a
 * rule that cannot be tested is a rule that ships broken and looks green.
 *
 * Three things shape it.
 *
 * **The snapshot on screen does not change, and the panel says so.** Snapshots are immutable,
 * so a correction is recorded beside the reading it was made on and applied when the next one
 * is built. Showing the corrected value in place of the archived one would mean the client
 * displaying something no stored document says; showing it *beside* the archived one, marked
 * as pending, is the honest version and the one a reader can act on.
 *
 * **What may be corrected comes from the server.** The field list and the character kinds are
 * served with the corrections rather than written down again here. A fourth copy of a
 * vocabulary is a fourth place for it to drift, and the drift would show up as a form
 * offering a field the API refuses.
 *
 * **A value keeps its type.** A shell and a text box both have only strings, but `types` is a
 * list and `valence` is a number. Parsing happens here, once, so the request body carries the
 * type the schema expects rather than a string the server would have to guess at.
 */

import type { Selection, SelectionKind } from "./detail.js";
import type { SnapshotCharacter, SnapshotDocument, SnapshotRelation } from "./graph.js";

/** One field a person has replaced, as the server holds it. */
export interface CorrectionEntry {
  kind: SelectionKind;
  id: string;
  field: string;
  value: unknown;
  /** What the reading said when the correction was made. */
  was: unknown;
  note: string | null;
  corrected_at: string;
  /** The snapshot the correction was made against. */
  corrected_in: string;
}

/** A reading proposing something other than what a correction replaced. */
export interface ConflictEntry {
  kind: SelectionKind;
  id: string;
  field: string;
  proposed: unknown;
  held: unknown;
  noticed_at: string;
  noticed_in: string;
}

export interface CorrectionsPayload {
  snapshot_id: string;
  work_id: string;
  corrections: CorrectionEntry[];
  conflicts: ConflictEntry[];
  correctable: Record<string, string[]>;
  character_kinds: string[];
}

export function correctionsUrl(snapshotId: string): string {
  return `/api/snapshots/${encodeURIComponent(snapshotId)}/corrections`;
}

/** Which fields the server will accept for this kind of subject. */
export function fieldsFor(payload: CorrectionsPayload | null, kind: SelectionKind): string[] {
  return payload?.correctable?.[kind] ?? [];
}

export function characterKinds(payload: CorrectionsPayload | null): string[] {
  return payload?.character_kinds ?? [];
}

/** Every correction standing against the selected subject, in the order the server gave. */
export function correctionsFor(
  payload: CorrectionsPayload | null,
  selection: Selection | null,
): CorrectionEntry[] {
  if (!selection) return [];
  return (payload?.corrections ?? []).filter(
    (entry) => entry.kind === selection.kind && entry.id === selection.id,
  );
}

/** Disagreements this reading raised with a correction to the selected subject. */
export function conflictsFor(
  payload: CorrectionsPayload | null,
  selection: Selection | null,
): ConflictEntry[] {
  if (!selection) return [];
  return (payload?.conflicts ?? []).filter(
    (entry) => entry.kind === selection.kind && entry.id === selection.id,
  );
}

/** Fold a freshly recorded correction in, replacing the one for that field if there was one. */
export function withCorrection(
  payload: CorrectionsPayload | null,
  entry: CorrectionEntry,
): CorrectionsPayload | null {
  if (!payload) return payload;

  const others = payload.corrections.filter(
    (candidate) =>
      !(
        candidate.kind === entry.kind &&
        candidate.id === entry.id &&
        candidate.field === entry.field
      ),
  );
  return { ...payload, corrections: [...others, entry] };
}

function subjectOf(
  document: SnapshotDocument | null,
  selection: Selection | null,
): SnapshotCharacter | SnapshotRelation | undefined {
  if (!document || !selection) return undefined;
  return selection.kind === "character"
    ? document.characters.find((candidate) => candidate.id === selection.id)
    : document.relations.find((candidate) => candidate.id === selection.id);
}

/**
 * What the snapshot currently says for a field, as text a form can hold.
 *
 * An absent field comes back as the empty string rather than "undefined": the run said
 * nothing, and a box that starts empty is the honest way to show that.
 */
export function currentText(
  document: SnapshotDocument | null,
  selection: Selection | null,
  field: string,
): string {
  const subject = subjectOf(document, selection) as Record<string, unknown> | undefined;
  return describe(subject?.[field]);
}

/**
 * Whether two stored values say the same thing.
 *
 * Absence and emptiness are one state, not two: clearing a note removes the key, and a
 * document that no longer carries the field is a document that agrees with the correction
 * that cleared it. Comparing them as different would report such a correction as forever
 * pending.
 */
export function sameValue(left: unknown, right: unknown): boolean {
  if (Array.isArray(left) || Array.isArray(right)) {
    const a = Array.isArray(left) ? left : [];
    const b = Array.isArray(right) ? right : [];
    return a.length === b.length && a.every((item, at) => item === b[at]);
  }
  const empty = (value: unknown) => value === undefined || value === null || value === "";
  if (empty(left) && empty(right)) return true;
  return left === right;
}

/**
 * Whether this reading already carries the correction.
 *
 * A correction is written in when a snapshot is built, so the one it was made against never
 * has it and every snapshot built since does. The panel needs to know which it is looking at,
 * or it tells somebody their correction is still pending while showing them the graph that
 * already has it.
 */
export function appliedIn(
  document: SnapshotDocument | null,
  selection: Selection | null,
  entry: CorrectionEntry,
): boolean {
  const subject = subjectOf(document, selection) as Record<string, unknown> | undefined;
  if (!subject) return false;
  return sameValue(subject[entry.field], entry.value);
}

/** A stored value as a person reads it. Lists are comma-separated, absence is empty. */
export function describe(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

/**
 * Turn what a text box holds into the type the field actually is.
 *
 * A list field splits on commas because that is how a person writes a list in one box.
 * Everything else keeps its own type, so the request body is what the schema expects.
 */
export function parseValue(field: string, raw: string): unknown {
  if (field === "aliases" || field === "types") {
    return raw
      .split(",")
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
  }
  if (field === "valence") return Number(raw.trim());
  if (field === "directed") return raw.trim().toLowerCase() === "true";
  return raw.trim();
}

/**
 * Whether this value may be sent as it stands.
 *
 * The server refuses the same things; checking here is so the button is disabled rather than
 * the click producing an error. Only what can be judged from the value alone is checked —
 * whether the field may be corrected at all is the server's answer, and it gives a reason.
 */
export function canSubmit(payload: CorrectionsPayload | null, field: string, raw: string): boolean {
  const text = raw.trim();
  if (field === "name") return text.length > 0;
  if (field === "kind") return characterKinds(payload).includes(text);
  if (field === "valence") {
    const value = Number(text);
    return text.length > 0 && Number.isFinite(value) && value >= -1 && value <= 1;
  }
  if (field === "directed") return text === "true" || text === "false";
  // aliases, types and notes may all legitimately be emptied: that is how a person removes
  // an alias list or clears a note.
  return true;
}

/** The body of a correction, with the value in its own type. */
export function correctionBody(
  selection: Selection,
  field: string,
  raw: string,
  note: string,
): { kind: SelectionKind; id: string; field: string; value: unknown; note?: string } {
  const trimmed = note.trim();
  return {
    kind: selection.kind,
    id: selection.id,
    field,
    value: parseValue(field, raw),
    ...(trimmed ? { note: trimmed } : {}),
  };
}
