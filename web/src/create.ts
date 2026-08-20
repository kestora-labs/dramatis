/**
 * Creating a project without touching the command line.
 *
 * The browser half of **4.9**: choose a file, a folder or a tree; say what each document is;
 * mark front matter to leave out; and ingest. The logic lives here rather than in the
 * component for the reason **4.4** learned the hard way — a rule that cannot be tested is a
 * rule that ships broken and looks green.
 *
 * **Nothing here calls a model.** The server proposes a structure by reading the folder
 * (`propose_structure`), which evidences what a folder can evidence and refuses to guess a
 * role from a directory name. The person supplies the rest. Asking a model to read the
 * documents is `structure --ask`, and analysing them is `analyse`; both stay separate acts,
 * so creating a project costs nothing.
 *
 * **The plans this builds are the same JSON the store already keeps.** `plansFor` emits what
 * `structure.as_json` writes and what `ingest.kept_text` reads, because a second shape for
 * one thing is a second thing to keep in step. That is why an excluded region here is a
 * region with role `excluded` and a narrative region carrying the boundary quotation, rather
 * than some browser-only spelling of "skip this bit".
 */

/**
 * What a person may say a document is. `excluded` means it is in the folder and is no part
 * of the work — a production spec, a to-do list, a sheet of image prompts — and ingest
 * leaves it out of the revision entirely rather than storing it as reference material
 * nobody wanted read.
 */
export type Role = "narrative" | "reference" | "excluded";

export interface ProposedRegion {
  label: string;
  role: { value: string | null; basis: string; settled: boolean };
  starts_at: number;
  ends_at: number | null;
  begins_with: string;
  ends_with: string;
}

export interface ProposedDocument {
  path: string;
  characters: number;
  role: { value: string | null; basis: string; settled: boolean };
  addressing: { value: string | null; basis: string; settled: boolean };
  revision_of: { value: string | null; basis: string; settled: boolean };
  regions: ProposedRegion[];
}

export interface ProposedStructure {
  root: string;
  documents: ProposedDocument[];
  skipped: { path: string; why: string }[];
  notes: string[];
}

/** What a person has decided about one document. */
export interface Choice {
  role: Role | null;
  /**
   * The verbatim text the narrative begins at, when front matter is to be dropped.
   *
   * Empty means keep the whole document. A quotation rather than an offset because the file
   * may be edited between confirming and ingesting, and a quotation still finds the boundary
   * where a number no longer would — the rule the structure map already follows.
   */
  excludeBefore: string;
}

const CONFIRMED = "confirmed in the browser";

/** Where each document starts: whatever is already settled, and nothing invented. */
export function initialChoices(structure: ProposedStructure): Record<string, Choice> {
  const choices: Record<string, Choice> = {};
  for (const document of structure.documents) {
    const settled = document.role.settled && isRole(document.role.value);
    choices[document.path] = {
      // A confirmed answer is offered back; an unconfirmed proposal is not, because a
      // pre-filled guess is a guess somebody will accept without reading.
      role: settled ? (document.role.value as Role) : null,
      excludeBefore: existingBoundary(document),
    };
  }
  return choices;
}

function isRole(value: string | null): boolean {
  return value === "narrative" || value === "reference" || value === "excluded";
}

/** A boundary already confirmed for this document, so re-opening the flow keeps it. */
function existingBoundary(document: ProposedDocument): string {
  const excluded = document.regions.some((region) => region.role.value === "excluded");
  if (!excluded) return "";
  const narrative = document.regions.find((region) => region.begins_with);
  return narrative?.begins_with ?? "";
}

/** Documents still without a role: the flow cannot finish while any remain. */
export function undecided(structure: ProposedStructure, choices: Record<string, Choice>): string[] {
  return structure.documents
    .filter((document) => !isRole(choices[document.path]?.role ?? null))
    .map((document) => document.path);
}

export function isReady(structure: ProposedStructure, choices: Record<string, Choice>): boolean {
  return structure.documents.length > 0 && undecided(structure, choices).length === 0;
}

/**
 * The structure map to save, in the shape the store and the ingest already speak.
 *
 * An excluded document becomes two regions — the front matter, and the narrative carrying the
 * quotation it begins at — because that is what `kept_text` cuts on. A document with nothing
 * to drop stays one region covering all of it.
 */
export function plansFor(
  structure: ProposedStructure,
  choices: Record<string, Choice>,
): Record<string, unknown> {
  const plans: Record<string, unknown> = {};

  for (const document of structure.documents) {
    const choice = choices[document.path];
    if (!choice || !isRole(choice.role)) continue;
    const role = choice.role as Role;
    const confirmed = (value: string) => ({ value, basis: CONFIRMED, settled: true });
    const boundary = role === "excluded" ? "" : choice.excludeBefore.trim();

    const regions = boundary
      ? [
          {
            label: "before the narrative",
            role: confirmed("excluded"),
            starts_at: 0,
            ends_at: null,
            begins_with: "",
            ends_with: "",
          },
          {
            label: "narrative",
            role: confirmed(role),
            starts_at: 0,
            ends_at: null,
            begins_with: boundary,
            ends_with: "",
          },
        ]
      : [
          {
            label: "whole document",
            role: confirmed(role),
            starts_at: 0,
            ends_at: document.characters,
            begins_with: "",
            ends_with: "",
          },
        ];

    plans[document.path] = {
      path: document.path,
      characters: document.characters,
      role: confirmed(role),
      addressing: document.addressing,
      revision_of: document.revision_of,
      regions,
    };
  }

  return plans;
}

/** How many documents will have front matter dropped, for the sentence before committing. */
export function excludedCount(choices: Record<string, Choice>): number {
  return Object.values(choices).filter((choice) => choice.excludeBefore.trim()).length;
}

/**
 * One sentence saying what creating the project will do.
 *
 * Said before the button rather than after the write, because ingesting is the step that
 * spends nothing but is hard to see the shape of afterwards.
 */
export function describePlan(
  structure: ProposedStructure,
  choices: Record<string, Choice>,
): string {
  const remaining = undecided(structure, choices);
  if (structure.documents.length === 0) return "There is nothing readable here to ingest.";
  if (remaining.length > 0) {
    const subject = remaining.length === 1 ? "document still needs" : "documents still need";
    return `${remaining.length} ${subject} a role: ${remaining.slice(0, 3).join(", ")}${
      remaining.length > 3 ? "..." : ""
    }`;
  }

  const documents = structure.documents.length;
  const reference = structure.documents.filter(
    (document) => choices[document.path]?.role === "reference",
  ).length;
  const dropped = excludedCount(choices);

  const parts = [`${documents} document${documents === 1 ? "" : "s"}`];
  if (reference > 0) parts.push(`${reference} as reference material`);
  if (dropped > 0) parts.push(`front matter dropped from ${dropped}`);
  return `Ready: ${parts.join(", ")}.`;
}
