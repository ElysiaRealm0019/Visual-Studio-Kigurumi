import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("vite dev proxy", () => {
  const config = readFileSync(resolve(process.cwd(), "vite.config.ts"), "utf8");

  it("proxies /api requests to the backend", () => {
    expect(config).toMatch(/proxy:\s*\{\s*"\s*\/api\s*":\s*\{/);
  });

  it("keeps the /api prefix and does not rewrite the proxied path", () => {
    const apiProxy = config.match(/"\/api":\s*\{([\s\S]*?)\n\s*\},/);

    expect(apiProxy).not.toBeNull();
    if (!apiProxy) return;

    expect(apiProxy[1]).toMatch(/target:/);
    expect(apiProxy[1]).not.toMatch(/rewrite/);
  });
});
