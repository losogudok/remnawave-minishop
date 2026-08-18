import { describe, expect, it } from "vitest";
import { easeChartMotion, morphChartData, revealBarChartData } from "./chartMotion.js";

describe("chart motion", () => {
  it("morphs differently sized area paths on stable target buckets", () => {
    const from: [number[], number[]] = [
      [1, 2],
      [10, 30],
    ];
    const to: [number[], number[]] = [
      [4, 5, 6, 7],
      [20, 10, 40, 60],
    ];

    const start = morphChartData(from, to, 0, "area");
    expect(start[0]).toHaveLength(4);
    expect(start[1]).toHaveLength(4);
    expect(start[0]).toEqual(to[0]);
    expect(start[1][0]).toBe(10);
    expect(start[1][1]).toBeCloseTo(50 / 3);
    expect(start[1][2]).toBeCloseTo(70 / 3);
    expect(start[1][3]).toBe(30);
    expect(morphChartData(from, to, 1, "area")).toEqual(to);
  });

  it("uses the target bucket count for bar transitions", () => {
    const result = morphChartData(
      [
        [1, 2, 3, 4],
        [4, 8, 12, 16],
      ],
      [
        [10, 20],
        [6, 14],
      ],
      0.5,
      "bar"
    );

    expect(result[0]).toHaveLength(2);
    expect(result[0]).toEqual([10, 20]);
    expect(result[1]).toHaveLength(2);
    expect(result[1]).toEqual([5, 15]);
  });

  it("grows bars with a restrained left-to-right stagger", () => {
    const target: [number[], number[]] = [
      [1, 2, 3],
      [10, 20, 30],
    ];

    expect(revealBarChartData(target, 0)[1]).toEqual([0, 0, 0]);
    const midway = revealBarChartData(target, 0.5)[1];
    expect(midway[0]).toBeGreaterThan(midway[1] / 2);
    expect(midway[1]).toBeGreaterThan(midway[2] / 3);
    expect(revealBarChartData(target, 1)).toEqual(target);
  });

  it("keeps easing bounded", () => {
    expect(easeChartMotion(-1)).toBe(0);
    expect(easeChartMotion(0.5)).toBe(0.5);
    expect(easeChartMotion(2)).toBe(1);
  });
});
