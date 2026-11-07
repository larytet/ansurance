import { type FormEvent, useCallback, useEffect, useState } from "react";
import { ApiError } from "../api";
import { type Key } from "../i18n";
import { useUi } from "../ui";

interface TaskRow { id: string; text: string; due: string; done: boolean; overdue: boolean; client_id: string | null; client: string | null; owner_id: string; owner: string }
interface Person { id: string; name: string }
const VIEWS = ["today", "over", "next", "done", "deleg"] as const;
type View = (typeof VIEWS)[number];

/** The agent's to-do list on Today, with the five chips of the mock. */
export function TodayTasks({ onOpenClient }: { onOpenClient: (id: string) => void }) {
  const { api, tr } = useUi();
  const [view, setView] = useState<View>("today");
  const [rows, setRows] = useState<TaskRow[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [text, setText] = useState("");
  const [due, setDue] = useState(() => new Date().toISOString().slice(0, 10));
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    api.get<TaskRow[]>(`/api/tasks?view=${view}`).then(setRows).catch(() => setError(tr("error")));
  }, [api, view, tr]);
  useEffect(load, [load]);
  useEffect(() => { api.get<Person[]>("/api/tasks/people").then(setPeople).catch(() => setPeople([])); }, [api]);

  async function add(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post("/api/tasks", { text: text.trim(), due });
      setText("");
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.code : tr("error"));
    }
  }
  async function patch(id: string, body: object) {
    try {
      await api.patch(`/api/tasks/${id}`, body);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.code : tr("error"));
    }
  }
  return (
    <section aria-label={tr("tasks")}>
      <h2>{tr("tasks")}</h2>
      <div role="group" aria-label={tr("tasks")} className="chips">
        {VIEWS.map((v) => (
          <button key={v} type="button" aria-pressed={view === v} onClick={() => setView(v)}>{tr(`task.${v}` as Key)}</button>
        ))}
      </div>
      {rows.length === 0 && <p>{tr("noTasks")}</p>}
      <ul className="work">
        {rows.map((t) => (
          <li key={t.id} className={t.overdue ? "attention" : undefined}>
            <label>
              <input type="checkbox" checked={t.done} onChange={(e) => patch(t.id, { done: e.target.checked })} />{" "}
              <bdi>{t.text}</bdi>
            </label>
            {" · "}{t.due}
            {t.client_id && <>{" · "}<button className="link" onClick={() => onOpenClient(t.client_id as string)}><bdi>{t.client}</bdi></button></>}
            {view === "deleg" && <>{" · "}{tr("owner")}: <bdi>{t.owner}</bdi></>}
            {!t.done && people.length > 1 && (
              <select aria-label={`${tr("delegate")}: ${t.text}`} value="" onChange={(e) => e.target.value && patch(t.id, { owner_id: e.target.value })}>
                <option value="">{tr("delegate")}</option>
                {people.filter((p) => p.id !== t.owner_id).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            )}
          </li>
        ))}
      </ul>
      <form onSubmit={add}>
        <label>{tr("newTask")} <input value={text} maxLength={200} onChange={(e) => setText(e.target.value)} required /></label>{" "}
        <label>{tr("dueDate")} <input type="date" value={due} onChange={(e) => setDue(e.target.value)} required /></label>{" "}
        <button type="submit">{tr("addTask")}</button>
      </form>
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

interface MeetingRow { id: string; kind: string; held_on: string; subject: string; summary: string | null }
interface MeetingList { items: MeetingRow[]; service_contact_due: string | null }
const KINDS = ["in_person", "phone", "online"] as const;

/** Meetings and tasks about one client, on the client file. */
export function ClientMeetings({ clientId }: { clientId: string }) {
  const { api, tr } = useUi();
  const [list, setList] = useState<MeetingList | null>(null);
  const [kind, setKind] = useState<(typeof KINDS)[number]>("in_person");
  const [heldOn, setHeldOn] = useState(() => new Date().toISOString().slice(0, 10));
  const [subject, setSubject] = useState("");
  const [summary, setSummary] = useState("");
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => { api.get<MeetingList>(`/api/clients/${clientId}/meetings`).then(setList).catch(() => setList(null)); }, [api, clientId]);
  useEffect(load, [load]);
  async function add(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post(`/api/clients/${clientId}/meetings`, { kind, held_on: heldOn, subject: subject.trim(), summary: summary.trim() || null });
      setSubject(""); setSummary("");
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.code : tr("error"));
    }
  }
  return (
    <>
      <h3>{tr("meetings")}</h3>
      {list?.service_contact_due && <p>{tr("serviceContactDue")}: {list.service_contact_due}</p>}
      {list && list.items.length === 0 && <p>{tr("noMeetings")}</p>}
      <ul className="rows">
        {(list?.items ?? []).map((m) => (
          <li key={m.id}><strong><bdi>{m.subject}</bdi></strong> · {m.held_on} · {tr(`meeting.${m.kind}` as Key)}{m.summary && <p><bdi>{m.summary}</bdi></p>}</li>
        ))}
      </ul>
      <form onSubmit={add}>
        <label>{tr("meetingKind")}{" "}
          <select value={kind} onChange={(e) => setKind(e.target.value as (typeof KINDS)[number])}>
            {KINDS.map((k) => <option key={k} value={k}>{tr(`meeting.${k}` as Key)}</option>)}
          </select>
        </label>{" "}
        <label>{tr("dueDate")} <input type="date" value={heldOn} onChange={(e) => setHeldOn(e.target.value)} required /></label>{" "}
        <label>{tr("subject")} <input value={subject} onChange={(e) => setSubject(e.target.value)} required /></label>{" "}
        <label>{tr("summary")} <input value={summary} onChange={(e) => setSummary(e.target.value)} /></label>{" "}
        <button type="submit">{tr("addMeeting")}</button>
      </form>
      {error && <p role="alert">{error}</p>}
    </>
  );
}
