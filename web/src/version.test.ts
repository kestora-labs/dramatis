/** Proves the TypeScript toolchain is wired up: the module resolves and is typed. */
import { describe, expect, it } from "vitest";

import { VERSION } from "./version.js";

describe("web scaffold", () => {
  it("exports a version string", () => {
    expect(typeof VERSION).toBe("string");
    expect(VERSION.length).toBeGreaterThan(0);
  });

  it("is pre-alpha", () => {
    expect(VERSION.startsWith("0.1.0")).toBe(true);
  });
});
