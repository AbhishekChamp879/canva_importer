"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync(require("node:path").join(__dirname, "../../figma-plugin/code.js"), "utf8");
const png = Uint8Array.from([137, 80, 78, 71]);

class Node {
  constructor(type) { this.type = type; this.children = []; this.parent = null; this.removed = false; this.data = {}; this.width = 100; this.height = 24; this.x = 0; this.y = 0; }
  appendChild(node) { if (this.removed) throw new Error("Removed parent"); if (node.parent) node.parent.children = node.parent.children.filter(c => c !== node); node.parent = this; this.children.push(node); }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter(c => c !== this); this.parent = null; this.removed = true; for (const child of this.children) child.removed = true; }
  resize(w, h) { this.width = w; this.height = h; }
  setPluginData(k, v) { this.data[k] = v; }
  getPluginData(k) { return this.data[k] || ""; }
  async exportAsync() { if (this.removed) throw new Error("Removed node"); return png; }
}
function runtime() {
  const page = new Node("PAGE"); page.selection = [];
  const posted = []; let hash = 0;
  const create = type => { const node = new Node(type); page.appendChild(node); return node; };
  const figma = { currentPage: page, showUI() {}, closePlugin() {}, notify() {},
    ui: { postMessage: msg => posted.push(msg) }, viewport: { center: { x: 500, y: 500 }, scrollAndZoomIntoView() {} },
    createFrame: () => create("FRAME"), createText: () => create("TEXT"), createRectangle: () => create("RECTANGLE"),
    createNodeFromSvg: svg => { assert.ok(!svg.includes("script")); return create("FRAME"); }, createImage: () => ({ hash: `image-${++hash}` }),
    listAvailableFontsAsync: async () => [{ fontName: { family: "Roboto", style: "Regular" } }], loadFontAsync: async () => {} };
  const context = vm.createContext({ figma, __html__: "", Uint8Array, setTimeout, console }); vm.runInContext(source, context);
  let request = 0;
  async function send(type, fields = {}, id = "session") { await figma.ui.onmessage({ type, sessionId: id, requestId: ++request, ...fields }); return posted.at(-1); }
  return { figma, page, posted, context, send };
}
function scene() {
  return { version: 1, width: 600, height: 400, referenceAsset: "reference", wholePageFallback: false, warnings: [],
    assets: [{ id: "reference", width: 600, height: 400 }, { id: "text-image", width: 100, height: 24 }],
    fonts: [{ id: "font-0", family: "Roboto", originalName: "ABCDEF+Roboto-Regular", style: "Regular", sample: "Hello", cropAsset: "text-image" }],
    nodes: [{ id: "text", name: "Hello", type: "text", x: 20, y: 30, width: 100, height: 24, text: "Hello", fontId: "font-0", fontSize: 24, baseline: 48, fallbackAsset: "text-image", fill: [0, 0, 0, 1] },
      { id: "shape", name: "Shape", type: "vector", x: 0, y: 0, width: 50, height: 50, path: "M 0 0 L 50 0 L 50 50 Z", fill: [1, 0, 0, 1], fallbackAsset: "text-image" }] };
}
async function setup(rt, data = scene(), id = "session") {
  assert.equal((await rt.send("editable-begin", { title: "Example", jobId: "job", pageIndex: 2 }, id)).type, "editable-ready");
  const text = JSON.stringify(data), middle = Math.floor(text.length / 2);
  await rt.send("editable-scene-chunk", { chunk: text.slice(0, middle), offset: 0, total: text.length }, id);
  assert.equal((await rt.send("editable-scene-chunk", { chunk: text.slice(middle), offset: middle, total: text.length }, id)).type, "editable-scene-ready");
  for (const asset of data.assets) await rt.send("editable-asset", { assetId: asset.id, bytes: png, offset: 0, total: png.length }, id);
}
async function main() {
  {
    const rt = runtime(); await setup(rt);
    const preview = await rt.send("editable-render");
    assert.equal(preview.type, "editable-preview"); assert.equal(preview.report.text, 1); assert.equal(preview.report.vectors, 1);
    const draft = rt.page.children.find(n => n.getPluginData("canva-importer.editable-draft"));
    assert.ok(draft); assert.equal(rt.page.selection.length, 0); assert.equal(draft.children[0].characters, "Hello");
    draft.children[0].characters = "Editable!"; assert.equal(draft.children[0].characters, "Editable!");
    await rt.send("editable-commit"); assert.equal(rt.page.selection[0], draft); assert.equal(draft.getPluginData("canva-importer.editable-draft"), "");
    await rt.send("editable-commit"); assert.equal(rt.page.children.length, 1, "duplicate commit must not insert twice");
  }
  {
    const rt = runtime(); const data = scene(); data.fonts[0].family = "Missing"; data.fonts[0].originalName = "Missing"; await setup(rt, data);
    const result = await rt.send("editable-render"); assert.equal(result.report.text, 0); assert.equal(result.report.fallbacks, 1);
    const replacement = await rt.send("editable-render", { replacements: { "font-0": { family: "Roboto", style: "Regular" } } });
    assert.equal(replacement.report.text, 1); assert.ok(replacement.report.fonts[0].substituted);
    const candidates = await rt.send("editable-font-preview", { fontId: "font-0", candidates: [{ family: "Roboto", style: "Regular", reason: "test" }, { family: "Unknown", style: "Regular", reason: "test" }] });
    assert.equal(candidates.candidates[0].available, true); assert.equal(candidates.candidates[1].available, false);
    await rt.send("editable-cancel"); assert.equal(rt.page.children.length, 0);
  }
  {
    const rt = runtime(); await setup(rt); await rt.send("editable-render");
    rt.figma.createImage = () => { throw new Error("bad image"); };
    const result = await rt.send("editable-asset", { assetId: "text-image", bytes: png, offset: 0, total: png.length });
    assert.equal(result.type, "editable-failed"); assert.equal(rt.page.children.length, 0, "failed session must roll back its preview");
  }
  {
    const rt = runtime(); await setup(rt);
    let unblock;
    rt.figma.loadFontAsync = () => new Promise(resolve => { unblock = resolve; });
    const running = rt.send("editable-render");
    while (!unblock) await new Promise(resolve => setTimeout(resolve, 0));
    await rt.send("editable-cancel"); unblock(); await running;
    assert.equal(rt.page.children.length, 0, "cancelling during font loading must leave no nodes");
  }
  {
    const rt = runtime(); const abandoned = new Node("FRAME"); abandoned.setPluginData("canva-importer.editable-draft", "old"); rt.page.appendChild(abandoned);
    const unrelated = new Node("FRAME"); rt.page.appendChild(unrelated); await setup(rt);
    assert.ok(abandoned.removed); assert.ok(!unrelated.removed);
    await rt.send("editable-render", { imageOnly: true });
    const done = await rt.send("editable-commit"); assert.equal(done.report.images, 1); assert.equal(done.report.text, 0);
  }
  {
    const rt = runtime(); await rt.send("editable-begin"); const data = scene(); data.nodes[1].path = '<script>alert(1)</script>';
    assert.equal((await rt.send("editable-scene", { scene: data })).type, "editable-failed"); assert.equal(rt.page.children.length, 0);
  }
  console.log("Editable Figma session, fonts, previews, chunks, commit, and rollback tests passed.");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
