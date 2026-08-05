import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const source = dirname(fileURLToPath(import.meta.url));
const root = resolve(source, "..");
const output = join(root, "preview-dist");

await mkdir(output, { recursive: true });
await cp(join(source, "index.html"), join(output, "index.html"));
await cp(join(source, "styles.css"), join(output, "styles.css"));
await cp(join(source, "app.js"), join(output, "app.js"));

const productCss = await readFile(join(root, "apps", "web", "app", "globals.css"), "utf8");
await writeFile(join(output, "product.css"), productCss, "utf8");
console.log(`Built static preview at ${output}`);
