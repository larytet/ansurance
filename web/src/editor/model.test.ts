/* eslint-disable @typescript-eslint/no-non-null-assertion -- fixtures are known to have these entries; a failed lookup should fail the test loudly */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  type Definition, addCheck, addStage, connect, deleteStage, diff, disconnect, fromGraph, layers, moveCheck, nextStageId,
  push, redo, removeCheck, renameStageId, startHistory, toGraph, topoSort, undo, updateCheck, updateStage,
} from "./model";

const SEED: Definition[] = JSON.parse(readFileSync(resolve(process.cwd(), "../backend/app/processes/seed/processes.json"), "utf8"));
const t = (s: string) => ({ en: s, he: s });
const chk = [{ text: t("x"), kind: "do" as const }];
const base = (): Definition => ({
  id: "t1", owner_role: "agent", name: t("T"),
  stages: [
    { id: "a", name: t("a"), role: "agent", depends_on: [], checks: chk },
    { id: "b", name: t("b"), role: "backoffice", depends_on: ["a"], checks: chk },
    { id: "c", name: t("c"), role: "manager", depends_on: [], checks: chk },
  ],
});

describe("definition and graph", () => {
  it("round-trips all seeded definitions through the graph unchanged", () => {
    expect(SEED.length).toBe(18);
    for (const d of SEED) {
      const g = toGraph(d);
      expect(g.nodes.map((n) => n.id)).toEqual(d.stages.map((s) => s.id));
      const normal = { ...d, stages: d.stages.map((s) => ({ ...s, depends_on: s.depends_on ?? [] })) }; // the server fills the defaults
      expect(fromGraph(d, g.edges)).toEqual(normal);
    }
  });

  it("puts stages without a dependency between them in the same column", () => {
    const col = layers(base().stages);
    expect(col.get("a")).toBe(0);
    expect(col.get("c")).toBe(0); // runs in parallel with a
    expect(col.get("b")).toBe(1);
    const g = toGraph(base());
    expect(g.nodes.find((n) => n.id === "a")!.x).toBe(g.nodes.find((n) => n.id === "c")!.x);
    expect(g.nodes.find((n) => n.id === "a")!.y).not.toBe(g.nodes.find((n) => n.id === "c")!.y);
  });
});

describe("connecting stages", () => {
  it("adds a dependency", () => {
    const r = connect(base(), "c", "b");
    expect(r.def!.stages.find((s) => s.id === "b")!.depends_on).toEqual(["a", "c"]);
  });
  it("refuses a self-loop, a duplicate, a cycle and an unknown stage, with the reason", () => {
    expect(connect(base(), "a", "a").refused).toBe("self_dependency");
    expect(connect(base(), "a", "b").refused).toBe("duplicate_edge");
    expect(connect(base(), "b", "a").refused).toBe("cycle"); // b already depends on a
    const chain = connect(connect(base(), "c", "a").def!, "b", "c"); // c -> a -> b, then b -> c closes it
    expect(chain.refused).toBe("cycle");
    expect(connect(base(), "a", "zzz").refused).toBe("unknown_stage");
  });
  it("deleting an edge removes the dependency", () => {
    expect(disconnect(base(), "a", "b").stages[1]!.depends_on).toEqual([]);
  });
  it("does not change the document it is given", () => {
    const d = base();
    connect(d, "c", "b");
    expect(d).toEqual(base());
  });
});

describe("editing stages and checklists", () => {
  it("adds a stage with one tickable item and a fresh id, and deletes one with its edges", () => {
    const d = base();
    const id = nextStageId(d);
    const added = addStage(d, id);
    expect(added.stages.at(-1)!.checks.length).toBe(1);
    expect(nextStageId(added)).not.toBe(id);
    const gone = deleteStage(d, "a");
    expect(gone.stages.map((s) => s.id)).toEqual(["b", "c"]);
    expect(gone.stages[0]!.depends_on).toEqual([]);
  });
  it("renames, changes role and four eyes", () => {
    const d = updateStage(updateStage(base(), "a", { name: { en: "First", he: "ראשון" } }), "b", { role: "manager", four_eyes: true });
    expect(d.stages[0]!.name.he).toBe("ראשון");
    expect(d.stages[1]).toMatchObject({ role: "manager", four_eyes: true });
  });
  it("renaming an id follows the dependencies and refuses a taken id", () => {
    const r = renameStageId(base(), "a", "first");
    expect(r.def!.stages[1]!.depends_on).toEqual(["first"]);
    expect(renameStageId(base(), "a", "c").refused).toBeDefined();
  });
  it("edits items: text in both languages, kind, verify note, add, remove, reorder", () => {
    let d = addCheck(base(), "a", "verify");
    d = updateCheck(d, "a", 1, { text: { en: "Check ID", he: "בדיקת זהות" }, verify_note: true });
    expect(d.stages[0]!.checks[1]).toEqual({ text: { en: "Check ID", he: "בדיקת זהות" }, kind: "verify", verify_note: true });
    d = moveCheck(d, "a", 1, -1);
    expect(d.stages[0]!.checks[0]!.text.en).toBe("Check ID");
    expect(moveCheck(d, "a", 0, -1)).toBe(d); // stops at the top
    d = removeCheck(d, "a", 0);
    expect(d.stages[0]!.checks.length).toBe(1);
  });
  it("orders stages so every dependency points to an earlier one, keeping the author's order otherwise", () => {
    const d = base();
    const swapped = { ...d, stages: [d.stages[1]!, d.stages[0]!, d.stages[2]!] }; // b before its dependency a
    expect(topoSort(swapped).stages.map((s) => s.id)).toEqual(["a", "b", "c"]);
    expect(topoSort(d).stages.map((s) => s.id)).toEqual(["a", "b", "c"]);
  });
});

describe("preview of the changes", () => {
  it("names added, removed and changed stages, dependencies and items", () => {
    const before = base();
    let after = addStage(before, "d");
    after = deleteStage(after, "c");
    after = updateStage(after, "a", { role: "manager" });
    after = disconnect(after, "a", "b");
    after = addCheck(after, "b");
    const text = diff(before, after).map((c) => `${c.kind}:${c.stage}:${c.what}`);
    expect(text).toContain("added:d:stage");
    expect(text).toContain("removed:c:stage");
    expect(text).toContain("changed:a:role agent -> manager");
    expect(text).toContain("removed:b:dependency a");
    expect(text).toContain("added:b:item New item");
  });
  it("says nothing when nothing changed, and calls a new definition all added", () => {
    expect(diff(base(), base())).toEqual([]);
    expect(diff(null, base()).every((c) => c.kind === "added")).toBe(true);
  });
  it("notices a reorder of items", () => {
    const d = addCheck(base(), "a");
    const e = updateCheck(d, "a", 1, { text: { en: "Second", he: "שני" } });
    expect(diff(e, moveCheck(e, "a", 1, -1)).some((c) => c.what === "item order")).toBe(true);
  });
});

describe("undo and redo", () => {
  it("steps back and forth and restores the exact document", () => {
    const d0 = base();
    const d1 = connect(d0, "c", "b").def!;
    const d2 = addStage(d1, "d");
    let h = push(push(startHistory(d0), d1), d2);
    h = undo(h);
    expect(h.present).toBe(d1);
    h = undo(h);
    expect(h.present).toBe(d0);
    expect(undo(h)).toBe(h); // nothing earlier
    h = redo(redo(h));
    expect(h.present).toBe(d2);
    expect(redo(h)).toBe(h);
  });
  it("a new edit after undo drops the redo branch", () => {
    const d0 = base();
    let h = push(startHistory(d0), addStage(d0, "x"));
    h = undo(h);
    h = push(h, addStage(d0, "y"));
    expect(h.future).toEqual([]);
  });
});
