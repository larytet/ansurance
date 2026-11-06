import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ApiError } from "../api";
import { type Key } from "../i18n";
import { useUi } from "../ui";

interface Msg { direction: "in" | "out"; channel: string; purpose?: string; status?: string; block_reason?: string | null; delivery?: string | null; body: string; at: string }

/** The conversation with one client. A send goes through the consent gate; a block is shown with its reason. */
export function Messages({ clientId }: { clientId: string }) {
  const { api, tr, me } = useUi();
  const [rows, setRows] = useState<Msg[] | null>(null);
  const [form, setForm] = useState({ channel: "whatsapp", purpose: "service", body: "" });
  const [message, setMessage] = useState("");
  const canSend = me?.capabilities.includes("message.send") ?? false;

  const load = useCallback(() => api.get<Msg[]>(`/api/clients/${clientId}/messages`).then(setRows).catch(() => setRows([])), [api, clientId]);
  useEffect(() => { void load(); }, [load]);

  async function send(e: FormEvent) {
    e.preventDefault();
    setMessage("");
    try {
      await api.post("/api/messages", { client_id: clientId, channel: form.channel, purpose: form.purpose, body: form.body });
      setForm({ ...form, body: "" });
    } catch (err) {
      const code = err instanceof ApiError ? err.code : "";
      setMessage(code === "opted_out" || code === "no_consent" ? tr(`reason.${code}` as Key) : code || tr("error"));
    }
    await load();
  }

  return (
    <>
      <h3>{tr("messages")}</h3>
      {message && <p role="alert">{message}</p>}
      {rows && rows.length === 0 && <p>{tr("noMessages")}</p>}
      <ul className="rows messages">
        {(rows ?? []).map((m, i) => (
          <li key={`${m.at}-${i}`} data-direction={m.direction} className={m.status === "blocked" ? "attention" : undefined}>
            <small>{m.at.slice(0, 16).replace("T", " ")} · {tr(`channel.${m.channel}` as Key)} · {m.direction === "in" ? tr("msgFromClient") : tr("msgToClient")}</small>{" "}
            <bdi>{m.body}</bdi>
            {m.status === "blocked" && <strong> · {tr("msgBlocked")}: {tr(`reason.${m.block_reason}` as Key)}</strong>}
            {m.status === "queued" && <em> · {tr("msgQueued")}</em>}
          </li>
        ))}
      </ul>
      {canSend && (
        <form onSubmit={(e) => void send(e)} aria-label={tr("sendMessage")}>
          <select aria-label={tr("purpose")} value={form.purpose} onChange={(e) => setForm({ ...form, purpose: e.target.value })}>
            {["service", "marketing", "legal"].map((x) => <option key={x} value={x}>{tr(`purpose.${x}` as Key)}</option>)}
          </select>
          <textarea aria-label={tr("body")} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} required />
          <button type="submit">{tr("sendMessage")}</button>
        </form>
      )}
    </>
  );
}
