import { existsSync, statSync } from "node:fs";
import { extname, join, normalize, resolve, sep } from "node:path";

const spaFallbackName = "index.html";

const backendOwnedPrefixes = ["/api", "/static", "/generated"];

function decodePathname(pathname) {
  try {
    return decodeURIComponent(pathname);
  } catch {
    return null;
  }
}

function isBackendOwnedPath(pathname) {
  return backendOwnedPrefixes.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

function isDirectory(filePath) {
  return existsSync(filePath) && statSync(filePath).isDirectory();
}

function isFile(filePath) {
  return existsSync(filePath) && statSync(filePath).isFile();
}

export function resolveStaticFile(pathname, distDir) {
  const decodedPath = decodePathname(pathname);
  if (decodedPath === null) return null;
  if (isBackendOwnedPath(decodedPath)) return null;

  const normalizedPath = normalize(decodedPath).replace(/^(\.\.(\/|\\|$))+/, "");
  const relativePath = normalizedPath === sep ? "" : normalizedPath.replace(/^[/\\]+/, "");
  let filePath = resolve(distDir, relativePath);

  if (!filePath.startsWith(distDir + sep) && filePath !== distDir) return null;
  if (isDirectory(filePath)) {
    filePath = join(filePath, spaFallbackName);
  }
  if (isFile(filePath)) return filePath;

  if (extname(relativePath) !== "") return null;

  const spaFallback = resolve(distDir, spaFallbackName);
  return isFile(spaFallback) ? spaFallback : null;
}
