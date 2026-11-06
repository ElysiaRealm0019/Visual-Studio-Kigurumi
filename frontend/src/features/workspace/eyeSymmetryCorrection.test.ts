import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { detectAnimeLandmarks } from "../editor/deformation/animeLandmarkDetector";
import { createDefaultLandmarks, type ManualLandmarks } from "../editor/deformation/landmarks";
import { correctEyeSymmetry, supportsEyeSymmetryCorrection } from "./eyeSymmetryCorrection";

vi.mock("../editor/deformation/animeLandmarkDetector", () => ({
  detectAnimeLandmarks: vi.fn(),
}));

const imageWidth = 800;
const imageHeight = 1100;

class FakeImage {
  height = imageHeight;
  naturalHeight = imageHeight;
  naturalWidth = imageWidth;
  onerror: (() => void) | null = null;
  onload: (() => void) | null = null;
  width = imageWidth;

  set src(_value: string) {
    queueMicrotask(() => this.onload?.());
  }
}

function symmetricLandmarks(overrides: Partial<ManualLandmarks> = {}): ManualLandmarks {
  return { ...createDefaultLandmarks(1, 1), ...overrides };
}

function fakeContext() {
  return {
    arc: () => undefined,
    beginPath: () => undefined,
    clearRect: () => undefined,
    createRadialGradient: () => ({ addColorStop: () => undefined }),
    drawImage: () => undefined,
    fill: () => undefined,
    fillStyle: "",
    globalCompositeOperation: "source-over",
    restore: () => undefined,
    save: () => undefined,
    scale: () => undefined,
    translate: () => undefined,
  };
}

function detect(controls: ManualLandmarks | null) {
  vi.mocked(detectAnimeLandmarks).mockResolvedValue(
    (controls ? { controls, debug: { points: [] } } : null) as never,
  );
}

const originalGetContext = HTMLCanvasElement.prototype.getContext;
const originalToBlob = HTMLCanvasElement.prototype.toBlob;

beforeEach(() => {
  vi.stubGlobal("Image", FakeImage);
  Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
    configurable: true,
    value: () => fakeContext(),
  });
  Object.defineProperty(HTMLCanvasElement.prototype, "toBlob", {
    configurable: true,
    value: (callback: BlobCallback) => callback(new Blob(["corrected"], { type: "image/png" })),
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
  Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
    configurable: true,
    value: originalGetContext,
  });
  Object.defineProperty(HTMLCanvasElement.prototype, "toBlob", {
    configurable: true,
    value: originalToBlob,
  });
});

describe("eye symmetry correction roles", () => {
  it("covers the generated stage roles only", () => {
    expect(supportsEyeSymmetryCorrection("design")).toBe(true);
    expect(supportsEyeSymmetryCorrection("design_turnaround")).toBe(true);
    expect(supportsEyeSymmetryCorrection("front")).toBe(true);
    expect(supportsEyeSymmetryCorrection("turnaround")).toBe(true);
    expect(supportsEyeSymmetryCorrection("reference")).toBe(false);
    expect(supportsEyeSymmetryCorrection(undefined)).toBe(false);
  });

  it("never rewrites a role it does not own", async () => {
    detect(symmetricLandmarks());

    await expect(correctEyeSymmetry({ imageUrl: "/source.png", role: "reference" })).resolves.toBeNull();
    expect(detectAnimeLandmarks).not.toHaveBeenCalled();
  });
});

describe("eye symmetry correction", () => {
  it("returns corrected pixels for an uneven pair", async () => {
    detect(
      symmetricLandmarks({
        leftEye: { x: 0.42, y: 0.4 },
        rightEye: { x: 0.58, y: 0.46 },
      }),
    );

    const correction = await correctEyeSymmetry({ imageUrl: "/source.png", role: "front" });

    expect(correction?.plan.eyeY).toBeCloseTo(0.43, 4);
    expect(correction?.plan.master).toBe("left");
    expect(correction?.width).toBe(imageWidth);
    expect(correction?.height).toBe(imageHeight);
    await expect(correction?.blob.text()).resolves.toBe("corrected");
    // Strict single-face detection, so a weak fit is rejected instead of guessed.
    expect(detectAnimeLandmarks).toHaveBeenCalledWith(expect.anything(), undefined, true, undefined);
  });

  it("pins the detection to the first panel of a four-view sheet", async () => {
    detect(
      symmetricLandmarks({
        leftEye: { x: 0.42, y: 0.4 },
        rightEye: { x: 0.58, y: 0.46 },
      }),
    );

    await correctEyeSymmetry({ imageUrl: "/source.png", role: "turnaround" });

    expect(detectAnimeLandmarks).toHaveBeenCalledWith(expect.anything(), undefined, true, {
      height: imageHeight,
      width: imageWidth * 0.26,
      x: 0,
      y: 0,
    });
  });

  it("does nothing when the eye pair is already symmetric", async () => {
    detect(symmetricLandmarks());

    await expect(correctEyeSymmetry({ imageUrl: "/source.png", role: "design" })).resolves.toBeNull();
  });

  it("does nothing when no face was found confidently", async () => {
    detect(null);

    await expect(correctEyeSymmetry({ imageUrl: "/source.png", role: "design" })).resolves.toBeNull();
  });

  it("honours an abort that arrives before the correction finishes", async () => {
    detect(
      symmetricLandmarks({
        leftEye: { x: 0.42, y: 0.4 },
        rightEye: { x: 0.58, y: 0.46 },
      }),
    );
    const controller = new AbortController();
    controller.abort();

    await expect(
      correctEyeSymmetry({ imageUrl: "/source.png", role: "front", signal: controller.signal }),
    ).rejects.toThrow();
  });
});
