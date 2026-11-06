import { useEffect, useState } from "react";
import { type AuditVerify, type Overview as Data } from "../api";
import { type Key } from "../i18n";
import { useUi } from "../ui";

const CARDS: (keyof Data)[] = [
  "inbox_urgent", "renewals_overdue", "renewals_next_30", "claims_open", "intake_waiting_review",
  "kyc_waiting_approval", "campaigns_sending", "complaints_overdue", "licences_attention", "clients",
];
const ATTENTION = new Set<keyof Data>(["inbox_urgent", "renewals_overdue", "complaints_overdue", "licences_attention"]);

export function Overview() {
  const { api, tr, me } = useUi();
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState(false);
  const [audit, setAudit] = useState<AuditVerify | null>(null);
  const canVerify = me?.capabilities.includes("audit.read") ?? false;

  useEffect(() => {
    api.overview().then(setData).catch(() => setError(true));
  }, [api]);

  async function verify() {
    setError(false);
    try {
      setAudit(await api.verifyAudit());
    } catch {
      setError(true);
    }
  }

  return (
    <section aria-label={tr("overview")}>
      {error && <p role="alert">{tr("error")}</p>}
      {data && (
        <ul className="cards">
          {CARDS.map((k) => (
            <li key={k} className={ATTENTION.has(k) && data[k] > 0 ? "attention" : undefined}>
              <strong>{data[k]}</strong> <span>{tr(`ov.${k}` as Key)}</span>
            </li>
          ))}
        </ul>
      )}
      {canVerify && (
        <div className="trust">
          <h2>{tr("trust")}</h2>
          <button onClick={() => void verify()}>{tr("verifyChain")}</button>
          {audit && (
            <p role="status" className={audit.ok ? "ok" : "bad"}>
              {audit.ok ? tr("chainOk") : tr("chainBad")} ({audit.entries} + {audit.sealed_entries})
              {!audit.ok && <> {audit.problems.join("; ")}</>}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
