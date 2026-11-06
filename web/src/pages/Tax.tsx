import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api";
import { useUi } from "../ui";

interface Cert { id: string; client: string; product: string; policy_number: string; status: string; requested: boolean; received: boolean; sent: boolean }

export function Tax() {
  const { api, tr, me } = useUi();
  const [year, setYear] = useState(new Date().getFullYear() - 1);
  const [rows, setRows] = useState<Cert[] | null>(null);
  const [message, setMessage] = useState("");
  const canWrite = me?.capabilities.includes("tax.write") ?? false;
  const mark = (b: boolean) => (b ? tr("yes") : tr("no"));

  const load = useCallback(() => api.get<Cert[]>(`/api/tax/certificates?tax_year=${year}`).then(setRows).catch(() => setMessage(tr("error"))), [api, year, tr]);
  useEffect(() => { void load(); }, [load]);

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
    </section>
  );
}
