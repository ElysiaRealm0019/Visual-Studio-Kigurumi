import type { LandmarkPoint, ManualLandmarks } from "./landmarks";

export type EyeSymmetrySide = "left" | "right";

/**
 * Everything needed to rebuild an uneven eye pair as one mirrored pair.
 *
 * All values are image-normalized, so one plan describes the correction for any resolution of the same artwork, and a
 * plan can be stored in a version's recipe and replayed later.
 */
export type EyeSymmetryPlan = {
  /** Face centreline the corrected pair is mirrored across. */
  axisX: number;
  /** Shared height both eye centres are moved to. */
  eyeY: number;
  /** Half the distance between the two corrected eye centres, kept from the detected pair. */
  halfSpacing: number;
  patchHalfWidth: number;
  patchHalfHeight: number;
  /** Eye whose pixels become the source of both corrected eyes; the other one is replaced by its mirror. */
  master: EyeSymmetrySide;
  /** Detected centre of the master eye: the patch is cropped here (in the master's own orientation). */
  masterCenter: LandmarkPoint;
  /** How uneven the detected pair was, so a caller can explain the correction. */
  heightDelta: number;
  spacingDelta: number;
  sizeRatio: number;
};

/**
 * When an uneven pair counts as needing correction.
 *
 * `heightDelta` is a fraction of the image height: on the 800x1100 front canvas it is about 7 px, roughly a tenth of an
 * eye's height, which is where an offset starts to read as a mistake instead of a style choice. The thresholds are
 * deliberately above landmark-detection noise, so an already symmetric pair never produces a correction.
 */
export const eyeSymmetryThresholds = {
  heightDelta: 0.006,
  /** Off-centre pair, relative to the distance between the eyes. */
  spacingDelta: 0.15,
  /** Relative difference between the two detected eye regions. */
  sizeRatio: 0.2,
  /** Patch size around the master eye's detected region: enough to cover the lashes and the lid edge. */
  patchScale: 1.35,
  /** The patch never reaches past this share of the distance to the centreline, so the two patches cannot overlap. */
  patchShareOfSpacing: 0.92,
  /** Fallback patch half sizes, relative to the distance between the eyes, when no eye region was detected. */
  fallbackPatchShare: 0.34,
  /** Outer share of the patch that fades into the original pixels, so no hard seam is left behind. */
  feather: 0.35,
} as const;

export type EyeSymmetryConfidence = {
  left: number;
  right: number;
};

/** Per-eye detector confidence, used to keep the more trustworthy eye as the mirrored master. */
const confidenceAdvantage = 0.08;

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function round(value: number) {
  return Number(value.toFixed(4));
}

/**
 * Reads one detected eye pair and returns the correction that makes it mirror-symmetric, or null when the pair is
 * already level, equally spaced and equally sized within tolerance.
 *
 * The master eye is the one a human eye would trust more: the more confidently detected one, or, without confidence
 * information, the larger region - the other eye is usually the one foreshortened or partly hidden.
 */
export function analyzeEyeSymmetry(
  landmarks: ManualLandmarks,
  confidence?: EyeSymmetryConfidence,
): EyeSymmetryPlan | null {
  const { leftEye, rightEye } = landmarks;
  const eyeDistance = Math.abs(rightEye.x - leftEye.x);
  if (!(eyeDistance > 0.02)) return null;

  const heightDelta = Math.abs(leftEye.y - rightEye.y);
  const eyeCenterX = (leftEye.x + rightEye.x) / 2;
  const axisX = faceCenterline(landmarks, eyeCenterX);
  const spacingDelta = Math.abs(Math.abs(axisX - leftEye.x) - Math.abs(rightEye.x - axisX)) / eyeDistance;
  const sizeRatio = eyeRegionSizeRatio(landmarks);

  const needsCorrection =
    heightDelta > eyeSymmetryThresholds.heightDelta ||
    spacingDelta > eyeSymmetryThresholds.spacingDelta ||
    sizeRatio > eyeSymmetryThresholds.sizeRatio;
  if (!needsCorrection) return null;

  const master = pickMasterEye(landmarks, confidence);
  const masterCenter = master === "left" ? leftEye : rightEye;
  const masterRegion = landmarks.eyeRegions?.[master];
  const halfSpacing = eyeDistance / 2;
  const maxPatchHalfWidth = halfSpacing * eyeSymmetryThresholds.patchShareOfSpacing;
  const fallbackHalfSize = eyeDistance * eyeSymmetryThresholds.fallbackPatchShare;
  const patchHalfWidth = Math.min(
    maxPatchHalfWidth,
    Math.max(masterRegion ? masterRegion.radiusX * eyeSymmetryThresholds.patchScale : 0, fallbackHalfSize),
  );
  const patchHalfHeight = Math.max(
    masterRegion
      ? Math.max(masterRegion.radiusTopY, masterRegion.radiusBottomY) * eyeSymmetryThresholds.patchScale
      : 0,
    fallbackHalfSize,
    // The patch has to reach over the difference in height, or the higher eye leaves pixels behind.
    heightDelta / 2 + (masterRegion ? Math.max(masterRegion.radiusTopY, masterRegion.radiusBottomY) : 0),
  );

  return {
    axisX: round(axisX),
    eyeY: round((leftEye.y + rightEye.y) / 2),
    halfSpacing: round(halfSpacing),
    patchHalfWidth: round(Math.max(patchHalfWidth, 0.01)),
    patchHalfHeight: round(Math.max(patchHalfHeight, 0.01)),
    master,
    masterCenter: { x: masterCenter.x, y: masterCenter.y },
    heightDelta: round(heightDelta),
    spacingDelta: round(spacingDelta),
    sizeRatio: round(sizeRatio),
  };
}

/**
 * Face centreline from the landmarks that cannot be moved by an uneven eye pair: chin, mouth and the jaw. The eye
 * midpoint only breaks ties, so a badly placed eye cannot drag the mirror axis with it.
 */
function faceCenterline(landmarks: ManualLandmarks, eyeCenterX: number) {
  const candidates = [
    landmarks.chin.x,
    landmarks.mouthCenter.x,
    (landmarks.jawLeft.x + landmarks.jawRight.x) / 2,
  ].filter((value) => Number.isFinite(value));
  const faceAxis = candidates.length
    ? candidates.reduce((sum, value) => sum + value, 0) / candidates.length
    : eyeCenterX;
  const low = Math.min(landmarks.leftEye.x, landmarks.rightEye.x);
  const high = Math.max(landmarks.leftEye.x, landmarks.rightEye.x);

  // A face axis outside the eye pair means the landmarks disagree with each other: fall back to the pair's own centre.
  if (!(faceAxis > low && faceAxis < high)) return eyeCenterX;
  return faceAxis;
}

/** Relative difference between the two detected eye regions: how much one eye is drawn larger than the other. */
function eyeRegionSizeRatio(landmarks: ManualLandmarks) {
  const regions = landmarks.eyeRegions;
  if (!regions) return 0;

  const leftArea = regions.left.radiusX * regions.left.radiusY;
  const rightArea = regions.right.radiusX * regions.right.radiusY;
  const mean = (leftArea + rightArea) / 2;
  if (!(mean > 0)) return 0;
  return Math.abs(leftArea - rightArea) / mean;
}

function pickMasterEye(landmarks: ManualLandmarks, confidence?: EyeSymmetryConfidence): EyeSymmetrySide {
  if (confidence) {
    const advantage = confidence.left - confidence.right;
    if (Math.abs(advantage) > confidenceAdvantage) return advantage > 0 ? "left" : "right";
  }

  const regions = landmarks.eyeRegions;
  if (regions) {
    const leftArea = regions.left.radiusX * regions.left.radiusY;
    const rightArea = regions.right.radiusX * regions.right.radiusY;
    if (Math.abs(leftArea - rightArea) > Number.EPSILON) return leftArea > rightArea ? "left" : "right";
  }

  return "left";
}

export type EyeSymmetrySize = {
  height: number;
  width: number;
};

/** Creates a canvas already sized for the given pixels; injectable so the correction can be tested without a GPU canvas. */
export type EyeSymmetryCanvasFactory = (width: number, height: number) => HTMLCanvasElement;

function createSizedCanvas(width: number, height: number): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(width));
  canvas.height = Math.max(1, Math.round(height));
  return canvas;
}

function requireContext(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const context = canvas.getContext("2d");
  if (!context) throw new Error("2D canvas is unavailable for the eye symmetry correction");
  return context;
}

/**
 * Paints the corrected pair onto `context`: the master eye's pixels are copied to both eye positions, the second copy
 * mirrored across the face centreline, so the result is exactly symmetric while keeping the character's own eye art.
 *
 * `source` is drawn as the base layer, so the returned image is the whole picture, not just the eyes.
 */
export function drawEyeSymmetry(
  context: CanvasRenderingContext2D,
  source: CanvasImageSource,
  plan: EyeSymmetryPlan,
  size: EyeSymmetrySize,
  createCanvas: EyeSymmetryCanvasFactory = createSizedCanvas,
) {
  const width = Math.max(1, Math.round(size.width));
  const height = Math.max(1, Math.round(size.height));
  context.clearRect(0, 0, width, height);
  context.drawImage(source, 0, 0, width, height);

  const axisX = plan.axisX * width;
  const eyeY = plan.eyeY * height;
  const halfSpacing = plan.halfSpacing * width;
  const patchHalfWidth = Math.max(2, plan.patchHalfWidth * width);
  const patchHalfHeight = Math.max(2, plan.patchHalfHeight * height);
  const patchWidth = Math.max(1, Math.round(patchHalfWidth * 2));
  const patchHeight = Math.max(1, Math.round(patchHalfHeight * 2));
  const masterCenter = { x: plan.masterCenter.x * width, y: plan.masterCenter.y * height };
  const patch = createMasterPatch(
    source,
    masterCenter,
    patchHalfWidth,
    patchHalfHeight,
    patchWidth,
    patchHeight,
    createCanvas,
  );

  for (const side of ["left", "right"] as const) {
    const targetX = axisX + (side === "left" ? -halfSpacing : halfSpacing);
    context.save();
    context.translate(targetX, eyeY);
    // The master's own side keeps the artwork as drawn; the other side is the mirror image of it.
    if (side !== plan.master) context.scale(-1, 1);
    context.drawImage(patch, -patchWidth / 2, -patchHeight / 2);
    context.restore();
  }
}

/** Renders the corrected image onto a new canvas; the caller owns the canvas and its blob. */
export function renderEyeSymmetry(
  source: CanvasImageSource,
  plan: EyeSymmetryPlan,
  size: EyeSymmetrySize,
  createCanvas: EyeSymmetryCanvasFactory = createSizedCanvas,
): HTMLCanvasElement {
  const canvas = createCanvas(size.width, size.height);
  drawEyeSymmetry(requireContext(canvas), source, plan, size, createCanvas);
  return canvas;
}

/** Crops the master eye and fades its edge, so pasting it leaves no visible ellipse on the artwork. */
function createMasterPatch(
  source: CanvasImageSource,
  masterCenter: LandmarkPoint,
  patchHalfWidth: number,
  patchHalfHeight: number,
  patchWidth: number,
  patchHeight: number,
  createCanvas: EyeSymmetryCanvasFactory,
): HTMLCanvasElement {
  const patch = createCanvas(patchWidth, patchHeight);
  const patchContext = requireContext(patch);
  patchContext.drawImage(
    source,
    masterCenter.x - patchHalfWidth,
    masterCenter.y - patchHalfHeight,
    patchWidth,
    patchHeight,
    0,
    0,
    patchWidth,
    patchHeight,
  );

  const mask = createFeatherMask(createCanvas, patchWidth, patchHeight);
  patchContext.save();
  patchContext.globalCompositeOperation = "destination-in";
  patchContext.drawImage(mask, 0, 0);
  patchContext.restore();
  return patch;
}

function createFeatherMask(
  createCanvas: EyeSymmetryCanvasFactory,
  width: number,
  height: number,
): HTMLCanvasElement {
  const mask = createCanvas(width, height);
  const context = requireContext(mask);
  const radius = width / 2;
  context.clearRect(0, 0, width, height);
  context.save();
  context.translate(width / 2, height / 2);
  context.scale(1, height / width);
  const gradient = context.createRadialGradient(0, 0, 0, 0, 0, radius);
  const solidStop = clamp(1 - eyeSymmetryThresholds.feather, 0, 1);
  gradient.addColorStop(0, "rgba(0, 0, 0, 1)");
  gradient.addColorStop(solidStop, "rgba(0, 0, 0, 1)");
  gradient.addColorStop(1, "rgba(0, 0, 0, 0)");
  context.fillStyle = gradient;
  context.beginPath();
  context.arc(0, 0, radius, 0, Math.PI * 2);
  context.fill();
  context.restore();
  return mask;
}
