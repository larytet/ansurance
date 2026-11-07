import { useState, type FormEvent } from "react";
import { ApiError, type ClientRow } from "../api";
import { LANGS, LANG_NAMES, type Lang } from "../i18n";
import { useUi } from "../ui";

interface Line { who: "caller" | "agent"; original: string; translated: string; flagged: string[]; notice: string }
interface Tr { translated: string; notice: string; needs_human_check: string[] }

/** A call panel without speech recognition: each line (typed or pasted) is translated, both texts are kept
 *  side by side in the client's timeline, and amounts, dates and coverage words are flagged for a person. */
export function LiveCall() {
  const { api, tr, lang } = useUi();
  const [callerLang, setCallerLang] = useState<Lang>("ru");
  const [agentLang, setAgentLang] = useState<Lang>(lang);
  const [client, setClient] = useState<ClientRow | null>(null);
  const [q, setQ] = useState("");
  const [found, setFound] = useState<ClientRow[]>([]);
  const [who, setWho] = useState<"caller" | "agent">("caller");
  const [text, setText] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [message, setMessage] = useState("");

  async function search(v: string) {
    setQ(v);
    setFound(v.trim().length >= 2 ? await api.clients(v).catch(() => []) : []);
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    setMessage("");
    const [source, target] = who === "caller" ? [callerLang, agentLang] : [agentLang, callerLang];
    if (source === target) {
      setLines((l) => [...l, { who, original: text, translated: text, flagged: [], notice: "" }]);
      setText("");
      return;
    }
    try {
      const r = await api.post<Tr>("/api/translate", { text, source, target, client_id: client?.id });
      setLines((l) => [...l, { who, original: text, translated: r.translated, flagged: r.needs_human_check, notice: r.notice }]);
      setText("");
    } catch (err) {
      setMessage(err instanceof ApiError ? err.code : tr("error"));
    }
  }

  const pickLang = (value: Lang, set: (l: Lang) => void, label: string) => (
    <label>{label}{" "}
      <select value={value} onChange={(e) => set(e.target.value as Lang)}>
        {LANGS.map((l) => <option key={l} value={l}>{LANG_NAMES[l]}</option>)}
      </select>
    </label>
  );

  return (
    <section aria-label={tr("liveCall")}>
      <p><small>{tr("liveCallNote")}</small></p>
      {message && <p role="alert">{message}</p>}
      {pickLang(callerLang, setCallerLang, tr("callerLanguage"))}
      {pickLang(agentLang, setAgentLang, tr("agentLanguage"))}
      <div>
        {client ? (
          <p>{tr("clients")}: <bdi>{client.name}</bdi> <button onClick={() => setClient(null)}>{tr("cancel")}</button></p>
        ) : (
          <>
            <input type="search" aria-label={tr("callClient")} placeholder={tr("callClient")} value={q} onChange={(e) => void search(e.target.value)} />
            <ul>{found.slice(0, 5).map((c) => <li key={c.id}><button className="link" onClick={() => { setClient(c); setFound([]); setQ(""); }}><bdi>{c.name}</bdi></button></li>)}</ul>
          </>
        )}
      </div>
      <ol className="transcript">
        {lines.map((l, i) => (
          <li key={i} data-who={l.who} className={l.flagged.length > 0 ? "attention" : undefined}>
            <strong>{l.who === "caller" ? tr("caller") : tr("agent")}</strong>: <bdi>{l.original}</bdi> → <bdi>{l.translated}</bdi>
            {l.flagged.length > 0 && <em> · {tr("checkWithPerson")}: {l.flagged.join(", ")}</em>}
            {l.notice && <small> · {l.notice}</small>}
          </li>
        ))}
      </ol>
      <form onSubmit={(e) => void add(e)} aria-label={tr("addLine")}>
        <select aria-label={tr("speaker")} value={who} onChange={(e) => setWho(e.target.value as "caller" | "agent")}>
          <option value="caller">{tr("caller")}</option>
          <option value="agent">{tr("agent")}</option>
        </select>
        <textarea aria-label={tr("lineText")} value={text} onChange={(e) => setText(e.target.value)} required />
        <button type="submit">{tr("addLine")}</button>
      </form>
    </section>
  );
}
