import { type FormEvent, useState } from "react";
import { ApiError } from "../api";
import { LANGS, LANG_NAMES, type Key, type Lang } from "../i18n";
import { useUi } from "../ui";

function ChangePassword() {
  const { api, tr } = useUi();
  const [current, setCurrent] = useState("");
  const [code, setCode] = useState("");
  const [next, setNext] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      await api.post("/api/auth/password", { current_password: current, code, new_password: next });
      setCurrent(""); setCode(""); setNext("");
      setMsg({ ok: true, text: tr("passwordChanged") });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError && err.status === 422 ? err.code : tr("loginFailed") });
    }
  }
  return (
    <form onSubmit={submit} aria-label={tr("changePassword")}>
      <h2>{tr("changePassword")}</h2>
      <label>{tr("currentPassword")} <input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required /></label>
      <label>{tr("authCode")} <input inputMode="numeric" autoComplete="one-time-code" dir="ltr" value={code} onChange={(e) => setCode(e.target.value)} required /></label>
      <label>{tr("newPassword")} <input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required /></label>
      {msg && <p role={msg.ok ? "status" : "alert"}>{msg.text}</p>}
      <button type="submit">{tr("changePassword")}</button>
    </form>
  );
}

export function Settings() {
  const { me, tr, lang, setLang, theme, setTheme } = useUi();
  if (!me) return null;
  return (
    <section aria-label={tr("settings")}>
      <h2>{tr("account")}</h2>
      <p><bdi>{me.name}</bdi> · {me.email} · {me.roles.map((r) => tr(`role.${r}` as Key)).join(", ")}</p>
      <label>{tr("language")}{" "}
        <select value={lang} onChange={(e) => setLang(e.target.value as Lang)}>
          {LANGS.map((l) => <option key={l} value={l}>{LANG_NAMES[l]}</option>)}
        </select>
      </label>
      <label>{tr("theme")}{" "}
        <select value={theme} onChange={(e) => setTheme(e.target.value as "light" | "dark")}>
          <option value="light">{tr("light")}</option>
          <option value="dark">{tr("dark")}</option>
        </select>
      </label>
      {me.auth_mode === "local" && <ChangePassword />}
      <h2>{tr("capabilities")}</h2>
      <ul className="caps">{me.capabilities.map((c) => <li key={c}><code>{c}</code></li>)}</ul>
    </section>
  );
}
