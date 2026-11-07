import { type FormEvent, useEffect, useState } from "react";
import { ApiError, type ClientRow } from "../api";
import { LANG_NAMES } from "../i18n";
import { useUi } from "../ui";

/** A transliteration is shown only when it differs from the name as written (spec 8.11). */
function translit(c: ClientRow, lang: string): string {
  const t = lang === "he" ? c.name_translit_he : c.name_translit_lat;
  return t && t !== c.name ? t : "";
}

interface Created { id: string; phone_matches: { id: string; name: string }[]; phone_shared_with_other_agent: boolean }
const LANG_CODES = ["he", "en", "ar", "ru", "fr"] as const;

export function Clients({ onOpen }: { onOpen: (id: string) => void }) {
  const { api, tr, lang, me } = useUi();
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<ClientRow[] | null>(null);
  const [idn, setIdn] = useState("");
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [language, setLanguage] = useState("he");
  const [error, setError] = useState<string | null>(null);
  const [made, setMade] = useState<Created | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const handle = setTimeout(() => {
      api.clients(q).then(setRows).catch(() => setRows([]));
    }, 250);
    return () => clearTimeout(handle);
  }, [api, q, version]);

  async function create(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setMade(null);
    try {
      const r = await api.post<Created>("/api/clients", {
        id_number: idn.trim(), name: name.trim(), phone: phone.trim() || null, email: email.trim() || null, preferred_language: language,
      });
      setMade(r);
      setIdn(""); setName(""); setPhone(""); setEmail("");
      setVersion((v) => v + 1);
    } catch (err) {
      const code = err instanceof ApiError ? err.code : "error";
      setError(code === "invalid_id_number" || code === "duplicate_id_number" ? tr(code) : code === "error" ? tr("error") : code);
    }
  }

  return (
    <section aria-label={tr("clients")}>
      {me?.capabilities.includes("client.write") && (
        <form onSubmit={create} aria-label={tr("newClient")}>
          <h2>{tr("newClient")}</h2>
          <label>{tr("idNumber")} <input dir="ltr" inputMode="numeric" value={idn} onChange={(e) => setIdn(e.target.value)} required /></label>{" "}
          <label>{tr("displayName")} <input value={name} onChange={(e) => setName(e.target.value)} required minLength={2} /></label>{" "}
          <label>{tr("phone")} <input dir="ltr" value={phone} onChange={(e) => setPhone(e.target.value)} /></label>{" "}
          <label>{tr("email")} <input type="email" dir="ltr" value={email} onChange={(e) => setEmail(e.target.value)} /></label>{" "}
          <label>{tr("language")}{" "}
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              {LANG_CODES.map((l) => <option key={l} value={l}>{LANG_NAMES[l]}</option>)}
            </select>
          </label>{" "}
          <button type="submit">{tr("createClient")}</button>
          {error && <p role="alert">{error}</p>}
          {made && (
            <div role="status">
              <p>{tr("clientCreated")} <button type="button" className="link" onClick={() => onOpen(made.id)}>{tr("openClient")}</button></p>
              {made.phone_matches.length > 0 && (
                <p>{tr("phoneSharedWarning")}{" "}
                  {made.phone_matches.map((m) => <button key={m.id} type="button" className="link" onClick={() => onOpen(m.id)}><bdi>{m.name}</bdi></button>)}
                </p>
              )}
              {made.phone_shared_with_other_agent && <p>{tr("phoneSharedOther")}</p>}
            </div>
          )}
        </form>
      )}
      <input type="search" aria-label={tr("search")} placeholder={tr("search")} value={q} onChange={(e) => setQ(e.target.value)} />
      {rows && rows.length === 0 && <p>{tr("noResults")}</p>}
      <ul className="clients">
        {(rows ?? []).map((c) => (
          <li key={c.id}>
            <button className="link" onClick={() => onOpen(c.id)}><bdi>{c.name}</bdi></button>{" "}
            <small>
              {translit(c, lang) && <><bdi>{translit(c, lang)}</bdi> · </>}
              {c.id_number} · {c.preferred_language}
            </small>
          </li>
        ))}
      </ul>
    </section>
  );
}
