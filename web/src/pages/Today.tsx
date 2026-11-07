import { useEffect, useState } from "react";
import { type ClientRow, type Counts } from "../api";
import { type Key } from "../i18n";
import { useUi } from "../ui";
import { TodayTasks } from "./Tasks";

interface Renewal { policy_id: string; client_id: string; client: string; product: string; renewal_date: string; days: number; stage: string }

export function Today({ onOpenClient }: { onOpenClient: (id: string) => void }) {
  const { api, tr, me } = useUi();
  const [renewals, setRenewals] = useState<Renewal[] | null>(null);
  const [counts, setCounts] = useState<Counts | null>(null);
  const [leads, setLeads] = useState<ClientRow[]>([]);
  const [error, setError] = useState(false);
  useEffect(() => { api.get<ClientRow[]>("/api/clients?status=lead").then(setLeads).catch(() => setLeads([])); }, [api]);
  useEffect(() => {
    Promise.all([api.get<Renewal[]>("/api/renewals?within=60"), api.counts()])
      .then(([r, c]) => { setRenewals(r); setCounts(c); })
      .catch(() => setError(true));
  }, [api]);
  return (
    <section aria-label={tr("today")}>
      {error && <p role="alert">{tr("error")}</p>}
      {counts && <p>{tr("open")}: {counts.open} · {tr("urgent")}: {counts.urgent} · {tr("doneToday")}: {counts.done_today}</p>}
      <h2>{tr("renewalsDue")}</h2>
      {renewals && renewals.length === 0 && <p>{tr("noRenewals")}</p>}
      <ul className="work">
        {(renewals ?? []).map((r) => (
          <li key={r.policy_id} className={r.stage === "overdue" || r.stage === "t10" ? "attention" : undefined}>
            <button className="link" onClick={() => onOpenClient(r.client_id)}><bdi>{r.client}</bdi></button> · {r.product} · {r.renewal_date} ·{" "}
            {tr(`stage.${r.stage}` as Key)} ({r.days} {tr("days")})
          </li>
        ))}
      </ul>
      <h2>{tr("leads")} ({leads.length})</h2>
      {leads.length === 0 && <p>{tr("noLeads")}</p>}
      <ul className="work">
        {leads.map((c) => <li key={c.id}><button className="link" onClick={() => onOpenClient(c.id)}><bdi>{c.name}</bdi></button> · {c.preferred_language}</li>)}
      </ul>
      {me?.capabilities.includes("task.write") && <TodayTasks onOpenClient={onOpenClient} />}
    </section>
  );
}
