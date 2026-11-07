import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

function readConfig(): string {
  return readFileSync(resolve(process.cwd(), "nginx.conf"), "utf8");
}

function locationBlock(config: string, header: RegExp): string {
  const match = config.match(header);

  if (!match || match.index === undefined) {
    throw new Error(`location block not found for ${header}`);
  }

  const start = config.indexOf("{", match.index);
  if (start === -1) {
    throw new Error(`location block has no opening brace for ${header}`);
  }

  let depth = 0;
  for (let index = start; index < config.length; index += 1) {
    const char = config[index];

    if (char === "{") {
      depth += 1;
    } else if (char === "}") {
      depth -= 1;
      if (depth === 0) {
        return config.slice(start + 1, index);
      }
    }
  }

  throw new Error(`unbalanced braces in location block for ${header}`);
}

function proxyPassTarget(block: string): string {
  const match = block.match(/\bproxy_pass\s+([^;]+);/);

  if (!match) {
    throw new Error("proxy_pass directive not found in location block");
  }

  return match[1].trim();
}

const sharedProxyDirectives = [
  "proxy_set_header Host $host;",
  "proxy_set_header X-Real-IP $remote_addr;",
  "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
  "proxy_set_header X-Forwarded-Proto $scheme;",
  "proxy_read_timeout 1800s;",
  "proxy_send_timeout 1800s;",
];

describe("nginx production config", () => {
  it("allows local revision multipart image payloads larger than the nginx 1MB default", () => {
    const config = readConfig();
    const match = config.match(/\bclient_max_body_size\s+(\d+)([mMgG])\s*;/);

    expect(match).not.toBeNull();
    if (!match) return;

    const size = Number(match[1]);
    const unit = match[2].toLowerCase();
    const sizeInMb = unit === "g" ? size * 1024 : size;

    expect(sizeInMb).toBeGreaterThanOrEqual(25);
  });

  it("routes a bare /api to the backend instead of the SPA fallback", () => {
    const block = locationBlock(readConfig(), /\blocation\s*=\s*\/api\s*\{/);
    const normalized = block.replace(/\s+/g, " ");

    expect(proxyPassTarget(block)).toBe("http://api:8000");

    for (const directive of sharedProxyDirectives) {
      expect(normalized).toContain(directive);
    }
  });

  it("keeps forwarding the full /api/ URI without stripping the prefix", () => {
    const block = locationBlock(readConfig(), /\blocation\s+\/api\/\s*\{/);
    const target = proxyPassTarget(block);

    expect(target).toBe("http://api:8000");
    expect(target).not.toMatch(/^http:\/\/api:8000\//);
  });
});
