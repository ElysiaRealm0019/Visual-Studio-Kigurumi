import { describe, expect, it } from "vitest";
import { analyzeEyeSymmetry, drawEyeSymmetry, eyeSymmetryThresholds, type EyeSymmetryPlan } from "./eyeSymmetry";
import { createDefaultLandmarks, type EyeRegion, type ManualLandmarks } from "./landmarks";

function symmetricLandmarks(overrides: Partial<ManualLandmarks> = {}): ManualLandmarks {
  return { ...createDefaultLandmarks(1, 1), ...overrides };
}

function eyeRegion(radiusX: number, radiusY = 0.05): EyeRegion {
  return { radiusBottomY: radiusY, radiusTopY: radiusY, radiusX, radiusY };
}

type RecordedCall = { args: unknown[]; type: string };

function createFakeContext() {
  const calls: RecordedCall[] = [];
  const record =
    (type: string) =>
    (...args: unknown[]) => {
      calls.push({ args, type });
    };
  const context = {
    calls,
    arc: record("arc"),
    beginPath: record("beginPath"),
    clearRect: record("clearRect"),
    createRadialGradient: () => ({ addColorStop: () => undefined }),
    drawImage: record("drawImage"),
    fill: record("fill"),
    fillStyle: "",
    globalCompositeOperation: "source-over",
    restore: record("restore"),
    save: record("save"),
    scale: record("scale"),
    translate: record("translate"),
  };
  return context;
}

function createFakeCanvasFactory(context: ReturnType<typeof createFakeContext>) {
  return (width: number, height: number) =>
    ({ getContext: () => context, height, width }) as unknown as HTMLCanvasElement;
}

/**
 * The offscreen patch and mask canvases log into their own context, so the assertions below see only the calls that
 * land on the image being corrected.
 */
function createRenderTarget() {
  const context = createFakeContext();
  const offscreen = createFakeContext();
  return { context, createCanvas: createFakeCanvasFactory(offscreen), offscreen };
}

describe("eye symmetry plan", () => {
  it("leaves an already symmetric eye pair alone", () => {
    expect(analyzeEyeSymmetry(symmetricLandmarks())).toBeNull();
  });

  it("ignores a difference inside the detection tolerance", () => {
    const withinTolerance = eyeSymmetryThresholds.heightDelta * 0.5;
    const landmarks = symmetricLandmarks({
      leftEye: { x: 0.42, y: 0.42 },
      rightEye: { x: 0.58, y: 0.42 + withinTolerance },
    });

    expect(analyzeEyeSymmetry(landmarks)).toBeNull();
  });

  it("levels the pair on their shared mean height, mirrored on the face centreline", () => {
    const landmarks = symmetricLandmarks({
      leftEye: { x: 0.42, y: 0.4 },
      rightEye: { x: 0.58, y: 0.46 },
    });

    const plan = analyzeEyeSymmetry(landmarks);

    expect(plan).not.toBeNull();
    expect(plan?.eyeY).toBeCloseTo(0.43, 4);
    expect(plan?.axisX).toBeCloseTo(0.5, 4);
    expect(plan?.halfSpacing).toBeCloseTo(0.08, 4);
    expect(plan?.heightDelta).toBeCloseTo(0.06, 4);
    // No detected eye region: the patch falls back to a share of the eye distance and never crosses the centreline.
    expect(plan?.patchHalfWidth).toBeLessThanOrEqual(0.08 * eyeSymmetryThresholds.patchShareOfSpacing);
    expect(plan?.master).toBe("left");
    expect(plan?.masterCenter).toEqual({ x: 0.42, y: 0.4 });
  });

  it("keeps the taller patch when the eyes are far apart vertically", () => {
    const landmarks = symmetricLandmarks({
      leftEye: { x: 0.42, y: 0.36 },
      rightEye: { x: 0.58, y: 0.5 },
    });

    const plan = analyzeEyeSymmetry(landmarks);

    expect(plan?.heightDelta).toBeCloseTo(0.14, 4);
    expect(plan?.patchHalfHeight).toBeGreaterThanOrEqual(0.07);
  });

  it("mirrors the eye the detector trusts most", () => {
    const landmarks = symmetricLandmarks({
      leftEye: { x: 0.42, y: 0.4 },
      rightEye: { x: 0.58, y: 0.46 },
    });

    expect(analyzeEyeSymmetry(landmarks, { left: 0.75, right: 0.3 })?.master).toBe("left");
    expect(analyzeEyeSymmetry(landmarks, { left: 0.3, right: 0.75 })?.master).toBe("right");
  });

  it("mirrors the larger eye when the confidence says nothing", () => {
    const landmarks = symmetricLandmarks({
      leftEye: { x: 0.42, y: 0.42 },
      rightEye: { x: 0.58, y: 0.42 },
      eyeRegions: { left: eyeRegion(0.03), right: eyeRegion(0.08) },
    });

    const plan = analyzeEyeSymmetry(landmarks, { left: 0.5, right: 0.52 });

    expect(plan?.master).toBe("right");
    // The larger master region sets the patch size, still capped before the centreline.
    expect(plan?.patchHalfWidth).toBeCloseTo(0.08 * eyeSymmetryThresholds.patchShareOfSpacing, 4);
  });

  it("corrects a pair that is not centred on the face midline", () => {
    const landmarks = symmetricLandmarks({
      leftEye: { x: 0.36, y: 0.42 },
      rightEye: { x: 0.6, y: 0.42 },
    });

    const plan = analyzeEyeSymmetry(landmarks);

    expect(plan?.spacingDelta).toBeCloseTo(1 / 6, 3);
    expect(plan?.axisX).toBeCloseTo(0.5, 4);
  });

  it("falls back to the eye midpoint when the landmarks disagree about the face axis", () => {
    const landmarks = symmetricLandmarks({
      chin: { x: 0.9, y: 0.7 },
      jawLeft: { x: 0.9, y: 0.6 },
      jawRight: { x: 0.92, y: 0.6 },
      mouthCenter: { x: 0.9, y: 0.57 },
      leftEye: { x: 0.42, y: 0.4 },
      rightEye: { x: 0.58, y: 0.46 },
    });

    expect(analyzeEyeSymmetry(landmarks)?.axisX).toBeCloseTo(0.5, 4);
  });

  it("gives up on landmarks too close together to hold two eyes", () => {
    const landmarks = symmetricLandmarks({
      leftEye: { x: 0.5, y: 0.4 },
      rightEye: { x: 0.51, y: 0.46 },
    });

    expect(analyzeEyeSymmetry(landmarks)).toBeNull();
  });
});

describe("eye symmetry rendering", () => {
  const plan: EyeSymmetryPlan = {
    axisX: 0.5,
    eyeY: 0.43,
    halfSpacing: 0.08,
    patchHalfWidth: 0.07,
    patchHalfHeight: 0.06,
    master: "left",
    masterCenter: { x: 0.4, y: 0.4 },
    heightDelta: 0.06,
    spacingDelta: 0.02,
    sizeRatio: 0,
  };

  it("paints the base image and one mirrored copy of the master eye", () => {
    const { context, createCanvas, offscreen } = createRenderTarget();
    const source = { height: 1100, width: 800 } as unknown as CanvasImageSource;
    drawEyeSymmetry(
      context as unknown as CanvasRenderingContext2D,
      source,
      plan,
      { height: 1100, width: 800 },
      createCanvas,
    );

    expect(context.calls[0]).toEqual({ args: [0, 0, 800, 1100], type: "clearRect" });
    expect(context.calls[1].type).toBe("drawImage");
    expect(context.calls[1].args[0]).toBe(source);

    const translations = context.calls.filter((call) => call.type === "translate");
    expect(translations.map((call) => call.args)).toEqual([
      [336, 473],
      [464, 473],
    ]);
    // Only the eye opposite the master is mirrored.
    expect(context.calls.filter((call) => call.type === "scale").map((call) => call.args)).toEqual([[-1, 1]]);
    // Base image plus the two pasted patches.
    expect(context.calls.filter((call) => call.type === "drawImage")).toHaveLength(3);
    // The patch is cropped around the master eye, so both corrected eyes keep that eye's own pixels.
    expect(offscreen.calls[0]).toEqual({
      args: [source, 264, 374, 112, 132, 0, 0, 112, 132],
      type: "drawImage",
    });
  });

  it("keeps the master's own side unmirrored when the right eye is the master", () => {
    const { context, createCanvas } = createRenderTarget();
    const source = { height: 1100, width: 800 } as unknown as CanvasImageSource;
    drawEyeSymmetry(
      context as unknown as CanvasRenderingContext2D,
      source,
      { ...plan, master: "right" },
      { height: 1100, width: 800 },
      createCanvas,
    );

    expect(context.calls.filter((call) => call.type === "scale").map((call) => call.args)).toEqual([[-1, 1]]);
    expect(context.calls.filter((call) => call.type === "translate").map((call) => call.args)).toEqual([
      [336, 473],
      [464, 473],
    ]);
  });
});
