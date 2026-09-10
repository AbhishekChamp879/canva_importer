"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class MockNode {
  constructor(type) {
    this.type = type;
    this.children = [];
    this.parent = null;
    this.pluginData = {};
    this.fills = [];
    this.x = 0;
    this.y = 0;
    this.width = 100;
    this.height = 100;
    this.name = type;
  }

  appendChild(child) {
    if (child.parent) child.parent.children = child.parent.children.filter(item => item !== child);
    child.parent = this;
    this.children.push(child);
  }

  resize(width, height) {
    this.width = width;
    this.height = height;
  }

  setPluginData(key, value) {
    this.pluginData[key] = value;
  }

  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter(item => item !== this);
    this.parent = null;
  }
}

function createMockFigma() {
  const currentPage = new MockNode("PAGE");
  currentPage.selection = [];
  const posted = [];
  let imageNumber = 0;

  function create(type) {
    const node = new MockNode(type);
    currentPage.appendChild(node);
    return node;
  }

  return {
    currentPage,
    posted,
    ui: {
      onmessage: null,
      postMessage(message) { posted.push(message); },
    },
    viewport: {
      center: { x: 500, y: 400 },
      scrollAndZoomIntoView() {},
    },
    showUI() {},
    closePlugin() {},
    notify() {},
    createImage: () => ({ hash: `image-${++imageNumber}` }),
    createFrame: () => create("FRAME"),
    createRectangle: () => create("RECTANGLE"),
  };
}

const REFERENCE_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==";

async function main() {
  const pluginRoot = path.resolve(__dirname, "../../figma-plugin");
  const source = fs.readFileSync(path.join(pluginRoot, "code.js"), "utf8");
  const figma = createMockFigma();
  vm.runInNewContext(source, { figma, __html__: "", Uint8Array, Map, Number, String, Boolean, Math, Error, console });

  const imageBytes = Uint8Array.from(Buffer.from(REFERENCE_PNG, "base64"));
  const dimensions = [[800, 600], [600, 800], [700, 700]];
  const capturedPages = Array.from({ length: 12 }, (_value, index) => ({
    id: `page-${index + 1}`, index,
    width: dimensions[index % dimensions.length][0],
    height: dimensions[index % dimensions.length][1],
    orientation: ["landscape", "portrait", "square"][index % dimensions.length],
  }));
  await figma.ui.onmessage({
    type: "begin-page-image-import",
    sessionId: "image-session",
    payload: {
      captureId: "capture-1",
      title: "Captured Canva",
      pages: capturedPages,
    },
  });
  for (const page of capturedPages) {
    await figma.ui.onmessage({ type: "append-page-image", sessionId: "image-session", pageId: page.id, imageBytes });
  }
  await figma.ui.onmessage({ type: "finish-page-image-import", sessionId: "image-session" });
  const imageCompleted = [...figma.posted].reverse().find(message => message.type === "import-completed");
  assert.equal(imageCompleted.report.mode, "page-images");
  assert.equal(imageCompleted.report.pages, 12);
  assert.equal(figma.currentPage.selection.length, 12);
  assert.ok(figma.currentPage.selection.every(frame => frame.children.some(node => node.name === "Canva page image")), "flat page image missing");
  assert.ok(figma.currentPage.selection.every((frame, index) => frame.width === capturedPages[index].width && frame.height === capturedPages[index].height), "mixed portrait/landscape logical dimensions were not preserved");
  assert.equal(figma.currentPage.selection.slice(0, 3).map(frame => frame.pluginData["canva-importer.orientation"]).join(","), "landscape,portrait,square", "page orientation metadata was not preserved");
  assert.ok(figma.currentPage.selection.every(frame => frame.children[0].pluginData["canva-importer.source"] === "captured-page-full-resolution"), "full-resolution source metadata missing");
  assert.equal(new Set(figma.currentPage.selection.map(frame => `${frame.x}|${frame.y}`)).size, 12, "large imports must use distinct grid positions");

  const frameCountBeforeCancellation = figma.currentPage.children.filter(node => node.type === "FRAME").length;
  await figma.ui.onmessage({
    type: "begin-page-image-import",
    sessionId: "cancel-session",
    payload: { captureId: "capture-2", title: "Cancelled", pages: capturedPages.slice(0, 2) },
  });
  await figma.ui.onmessage({ type: "append-page-image", sessionId: "cancel-session", pageId: capturedPages[0].id, imageBytes });
  await figma.ui.onmessage({ type: "cancel-page-image-import", sessionId: "cancel-session" });
  assert.equal(figma.currentPage.children.filter(node => node.type === "FRAME").length, frameCountBeforeCancellation, "cancelled imports must roll back partial frames");
  console.log("Figma renderer tests passed.");
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
