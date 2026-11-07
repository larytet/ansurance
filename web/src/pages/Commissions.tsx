import { useState, type FormEvent } from "react";
import { ApiError } from "../api";
import { useUi } from "../ui";

interface Statement {
  id: string; period: string; status: string; declared_total: string; lines_total: string; ties: boolean;
  counts: Record<string, number>; lines: { policy_number: string; amount: string; expected: string | null; result: string }[];
}
interface Payout { agents: Record<string, string>; total: string }

export function Commissions() {
  const { api, tr, me } = useUi();
  const [form, setForm] = useState({ insurer: "", period: "", total: "", lines: "" });
  const [st, setSt] = useState<Statement | null>(null);
  const [payout, setPayout] = useState<Payout | null>(null);
  const [message, setMessage] = useState("");
  const canApprove = me?.capabilities.includes("flow.approve") ?? false;

  async function run(action: () => Promise<unknown>) {
    setMessage("");
    try { await action(); } catch (e) { setMessage(e instanceof ApiError ? e.code : tr("error")); }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const lines = form.lines.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
      const [policy_number, amount] = l.split(/[,;\t]/).map((x) => x.trim());
      return { policy_number: policy_number ?? "", amount: amount ?? "" };
    });
    void run(async () => { setPayout(null); setSt(await api.post<Statement>("/api/commissions/statements", { insurer: form.insurer, period: form.period, declared_total: form.total, lines })); });
  }

  const [file, setFile] = useState<File | null>(null);
  async function upload(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    body.append("insurer", form.insurer);
    body.append("period", form.period);
    body.append("declared_total", form.total);
    setMessage("");
    const r = await fetch("/api/commissions/statements/xlsx", { method: "POST", credentials: "same-origin", headers: { "x-csrf-token": api.csrf }, body });
    if (!r.ok) {
      let code = r.statusText;
      try {
        const j = (await r.json()) as { detail?: unknown };
        if (typeof j.detail === "string") code = j.detail;
        else if (j.detail && typeof j.detail === "object" && "error" in j.detail) code = String((j.detail as { error: string }).error);
      } catch { /* not JSON */ }
      setMessage(code);
      return;
    }
    setPayout(null);
    setSt((await r.json()) as Statement);
  }

  return (
    <section aria-label={tr("commissions")}>
      {message && <p role="alert">{message}</p>}
      <form onSubmit={submit} aria-label={tr("importStatement")}>
        <h2>{tr("importStatement")}</h2>
        <label>{tr("insurer")} <input value={form.insurer} onChange={(e) => setForm({ ...form, insurer: e.target.value })} required /></label>
        <label>{tr("period")} <input value={form.period} onChange={(e) => setForm({ ...form, period: e.target.value })} required pattern="\d{4}-\d{2}" /></label>
        <label>{tr("declaredTotal")} <input value={form.total} onChange={(e) => setForm({ ...form, total: e.target.value })} required inputMode="decimal" /></label>
        <label>{tr("statementLines")} <textarea value={form.lines} onChange={(e) => setForm({ ...form, lines: e.target.value })} required /></label>
        <button type="submit">{tr("importStatement")}</button>
      </form>
      <form onSubmit={(e) => void upload(e)} aria-label={tr("importFile")}>
        <label>{tr("uploadStatement")} <input type="file" accept=".xlsx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
        <button type="submit" disabled={!file || !form.insurer || !form.period || !form.total}>{tr("importFile")}</button>
      </form>
      {st && (
        <div>
          <p role="status">{st.period} · {tr("ties")}: {st.ties ? tr("yes") : tr("no")} ({st.lines_total} / {st.declared_total}) · {tr("status")}: {st.status}</p>
          <p>{tr("differences")}: {Object.entries(st.counts).filter(([k]) => k !== "matched").map(([k, v]) => `${k} ${v}`).join(", ") || "0"}</p>
          <table>
            <tbody>
              {st.lines.map((l) => (
                <tr key={l.policy_number} className={l.result === "matched" ? undefined : "attention"}>
                  <td>{l.policy_number}</td><td>{l.amount}</td><td>{l.expected ?? ""}</td><td>{l.result}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {canApprove && st.status === "open" && (
            <button onClick={() => void run(async () => setSt(await api.post<Statement>(`/api/commissions/statements/${st.id}/approve`)))}>{tr("approveStatement")}</button>
          )}
          {canApprove && st.status === "approved" && (
            <button onClick={() => void run(async () => setPayout(await api.get<Payout>(`/api/commissions/statements/${st.id}/payout`)))}>{tr("payout")}</button>
          )}
          {payout && <ul>{Object.entries(payout.agents).map(([a, v]) => <li key={a}><bdi>{a}</bdi>: {v}</li>)}<li><strong>{payout.total}</strong></li></ul>}
        </div>
      )}
    </section>
  );
}
