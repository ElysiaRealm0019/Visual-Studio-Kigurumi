import { describe, expect, it } from "vitest";
import { deriveBrows, mapAnimePointsToManualLandmarks, type DetectedPoint } from "./animeLandmarkMapping";
import { createDefaultLandmarks, resolveBrows, type ManualLandmarks } from "./landmarks";
import { createFeatureLiquifyStrokes } from "./pixiStage";
import { createEmptyRecipe, normalizeEditRecipe, updateBrowControl, updateEyeControl, updateFaceControl } from "./recipe";

const landmarks: ManualLandmarks = {
  ...createDefaultLandmarks(1, 1),
  brows: {
    left: { inner: { x: 0.46, y: 0.35 }, peak: { x: 0.41, y: 0.33 }, outer: { x: 0.36, y: 0.35 } },
    right: { inner: { x: 0.54, y: 0.35 }, peak: { x: 0.59, y: 0.33 }, outer: { x: 0.64, y: 0.35 } },
  },
};

describe("new face, eye and brow controls", () => {
  it("defaults the new controls to zero and survives legacy recipes without them", () => {
    const legacy = normalizeEditRecipe({ face: createEmptyRecipe().face, eyes: { eyeSize: 0.1 } as never });
    expect(legacy.brows.browArch).toBe(0);
    expect(legacy.face.temple).toBe(0);
    expect(legacy.eyes.pupilSize).toBe(0);
    expect(legacy.eyes.eyeSize).toBe(0.1);
    expect(createFeatureLiquifyStrokes(createEmptyRecipe(), landmarks)).toHaveLength(0);
  });

  it("raises both brows symmetrically", () => {
    const strokes = createFeatureLiquifyStrokes(updateBrowControl(createEmptyRecipe(), "browVertical", 0.3), landmarks);
    expect(strokes).toHaveLength(2);
    expect(strokes.every((stroke) => stroke.mode === "push-up")).toBe(true);
    expect(strokes[0].x + strokes[1].x).toBeCloseTo(1, 2);
  });

  it("moves brow tails outward and inner ends apart", () => {
    const longer = createFeatureLiquifyStrokes(updateBrowControl(createEmptyRecipe(), "browLength", 0.3), landmarks);
    expect(longer.map((stroke) => stroke.mode)).toEqual(["push-left", "push-right"]);
    expect(longer[0].x).toBeCloseTo(0.36, 2);

    const narrower = createFeatureLiquifyStrokes(updateBrowControl(createEmptyRecipe(), "browInnerSpacing", -0.3), landmarks);
    expect(narrower.map((stroke) => stroke.mode)).toEqual(["push-right", "push-left"]);
    expect(narrower[0].x).toBeCloseTo(0.46, 2);
  });

  it("tilts brows by lifting the tail and lowering the head", () => {
    const strokes = createFeatureLiquifyStrokes(updateBrowControl(createEmptyRecipe(), "browTilt", 0.3), landmarks);
    expect(strokes.filter((stroke) => stroke.mode === "push-up").map((stroke) => stroke.x)).toEqual([0.36, 0.64]);
    expect(strokes.filter((stroke) => stroke.mode === "push-down").map((stroke) => stroke.x)).toEqual([0.46, 0.54]);
  });

  it("maps the four new eye controls onto lid, iris and corner strokes", () => {
    let recipe = createEmptyRecipe();
    recipe = updateEyeControl(recipe, "eyeLift", 0.3);
    recipe = updateEyeControl(recipe, "pupilSize", 0.3);
    recipe = updateEyeControl(recipe, "lowerLid", 0.3);
    recipe = updateEyeControl(recipe, "eyeTail", 0.3);
    const strokes = createFeatureLiquifyStrokes(recipe, landmarks);
    expect(strokes).toHaveLength(8);
    const eye = landmarks.leftEye;
    const leftStrokes = strokes.filter((stroke) => Math.abs(stroke.x - eye.x) < 0.12);
    expect(leftStrokes.map((stroke) => stroke.mode).sort()).toEqual(["expand", "push-down", "push-up", "push-up"]);
    // The outer corner of the image-left eye is further left than its centre.
    const tail = leftStrokes.find((stroke) => stroke.mode === "push-up" && Math.abs(stroke.y - eye.y) < 0.001);
    expect(tail?.x).toBeLessThan(eye.x);
  });

  it("keeps eye lid and corner strokes strong enough to be visible at the default slider range", () => {
    const lift = createFeatureLiquifyStrokes(updateEyeControl(createEmptyRecipe(), "eyeLift", 0.18), landmarks);
    const lowerLid = createFeatureLiquifyStrokes(updateEyeControl(createEmptyRecipe(), "lowerLid", 0.18), landmarks);
    const tail = createFeatureLiquifyStrokes(updateEyeControl(createEmptyRecipe(), "eyeTail", 0.18), landmarks);
    const strengths = (strokes: typeof lift) => strokes.map((stroke) => stroke.strength);

    expect(lift).toHaveLength(2);
    expect(lowerLid).toHaveLength(2);
    expect(tail).toHaveLength(2);
    expect(Math.min(...strengths(lift))).toBeGreaterThanOrEqual(0.35);
    expect(Math.min(...strengths(lowerLid))).toBeGreaterThanOrEqual(0.3);
    expect(Math.min(...strengths(tail))).toBeGreaterThanOrEqual(0.25);
  });

  it("widens the temples outward at brow height", () => {
    const strokes = createFeatureLiquifyStrokes(updateFaceControl(createEmptyRecipe(), "temple", 0.2), landmarks);
    expect(strokes.map((stroke) => stroke.mode)).toEqual(["push-left", "push-right"]);
    expect(strokes[0].y).toBeCloseTo(0.35, 2);
    expect(strokes[0].x).toBeLessThan(landmarks.leftEye.x);
  });

  it("estimates brows above the eyes when detection did not provide them", () => {
    const estimated = resolveBrows(createDefaultLandmarks(1, 1));
    expect(estimated.left.peak.y).toBeLessThan(0.42);
    expect(estimated.left.outer.x).toBeLessThan(estimated.left.inner.x);
    expect(estimated.right.outer.x).toBeGreaterThan(estimated.right.inner.x);
  });
});

describe("brow detection mapping", () => {
  function point(x: number, y: number): DetectedPoint {
    return { score: 1, x, y };
  }

  it("splits the six brow points by side and orders them inner, peak, outer", () => {
    const points = Array.from({ length: 28 }, () => point(50, 50));
    // indexes 5-10 are brows in the anime HRNet layout; give them shuffled x positions
    [point(30, 30), point(45, 32), point(38, 28), point(70, 30), point(55, 32), point(62, 28)].forEach((p, i) => {
      points[5 + i] = p;
    });
    const brows = deriveBrows(points, 50);
    expect(brows?.left.inner.x).toBe(45);
    expect(brows?.left.peak.x).toBe(38);
    expect(brows?.left.outer.x).toBe(30);
    expect(brows?.right.inner.x).toBe(55);
    expect(brows?.right.outer.x).toBe(70);
  });

  it("returns normalised brows from the full landmark mapping", () => {
    const points = Array.from({ length: 28 }, (_, index) => point(40 + (index % 5), 50));
    [11, 12, 13, 14, 15, 16].forEach((i, k) => (points[i] = point(30 + k, 40 + (k % 2))));
    [17, 18, 19, 20, 21, 22].forEach((i, k) => (points[i] = point(64 + k, 40 + (k % 2))));
    [point(26, 30), point(40, 31), point(33, 28), point(76, 30), point(60, 31), point(68, 28)].forEach((p, i) => {
      points[5 + i] = p;
    });
    points[1] = point(30, 70);
    points[2] = point(50, 85);
    points[3] = point(70, 70);
    const mapped = mapAnimePointsToManualLandmarks(points, 100, 100);
    expect(mapped?.brows?.left.inner).toEqual({ x: 0.4, y: 0.31 });
    expect(mapped?.brows?.right.outer).toEqual({ x: 0.76, y: 0.3 });
  });
});
