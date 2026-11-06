import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ApiError } from "../api";
import { type Key } from "../i18n";
import { useUi } from "../ui";

interface Complaint { id: string; kind: string; text: string; status: string; overdue: boolean; due_at: string; response: string }

export function Complaints() {
  const { api, tr, me } = useUi();
  const [rows, setRows] = useState<Complaint[] | null>(null);
  const [message, setMessage] = useState("");
  const [form, setForm] = useState({ kind: "complaint", text: "" });
  const [answering, setAnswering] = useState<{ id: string; text: string } | null>(null);
  const canExport = me?.capabilities.includes("flow.approve") ?? false;

  const load = useCallback(() => api.get<Complaint[]>("/api/complaints").then(setRows).catch(() => setMessage(tr("error"))), [api, tr]);
  useEffect(() => { void load(); }, [load]);

  async function run(action: () => Promise<unknown>) {
    setMessage("");
    try { await action(); await load(); } catch (e) { setMessage(e instanceof ApiError ? e.code : tr("error")); }
  }

  return (
    <section aria-label={tr("complaints")}>
      {message && <p role="alert">{message}</p>}
      <form aria-label={tr("newComplaint")} onSubmit={(e: FormEvent) => { e.preventDefault(); void run(async () => { await api.post("/api/complaints", { kind: form.kind, text: form.text }); setForm({ ...form, text: "" }); }); }}>
        <h2>{tr("newComplaint")}</h2>
        <label>{tr("complaintKind")}{" "}
          <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
            {["complaint", "privacy", "regulator"].map((k) => <option key={k} value={k}>{tr(`kind.${k}` as Key)}</option>)}
          </select>
        </label>
        <label>{tr("complaintText")} <textarea value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })} required minLength={3} /></label>
        <button type="submit">{tr("create")}</button>
      </form>
      {canExport && <p><a href="/api/complaints/export.csv">{tr("exportCsv")}</a></p>}
      {rows && rows.length === 0 && <p>{tr("noComplaints")}</p>}
      <ul className="work">
        {(rows ?? []).map((c) => (
          <li key={c.id} className={c.overdue ? "attention" : undefined}>
            <strong>{tr(`kind.${c.kind}` as Key)}</strong> · <bdi>{c.text}</bdi> ·{" "}
            {c.status === "answered" ? tr("answered") : c.overdue ? tr("overdue") : c.due_at.slice(0, 10)}
            {c.status !== "answered" && (answering?.id === c.id ? (
              <form onSubmit={(e) => { e.preventDefault(); void run(async () => { await api.post(`/api/complaints/${c.id}/respond`, { response: answering.text }); setAnswering(null); }); }}>
                <label>{tr("responseText")} <input value={answering.text} onChange={(e) => setAnswering({ id: c.id, text: e.target.value })} required minLength={3} /></label>
                <button type="submit">{tr("record")}</button>
              </form>
            ) : <button onClick={() => setAnswering({ id: c.id, text: "" })}>{tr("respond")}</button>)}
          </li>
        ))}
      </ul>
    </section>
  );
}
