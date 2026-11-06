import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api";
import { type Key } from "../i18n";
import { useUi } from "../ui";

interface Auth { state: "missing" | "valid" | "expiring"; expires_at?: string | null }
interface Flow { authorizations: Record<string, Auth> }

/** F5: the three data-lookup authorizations and the request that only runs when all three are valid. */
export function Pension({ clientId }: { clientId: string }) {
  const { api, tr, me } = useUi();
  const [flow, setFlow] = useState<Flow | null>(null);
  const [message, setMessage] = useState("");
  const canAct = me?.capabilities.includes("flow.write") ?? false;

  const load = useCallback(() => api.get<Flow>(`/api/clients/${clientId}/flow-status`).then(setFlow).catch(() => setFlow(null)), [api, clientId]);
  useEffect(() => { void load(); }, [load]);

  async function request() {
    setMessage("");
    try {
      await api.post(`/api/clients/${clientId}/lookup-requests`);
      setMessage(tr("lookupRecorded"));
    } catch (e) {
      setMessage(e instanceof ApiError ? e.code : tr("error"));
    }
  }

  if (!flow?.authorizations) return null;
  return (
    <>
      <h3>{tr("pension")}</h3>
      <p>{tr("authorizations")}</p>
      <ul className="rows">
        {Object.entries(flow.authorizations).map(([kind, a]) => (
          <li key={kind} data-state={a.state} className={a.state === "valid" ? undefined : "attention"}>
            {tr(`auth.${kind}` as Key)}: {tr(`auth.${a.state}` as Key)}{a.expires_at ? ` (${a.expires_at})` : ""}
          </li>
        ))}
      </ul>
      {canAct && <button onClick={() => void request()}>{tr("requestLookup")}</button>}
      {message && <p role="status">{message}</p>}
    </>
  );
}
