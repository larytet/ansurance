import { type FormEvent, useState } from "react";
import { ApiError } from "../api";
import { LANGS, LANG_NAMES, type Lang } from "../i18n";
import { useUi } from "../ui";

export function Login() {
  const { api, setMe, tr, lang, setLang } = useUi();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setMe(await api.login(email.trim(), password, code.trim()));
    } catch (err) {
      const locked = err instanceof ApiError && (err.status === 423 || err.status === 429);
      setError(locked ? tr("locked") : tr("loginFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <select value={lang} onChange={(e) => setLang(e.target.value as Lang)} aria-label={tr("language")} className="login-lang">
        {LANGS.map((l) => <option key={l} value={l}>{LANG_NAMES[l]}</option>)}
      </select>
      <h1>{tr("app")}</h1>
      <form onSubmit={submit} method="post" action="/api/auth/login">
        <label>
          {tr("email")}
          <input type="email" id="username" name="username" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          {tr("password")}
          <input type="password" id="password" name="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        <label>
          {tr("code")}
          <input inputMode="numeric" id="code" name="code" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} required />
        </label>
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={busy}>
          {tr("login")}
        </button>
      </form>
    </main>
  );
}
