import { expect, test } from "bun:test";
import { resolve } from "node:path";

test("web workspaces use the shared shadcn primitives", async () => {
  const root = resolve(import.meta.dir, "../src/components");
  const raw: string[] = [];

  for await (const file of new Bun.Glob("**/*.tsx").scan({ cwd: root })) {
    if (file.startsWith("ui/")) continue;
    const source = (await Bun.file(resolve(root, file)).text()).replace(/\/\*[\s\S]*?\*\//g, "");
    if (/<(?:button|input|select|table|thead|tbody|tr|th|td)\b/.test(source)) raw.push(file);
  }

  expect(raw).toEqual([]);
});
