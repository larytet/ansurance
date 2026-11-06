import { execFileSync } from "node:child_process";
import { createHmac } from "node:crypto";

const ROOT = new URL("../..", import.meta.url).pathname;
const COMPOSE = ["compose", "-f", `${ROOT}deploy/compose/docker-compose.yml`, "--env-file", `${ROOT}deploy/compose/.env`];
export const PASSWORD = "an-e2e-password-1";

export function totp(secret: string): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secret) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g)!.map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const h = createHmac("sha1", key).update(counter).digest();
  const o = h[h.length - 1]! & 15;
  const n = ((h[o]! & 0x7f) << 24) | (h[o + 1]! << 16) | (h[o + 2]! << 8) | h[o + 3]!;
  return String(n % 1_000_000).padStart(6, "0");
}

export function createUser(email: string, role: string): string {
  const out = execFileSync("docker", [...COMPOSE, "exec", "-T", "api", "python", "-m", "app.cli", "create-user",
    "--email", email, "--name", `E2E ${role}`, "--role", role, "--password", PASSWORD], { encoding: "utf8" });
  return /secret=([A-Z2-7]+)/.exec(out)![1]!;
}

