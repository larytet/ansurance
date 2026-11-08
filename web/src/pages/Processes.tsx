import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { type Key } from "../i18n";
import { BOX_H, BOX_W, positions, layers } from "../editor/model";
import { useUi } from "../ui";

interface Bi { en: string; he: string }
interface Check { text: Bi; kind: string }
interface StageDef { id: string; name: Bi; role: string; depends_on: string[]; checks: Check[] }
interface Definition { key: string; name: Bi; owner_role: string; stages: StageDef[] }
interface DefRow { key: string; name: Bi; source_flow: string; stages: number; checks: number }

export { layers };

// the editor and its library are loaded only when someone opens it
const ProcessEditor = lazy(() => import("../editor/ProcessEditor").then((m) => ({ default: m.ProcessEditor })));

function Diagram({ def, lang }: { def: Definition; lang: "he" | "en" }) {
  const { tr } = useUi();
  const geo = useMemo(() => {
    const pos = positions(def.stages);
    const cols = Math.max(...layers(def.stages).values(), 0) + 1;
    const colRows = new Map<number, number>();
    const depth = layers(def.stages);
    for (const s of def.stages) colRows.set(depth.get(s.id) ?? 0, (colRows.get(depth.get(s.id) ?? 0) ?? 0) + 1);
    const tallest = Math.max(...colRows.values(), 1);
    return { pos, w: cols * (BOX_W + 64) - 64, h: tallest * (BOX_H + 20) - 20 };
  }, [def]);
  return (
    <svg role="img" aria-label={def.name[lang]} viewBox={`-2 -2 ${geo.w + 4} ${geo.h + 4}`} width={geo.w + 4} height={geo.h + 4} className="diagram" style={{ direction: "ltr" }}>
      {def.stages.flatMap((s) =>
        s.depends_on.map((d) => {
          const a = geo.pos.get(d);
          const b = geo.pos.get(s.id);
          if (!a || !b) return null;
          const x1 = a.x + BOX_W, y1 = a.y + BOX_H / 2, x2 = b.x, y2 = b.y + BOX_H / 2, mx = (x1 + x2) / 2;
          return <path key={`${d}-${s.id}`} data-edge={`${d}>${s.id}`} d={`M${x1} ${y1} C${mx} ${y1} ${mx} ${y2} ${x2} ${y2}`} fill="none" stroke="currentColor" strokeWidth="1.5" />;
        }),
      )}
      {def.stages.map((s) => {
        const p = geo.pos.get(s.id) ?? { x: 0, y: 0 };
        const name = s.name[lang] ?? s.name.en;
        return (
          <g key={s.id} data-stage={s.id} transform={`translate(${p.x} ${p.y})`}>
            <title>{name}</title>
            <rect width={BOX_W} height={BOX_H} rx="8" stroke="currentColor" strokeWidth="1.5" />
            <text x={BOX_W / 2} y={BOX_H / 2 - 4} textAnchor="middle" fontSize="12">{name.length > 24 ? `${name.slice(0, 23)}…` : name}</text>
            <text x={BOX_W / 2} y={BOX_H / 2 + 14} textAnchor="middle" fontSize="10" opacity="0.7">{tr(`role.${s.role}` as Key)}</text>
          </g>
        );
      })}
    </svg>
  );
}

/** Process definitions: the pipeline drawn from the data, the checklists under it, and the
 * editable Mermaid source (opened elsewhere only when the user follows the link). */
export function Processes() {
  const { api, tr, lang, me } = useUi();
  const dl: "he" | "en" = lang === "he" ? "he" : "en";
  const [editing, setEditing] = useState(false);
  const [rev, setRev] = useState(0);
  const [defs, setDefs] = useState<DefRow[]>([]);
  const [key, setKey] = useState("");
  const [def, setDef] = useState<Definition | null>(null);
  const [source, setSource] = useState("");
  const [error, setError] = useState(false);

  useEffect(() => {
    api.get<DefRow[]>("/api/process-defs").then((r) => { setDefs(r); setKey((k) => k || r[0]?.key || ""); }).catch(() => setError(true));
  }, [api]);
  useEffect(() => {
    if (!key) return;
    api.get<{ definition: Definition }>(`/api/process-defs/${key}`).then((r) => setDef(r.definition)).catch(() => setError(true));
  }, [api, key, rev]);
  useEffect(() => {
    if (!key) return;
    api.getText(`/api/process-defs/${key}/mermaid?lang=${dl}`).then(setSource).catch(() => setSource(""));
  }, [api, key, dl]);

  const live = source ? `https://mermaid.live/edit#base64:${btoa(unescape(encodeURIComponent(JSON.stringify({ code: source, mermaid: "{}" }))))}` : "";
  return (
    <section aria-label={tr("processes")}>
      {error && <p role="alert">{tr("error")}</p>}
      <label>{tr("processes")}{" "}
        <select value={key} onChange={(e) => setKey(e.target.value)}>
          {defs.map((d) => <option key={d.key} value={d.key}>{d.name[dl]} ({d.stages})</option>)}
        </select>
      </label>
      {def && me?.capabilities.includes("process.define") && !editing && (
        <> <button onClick={() => setEditing(true)}>{tr("editDef")}</button></>
      )}
      {editing && key && (
        <Suspense fallback={<p>{tr("loading")}</p>}>
          <ProcessEditor defKey={key} onClose={() => setEditing(false)} onPublished={() => setRev((n) => n + 1)} />
        </Suspense>
      )}
      {def && (
        <>
          <p>{tr("processNote")}</p>
          <div className="diagram-box"><Diagram def={def} lang={dl} /></div>
          <h3>{tr("checklists")}</h3>
          <ol>
            {def.stages.map((s) => (
              <li key={s.id}>
                <strong>{s.name[dl]}</strong> · {tr(`role.${s.role}` as Key)}
                {s.depends_on.length > 0 && <> · {tr("after")}: {s.depends_on.join(", ")}</>}
                <ul>
                  {s.checks.map((c, i) => <li key={i} className={c.kind === "heading" ? "heading" : undefined}>{c.kind === "heading" ? <em>{c.text[dl]}</em> : c.text[dl]}</li>)}
                </ul>
              </li>
            ))}
          </ol>
          {source && (
            <>
              <h3>{tr("mermaidSource")}</h3>
              <textarea readOnly dir="ltr" rows={8} value={source} aria-label={tr("mermaidSource")} />
              <p><a href={live} target="_blank" rel="noreferrer noopener">{tr("openMermaid")}</a> <small>{tr("mermaidNote")}</small></p>
            </>
          )}
        </>
      )}
    </section>
  );
}
