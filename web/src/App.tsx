import { useEffect, useState } from "react";
import { type Health, fetchHealth } from "./api";

type State = { kind: "loading" } | { kind: "ok"; health: Health } | { kind: "error" };

export function App({ fetcher }: { fetcher?: typeof fetch }) {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    fetchHealth(fetcher)
      .then((health) => setState({ kind: "ok", health }))
      .catch(() => setState({ kind: "error" }));
  }, [fetcher]);

  return (
    <main>
      <h1>ansurance</h1>
      {state.kind === "loading" && <p>Checking the server…</p>}
      {state.kind === "ok" && (
        <p data-testid="status">
          Server {state.health.status}, version {state.health.version}, profile{" "}
          {state.health.profile}
        </p>
      )}
      {state.kind === "error" && <p role="alert">The server is not reachable.</p>}
    </main>
  );
}
