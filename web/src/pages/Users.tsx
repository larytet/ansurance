import qrcode from "qrcode-generator";
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { ApiError } from "../api";
import { type Key } from "../i18n";
import { useUi } from "../ui";

const ROLES = ["agent", "backoffice", "marketing", "accountant", "manager"] as const;

interface UserRow { id: string; name: string; email: string; roles: string[]; active: boolean; auth_mode: "local" | "sso" }
interface Created { totp_uri?: string; recovery_codes?: string[] }

/** The shared secret inside an otpauth:// URI, shown as a typed-in fallback. */
export function setupKeyOf(uri: string): string {
  try {
    return new URL(uri).searchParams.get("secret") ?? "";
  } catch {
    return "";
  }
}

/** QR drawn in the browser: the secret never goes to a third-party service. */
function Qr({ text, label }: { text: string; label: string }) {
  const svg = useMemo(() => {
    const q = qrcode(0, "M");
    q.addData(text);
    q.make();
    return q.createSvgTag({ cellSize: 5, margin: 4, scalable: true });
  }, [text]);
  return <div className="qr" role="img" aria-label={label} dangerouslySetInnerHTML={{ __html: svg }} />;
}

export function TotpSetup({ name, uri, codes, onDone }: { name: string; uri: string; codes: string[]; onDone: () => void }) {
  const { tr } = useUi();
  const key = setupKeyOf(uri);
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(key);
      setCopied(true);
    } catch {
      /* the key stays visible and selectable */
    }
  }
  return (
    <section aria-label={tr("authSetup")} className="totp-setup">
      <h2>{tr("authSetup")}: <bdi>{name}</bdi></h2>
      <p>{tr("authSetupNote")}</p>
      <h3>{tr("scanQr")}</h3>
      <Qr text={uri} label={tr("qrLabel")} />
      <h3>{tr("onPhone")}</h3>
      <p><a className="button" href={uri}>{tr("openInApp")}</a></p>
      <h3>{tr("setupKey")}</h3>
      <p><code dir="ltr" className="setup-key">{key}</code>{" "}<button type="button" onClick={copy}>{copied ? tr("copied") : tr("copy")}</button></p>
      <h3>{tr("recoveryCodes")}</h3>
      <p>{tr("recoveryNote")}</p>
      <ul dir="ltr" className="recovery">{codes.map((c) => <li key={c}><code>{c}</code></li>)}</ul>
      <button type="button" onClick={onDone}>{tr("savedIt")}</button>
    </section>
  );
}

type Pending = "roles" | "deactivate" | "reactivate" | "reset" | "password" | null;

function UserActions({ u, onChanged, onSetup }: { u: UserRow; onChanged: () => void; onSetup: (name: string, extra: Created) => void }) {
  const { api, tr } = useUi();
  const [pending, setPending] = useState<Pending>(null);
  const [roles, setRoles] = useState<string[]>(u.roles);
  const [password, setPassword] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const local = u.auth_mode === "local";

  async function run(done: string, call: () => Promise<Created>) {
    setMsg(null);
    try {
      const extra = await call();
      setPending(null);
      setPassword("");
      onChanged();
      if (extra.totp_uri) onSetup(u.name, extra);
      else setMsg({ ok: true, text: done });
    } catch (err) {
      const code = err instanceof ApiError ? err.code : "error";
      setMsg({ ok: false, text: code === "last_manager" ? tr("last_manager") : code });
    }
  }
  const cancel = <button type="button" onClick={() => setPending(null)}>{tr("cancel")}</button>;
  return (
    <td>
      {pending === null && (
        <span className="row-actions">
          <button type="button" onClick={() => { setRoles(u.roles); setPending("roles"); }}>{tr("editRoles")}</button>{" "}
          {u.active && local && <button type="button" onClick={() => setPending("reset")}>{tr("resetAuth")}</button>}{" "}
          {u.active && local && <button type="button" onClick={() => setPending("password")}>{tr("setPassword")}</button>}{" "}
          <button type="button" onClick={() => setPending(u.active ? "deactivate" : "reactivate")}>{u.active ? tr("deactivate") : tr("reactivate")}</button>
        </span>
      )}
      {pending === "roles" && (
        <fieldset>
          <legend>{tr("editRoles")}: <bdi>{u.name}</bdi></legend>
          {ROLES.map((r) => (
            <label key={r}>
              <input type="checkbox" checked={roles.includes(r)} onChange={(e) => setRoles(e.target.checked ? [...roles, r] : roles.filter((x) => x !== r))} />{" "}
              {tr(`role.${r}` as Key)}
            </label>
          ))}
          <button type="button" disabled={roles.length === 0} onClick={() => run(tr("userUpdated"), () => api.patch<Created>(`/api/users/${u.id}`, { roles }))}>{tr("save")}</button> {cancel}
        </fieldset>
      )}
      {pending === "password" && (
        <form onSubmit={(e) => { e.preventDefault(); void run(tr("passwordSet"), async () => { await api.post(`/api/users/${u.id}/password`, { password }); return {}; }); }}>
          <label>{tr("newPassword")} <input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>{" "}
          <button type="submit">{tr("save")}</button> {cancel}
        </form>
      )}
      {pending === "reset" && (
        <span><p>{tr("resetNote")}</p><button type="button" onClick={() => run("", () => api.post<Created>(`/api/users/${u.id}/reset-authenticator`))}>{tr("confirmAction")}: {tr("resetAuth")}</button> {cancel}</span>
      )}
      {pending === "deactivate" && (
        <span><button type="button" onClick={() => run(tr("userUpdated"), () => api.patch<Created>(`/api/users/${u.id}`, { active: false }))}>{tr("confirmAction")}: {tr("deactivate")}</button> {cancel}</span>
      )}
      {pending === "reactivate" && (
        <span><p>{tr("reactivateNote")}</p><button type="button" onClick={() => run(tr("userUpdated"), () => api.patch<Created>(`/api/users/${u.id}`, { active: true }))}>{tr("confirmAction")}: {tr("reactivate")}</button> {cancel}</span>
      )}
      {msg && <p role={msg.ok ? "status" : "alert"}>{msg.text}</p>}
    </td>
  );
}

export function Users() {
  const { api, me, tr } = useUi();
  const [rows, setRows] = useState<UserRow[]>([]);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [roles, setRoles] = useState<string[]>(["agent"]);
  const [mode, setMode] = useState<"local" | "sso">("local");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ name: string; extra: Created } | null>(null);

  const load = useCallback(() => { api.get<UserRow[]>("/api/users").then(setRows).catch(() => undefined); }, [api]);
  useEffect(load, [load]);
  if (!me) return null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const extra = await api.post<Created>("/api/users", {
        email: email.trim(), display_name: name.trim(), roles, auth_mode: mode, password: mode === "local" ? password : null,
      });
      setCreated({ name: name.trim(), extra });
      setEmail(""); setName(""); setPassword("");
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.code : tr("loginFailed"));
    }
  }

  if (created?.extra.totp_uri) {
    return <TotpSetup name={created.name} uri={created.extra.totp_uri} codes={created.extra.recovery_codes ?? []} onDone={() => setCreated(null)} />;
  }
  return (
    <section aria-label={tr("users")}>
      <h2>{tr("addUser")}</h2>
      {created && <p role="status">{tr("userCreated")}</p>}
      <form onSubmit={submit}>
        <label>{tr("email")} <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
        <label>{tr("displayName")} <input value={name} onChange={(e) => setName(e.target.value)} required /></label>
        <fieldset>
          <legend>{tr("role")}</legend>
          {ROLES.map((r) => (
            <label key={r}>
              <input type="checkbox" checked={roles.includes(r)} onChange={(e) => setRoles(e.target.checked ? [...roles, r] : roles.filter((x) => x !== r))} />{" "}
              {tr(`role.${r}` as Key)}
            </label>
          ))}
        </fieldset>
        <label>{tr("signInMode")}{" "}
          <select value={mode} onChange={(e) => setMode(e.target.value as "local" | "sso")}>
            <option value="local">{tr("modeLocal")}</option>
            <option value="sso">{tr("modeSso")}</option>
          </select>
        </label>
        {mode === "local" && (
          <label>{tr("password")} <input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
        )}
        {error && <p role="alert">{error}</p>}
        <button type="submit" disabled={roles.length === 0}>{tr("createUser")}</button>
      </form>
      <h2>{tr("users")}</h2>
      <table>
        <tbody>
          {rows.map((u) => (
            <tr key={u.id}>
              <td><bdi>{u.name}</bdi>{!u.active && <> <em>({tr("inactive")})</em></>}</td>
              <td dir="ltr">{u.email}</td>
              <td>{u.roles.map((r) => tr(`role.${r}` as Key)).join(", ")}</td>
              <UserActions u={u} onChanged={load} onSetup={(n, extra) => setCreated({ name: n, extra })} />
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
