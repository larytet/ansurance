// The process editor and its canvas library are loaded only when someone opens the editor,
// so no other screen pays for them. Run after `npm run build`.
import { readdirSync, readFileSync } from "node:fs";

const dir = new URL("../dist/assets/", import.meta.url);
const files = readdirSync(dir).filter((f) => f.endsWith(".js"));
const entry = files.filter((f) => f.startsWith("index-"));
const marker = "react-flow"; // the library's class names appear in whichever chunk contains it
const holders = files.filter((f) => readFileSync(new URL(f, dir), "utf8").includes(marker));
const bad = holders.filter((f) => entry.includes(f));
if (holders.length === 0) { console.error("the editor chunk was not found: is the library bundled at all?"); process.exit(1); }
if (bad.length > 0) { console.error(`the canvas library is in the main bundle: ${bad.join(", ")}`); process.exit(1); }
console.log(`ok: the canvas library is only in ${holders.join(", ")} (main bundle: ${entry.join(", ")})`);
