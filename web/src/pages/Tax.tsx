import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api";
import { type Key } from "../i18n";
import { useUi } from "../ui";

interface Cert { id: string; client: string; product: string; policy_number: string; status: string; requested: boolean; received: boolean; sent: boolean }

interface Deadline { id: string; kind: string; title: string; due_on: string; done: boolean }
interface Gap { client_id: string; client: string; gap: string }
const KINDS = ["commission_invoice", "withholding_cert", "vat_report", "accountant_package", "other"];

export function Tax() {
  const { api, tr, me } = useUi();
  const [year, setYear] = useState(new Date().getFullYear() - 1);
  const [rows, setRows] = useState<Cert[] | null>(null);
  const [message, setMessage] = useState("");
  const [deadlines, setDeadlines] = useState<Deadline[]>([]);
  const [gaps, setGaps] = useState<Gap[]>([]);
  const [dl, setDl] = useState({ kind: "vat_report", title: "", due_on: "" });
  const canWrite = me?.capabilities.includes("tax.write") ?? false;
  const mark = (b: boolean) => (b ? tr("yes") : tr("no"));

  const load = useCallback(() => api.get<Cert[]>(`/api/tax/certificates?tax_year=${year}`).then(setRows).catch(() => setMessage(tr("error"))), [api, year, tr]);
  useEffect(() => { void load(); }, [load]);
  const loadExtra = useCallback(async () => {
    try {
      setDeadlines(await api.get<Deadline[]>("/api/tax/deadlines"));
      if (canWrite) setGaps(await api.get<Gap[]>("/api/tax/residency/gaps"));
    } catch { setMessage(tr("error")); }
  }, [api, canWrite, tr]);
  useEffect(() => { void loadExtra(); }, [loadExtra]);

  async function act(action: () => Promise<unknown>) {
    setMessage("");
    try { await action(); await loadExtra(); } catch (e) { setMessage(e instanceof ApiError ? e.code : tr("error")); }
  }

  async function run(path: string) {
    setMessage("");
    try { await api.post(path, { tax_year: year }); await load(); } catch (e) { setMessage(e instanceof ApiError ? e.code : tr("error")); }
  }

  return (
    <section aria-label={tr("tax")}>
      {message && <p role="alert">{message}</p>}
      <label>{tr("taxYear")} <input type="number" value={year} onChange={(e) => setYear(Number(e.target.value))} /></label>
      {canWrite && (
        <>
          <button onClick={() => void run("/api/tax/certificates/generate")}>{tr("generate")}</button>
          <button onClick={() => void run("/api/tax/certificates/request-all")}>{tr("requestAll")}</button>
          <button onClick={() => void run("/api/tax/certificates/send-all")}>{tr("sendAll")}</button>
        </>
      )}
      <table>
        <caption>{tr("certificates")}</caption>
        <thead><tr><th>{tr("clients")}</th><th>{tr("policies")}</th><th>{tr("requested")}</th><th>{tr("received")}</th><th>{tr("sent")}</th></tr></thead>
        <tbody>
          {(rows ?? []).map((c) => (
            <tr key={c.id}><td><bdi>{c.client}</bdi></td><td>{c.product} {c.policy_number}</td><td>{mark(c.requested)}</td><td>{mark(c.received)}</td><td>{mark(c.sent)}</td></tr>
          ))}
        </tbody>
      </table>
      <h2>{tr("deadlines")}</h2>
      <ul className="work">
        {deadlines.map((d) => (
          <li key={d.id} className={!d.done && d.due_on <= new Date(Date.now() + 14 * 86400_000).toISOString().slice(0, 10) ? "attention" : undefined}>
            {d.due_on} · {tr(`dl.${d.kind}` as Key)} · <bdi>{d.title}</bdi> · {d.done ? tr("done") : canWrite ? <button onClick={() => void act(() => api.put(`/api/tax/deadlines/${d.id}/done`))}>{tr("markDone")}</button> : tr("open")}
          </li>
        ))}
      </ul>
      {canWrite && (
        <form aria-label={tr("addDeadline")} onSubmit={(e) => { e.preventDefault(); void act(async () => { await api.post("/api/tax/deadlines", dl); setDl({ ...dl, title: "" }); }); }}>
          <select aria-label={tr("campaignType")} value={dl.kind} onChange={(e) => setDl({ ...dl, kind: e.target.value })}>
            {KINDS.map((k) => <option key={k} value={k}>{tr(`dl.${k}` as Key)}</option>)}
          </select>
          <input aria-label={tr("campaignName")} value={dl.title} onChange={(e) => setDl({ ...dl, title: e.target.value })} required />
          <input aria-label={tr("dueOn")} type="date" value={dl.due_on} onChange={(e) => setDl({ ...dl, due_on: e.target.value })} required />
          <button type="submit">{tr("addDeadline")}</button>
        </form>
      )}
      {canWrite && (
        <>
          <h2>{tr("residency")}</h2>
          {gaps.length === 0 && <p>{tr("noGaps")}</p>}
          <ul className="work">
            {gaps.map((g) => (
              <li key={g.client_id}>
                <bdi>{g.client}</bdi> · {tr(`gap.${g.gap}` as Key)}{" "}
                <button onClick={() => void act(() => api.put(`/api/tax/residency/${g.client_id}`, { residency: g.gap === "no_declaration" ? "israel" : "foreign", form_received: true }))}>{tr(g.gap === "no_declaration" ? "markIsrael" : "markReceived")}</button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
