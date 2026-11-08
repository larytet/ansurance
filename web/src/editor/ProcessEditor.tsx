import "@xyflow/react/dist/style.css";
import { Background, Controls, ReactFlow, applyNodeChanges, type Connection, type Edge, type Node, type NodeChange } from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import { ApiError } from "../api";
import { type Key } from "../i18n";
import { useUi } from "../ui";
import {
  KINDS, ROLES, type Definition, type Kind, addCheck, addStage, connect, deleteStage, diff, disconnect, moveCheck, nextStageId,
  push, redo, removeCheck, renameStageId, startHistory, toGraph, topoSort, undo, updateCheck, updateStage, type History,
} from "./model";

interface Problem { code: string; message: string; stage: string | null; check: number | null; edge: string[] | null }
interface Verdict { ok: boolean; errors: Problem[]; current_version: number | null; running_cases: number }

/** Edit a process definition: the canvas, the stage list and panel (the keyboard path), a live
 * check by the server, the changes against the published version, and publishing. */
export function ProcessEditor({ defKey, onClose, onPublished }: { defKey: string; onClose: () => void; onPublished: (version: number) => void }) {
  const { api, tr, lang, theme } = useUi();
  const dl: "he" | "en" = lang === "he" ? "he" : "en";
  const [published, setPublished] = useState<Definition | null>(null);
  const [hist, setHist] = useState<History<Definition> | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [moved, setMoved] = useState<Record<string, { x: number; y: number }>>({});
  const [failure, setFailure] = useState(false);
  const [done, setDone] = useState<number | null>(null);
  const importRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.get<{ definition: Definition }>(`/api/process-defs/${defKey}`)
      .then((r) => { setPublished(r.definition); setHist(startHistory(r.definition)); setSelected(r.definition.stages[0]?.id ?? null); })
      .catch(() => setFailure(true));
  }, [api, defKey]);

  const def = hist?.present ?? null;
  const change = useCallback((next: Definition) => { setHist((h) => (h ? push(h, next) : h)); setNote(null); setDone(null); }, []);

  // the server's verdict on the draft, a moment after the last edit
  useEffect(() => {
    if (!def) return;
    const t = window.setTimeout(() => {
      api.post<Verdict>("/api/process-defs/validate", topoSort(def)).then(setVerdict).catch(() => setFailure(true));
    }, 250);
    return () => window.clearTimeout(t);
  }, [api, def]);

  const refuse = (why: string) => setNote(tr(`refused.${why}` as Key));
  const doConnect = (from: string, to: string) => {
    if (!def) return;
    const r = connect(def, from, to);
    if (r.def) change(r.def);
    else refuse(r.refused);
  };

  const graph = useMemo(() => (def ? toGraph(def) : { nodes: [], edges: [] }), [def]);
  const bad = useMemo(() => new Set((verdict?.errors ?? []).map((e) => e.stage).filter(Boolean) as string[]), [verdict]);
  const nodes: Node[] = graph.nodes.map((n) => ({
    id: n.id,
    position: moved[n.id] ?? { x: n.x, y: n.y },
    data: { label: `${n.stage.name[dl] || n.id} · ${tr(`role.${n.stage.role}` as Key)}` },
    className: `pe-node role-${n.stage.role}${bad.has(n.id) ? " bad" : ""}${selected === n.id ? " selected" : ""}`,
    style: { width: 176 },
  }));
  const edges: Edge[] = graph.edges.map((e) => ({ id: e.id, source: e.source, target: e.target, markerEnd: { type: "arrowclosed" as never } }));

  const onNodesChange = (changes: NodeChange[]) => {
    const moves = changes.filter((c) => c.type === "position" && c.position);
    if (moves.length === 0) return;
    setMoved((m) => {
      const next = { ...m };
      for (const c of applyNodeChanges(moves, nodes)) next[c.id] = c.position;
      return next;
    });
  };

  if (failure && !def) return <p role="alert">{tr("error")}</p>;
  if (!def || !hist) return <p>{tr("loading")}</p>;
  const stage = def.stages.find((s) => s.id === selected) ?? null;
  const locked = (id: string) => !!published?.stages.some((s) => s.id === id);
  const changes = diff(published, def);
  const errorsOf = (id: string) => (verdict?.errors ?? []).filter((e) => e.stage === id);
  const canPublish = !!verdict && verdict.ok && changes.length > 0 && done === null;

  async function publish() {
    if (!def) return;
    try {
      const r = await api.post<{ version: number }>("/api/process-defs", topoSort(def));
      setDone(r.version);
      onPublished(r.version);
      const fresh = await api.get<{ definition: Definition }>(`/api/process-defs/${defKey}`);
      setPublished(fresh.definition);
    } catch (e) {
      setNote(e instanceof ApiError ? `${e.status} ${e.code}` : tr("error"));
    }
  }

  function exportJson() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(topoSort(def as Definition), null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${defKey}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function importJson(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      const doc = JSON.parse(await file.text()) as Definition;
      const v = await api.post<Verdict>("/api/process-defs/validate", doc);
      if (!v.ok) { setNote(`${tr("importFailed")} ${v.errors.map((x) => x.message).join("; ")}`); return; }
      change(doc);
      setSelected(doc.stages[0]?.id ?? null);
      setMoved({});
    } catch {
      setNote(`${tr("importFailed")} JSON`);
    }
  }

  const onKey = (e: React.KeyboardEvent) => {
    const typing = (e.target as HTMLElement).tagName === "INPUT" || (e.target as HTMLElement).tagName === "TEXTAREA" || (e.target as HTMLElement).tagName === "SELECT";
    if (typing || !(e.ctrlKey || e.metaKey)) return;
    if (e.key === "z") { e.preventDefault(); setHist((h) => (h ? (e.shiftKey ? redo(h) : undo(h)) : h)); }
    if (e.key === "y") { e.preventDefault(); setHist((h) => (h ? redo(h) : h)); }
  };

  return (
    <section className="process-editor" aria-label={tr("editDef")} onKeyDown={onKey}>
      <div className="pe-bar">
        <strong>{def.name[dl]}</strong>{" "}
        <button disabled={hist.past.length === 0} onClick={() => setHist(undo(hist))}>{tr("undo")}</button>{" "}
        <button disabled={hist.future.length === 0} onClick={() => setHist(redo(hist))}>{tr("redo")}</button>{" "}
        <button onClick={() => setMoved({})}>{tr("tidy")}</button>{" "}
        <button onClick={exportJson}>{tr("exportJson")}</button>{" "}
        <button onClick={() => importRef.current?.click()}>{tr("importJson")}</button>
        <input ref={importRef} type="file" accept="application/json,.json" hidden aria-label={tr("importJson")} onChange={(e) => void importJson(e)} />{" "}
        <button onClick={() => { const id = nextStageId(def); change(addStage(def, id)); setSelected(id); }}>{tr("addStage")}</button>{" "}
        <button disabled={!canPublish} onClick={() => void publish()}>{tr("publish")}</button>{" "}
        <button className="link" onClick={onClose}>{tr("closeEditor")}</button>
      </div>
      <p><small>{tr("editorHint")}</small></p>
      {note && <p role="alert">{note}</p>}
      {done !== null && <p role="status">{tr("publishedAs")} {done}</p>}

      <div className="pe-canvas" style={{ direction: "ltr" }} data-testid="canvas">
        <ReactFlow
          nodes={nodes} edges={edges} colorMode={theme} fitView
          onNodesChange={onNodesChange}
          onConnect={(c: Connection) => c.source && c.target && doConnect(c.source, c.target)}
          onEdgesDelete={(es) => { let d = def; for (const e of es) d = disconnect(d, e.source, e.target); change(d); }}
          onNodeClick={(_, n) => setSelected(n.id)}
          nodesConnectable edgesFocusable deleteKeyCode={["Backspace", "Delete"]}
        >
          <Background /><Controls showInteractive={false} />
        </ReactFlow>
      </div>

      <div className="pe-cols">
        <nav aria-label={tr("stagesList")}>
          <h4>{tr("stagesList")}</h4>
          <ul className="pe-stages">
            {def.stages.map((s) => (
              <li key={s.id}>
                <button aria-pressed={s.id === selected} className={bad.has(s.id) ? "bad" : undefined} onClick={() => setSelected(s.id)}>
                  {s.name[dl] || s.id}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        {stage && (
          <div className="pe-panel" data-stage={stage.id}>
            <label>{tr("stageId")}{" "}
              <input value={stage.id} readOnly={locked(stage.id)} onChange={(e) => {
                const r = renameStageId(def, stage.id, e.target.value);
                if (r.def) { change(r.def); setSelected(e.target.value); } else refuse("duplicate_edge");
              }} />
            </label>{locked(stage.id) && <small> {tr("idLocked")}</small>}
            <label>{tr("nameEn")}{" "}<input value={stage.name.en} onChange={(e) => change(updateStage(def, stage.id, { name: { ...stage.name, en: e.target.value } }))} /></label>
            <label>{tr("nameHe")}{" "}<input dir="rtl" value={stage.name.he} onChange={(e) => change(updateStage(def, stage.id, { name: { ...stage.name, he: e.target.value } }))} /></label>
            <label>{tr("ownerRole")}{" "}
              <select value={stage.role} onChange={(e) => change(updateStage(def, stage.id, { role: e.target.value }))}>
                {ROLES.map((r) => <option key={r} value={r}>{tr(`role.${r}` as Key)}</option>)}
              </select>
            </label>
            <label><input type="checkbox" checked={!!stage.four_eyes} onChange={(e) => change(updateStage(def, stage.id, { four_eyes: e.target.checked }))} /> {tr("fourEyes")}</label>
            <fieldset>
              <legend>{tr("dependsOn")}</legend>
              {def.stages.filter((s) => s.id !== stage.id).map((s) => (
                <label key={s.id}>
                  <input type="checkbox" checked={stage.depends_on.includes(s.id)}
                    onChange={(e) => (e.target.checked ? doConnect(s.id, stage.id) : change(disconnect(def, s.id, stage.id)))} /> {s.name[dl] || s.id}
                </label>
              ))}
            </fieldset>
            <h4>{tr("items")}</h4>
            <ol className="pe-items">
              {stage.checks.map((c, i) => (
                <li key={i}>
                  <select aria-label={tr("itemKind")} value={c.kind} onChange={(e) => change(updateCheck(def, stage.id, i, { kind: e.target.value as Kind }))}>
                    {KINDS.map((k) => <option key={k} value={k}>{tr(`kind.${k}` as Key)}</option>)}
                  </select>{" "}
                  <input aria-label={tr("itemEn")} value={c.text.en} onChange={(e) => change(updateCheck(def, stage.id, i, { text: { ...c.text, en: e.target.value } }))} />{" "}
                  <input aria-label={tr("itemHe")} dir="rtl" value={c.text.he} onChange={(e) => change(updateCheck(def, stage.id, i, { text: { ...c.text, he: e.target.value } }))} />{" "}
                  <label><input type="checkbox" checked={!!c.verify_note} onChange={(e) => change(updateCheck(def, stage.id, i, { verify_note: e.target.checked }))} /> {tr("verifyNote")}</label>{" "}
                  <button aria-label={`${tr("moveUp")} ${i + 1}`} onClick={() => change(moveCheck(def, stage.id, i, -1))}>↑</button>
                  <button aria-label={`${tr("moveDown")} ${i + 1}`} onClick={() => change(moveCheck(def, stage.id, i, 1))}>↓</button>
                  <button aria-label={`${tr("removeItem")} ${i + 1}`} onClick={() => change(removeCheck(def, stage.id, i))}>✕</button>
                  {errorsOf(stage.id).filter((e) => e.check === i).map((e, n) => <small key={n} role="alert"> {e.message}</small>)}
                </li>
              ))}
            </ol>
            <button onClick={() => change(addCheck(def, stage.id))}>{tr("addItem")}</button>{" "}
            <button onClick={() => { change(deleteStage(def, stage.id)); setSelected(def.stages.find((s) => s.id !== stage.id)?.id ?? null); }}>{tr("deleteStage")}</button>
          </div>
        )}
      </div>

      <h4>{tr("problems")}</h4>
      {verdict && verdict.errors.length === 0 && <p role="status">{tr("noProblems")}</p>}
      <ul className="pe-problems">
        {(verdict?.errors ?? []).map((e, i) => (
          <li key={i} role="alert"><button className="link" onClick={() => e.stage && setSelected(e.stage)}>{e.stage ?? "-"}</button>: {e.message}</li>
        ))}
      </ul>

      <h4>{tr("changes")}</h4>
      {changes.length === 0 ? <p>{tr("noChanges")}</p> : (
        <ul className="pe-changes">{changes.map((c, i) => <li key={i} className={c.kind}>{tr(`change.${c.kind}` as Key)}: {c.stage} · {c.what}</li>)}</ul>
      )}
      <p><small>{tr("runningNote")} {verdict?.running_cases ?? 0}</small></p>
    </section>
  );
}
