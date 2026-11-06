import { useEffect, useState } from "react";
import { useUi } from "../ui";

interface Adapter { name: string; configured: boolean; approved: boolean; calls_24h: { ok: number; failed: number }; last_error: string | null }
interface Data { adapters: Adapter[]; syncs: { system: string; last_run: string | null }[] }

export function Integrations() {
  const { api, tr } = useUi();
  const [d, setD] = useState<Data | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => { api.get<Data>("/api/integrations/status").then(setD).catch(() => setError(true)); }, [api]);
  return (
    <section aria-label={tr("integrations")}>
      {error && <p role="alert">{tr("error")}</p>}
      <ul className="cards">
        {(d?.adapters ?? []).map((a) => (
          <li key={a.name} className={!a.configured || a.calls_24h.failed > 0 ? "attention" : undefined}>
            <strong>{a.name}</strong>
            <div>{a.configured ? tr("adapterConfigured") : tr("notConfigured")} · {a.approved ? tr("adapterApproved") : "—"}</div>
            <small>{tr("calls24")}: {a.calls_24h.ok}{a.calls_24h.failed > 0 && <>, {a.calls_24h.failed} {tr("failed")}</>}</small>
            {a.last_error && <div><small>{tr("lastError")}: <bdi>{a.last_error}</bdi></small></div>}
          </li>
        ))}
      </ul>
      {d && d.syncs.length > 0 && (
        <>
          <h2>{tr("syncs")}</h2>
          <ul>{d.syncs.map((s) => <li key={s.system}>{s.system}: {s.last_run ?? "—"}</li>)}</ul>
        </>
      )}
    </section>
  );
}
