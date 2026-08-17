/**
 * A work's snapshots, arranged so the two time axes stay apart.
 *
 * Invariant 4: a snapshot binds a *text revision* to an *analysis run*, and the reader must
 * always be able to tell whether a graph changed because the work changed or because the
 * analysis did. A list of snapshots down the side of the screen cannot answer that. It can
 * say the graph moved; it cannot say which of the two things moved it, and the two are not
 * comparable kinds of change — one is the novel being rewritten, the other is the tool
 * being asked a better question.
 *
 * So the snapshots are laid out on a grid: a row per text revision, a column per analysis
 * run. Reading across a row holds the text still and varies the analysis. Reading down a
 * column holds the analysis still and varies the text. That is the distinction the
 * invariant demands, drawn rather than described.
 *
 * **Empty cells are part of the point.** Not every revision has been analysed by every run,
 * and a gap says so — it is the difference between "this pairing produced nothing
 * different" and "this pairing was never tried", which a list of what exists cannot express
 * at all.
 */

export interface LineageRevision {
  id: string;
  label: string | null;
  created_at: string;
  sha256: string;
  documents: number;
}

export interface LineageRun {
  id: string;
  model: string;
  provider: string | null;
  prompt_version: string;
  started_at: string | null;
  /**
   * What makes this run the same *reading* as another, rather than the same execution.
   *
   * A run identifier includes when it ran, so no two are ever equal. Comparing by it would
   * report every pair of snapshots as differing on both axes, which is the one answer
   * Invariant 4 exists to prevent. Columns are configurations for that reason.
   */
  configuration: string;
}

export interface LineageSnapshot {
  id: string;
  label: string | null;
  created_at: string;
  text_revision_id: string;
  analysis_run_id: string;
  characters: number;
  relations: number;
}

export interface Lineage {
  work: { id: string; title: string; creator: string | null; collection_id: string };
  text_revisions: LineageRevision[];
  analysis_runs: LineageRun[];
  snapshots: LineageSnapshot[];
}

/**
 * A column: one way of reading the work, and every run that read it that way.
 *
 * Not one run. Two executions of the same settings are two runs by design — models are not
 * deterministic, so collapsing them would make a snapshot's identity ambiguous — but they
 * are one *reading*, and the column is what lets a reader hold the analysis still and see
 * what the text did.
 */
export interface Reading {
  configuration: string;
  runs: LineageRun[];
}

/** One intersection of a revision and a reading. */
export interface Cell {
  revision: LineageRevision;
  reading: Reading;
  /** Usually one, occasionally none. A list because the grid does not get to assume. */
  snapshots: LineageSnapshot[];
}

export interface Grid {
  revisions: LineageRevision[];
  readings: Reading[];
  rows: Cell[][];
  /** Snapshots naming a revision or a run the work does not list. */
  orphaned: LineageSnapshot[];
}

/**
 * Arrange a work's snapshots on the grid its two axes describe.
 *
 * A snapshot whose revision or run is not among the work's own is kept aside rather than
 * dropped. It should not happen — both are recorded when the snapshot is written — and if
 * it does, a graph silently missing from the list is worse than one that has to be
 * explained.
 */
export function buildGrid(lineage: Lineage): Grid {
  const revisions = lineage.text_revisions;
  const known = new Set(revisions.map((revision) => revision.id));
  const byRun = new Map(lineage.analysis_runs.map((run) => [run.id, run]));

  // Columns in the order their first run appears, so the axis reads as the analysis
  // developed rather than by whatever a digest happens to sort as.
  const readings: Reading[] = [];
  const columnOf = new Map<string, Reading>();
  for (const run of lineage.analysis_runs) {
    let reading = columnOf.get(run.configuration);
    if (!reading) {
      reading = { configuration: run.configuration, runs: [] };
      readings.push(reading);
      columnOf.set(run.configuration, reading);
    }
    reading.runs.push(run);
  }

  const placed = new Map<string, LineageSnapshot[]>();
  const orphaned: LineageSnapshot[] = [];

  for (const snapshot of lineage.snapshots) {
    const run = byRun.get(snapshot.analysis_run_id);
    if (!known.has(snapshot.text_revision_id) || !run) {
      orphaned.push(snapshot);
      continue;
    }
    const key = `${snapshot.text_revision_id} ${run.configuration}`;
    placed.set(key, [...(placed.get(key) ?? []), snapshot]);
  }

  const rows = revisions.map((revision) =>
    readings.map((reading) => ({
      revision,
      reading,
      snapshots: placed.get(`${revision.id} ${reading.configuration}`) ?? [],
    })),
  );

  return { revisions, readings, rows, orphaned };
}

/**
 * How to describe what separates two snapshots.
 *
 * The whole reason the axes are kept apart, reduced to the sentence a reader wants: same
 * text and a different analysis, same analysis and a different text, or both at once —
 * which is the case where nothing can be attributed to either, and saying so is the honest
 * answer rather than picking whichever changed more.
 */
export type Difference = "same" | "analysis" | "text" | "both";

export function differenceBetween(a: LineageSnapshot, b: LineageSnapshot): Difference {
  const text = a.text_revision_id !== b.text_revision_id;
  const analysis = a.analysis_run_id !== b.analysis_run_id;

  if (text && analysis) return "both";
  if (text) return "text";
  if (analysis) return "analysis";
  return "same";
}

export function describeDifference(difference: Difference): string {
  switch (difference) {
    case "text":
      return "Same analysis, different text — the work changed.";
    case "analysis":
      return "Same text, different analysis — the reading changed.";
    case "both":
      return "Both the text and the analysis differ, so neither can be credited with the change.";
    case "same":
      return "The same text revision and the same analysis run.";
  }
}

/** A revision as a reader would name it: its label, or a short form of its id. */
export function revisionName(revision: LineageRevision): string {
  return revision.label?.trim() || revision.id;
}

/**
 * A run as a reader would name it.
 *
 * The model and the prompt version, because those are the two things that make one reading
 * differ from another, and a run identifier alone says nothing about why its graph is not
 * the graph next to it.
 */
export function runName(run: LineageRun): string {
  return `${run.model} · ${run.prompt_version}`;
}

/**
 * A label per reading, distinct even when the readable part of two is identical.
 *
 * Two readings can differ only in something `runName` does not show — an effort level, a
 * window size, whether the resolution prompt was reached at all. Left alone they render as
 * two columns with the same caption, which reads as a duplicate rather than as a
 * distinction, and a reader comparing them has no way to tell which is which.
 *
 * Where that happens the configuration digest is appended. It is not informative about
 * *what* differs — saying that needs the parameters, which is 3.6's business — but it is
 * honest that the two are not the same, which a repeated caption is not.
 */
export function readingLabels(readings: Reading[]): Map<string, string> {
  const plain = new Map<string, number>();
  for (const reading of readings) {
    const name = runName(reading.runs[0]);
    plain.set(name, (plain.get(name) ?? 0) + 1);
  }

  const labels = new Map<string, string>();
  for (const reading of readings) {
    const name = runName(reading.runs[0]);
    labels.set(
      reading.configuration,
      (plain.get(name) ?? 0) > 1 ? `${name} (${reading.configuration.slice(0, 6)})` : name,
    );
  }
  return labels;
}

/** Whether this work has enough history for the grid to be telling the reader anything. */
export function hasHistory(grid: Grid): boolean {
  return grid.revisions.length > 1 || grid.readings.length > 1;
}
