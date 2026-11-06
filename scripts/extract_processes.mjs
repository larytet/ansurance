// Extract the PROCS table from the mock (index.html) into normalized JSON for the backend.
// Usage: node scripts/extract_processes.mjs > backend/app/processes/seed/processes.json
import { readFileSync } from "node:fs";
import vm from "node:vm";

const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const start = html.indexOf("var PROCS=[");
if (start < 0) throw new Error("PROCS not found in index.html");
// the table ends at the first line that is exactly "];" after the start
const end = html.indexOf("\n];", start);
const code = html.slice(start, end + 3);
const sandbox = {};
vm.runInNewContext(code, sandbox);
const ROLE = { agent: "agent", back: "backoffice", mgr: "manager", mkt: "marketing" };
const KIND = { f: "fill", v: "verify", s: "send" };

const bi = (s) => {
  const [en, he] = s.split("|");
  return { en: en.trim(), he: (he ?? en).trim() };
};

const out = sandbox.PROCS.map((p) => ({
  id: p.id,
  source_flow: p.src,
  owner_role: ROLE[p.o] ?? p.o,
  name: bi(p.n),
  stages: p.st.map((s, i) => {
    const [id, name, role, deps, checks] = s;
    // deps: undefined/null = previous stage (locked order), [] = can start at once
    const depends_on = deps == null ? (i === 0 ? [] : [p.st[i - 1][0]]) : deps;
    return {
      id,
      name: bi(name),
      role: ROLE[role] ?? role,
      depends_on,
      checks: checks.map((c) => {
        const parts = c.split("|");
        const heading = parts[0].startsWith("#");
        const text = bi(parts.slice(0, 2).join("|").replace(/^#/, ""));
        const kindKey = parts[2] ?? "";
        const verifyNote = /\[verify\]/i.test(c);
        return { text, kind: heading ? "heading" : (KIND[kindKey] ?? "do"), verify_note: verifyNote };
      }),
    };
  }),
}));
process.stdout.write(JSON.stringify(out, null, 1) + "\n");
