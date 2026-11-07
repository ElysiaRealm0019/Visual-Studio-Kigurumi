import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, describe, it } from "node:test";

import { resolveStaticFile } from "./serverStatic.mjs";

const indexHtml = "<!doctype html><html><body><div id=\"root\"></div></body></html>";

describe("resolveStaticFile", () => {
  let distDir;

  before(() => {
    distDir = mkdtempSync(join(tmpdir(), "kig-server-static-"));
    writeFileSync(join(distDir, "index.html"), indexHtml);
    mkdirSync(join(distDir, "assets"));
    writeFileSync(join(distDir, "assets", "app-abc123.js"), "console.log(\"app\");");
  });

  after(() => {
    rmSync(distDir, { recursive: true, force: true });
  });

  it("returns dist/index.html for the root path", () => {
    assert.equal(resolveStaticFile("/", distDir), join(distDir, "index.html"));
  });

  it("falls back to index.html for frontend routes", () => {
    for (const route of ["/p/project-1", "/settings", "/blender", "/admin/audit", "/c/conversation-1"]) {
      assert.equal(resolveStaticFile(route, distDir), join(distDir, "index.html"), route);
    }
  });

  it("serves real static files instead of the SPA fallback", () => {
    assert.equal(resolveStaticFile("/assets/app-abc123.js", distDir), join(distDir, "assets", "app-abc123.js"));
  });

  it("returns null for missing assets that carry a file extension", () => {
    for (const missing of ["/missing.webp", "/assets/missing.js", "/styles/missing.css", "/models/missing.onnx"]) {
      assert.equal(resolveStaticFile(missing, distDir), null, missing);
    }
  });

  it("keeps backend-owned prefixes out of the SPA fallback", () => {
    assert.equal(resolveStaticFile("/static/fixtures/x.webp", distDir), null);
    assert.equal(resolveStaticFile("/static", distDir), null);
    assert.equal(resolveStaticFile("/generated/images/x.png", distDir), null);
    assert.equal(resolveStaticFile("/api/projects", distDir), null);
  });

  it("returns null for paths that try to escape the dist directory", () => {
    assert.equal(resolveStaticFile("/../outside.webp", distDir), null);
    assert.equal(resolveStaticFile("/..%2F..%2Foutside.webp", distDir), null);
  });

  it("returns null for malformed percent-encoding", () => {
    assert.equal(resolveStaticFile("/%E0%A4%A.webp", distDir), null);
  });

  it("returns null for frontend routes when index.html is missing", () => {
    const emptyDist = mkdtempSync(join(tmpdir(), "kig-server-static-empty-"));
    try {
      assert.equal(resolveStaticFile("/settings", emptyDist), null);
    } finally {
      rmSync(emptyDist, { recursive: true, force: true });
    }
  });
});
