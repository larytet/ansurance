import { useEffect, useState } from "react";
import { type ClientRow } from "../api";
import { useUi } from "../ui";

/** A transliteration is shown only when it differs from the name as written (spec 8.11). */
function translit(c: ClientRow, lang: string): string {
  const t = lang === "he" ? c.name_translit_he : c.name_translit_lat;
  return t && t !== c.name ? t : "";
}

export function Clients({ onOpen }: { onOpen: (id: string) => void }) {
  const { api, tr, lang } = useUi();
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<ClientRow[] | null>(null);

  useEffect(() => {
    const handle = setTimeout(() => {
      api.clients(q).then(setRows).catch(() => setRows([]));
    }, 250);
    return () => clearTimeout(handle);
  }, [api, q]);

  return (
    <section aria-label={tr("clients")}>
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
