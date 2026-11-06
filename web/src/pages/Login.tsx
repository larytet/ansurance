import { type FormEvent, useState } from "react";
import { ApiError } from "../api";
import { useUi } from "../ui";

export function Login() {
  const { api, setMe, tr } = useUi();
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
      <h1>{tr("app")}</h1>
      <form onSubmit={submit}>
        <label>
          {tr("email")}
          <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label>
          {tr("password")}
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        <label>
          {tr("code")}
          <input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} required />
        </label>
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={busy}>
          {tr("login")}
        </button>
      </form>
    </main>
  );
}
