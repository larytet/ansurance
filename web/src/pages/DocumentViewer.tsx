import { useEffect, useState } from "react";
import { useUi } from "../ui";

interface Pages { status: "pending" | "ready" | "refused"; pages: number; reason: string | null }

/** The only way a document is shown: page images made by the server in a sandbox.
 *  The file itself is never opened in the browser. */
export function DocumentViewer({ docId }: { docId: string }) {
  const { api, tr } = useUi();
  const [info, setInfo] = useState<Pages | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () => {
      api.get<Pages>(`/api/documents/${docId}/pages`).then((p) => {
        if (!alive) return;
        setInfo(p);
        if (p.status === "pending") timer = setTimeout(load, 2000);
      }).catch(() => { if (alive) setInfo({ status: "refused", pages: 0, reason: "error" }); });
    };
    load();
    return () => { alive = false; if (timer) clearTimeout(timer); };
  }, [api, docId]);

  if (!info || info.status === "pending") return <p role="status">{tr("rendering")}</p>;
  if (info.status === "refused") return <p role="alert">{tr("renderRefused")}</p>;
  return (
    <div className="doc-pages">
      {Array.from({ length: info.pages }, (_, i) => i + 1).map((n) => (
        <img key={n} src={`/api/documents/${docId}/pages/${n}`} alt={`${tr("pageN")} ${n}`} loading="lazy" />
      ))}
    </div>
  );
}
