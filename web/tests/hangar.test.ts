import { expect, test } from "bun:test";
import { salePricesValid, seatCeiling } from "../src/components/Hangar";

test("class cabin space caps the screenshot configuration at 111 economy seats", () => {
  expect(seatCeiling(
    "eco",
    { eco: 111, bus: 20, first: 10, payload: 5 },
    189,
    20.47,
  )).toBe(111);
});

test("buy-now minimum follows the opening bid and opening bid keeps its own range", () => {
  expect(salePricesValid(900, 177, 88, 177, 1209)).toBe(true);
  expect(salePricesValid(176, 177, 88, 177, 1209)).toBe(false);
  expect(salePricesValid(900, 87, 88, 177, 1209)).toBe(false);
});
