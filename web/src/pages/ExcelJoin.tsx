import { useState, type FormEvent } from "react";
import { useUi } from "../ui";

interface Summary { base_rows: number; unmatched: number; methods: Record<string, Record<string, number>> }

/** Join the agency file with the insurer and clearing-house files on partial keys; the workbook downloads. */
export function ExcelJoin() {
  const { api, tr } = useUi();
  const [files, setFiles] = useState<{ agency?: File; insurer?: File; clearing?: File }>({});
  const [summary, setSummary] = useState<Summary | null>(null);
  const [message, setMessage] = useState("");

  async function run(e: FormEvent) {
    e.preventDefault();
    setMessage("");
    setSummary(null);
    const form = new FormData();
    for (const [k, f] of Object.entries(files)) if (f) form.append(k, f);
    const r = await fetch("/api/excel/join", { method: "POST", credentials: "same-origin", headers: { "x-csrf-token": api.csrf }, body: form });
    if (!r.ok) {
      let code = r.statusText;
      try { const j = (await r.json()) as { detail?: unknown }; if (typeof j.detail === "string") code = j.detail; } catch { /* not JSON */ }
      setMessage(code);
      return;
    }
    const raw = r.headers.get("x-join-summary");
    if (raw) setSummary(JSON.parse(raw) as Summary);
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = "joined.xlsx";
    a.click();
    URL.revokeObjectURL(url);
    setMessage(tr("joinDone"));
  }

  const pick = (key: "agency" | "insurer" | "clearing", label: string) => (
    <label>{label} <input type="file" accept=".xlsx" onChange={(e) => setFiles({ ...files, [key]: e.target.files?.[0] })} /></label>
  );
  return (
    <section aria-label={tr("excelJoin")}>
      <form onSubmit={(e) => void run(e)}>
        {pick("agency", tr("agencyFile"))}
        {pick("insurer", tr("insurerFile"))}
        {pick("clearing", tr("clearingFile"))}
        <button type="submit" disabled={!files.agency || (!files.insurer && !files.clearing)}>{tr("runJoin")}</button>
      </form>
      {message && <p role="status">{message}</p>}
      {summary && <p>{tr("joinSummary")}: {summary.unmatched} / {summary.base_rows}</p>}
    </section>
  );
}
