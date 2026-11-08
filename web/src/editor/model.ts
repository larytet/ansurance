/** The editor's document is the process definition itself (the JSON the server validates).
 * Everything here is pure: operations return a new definition, or a reason for refusing. */

export interface Bi { en: string; he: string }
export type Kind = "fill" | "verify" | "send" | "do" | "heading";
export const KINDS: Kind[] = ["fill", "verify", "send", "do", "heading"];
export const ROLES = ["agent", "backoffice", "manager", "marketing"] as const;
export interface CheckDef { text: Bi; kind: Kind; verify_note?: boolean }
export interface StageDef {
  id: string; name: Bi; role: string; depends_on: string[]; four_eyes?: boolean; checks: CheckDef[];
}
export interface Definition {
  id: string; source_flow?: string; owner_role: string; name: Bi; stages: StageDef[];
}

export const BOX_W = 176;
export const BOX_H = 56;
export const GAP_X = 64;
export const GAP_Y = 20;

/** Column of a stage: one more than its deepest dependency. Stages in one column can run in
 * parallel; a stage is locked until everything left of it that it depends on is final. */
export function layers(stages: { id: string; depends_on: string[] }[]): Map<string, number> {
  const depth = new Map<string, number>();
  const byId = new Map(stages.map((s) => [s.id, s]));
  const visit = (id: string, seen: Set<string>): number => {
    const known = depth.get(id);
    if (known !== undefined) return known;
    if (seen.has(id)) return 0; // a cycle is refused by the server; do not loop on bad data
    seen.add(id);
    const deps = byId.get(id)?.depends_on ?? [];
    const d = deps.length === 0 ? 0 : 1 + Math.max(...deps.map((x) => visit(x, seen)));
    depth.set(id, d);
    return d;
  };
  stages.forEach((s) => visit(s.id, new Set()));
  return depth;
}

/** Positions computed from the dependencies, left to right. Never stored. */
export function positions(stages: { id: string; depends_on: string[] }[]): Map<string, { x: number; y: number }> {
  const depth = layers(stages);
  const rows = new Map<number, number>();
  const pos = new Map<string, { x: number; y: number }>();
  for (const s of stages) {
    const col = depth.get(s.id) ?? 0;
    const row = rows.get(col) ?? 0;
    rows.set(col, row + 1);
    pos.set(s.id, { x: col * (BOX_W + GAP_X), y: row * (BOX_H + GAP_Y) });
  }
  return pos;
}

export interface GraphNode { id: string; x: number; y: number; stage: StageDef }
export interface GraphEdge { id: string; source: string; target: string }

export function toGraph(def: Definition): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const pos = positions(def.stages);
  return {
    nodes: def.stages.map((s) => ({ id: s.id, ...(pos.get(s.id) ?? { x: 0, y: 0 }), stage: s })),
    edges: def.stages.flatMap((s) => s.depends_on.map((d) => ({ id: `${d}>${s.id}`, source: d, target: s.id }))),
  };
}

/** Rebuild the dependencies from the edges (what the canvas reports), keeping everything else. */
export function fromGraph(base: Definition, edges: GraphEdge[]): Definition {
  const order = new Map(base.stages.map((s, i) => [s.id, i]));
  return {
    ...base,
    stages: base.stages.map((s) => {
      const wanted = edges.filter((e) => e.target === s.id).map((e) => e.source);
      const kept = s.depends_on.filter((d) => wanted.includes(d)); // the author's order stays
      const added = wanted.filter((d) => !kept.includes(d)).sort((x, y) => (order.get(x) ?? 0) - (order.get(y) ?? 0));
      return { ...s, depends_on: [...kept, ...added] };
    }),
  };
}

/** True when `to` can already reach `from` along dependencies, so `from -> to` would close a loop. */
function reaches(def: Definition, start: string, goal: string): boolean {
  const byId = new Map(def.stages.map((s) => [s.id, s]));
  const seen = new Set<string>();
  const walk = (id: string): boolean => {
    if (id === goal) return true;
    if (seen.has(id)) return false;
    seen.add(id);
    return (byId.get(id)?.depends_on ?? []).some(walk);
  };
  return walk(start);
}

export type Refusal = "self_dependency" | "duplicate_edge" | "cycle" | "unknown_stage";
export type Result = { def: Definition; refused?: undefined } | { def?: undefined; refused: Refusal };

/** `from` must be final before `to` can start. */
export function connect(def: Definition, from: string, to: string): Result {
  const target = def.stages.find((s) => s.id === to);
  if (!target || !def.stages.some((s) => s.id === from)) return { refused: "unknown_stage" };
  if (from === to) return { refused: "self_dependency" };
  if (target.depends_on.includes(from)) return { refused: "duplicate_edge" };
  if (reaches(def, from, to)) return { refused: "cycle" }; // `from` already depends on `to`, directly or not
  return { def: edit(def, to, (s) => ({ ...s, depends_on: [...s.depends_on, from] })) };
}

export function disconnect(def: Definition, from: string, to: string): Definition {
  return edit(def, to, (s) => ({ ...s, depends_on: s.depends_on.filter((d) => d !== from) }));
}

function edit(def: Definition, id: string, f: (s: StageDef) => StageDef): Definition {
  return { ...def, stages: def.stages.map((s) => (s.id === id ? f(s) : s)) };
}

export const updateStage = (def: Definition, id: string, patch: Partial<Omit<StageDef, "id">>): Definition =>
  edit(def, id, (s) => ({ ...s, ...patch }));

export function renameStageId(def: Definition, from: string, to: string): Result {
  if (from === to) return { def };
  if (def.stages.some((s) => s.id === to)) return { refused: "duplicate_edge" };
  return {
    def: {
      ...def,
      stages: def.stages.map((s) => ({
        ...s, id: s.id === from ? to : s.id, depends_on: s.depends_on.map((d) => (d === from ? to : d)),
      })),
    },
  };
}

export function addStage(def: Definition, id: string): Definition {
  const stage: StageDef = {
    id, name: { en: id, he: id }, role: "agent", depends_on: [],
    checks: [{ text: { en: "New item", he: "פריט חדש" }, kind: "do" }],
  };
  return { ...def, stages: [...def.stages, stage] };
}

export function nextStageId(def: Definition): string {
  for (let n = def.stages.length + 1; ; n++) if (!def.stages.some((s) => s.id === `stage${n}`)) return `stage${n}`;
}

export const deleteStage = (def: Definition, id: string): Definition => ({
  ...def,
  stages: def.stages.filter((s) => s.id !== id).map((s) => ({ ...s, depends_on: s.depends_on.filter((d) => d !== id) })),
});

export const addCheck = (def: Definition, id: string, kind: Kind = "do"): Definition =>
  edit(def, id, (s) => ({ ...s, checks: [...s.checks, { text: { en: "New item", he: "פריט חדש" }, kind }] }));

export const removeCheck = (def: Definition, id: string, i: number): Definition =>
  edit(def, id, (s) => ({ ...s, checks: s.checks.filter((_, n) => n !== i) }));

export const updateCheck = (def: Definition, id: string, i: number, patch: Partial<CheckDef>): Definition =>
  edit(def, id, (s) => ({ ...s, checks: s.checks.map((c, n) => (n === i ? { ...c, ...patch } : c)) }));

export function moveCheck(def: Definition, id: string, i: number, by: -1 | 1): Definition {
  const j = i + by;
  if (j < 0 || j >= (def.stages.find((s) => s.id === id)?.checks.length ?? 0)) return def;
  return edit(def, id, (s) => {
    const a = s.checks[i];
    const b = s.checks[j];
    if (!a || !b) return s;
    const checks = [...s.checks];
    checks[i] = b;
    checks[j] = a;
    return { ...s, checks };
  });
}

/** The server wants a dependency to point to an earlier stage. Order so that holds, keeping the
 * author's order wherever the dependencies allow. A cycle is left as it is (the server names it). */
export function topoSort(def: Definition): Definition {
  const left = [...def.stages];
  const done = new Set<string>();
  const out: StageDef[] = [];
  while (left.length) {
    const i = left.findIndex((s) => s.depends_on.every((d) => done.has(d) || !def.stages.some((x) => x.id === d)));
    const [s] = left.splice(i === -1 ? 0 : i, 1);
    if (!s) break;
    out.push(s);
    done.add(s.id);
  }
  return { ...def, stages: out };
}

// ---- preview: what changes against the published version ------------------------------------
export interface Change { kind: "added" | "removed" | "changed"; stage: string; what: string }

export function diff(before: Definition | null, after: Definition): Change[] {
  const out: Change[] = [];
  const old = new Map((before?.stages ?? []).map((s) => [s.id, s]));
  const now = new Map(after.stages.map((s) => [s.id, s]));
  for (const s of after.stages) {
    if (old.has(s.id)) continue;
    out.push({ kind: "added", stage: s.id, what: "stage" });
    for (const d of s.depends_on) out.push({ kind: "added", stage: s.id, what: `dependency ${d}` });
  }
  for (const s of before?.stages ?? []) if (!now.has(s.id)) out.push({ kind: "removed", stage: s.id, what: "stage" });
  for (const s of after.stages) {
    const o = old.get(s.id);
    if (!o) continue;
    if (o.role !== s.role) out.push({ kind: "changed", stage: s.id, what: `role ${o.role} -> ${s.role}` });
    if (!!o.four_eyes !== !!s.four_eyes) out.push({ kind: "changed", stage: s.id, what: `four eyes ${!!s.four_eyes}` });
    if (o.name.en !== s.name.en || o.name.he !== s.name.he) out.push({ kind: "changed", stage: s.id, what: "name" });
    for (const d of s.depends_on) if (!o.depends_on.includes(d)) out.push({ kind: "added", stage: s.id, what: `dependency ${d}` });
    for (const d of o.depends_on) if (!s.depends_on.includes(d)) out.push({ kind: "removed", stage: s.id, what: `dependency ${d}` });
    const key = (c: CheckDef) => `${c.kind}|${c.text.en}|${c.text.he}|${!!c.verify_note}`;
    const oldKeys = o.checks.map(key);
    const newKeys = s.checks.map(key);
    newKeys.forEach((k) => { if (!oldKeys.includes(k)) out.push({ kind: "added", stage: s.id, what: `item ${k.split("|")[1]}` }); });
    oldKeys.forEach((k) => { if (!newKeys.includes(k)) out.push({ kind: "removed", stage: s.id, what: `item ${k.split("|")[1]}` }); });
    const common = newKeys.filter((k) => oldKeys.includes(k));
    if (common.join("\n") !== oldKeys.filter((k) => newKeys.includes(k)).join("\n")) out.push({ kind: "changed", stage: s.id, what: "item order" });
  }
  return out;
}

// ---- undo and redo ----------------------------------------------------------------------------
export interface History<T> { past: T[]; present: T; future: T[] }
export const startHistory = <T,>(present: T): History<T> => ({ past: [], present, future: [] });
export const push = <T,>(h: History<T>, next: T): History<T> =>
  next === h.present ? h : { past: [...h.past, h.present].slice(-100), present: next, future: [] };
export function undo<T>(h: History<T>): History<T> {
  const prev = h.past[h.past.length - 1];
  return prev === undefined ? h : { past: h.past.slice(0, -1), present: prev, future: [h.present, ...h.future] };
}
export function redo<T>(h: History<T>): History<T> {
  const next = h.future[0];
  return next === undefined ? h : { past: [...h.past, h.present], present: next, future: h.future.slice(1) };
}
