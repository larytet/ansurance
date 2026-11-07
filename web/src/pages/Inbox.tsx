import { useCallback, useEffect, useState } from "react";
import { type Counts, type InboxItem } from "../api";
import { useUi } from "../ui";
import { LENS_SIZE, LensStrip, clampStart } from "./Lens";

type Pending = { id: string; kind: "dismiss" | "reassign" } | null;

export function Inbox() {
  const { api, tr } = useUi();
  const [items, setItems] = useState<InboxItem[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [counts, setCounts] = useState<Counts | null>(null);
  const [urgency, setUrgency] = useState("");
  const [pending, setPending] = useState<Pending>(null);
  const [text, setText] = useState("");
  const [role, setRole] = useState("backoffice");
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [lens, setLens] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const [page, c] = await Promise.all([api.inbox({ urgency: urgency || undefined }), api.counts()]);
      setItems(page.items);
      setNext(page.next);
      setCounts(c);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [api, urgency]);

  useEffect(() => {
    void load();
  }, [load]);

  async function more() {
    if (!next) return;
    const page = await api.inbox({ urgency: urgency || undefined, cursor: next });
    setItems((cur) => [...cur, ...page.items]);
    setNext(page.next);
  }

  async function run(action: () => Promise<unknown>) {
    try {
      await action();
      setPending(null);
      setText("");
      await load();
    } catch {
      setError(true);
    }
  }

  const start = clampStart(lens, items.length);
  const roles = ["agent", "backoffice", "marketing", "manager"];
  return (
    <section aria-label={tr("inbox")}>
      {counts && (
        <dl className="counts">
          <div><dt>{tr("open")}</dt><dd data-testid="count-open">{counts.open}</dd></div>
          <div><dt>{tr("urgent")}</dt><dd data-testid="count-urgent">{counts.urgent}</dd></div>
          <div><dt>{tr("doneToday")}</dt><dd data-testid="count-done">{counts.done_today}</dd></div>
        </dl>
      )}
      <div className="filters" role="group">
        {(["", "urgent", "important", "normal"] as const).map((u) => (
          <button key={u || "all"} aria-pressed={urgency === u} onClick={() => setUrgency(u)}>
            {u === "" ? tr("allUrgencies") : tr(u)}
          </button>
        ))}
      </div>
      {error && <p role="alert">{tr("error")}</p>}
      {loading && <p>{tr("loading")}</p>}
      {!loading && items.length === 0 && <p>{tr("empty")}</p>}
      {items.length > LENS_SIZE && (
        <LensStrip urgencies={items.map((i) => i.urgency)} start={start} onChange={setLens} />
      )}
      <ul className="items">
        {items.map((it, idx) => idx < start || idx >= start + LENS_SIZE ? (
          <li key={it.id} className={`squashed ${it.urgency}`} data-urgency={it.urgency}>
            <button type="button" aria-label={it.title} title={it.title} onClick={() => setLens(clampStart(idx - Math.floor(LENS_SIZE / 2), items.length))} />
          </li>
        ) : (
          <li key={it.id} className={`item ${it.urgency}`} data-urgency={it.urgency}>
            <div className="title">{it.title}</div>
            {it.detail && <div className="detail">{it.detail}</div>}
            <div className="actions">
              <button onClick={() => run(() => api.done(it.id))}>{tr("done")}</button>
              <button onClick={() => setPending({ id: it.id, kind: "dismiss" })}>{tr("dismiss")}</button>
              <button onClick={() => setPending({ id: it.id, kind: "reassign" })}>{tr("reassign")}</button>
            </div>
            {pending?.id === it.id && (
              <form
                className="inline"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (text.trim().length < 3) return; // the server requires 3 characters
                  void run(() =>
                    pending.kind === "dismiss" ? api.dismiss(it.id, text) : api.reassign(it.id, role, text),
                  );
                }}
              >
                {pending.kind === "reassign" && (
                  <select aria-label={tr("role")} value={role} onChange={(e) => setRole(e.target.value)}>
                    {roles.map((r) => (
                      <option key={r} value={r}>{tr(`role.${r}` as never)}</option>
                    ))}
                  </select>
                )}
                <input
                  aria-label={pending.kind === "dismiss" ? tr("reason") : tr("note")}
                  placeholder={pending.kind === "dismiss" ? tr("reason") : tr("note")}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  minLength={3}
                  required
                />
                <button type="submit">{tr("confirm")}</button>
                <button type="button" onClick={() => setPending(null)}>{tr("cancel")}</button>
              </form>
            )}
          </li>
        ))}
      </ul>
      {next && <button onClick={() => void more()}>{tr("more")}</button>}
    </section>
  );
}
