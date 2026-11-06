import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ApiError, type Campaign, type CampaignPreview } from "../api";
import { type Key } from "../i18n";
import { useUi } from "../ui";

const TYPES = ["regulatory", "renewal", "offer", "gap", "life_event"];

export function Campaigns() {
  const { api, tr, me } = useUi();
  const [list, setList] = useState<Campaign[] | null>(null);
  const [preview, setPreview] = useState<{ id: string; data: CampaignPreview } | null>(null);
  const [message, setMessage] = useState("");
  const [form, setForm] = useState({ name: "", type: "offer", benefit: "", body: "", base_language: "he", product: "" });
  const canApprove = me?.capabilities.includes("flow.approve") ?? false;

  const load = useCallback(() => api.campaigns().then(setList).catch(() => setMessage(tr("error"))), [api, tr]);
  useEffect(() => { void load(); }, [load]);

  async function run(action: () => Promise<unknown>) {
    setMessage("");
    try {
      await action();
      await load();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.code : tr("error"));  // the server's reason, e.g. translation_review_missing
    }
  }

  function create(e: FormEvent) {
    e.preventDefault();
    void run(async () => {
      await api.createCampaign({
        name: form.name, type: form.type, benefit: form.benefit, body: form.body, base_language: form.base_language,
        segment: form.product ? { has_product: [form.product.trim()] } : {},
      });
      setForm({ ...form, name: "", benefit: "", body: "" });
    });
  }

  return (
    <section aria-label={tr("campaigns")}>
      {message && <p role="alert">{message}</p>}
      <form onSubmit={create} aria-label={tr("newCampaign")}>
        <h2>{tr("newCampaign")}</h2>
        <label>{tr("campaignName")} <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required minLength={2} /></label>
        <label>{tr("campaignType")}{" "}
          <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
            {TYPES.map((x) => <option key={x} value={x}>{tr(`type.${x}` as Key)}</option>)}
          </select>
        </label>
        <label>{tr("benefit")} <input value={form.benefit} onChange={(e) => setForm({ ...form, benefit: e.target.value })} required /></label>
        <label>{tr("body")} <textarea value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} required minLength={3} /></label>
        <label>{tr("hasProduct")} <input value={form.product} onChange={(e) => setForm({ ...form, product: e.target.value })} /></label>
        <button type="submit">{tr("create")}</button>
      </form>

      {list && list.length === 0 && <p>{tr("noCampaigns")}</p>}
      <ul className="campaigns">
        {(list ?? []).map((c) => (
          <li key={c.id}>
            <strong><bdi>{c.name}</bdi></strong> · {tr(`type.${c.type}` as Key)} · <small>{c.status === "pending_approval" ? tr("pendingApproval") : c.status}</small>{" "}
            <button onClick={() => void run(async () => setPreview({ id: c.id, data: await api.previewCampaign(c.id) }))}>{tr("preview")}</button>
            {canApprove && c.status === "pending_approval" && <button onClick={() => void run(() => api.approveCampaign(c.id))}>{tr("approve")}</button>}
            {c.status === "draft" && <button onClick={() => void run(() => api.startCampaign(c.id))}>{tr("send")}</button>}
            {preview?.id === c.id && (
              <p role="status">
                {tr("matching")}: {preview.data.matching} · {tr("selected")}: {preview.data.selected} · {tr("excluded")}:{" "}
                {Object.entries(preview.data.excluded).map(([k, v]) => `${k} ${v}`).join(", ") || "0"}
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
