import { expect, test } from "bun:test";
import { EMPTY, dateTime, parseGameDate, shortDate, shortMoney } from "../src/format";

test("game timestamps are UTC with microseconds, and ISO strings pass through", () => {
  expect(parseGameDate("2024-11-03 23:01:21.123456")?.toISOString()).toBe("2024-11-03T23:01:21.123Z");
  expect(parseGameDate("2024-11-03 23:01:21")?.toISOString()).toBe("2024-11-03T23:01:21.000Z");
  expect(parseGameDate("2024-11-03T23:01:21Z")?.toISOString()).toBe("2024-11-03T23:01:21.000Z");
  expect(parseGameDate("garbage")).toBeNull();
  expect(parseGameDate(null)).toBeNull();
});

test("missing values fall back instead of printing Invalid Date", () => {
  expect(shortDate(null)).toBe(EMPTY);
  expect(shortDate("garbage")).toBe(EMPTY);
  expect(dateTime(null)).toBe("Never");
  expect(dateTime("garbage")).toBe("garbage");
});

test("short money picks B / M / plain the way parseMoney reads it back", () => {
  expect(shortMoney(2_000_000_000)).toBe("$2.00B");
  expect(shortMoney(850_000_000)).toBe("$850M");
  expect(shortMoney(null)).toBe(EMPTY);
});
