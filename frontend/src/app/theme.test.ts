import { describe, expect, it } from "vitest";
import { createKigTheme, idePalettes } from "./theme";

describe("createKigTheme", () => {
  it("builds dark and light IDE themes from the shared palette", () => {
    const dark = createKigTheme("dark");
    const light = createKigTheme("light");
    expect(dark.palette.mode).toBe("dark");
    expect(dark.palette.primary.main).toBe(idePalettes.dark.accent);
    expect(dark.palette.background.default).toBe(idePalettes.dark.background);
    expect(light.palette.mode).toBe("light");
    expect(light.palette.background.paper).toBe(idePalettes.light.panel);
  });

  it("keeps compact Material UI defaults", () => {
    expect(createKigTheme("dark").components?.MuiButton?.defaultProps).toMatchObject({ disableElevation: true });
  });
});
