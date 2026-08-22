import { describe, expect, it } from "vitest";
import { nextOverlayHeight } from "../src/core/overlayFit";

describe("nextOverlayHeight", () => {
  it("does not shrink when the bar gets shorter", () => {
    expect(nextOverlayHeight(240, 120, 800)).toBeNull();
  });

  it("grows when text would clip", () => {
    expect(nextOverlayHeight(180, 220, 800)).toBe(220);
  });

  it("caps at max so the window cannot eat the screen", () => {
    expect(nextOverlayHeight(180, 2000, 400)).toBe(400);
  });
});
