import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api";
import { type Key } from "../i18n";
import { useUi } from "../ui";

interface Check { idx: number; text: Record<string, string>; kind: string; done?: boolean; done_by?: string | null }
interface Stage {
  id: string; name: Record<string, string>; role: string; status: string; depends_on: string[]; checks: Check[];
}
interface Instance {
  id: string; title: string; status: string; stages: Stage[];
  progress: { checks_done: number; checks_total: number; stages_final: number; stages_total: number };
}

const KNOWN_ERRORS = ["wrong_role", "stage_locked", "stage_final", "checks_incomplete", "four_eyes_violation"];

/** One running process: every stage with its checklist, ticked by hand, then finished by the stage's role. */
export function ProcessRun({ instanceId, onChange }: { instanceId: string; onChange: () => void }) {
  const { api, tr, lang, me } = useUi();
  const dl: "he" | "en" = lang === "he" ? "he" : "en";
  const [inst, setInst] = useState<Instance | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.get<Instance>(`/api/processes/${instanceId}`).then(setInst).catch(() => setProblem("error"));
  }, [api, instanceId]);
  useEffect(load, [load]);

  const mayAct = (s: Stage) => !!me && (me.roles.includes(s.role) || me.roles.includes("manager"));

  async function run(call: () => Promise<Instance>) {
    setBusy(true);
    setProblem(null);
    try {
      setInst(await call());
      onChange();
    } catch (e) {
      const code = e instanceof ApiError ? e.code : "";
      setProblem(KNOWN_ERRORS.includes(code) ? code : "error");
      load(); // show what the server really holds
    } finally {
      setBusy(false);
    }
  }
  const tick = (s: Stage, c: Check, done: boolean) =>
    run(() => api.put<Instance>(`/api/processes/${instanceId}/stages/${s.id}/checks/${c.idx}`, { done }));
  const finish = (s: Stage) => run(() => api.post<Instance>(`/api/processes/${instanceId}/stages/${s.id}/finalize`));

  if (!inst) return problem ? <p role="alert">{tr("error")}</p> : <p>{tr("loading")}</p>;
  const names = new Map(inst.stages.map((s) => [s.id, s.name[dl] ?? s.name.en]));
  return (
    <div className="process-run" data-process-run={instanceId}>
      <p><small>{tr("checksDone")}: {inst.progress.checks_done}/{inst.progress.checks_total} · {tr("stagesFinal")}: {inst.progress.stages_final}/{inst.progress.stages_total}</small></p>
      {problem && <p role="alert">{tr(problem === "error" ? "error" : (`err.${problem}` as Key))}</p>}
      {inst.stages.map((s) => {
        const real = s.checks.filter((c) => c.kind !== "heading");
        const allDone = real.every((c) => c.done);
        const editable = inst.status === "running" && (s.status === "ready" || s.status === "in_progress") && mayAct(s);
        return (
          <fieldset key={s.id} className={`stage-run ${s.status}`} data-stage={s.id}>
            <legend>
              {s.name[dl] ?? s.name.en} · {tr(`role.${s.role}` as Key)} · <small>{tr(`stagestatus.${s.status}` as Key)}</small>
            </legend>
            {s.status === "locked" && <p><small>{tr("after")}: {s.depends_on.map((d) => names.get(d) ?? d).join(", ")}</small></p>}
            {s.status !== "locked" && !mayAct(s) && s.status !== "final" && <p><small>{tr("notYourStage")}</small></p>}
            <ul className="checks">
              {s.checks.map((c) => c.kind === "heading" ? (
                <li key={c.idx} className="heading"><em>{c.text[dl] ?? c.text.en}</em></li>
              ) : (
                <li key={c.idx}>
                  <label>
                    <input type="checkbox" checked={!!c.done} disabled={!editable || busy} onChange={(e) => void tick(s, c, e.target.checked)} />{" "}
                    {c.text[dl] ?? c.text.en}
                  </label>
                </li>
              ))}
            </ul>
            {editable && (
              <>
                <button disabled={busy || !allDone} onClick={() => void finish(s)}>{tr("finishStage")}</button>{" "}
                {!allDone && <small>{tr("err.checks_incomplete")}</small>}
              </>
            )}
          </fieldset>
        );
      })}
    </div>
  );
}
