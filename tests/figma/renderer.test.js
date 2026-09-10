"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class MockNode {
  constructor(type, root) {
    this.type = type;
    this.root = root;
    this.children = [];
    this.parent = null;
    this.pluginData = {};
    this.fills = [];
    this.strokes = [];
    this.effects = [];
    this.visible = true;
    this.locked = false;
    this.opacity = 1;
    this.x = 0;
    this.y = 0;
    this.width = 100;
    this.height = 100;
    this.characters = "";
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

  setRangeFontName() {}
  setRangeFontSize() {}
  setRangeFills() {}
  setRangeLetterSpacing() {}
  setRangeLineHeight() {}
  setRangeTextDecoration() {}

  async exportAsync() {
    return Uint8Array.from(Buffer.from(REFERENCE_PNG, "base64"));
  }

  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter(item => item !== this);
    this.parent = null;
  }
}

function createMockFigma() {
  const currentPage = new MockNode("PAGE", null);
  currentPage.selection = [];
  const posted = [];
  let imageNumber = 0;

  function create(type) {
    const node = new MockNode(type, currentPage);
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
    loadFontAsync: async () => {},
    createImage: () => ({ hash: `image-${++imageNumber}` }),
    createImageAsync: async () => ({ hash: `image-${++imageNumber}` }),
    createFrame: () => create("FRAME"),
    createRectangle: () => create("RECTANGLE"),
    createEllipse: () => create("ELLIPSE"),
    createText: () => create("TEXT"),
    createNodeFromSvg: () => create("VECTOR"),
  };
}

function walk(node) {
  return [node, ...node.children.flatMap(walk)];
}

const REFERENCE_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==";

function createEditableTestDocument() {
  const base = (id, type, name, box, zIndex, extraction = "vision") => ({
    id, type, name, box, rotation: 0, opacity: 1, visible: true, locked: false,
    zIndex, confidence: 1, source: { sourceType: type, extraction },
  });
  return {
    schemaVersion: 1,
    id: "renderer-test-document",
    title: "Renderer test document",
    sourceUrl: "https://www.canva.com/design/ABC/Token123/view",
    createdAt: "2026-08-18T00:00:00.000Z",
    assets: {
      "reference-image": { id: "reference-image", mimeType: "image/png", dataBase64: REFERENCE_PNG, source: "reference" },
      "qa-reference": { id: "qa-reference", mimeType: "image/png", dataBase64: REFERENCE_PNG, source: "reference" },
    },
    pages: [{
      id: "renderer-test-page", name: "Renderer test page", width: 800, height: 600,
      background: { type: "solid", color: { r: 0.96, g: 0.96, b: 0.98, a: 1 } },
      qaReferenceAssetId: "qa-reference",
      metrics: {
        nativeCoverage: 0.85, fallbackCoverage: 0.15, exactTextRate: 1,
        visualSimilarity: 0.96, pixelDifference: 0.04, missingRegionRate: 0,
        duplicateTextBlocks: 0, missingFonts: [], warnings: ["Test fallback"],
      },
      children: [
        {
          ...base("hero-group", "group", "Hero group", { x: 60, y: 50, width: 680, height: 220 }, 1, "dom"),
          clipsContent: true,
          children: [
            {
              ...base("hero-background", "rectangle", "Hero background", { x: 0, y: 0, width: 680, height: 220 }, 0),
              cornerRadius: 24,
              fills: [{ type: "solid", color: { r: 0.18, g: 0.1, b: 0.55, a: 1 } }],
            },
            {
              ...base("hero-text", "text", "Editable heading", { x: 38, y: 42, width: 420, height: 90 }, 1, "ocr"),
              rotation: -2,
              text: "Editable Canva design",
              runs: [{
                start: 0, end: 21, fontFamily: "Inter", fontStyle: "Bold", fontSize: 42,
                color: { r: 1, g: 1, b: 1, a: 1 }, textDecoration: "none",
              }],
              horizontalAlign: "left", verticalAlign: "top",
            },
            {
              ...base("hero-image", "image", "Image fill", { x: 500, y: 30, width: 150, height: 150 }, 2, "asset"),
              rotation: 5,
              fill: { type: "image", assetId: "reference-image", scaleMode: "crop", transform: [[1, 0, 0], [0, 1, 0]] },
              cornerRadius: 75,
            },
          ],
        },
        {
          ...base("ellipse", "ellipse", "Native ellipse", { x: 90, y: 330, width: 100, height: 100 }, 2),
          opacity: 0.8, confidence: 0.96,
          fills: [{
            type: "linear-gradient",
            stops: [
              { position: 0, color: { r: 0.05, g: 0.72, b: 0.65, a: 1 } },
              { position: 1, color: { r: 0.15, g: 0.2, b: 0.8, a: 1 } },
            ],
            transform: [[1, 0, 0], [0, 1, 0]],
          }],
          effects: [
            { type: "drop-shadow", color: { r: 0, g: 0, b: 0, a: 0.3 }, offsetX: 2, offsetY: 4, radius: 8, spread: 1 },
            { type: "layer-blur", radius: 2 },
          ],
        },
        {
          ...base("vector", "vector", "Native vector", { x: 240, y: 330, width: 110, height: 100 }, 3),
          confidence: 0.94,
          svg: "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 110 100'><path d='M55 0L110 100H0Z' fill='#f97316'/></svg>",
        },
        {
          ...base("fallback", "raster-fallback", "Unsupported effect", { x: 430, y: 330, width: 250, height: 140 }, 4, "fallback"),
          confidence: 0.4, assetId: "reference-image", reason: "Unsupported effect retained as pixels",
        },
      ],
    }],
  };
}

async function main() {
  const pluginRoot = path.resolve(__dirname, "../../figma-plugin");
  const designDocument = createEditableTestDocument();
  const source = fs.readFileSync(path.join(pluginRoot, "code.js"), "utf8");
  const figma = createMockFigma();
  vm.runInNewContext(source, { figma, __html__: "", Uint8Array, Map, Number, String, Boolean, Math, Error, console });

  await figma.ui.onmessage({ type: "import-design", document: designDocument, jobId: "reconstruction-job-1" });
  const completed = figma.posted.find(message => message.type === "import-completed");
  assert.ok(completed, "renderer must report successful completion");
  assert.equal(completed.report.pages, 1);
  assert.equal(completed.report.fallbacks, 1);
  assert.equal(completed.report.originalAssets, 1);
  assert.equal(completed.report.croppedImages, 0);
  assert.equal(completed.report.clippedGroups, 1);
  assert.equal(completed.report.gradientFills, 1);
  assert.equal(completed.report.nativeEffects, 2);
  assert.equal(completed.report.fontSubstitutions.length, 0);
  assert.equal(completed.report.failedNodes, 0);
  assert.equal(completed.report.mode, "editable");
  assert.equal(completed.report.nativeCoverage, 0.85);
  assert.equal(completed.report.fallbackCoverage, 0.15);
  assert.equal(completed.report.exactTextRate, 1);
  assert.equal(completed.report.visualSimilarity, 0.96);
  assert.equal(completed.report.pixelDifference, 0.04);
  assert.equal(completed.report.missingRegionRate, 0);
  assert.equal(completed.report.duplicateTextBlocks, 0);
  assert.equal(completed.report.figmaQaExports, 1);
  assert.equal(completed.report.reconstructionJobId, "reconstruction-job-1");
  assert.equal(figma.currentPage.selection.length, 1);

  const qaExport = figma.posted.find(message => message.type === "figma-qa-export");
  assert.ok(qaExport, "renderer must export the final Figma frame for QA");
  assert.equal(qaExport.jobId, "reconstruction-job-1");
  assert.equal(qaExport.pageId, "renderer-test-page");
  assert.ok(qaExport.imageBytes instanceof Uint8Array);

  const pageFrame = figma.currentPage.selection[0];
  const nodes = walk(pageFrame);
  assert.ok(nodes.some(node => node.type === "TEXT" && node.characters.length > 0), "editable text node missing");
  assert.ok(nodes.some(node => node.type === "ELLIPSE"), "ellipse node missing");
  assert.ok(nodes.some(node => node.type === "VECTOR"), "vector node missing");
  assert.ok(nodes.some(node => node.pluginData["canva-importer.fallback-reason"]), "fallback metadata missing");
  assert.ok(nodes.some(node => node.name === "QA Reference · hidden" && node.visible === false), "hidden QA reference missing");
  assert.ok(nodes.some(node => node.pluginData["canva-importer.ir-id"] === "hero-group"), "source IR metadata missing");
  const heroGroup = nodes.find(node => node.pluginData["canva-importer.ir-id"] === "hero-group");
  const heroImage = nodes.find(node => node.pluginData["canva-importer.ir-id"] === "hero-image");
  const gradientEllipse = nodes.find(node => node.pluginData["canva-importer.ir-id"] === "ellipse");
  assert.equal(heroGroup.clipsContent, true, "clipped IR groups must clip their Figma children");
  assert.equal(heroImage.fills[0].scaleMode, "CROP", "image crop mode was not preserved");
  assert.deepEqual(heroImage.fills[0].imageTransform, [[1, 0, 0], [0, 1, 0]], "image crop transform was not preserved");
  assert.equal(gradientEllipse.fills[0].type, "GRADIENT_LINEAR", "native gradient was not preserved");
  assert.equal(gradientEllipse.effects[0].type, "DROP_SHADOW", "drop shadow was not preserved");
  assert.equal(gradientEllipse.effects[1].type, "LAYER_BLUR", "layer blur was not preserved");
  assert.equal(pageFrame.pluginData["canva-importer.native-coverage"], "0.85", "page quality metadata missing");
  assert.equal(pageFrame.pluginData["canva-importer.visual-similarity"], "0.96", "visual QA metadata missing");
  assert.equal(pageFrame.pluginData["canva-importer.reconstruction-job-id"], "reconstruction-job-1", "reconstruction job metadata missing");

  const invalidAssetDocument = JSON.parse(JSON.stringify(designDocument));
  invalidAssetDocument.id = "invalid-asset-document";
  invalidAssetDocument.assets["broken-image"] = { id: "broken-image", mimeType: "image/png", dataBase64: "%%%invalid%%%", source: "reference" };
  invalidAssetDocument.pages[0].children = [{
    id: "broken-image-node", type: "image", name: "Broken image", box: { x: 10, y: 10, width: 100, height: 100 },
    rotation: 0, opacity: 1, visible: true, locked: false, zIndex: 0, confidence: 1,
    source: { sourceType: "image", extraction: "asset" }, fill: { type: "image", assetId: "broken-image", scaleMode: "fill" },
  }];
  await figma.ui.onmessage({ type: "import-design", document: invalidAssetDocument });
  const invalidCompleted = [...figma.posted].reverse().find(message => message.type === "import-completed");
  assert.equal(invalidCompleted.report.failedNodes, 1, "invalid embedded images must be reported as failed nodes");
  assert.ok(walk(figma.currentPage.selection[0]).some(node => node.pluginData["canva-importer.render-error"]), "failed image layer must retain render error metadata");

  const imageBytes = Uint8Array.from(Buffer.from(designDocument.assets["reference-image"].dataBase64, "base64"));
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
