import { isTurnaroundRole } from "../agent/agentApi";
import { detectAnimeLandmarks } from "../editor/deformation/animeLandmarkDetector";
import { eyeLandmarkConfidences } from "../editor/deformation/animeLandmarkMapping";
import { analyzeEyeSymmetry, renderEyeSymmetry, type EyeSymmetryPlan } from "../editor/deformation/eyeSymmetry";

/**
 * Roles that are generated from the user's references or from a previous version, and can therefore inherit an uneven
 * eye pair from them. Reference uploads are never touched.
 */
export function supportsEyeSymmetryCorrection(role: string | undefined): boolean {
  return role === "design" || role === "design_turnaround" || role === "front" || role === "turnaround";
}

/** The front view is the leftmost head of a four-view sheet; the detector is pinned to that panel. */
const turnaroundRoiWidthShare = 0.26;

/** The version endpoint rejects larger uploads, so a very detailed sheet keeps its original pixels instead. */
const maxCorrectedBytes = 19 * 1024 * 1024;

export type EyeSymmetryCorrection = {
  blob: Blob;
  height: number;
  plan: EyeSymmetryPlan;
  width: number;
};

/**
 * Measures the generated image's eye pair and, when it is uneven, returns the pixels of a corrected copy whose eyes are
 * one mirrored pair. Returns null whenever there is nothing to fix or the eyes cannot be located confidently enough to
 * rewrite pixels - a wrong correction would be worse than the asymmetry.
 *
 * The correction is deterministic and costs no image generation.
 */
export async function correctEyeSymmetry(options: {
  imageUrl: string;
  role?: string;
  signal?: AbortSignal;
}): Promise<EyeSymmetryCorrection | null> {
  if (!supportsEyeSymmetryCorrection(options.role)) return null;
  const signal = options.signal;
  const image = await loadImageElement(options.imageUrl, signal);
  const width = image.naturalWidth || image.width;
  const height = image.naturalHeight || image.height;
  if (width <= 1 || height <= 1) return null;

  const regionOfInterest = isTurnaroundRole(options.role)
    ? { height, width: width * turnaroundRoiWidthShare, x: 0, y: 0 }
    : undefined;
  // Strict single-face mode on purpose: it rejects a weak landmark fit instead of guessing, which is what we need
  // before rewriting pixels.
  const detection = await detectAnimeLandmarks(image, signal, true, regionOfInterest);
  if (!detection || signal?.aborted) return null;

  const plan = analyzeEyeSymmetry(detection.controls, eyeLandmarkConfidences(detection.debug.points));
  if (!plan) return null;

  const canvas = renderEyeSymmetry(image, plan, { height, width });
  const blob = await canvasToPngBlob(canvas);
  if (signal?.aborted || blob.size > maxCorrectedBytes) return null;

  return { blob, height, plan, width };
}

function loadImageElement(url: string, signal?: AbortSignal): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const abort = () => {
      image.onload = null;
      image.onerror = null;
      reject(new DOMException("Eye symmetry correction was cancelled", "AbortError"));
    };
    if (signal?.aborted) {
      abort();
      return;
    }
    signal?.addEventListener("abort", abort, { once: true });
    image.onload = () => {
      signal?.removeEventListener("abort", abort);
      resolve(image);
    };
    image.onerror = () => {
      signal?.removeEventListener("abort", abort);
      reject(new Error("Unable to load the generated image for the eye symmetry correction"));
    };
    // The source endpoint is same-origin through the app's API proxy, so the canvas stays readable.
    image.src = url;
  });
}

function canvasToPngBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) {
        resolve(blob);
        return;
      }
      reject(new Error("Unable to encode the corrected image"));
    }, "image/png");
  });
}
