import { useState } from "react";
import { ApiError } from "../api";
import { useUi } from "../ui";

/** Issues the client's read-only link (the "client app preview"). The token is shown once. */
export function ClientLink({ clientId }: { clientId: string }) {
  const { api, tr, me } = useUi();
  const [link, setLink] = useState<{ url: string; expires: string } | null>(null);
  const [message, setMessage] = useState("");
  if (!me?.capabilities.includes("flow.write")) return null;

  async function issue() {
    setMessage("");
    try {
      const r = await api.post<{ token: string; expires_at: string }>(`/api/clients/${clientId}/portal-link`);
      setLink({ url: `${window.location.origin}/portal#${r.token}`, expires: r.expires_at.slice(0, 10) });
    } catch (e) {
      setMessage(e instanceof ApiError ? e.code : tr("error"));
    }
  }

  return (
    <>
      <h3>{tr("clientView")}</h3>
      <button onClick={() => void issue()}>{tr("issueLink")}</button>
      {message && <p role="alert">{message}</p>}
      {link && (
        <p role="status">
          <a href={link.url} target="_blank" rel="noopener noreferrer">{tr("openPreview")}</a> · {tr("linkExpires")} {link.expires} · <small>{tr("linkOnce")}</small>
          <br /><input readOnly aria-label={tr("clientView")} value={link.url} onFocus={(e) => e.currentTarget.select()} />
        </p>
      )}
    </>
  );
}
