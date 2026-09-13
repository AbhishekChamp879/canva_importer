/* Canva to Figma Pages — image import and deterministic editable PDF import. */

figma.showUI(__html__, { width: 520, height: 420, themeColors: true });

const pageImageSessions = new Map();

function pageOrientation(width, height) {
  return Math.abs(width - height) / Math.max(width, height) <= 0.01 ? "square" : width > height ? "landscape" : "portrait";
}

function beginPageImageImport(sessionId, payload) {
  if (!sessionId || pageImageSessions.has(sessionId)) throw new Error("Invalid or duplicate page-image import session.");
  const pages = payload && Array.isArray(payload.pages) ? payload.pages : [];
  if (pages.length < 1) throw new Error("Select at least one Canva page.");
  const center = figma.viewport.center;
  const columns = Math.max(1, Math.min(5, Math.ceil(Math.sqrt(pages.length))));
  const maxWidth = pages.reduce((maximum, page) => Math.max(maximum, Number(page.width || 0)), 1);
  const maxHeight = pages.reduce((maximum, page) => Math.max(maximum, Number(page.height || 0)), 1);
  const gridWidth = columns * maxWidth + Math.max(0, columns - 1) * 80;
  const rows = Math.ceil(pages.length / columns);
  const gridHeight = rows * maxHeight + Math.max(0, rows - 1) * 80;
  const originX = center.x - gridWidth / 2;
  const originY = center.y - gridHeight / 2;
  const normalizedPages = pages.map((page, position) => {
    const width = Number(page.width);
    const height = Number(page.height);
    if (!page.id || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0 || width > 8192 || height > 8192) {
      throw new Error(`Page ${position + 1} has invalid capture metadata.`);
    }
    const orientation = pageOrientation(width, height);
    if (page.orientation && page.orientation !== orientation) throw new Error(`Page ${position + 1} has inconsistent orientation metadata.`);
    return { id: String(page.id), index: Number(page.index || 0), width, height, orientation, position };
  });
  if (new Set(normalizedPages.map(page => page.id)).size !== normalizedPages.length) {
    throw new Error("Captured page IDs must be unique.");
  }
  pageImageSessions.set(sessionId, {
    captureId: String(payload.captureId || ""),
    title: String(payload.title || "Canva design"),
    pages: normalizedPages,
    columns,
    maxWidth,
    maxHeight,
    originX,
    originY,
    createdFrames: [],
    importedPageIds: new Set(),
  });
}

function pageImageBytes(value) {
  const bytes = value instanceof Uint8Array ? value : new Uint8Array(value || []);
  if (bytes.length < 1) throw new Error("The captured Canva page image is empty.");
  if (bytes.length > 25 * 1024 * 1024) throw new Error("The captured Canva page image exceeds the 25MB import limit.");
  return bytes;
}

async function appendPageImage(sessionId, pageId, rawBytes) {
  const session = pageImageSessions.get(sessionId);
  if (!session) throw new Error("Page-image import session expired.");
  const page = session.pages.find(item => item.id === String(pageId));
  if (!page) throw new Error("The page does not belong to this import session.");
  if (session.importedPageIds.has(page.id)) throw new Error(`Page ${page.index + 1} was already imported.`);
  const bytes = pageImageBytes(rawBytes);
  let frame = null;
  try {
    const image = figma.createImage(bytes);
    frame = figma.createFrame();
    frame.name = `${session.title} · Page ${page.index + 1}`;
    frame.resize(page.width, page.height);
    frame.x = session.originX + (page.position % session.columns) * (session.maxWidth + 80);
    frame.y = session.originY + Math.floor(page.position / session.columns) * (session.maxHeight + 80);
    frame.clipsContent = true;
    frame.layoutMode = "NONE";
    frame.fills = [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }];
    frame.setPluginData("canva-importer.capture-id", session.captureId);
    frame.setPluginData("canva-importer.page-id", page.id);
    frame.setPluginData("canva-importer.orientation", page.orientation || pageOrientation(page.width, page.height));
    frame.setPluginData("canva-importer.mode", "page-image");

    const layer = figma.createRectangle();
    frame.appendChild(layer);
    layer.name = "Canva page image";
    layer.resize(page.width, page.height);
    layer.x = 0;
    layer.y = 0;
    layer.fills = [{ type: "IMAGE", imageHash: image.hash, scaleMode: "FILL" }];
    layer.setPluginData("canva-importer.page-id", page.id);
    layer.setPluginData("canva-importer.source", "captured-page-full-resolution");
    session.createdFrames.push(frame);
    session.importedPageIds.add(page.id);
  } catch (error) {
    if (frame) frame.remove();
    throw error;
  }
}

function finishPageImageImport(sessionId) {
  const session = pageImageSessions.get(sessionId);
  if (!session) throw new Error("Page-image import session expired.");
  if (session.importedPageIds.size !== session.pages.length) {
    throw new Error(`Only ${session.importedPageIds.size} of ${session.pages.length} pages reached Figma.`);
  }
  pageImageSessions.delete(sessionId);
  figma.currentPage.selection = session.createdFrames;
  figma.viewport.scrollAndZoomIntoView(session.createdFrames);
  return { mode: "page-images", warnings: [], pages: session.pages.length };
}

function cancelPageImageImport(sessionId) {
  const session = pageImageSessions.get(sessionId);
  if (!session) return;
  for (const frame of session.createdFrames) {
    try { frame.remove(); } catch (_error) { /* frame may already be gone */ }
  }
  pageImageSessions.delete(sessionId);
}

const editableSessions = new Map();
const editableFinished = new Map();
const DRAFT_KEY = "canva-importer.editable-draft";

function normalizeFont(value) {
  return String(value || "").replace(/^[A-Z]{6}\+/, "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function findExactFont(requirement, available) {
  const family = normalizeFont(requirement.family);
  const original = normalizeFont(requirement.originalName);
  const style = normalizeFont(requirement.style);
  const matches = available.filter(font => {
    const f = normalizeFont(font.family), s = normalizeFont(font.style);
    return (f === family && s === style) || (f + s === original) || (s === "regular" && f === original);
  });
  return matches.length === 1 ? matches[0] : null;
}

function cancelEditable(sessionId) {
  const session = editableSessions.get(sessionId);
  if (!session) return;
  session.cancelled = true;
  if (session.frame && !session.frame.removed) session.frame.remove();
  for (const node of session.scratch || []) if (!node.removed) node.remove();
  editableSessions.delete(sessionId);
}

function checkEditable(session) {
  if (session.cancelled || editableSessions.get(session.id) !== session) throw new Error("Editable import cancelled.");
}

function validateEditableScene(scene) {
  if (!scene || scene.version !== 1 || !Number.isFinite(scene.width) || !Number.isFinite(scene.height)
      || scene.width <= 0 || scene.height <= 0 || scene.width > 8192 || scene.height > 8192
      || !Array.isArray(scene.nodes) || scene.nodes.length > 10000 || !Array.isArray(scene.assets)
      || scene.assets.length > 10002 || !Array.isArray(scene.fonts) || scene.fonts.length > 1000) throw new Error("Invalid editable scene.");
  const assetIds = new Set(scene.assets.map(a => a.id)), fonts = new Set(scene.fonts.map(f => f.id)), ids = new Set();
  if (assetIds.size !== scene.assets.length || fonts.size !== scene.fonts.length || !assetIds.has(scene.referenceAsset)) throw new Error("Invalid scene assets.");
  function visit(nodes, depth) {
    if (depth > 32) throw new Error("Scene nesting limit exceeded.");
    for (const node of nodes) {
      if (!node || ids.has(node.id) || ids.size >= 10000 || !["text", "vector", "image", "group"].includes(node.type)
          || ![node.x, node.y, node.width, node.height].every(Number.isFinite) || node.width <= 0 || node.height <= 0
          || node.width > 32768 || node.height > 32768 || Math.abs(node.x) > 1e7 || Math.abs(node.y) > 1e7) throw new Error("Invalid scene node.");
      ids.add(node.id);
      for (const id of [node.assetId, node.fallbackAsset]) if (id && !assetIds.has(id)) throw new Error("Missing image dependency.");
      if (node.fontId && !fonts.has(node.fontId)) throw new Error("Missing font dependency.");
      if (node.type === "text" && (typeof node.text !== "string" || !node.text || node.text.length > 10000
          || !Number.isFinite(node.fontSize) || node.fontSize <= 0 || node.fontSize > 8192 || !node.fallbackAsset)) throw new Error("Invalid text node.");
      if (node.type === "image" && !node.assetId) throw new Error("Missing image.");
      if (node.type === "vector" && (typeof node.path !== "string" || node.path.length > 1000000 || !/^[MmLlCcZz0-9eE+.,\s-]+$/.test(node.path))) throw new Error("Invalid vector path.");
      for (const color of [node.fill, node.stroke]) if (color && (!Array.isArray(color) || color.length !== 4 || !color.every(v => Number.isFinite(v) && v >= 0 && v <= 1))) throw new Error("Invalid object color.");
      if (!Number.isFinite(node.strokeWidth || 0) || (node.strokeWidth || 0) < 0 || (node.strokeWidth || 0) > 8192) throw new Error("Invalid stroke width.");
      visit(node.children || [], depth + 1);
    }
  }
  visit(scene.nodes, 0);
}

async function beginEditable(message) {
  if (!message.sessionId || editableSessions.size || editableFinished.has(message.sessionId)) throw new Error("Another editable import is active or this session was already used.");
  // Only our own abandoned, top-level draft frames are eligible for cleanup.
  for (const child of [...(figma.currentPage.children || [])]) {
    if (child.type === "FRAME" && child.getPluginData && child.getPluginData(DRAFT_KEY)) child.remove();
  }
  const session = { id: message.sessionId, page: figma.currentPage, assets: new Map(), incoming: new Map(), bytes: 0,
    scene: null, cancelled: false, busy: false, frame: null, title: String(message.title || "Canva design").slice(0, 256),
    jobId: String(message.jobId || ""), pageIndex: Number(message.pageIndex || 0), selected: [...figma.currentPage.selection] };
  editableSessions.set(session.id, session);
  try {
    session.fonts = (await figma.listAvailableFontsAsync()).map(f => f.fontName);
    checkEditable(session);
    return { type: "editable-ready", fonts: session.fonts };
  } catch (error) { cancelEditable(session.id); throw error; }
}

function appendEditableAsset(session, message) {
  if (session.busy || !session.scene || !session.scene.assets.some(a => a.id === message.assetId) || session.assets.has(message.assetId)) throw new Error("Invalid or duplicate editable asset.");
  const bytes = new Uint8Array(message.bytes || []);
  if (!bytes.length || bytes.length > 512 * 1024 || !Number.isInteger(message.offset) || !Number.isInteger(message.total)
      || message.total <= 0 || message.total > 25 * 1024 * 1024) throw new Error("Invalid asset chunk.");
  let incoming = session.incoming.get(message.assetId);
  if (!incoming) {
    if (message.offset !== 0 || session.bytes + message.total > 512 * 1024 * 1024) throw new Error("Asset transfer exceeds the limit.");
    incoming = { bytes: new Uint8Array(message.total), offset: 0 };
    session.incoming.set(message.assetId, incoming);
    session.bytes += message.total;
  }
  if (message.total !== incoming.bytes.length || message.offset !== incoming.offset || incoming.offset + bytes.length > message.total) throw new Error("Duplicate or out-of-order asset chunk.");
  incoming.bytes.set(bytes, incoming.offset);
  incoming.offset += bytes.length;
  if (incoming.offset === message.total) {
    session.assets.set(message.assetId, figma.createImage(incoming.bytes).hash);
    session.incoming.delete(message.assetId);
  }
}

function imageObject(session, source, assetId, parent, reason) {
  const hash = session.assets.get(assetId);
  if (!hash) throw new Error("An editable image asset is missing.");
  const node = figma.createRectangle();
  parent.appendChild(node);
  node.resize(source.width, source.height);
  node.x = source.x; node.y = source.y;
  node.name = reason ? `${source.name} (image fallback)` : source.name;
  node.fills = [{ type: "IMAGE", imageHash: hash, scaleMode: "FILL" }];
  if (reason) node.setPluginData("canva-importer.fallback", reason);
  if (source.text) node.setPluginData("canva-importer.original-text", source.text);
  if (source.fontId) node.setPluginData("canva-importer.font-id", source.fontId);
  return node;
}

async function renderEditable(session, replacements = {}, imageOnly = false) {
  checkEditable(session);
  if (session.busy || !session.scene || session.incoming.size) throw new Error("Editable scene is not ready.");
  session.busy = true;
  try {
    const scene = session.scene;
    for (const asset of scene.assets) if (!session.assets.has(asset.id)) throw new Error("Asset transfer is incomplete.");
    if (session.frame && !session.frame.removed) session.frame.remove();
    const frame = figma.createFrame(); session.frame = frame;
    session.page.appendChild(frame);
    frame.name = `${session.title} · Page ${session.pageIndex + 1} · Preview`;
    frame.resize(scene.width, scene.height); frame.x = -100000; frame.y = -100000;
    frame.clipsContent = true; frame.layoutMode = "NONE"; frame.fills = [];
    frame.setPluginData(DRAFT_KEY, session.id);
    frame.setPluginData("canva-importer.job-id", session.jobId);
    frame.setPluginData("canva-importer.mode", imageOnly ? "pdf-image" : "editable-pdf");
    const report = { pages: 1, text: 0, vectors: 0, images: 0, fallbacks: 0, warnings: [...(scene.warnings || [])], fonts: [] };
    const resolved = new Map();
    for (const requirement of scene.fonts) {
      const override = replacements[requirement.id];
      let match = override === "appearance" ? null : override
        ? session.fonts.find(f => f.family === override.family && f.style === override.style)
        : findExactFont(requirement, session.fonts);
      if (match) { try { await figma.loadFontAsync(match); } catch (_) { match = null; } }
      checkEditable(session);
      resolved.set(requirement.id, match);
      report.fonts.push({ id: requirement.id, matched: match, substituted: Boolean(override && override !== "appearance" && match) });
      if (!match) report.warnings.push(`${requirement.originalName}: original appearance retained; font unavailable or appearance selected.`);
      else if (override) report.warnings.push(`${requirement.originalName}: substituted with ${match.family} ${match.style}.`);
    }
    async function visit(sources, parent) {
      for (const [index, source] of sources.entries()) {
        checkEditable(session);
        if (index % 30 === 0) { await new Promise(resolve => setTimeout(resolve, 0)); checkEditable(session); }
        if (source.type === "group") {
          const group = figma.createFrame(); parent.appendChild(group);
          group.resize(scene.width, scene.height); group.x = 0; group.y = 0; group.fills = []; group.clipsContent = false; group.name = source.name;
          await visit(source.children || [], group); continue;
        }
        if (source.type === "text" && resolved.get(source.fontId)) {
          let text = null;
          try {
            text = figma.createText(); parent.appendChild(text);
            text.fontName = resolved.get(source.fontId); text.fontSize = source.fontSize;
            text.lineHeight = { unit: "PIXELS", value: source.fontSize };
            text.textAutoResize = "WIDTH_AND_HEIGHT"; text.characters = source.text;
            text.x = source.x; text.y = (source.baseline == null ? source.y + source.fontSize * 0.8 : source.baseline) - source.fontSize * 0.8;
            text.name = source.name;
            const c = source.fill || [0, 0, 0, 1];
            text.fills = [{ type: "SOLID", color: { r: c[0], g: c[1], b: c[2] }, opacity: c[3] }];
            text.setPluginData("canva-importer.font-id", source.fontId);
            text.setPluginData("canva-importer.original-text", source.text);
            // PDF ink bounds and native font advances differ. Large differences are not
            // silently accepted for an automatic exact match; explicit substitutes are previewed.
            if (!replacements[source.fontId] && Math.abs(text.width - source.width) > Math.max(4, source.width * 0.08)) throw new Error("Font metrics differ from PDF");
            report.text++; continue;
          } catch (_) { if (text && !text.removed) text.remove(); }
        }
        if (source.type === "vector") {
          try {
            const css = c => c ? `rgba(${Math.round(c[0] * 255)},${Math.round(c[1] * 255)},${Math.round(c[2] * 255)},${c[3]})` : "none";
            const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${scene.width}" height="${scene.height}" viewBox="0 0 ${scene.width} ${scene.height}"><path d="${source.path}" fill="${css(source.fill)}" stroke="${css(source.stroke)}" stroke-width="${source.strokeWidth || 0}" fill-rule="${source.fillRule === "EVENODD" ? "evenodd" : "nonzero"}"/></svg>`;
            const vector = figma.createNodeFromSvg(svg); parent.appendChild(vector); vector.x = 0; vector.y = 0; vector.name = source.name;
            report.vectors++; continue;
          } catch (_) { /* preserve exact rasterized appearance below */ }
        }
        const reason = source.fallbackReason || (source.type !== "image" ? "Native font or vector could not be reproduced" : null);
        imageObject(session, source, source.assetId || source.fallbackAsset, parent, reason);
        report.images++;
        if (reason) { report.fallbacks++; report.warnings.push(`${source.id}: ${reason}`); }
      }
    }
    if (imageOnly || scene.wholePageFallback) {
      imageObject(session, { x: 0, y: 0, width: scene.width, height: scene.height, name: "PDF page image" }, scene.referenceAsset, frame, "Whole-page image import");
      report.images = 1; report.fallbacks = 1;
    } else await visit(scene.nodes, frame);
    checkEditable(session);
    const preview = await frame.exportAsync({ format: "PNG", constraint: { type: "WIDTH", value: Math.min(1000, scene.width) } });
    checkEditable(session);
    session.report = report;
    return { type: "editable-preview", preview, report };
  } finally { session.busy = false; }
}

async function handleEditable(message) {
  if (message.type === "editable-begin") return beginEditable(message);
  if (message.type === "editable-cancel") { cancelEditable(message.sessionId); return { type: "editable-cancelled" }; }
  if (message.type === "editable-commit" && editableFinished.has(message.sessionId)) return editableFinished.get(message.sessionId);
  const session = editableSessions.get(message.sessionId);
  if (!session) throw new Error("Editable session expired.");
  checkEditable(session);
  if (message.type === "editable-scene") {
    if (session.scene || session.busy) throw new Error("Duplicate scene transfer.");
    validateEditableScene(message.scene); session.scene = message.scene;
    return { type: "editable-scene-ready" };
  }
  if (message.type === "editable-scene-chunk") {
    if (session.scene || session.busy || typeof message.chunk !== "string" || message.chunk.length > 131072
        || !Number.isInteger(message.offset) || !Number.isInteger(message.total) || message.total <= 0 || message.total > 64 * 1024 * 1024) throw new Error("Invalid scene chunk.");
    if (!session.sceneTransfer) session.sceneTransfer = { chunks: [], offset: 0, total: message.total };
    const transfer = session.sceneTransfer;
    if (transfer.offset !== message.offset || transfer.total !== message.total || transfer.offset + message.chunk.length > transfer.total) throw new Error("Out-of-order scene chunk.");
    transfer.chunks.push(message.chunk); transfer.offset += message.chunk.length;
    if (transfer.offset === transfer.total) {
      const scene = JSON.parse(transfer.chunks.join("")); validateEditableScene(scene);
      session.scene = scene; session.sceneTransfer = null;
    }
    return { type: "editable-scene-ready" };
  }
  if (message.type === "editable-asset") { appendEditableAsset(session, message); return { type: "editable-asset-ready", assetId: message.assetId, offset: message.offset }; }
  if (message.type === "editable-render") return renderEditable(session, message.replacements || {}, Boolean(message.imageOnly));
  if (message.type === "editable-font-preview") {
    if (session.busy || !session.scene || !Array.isArray(message.candidates) || message.candidates.length > 3) throw new Error("Font preview is not ready.");
    const requirement = session.scene.fonts.find(f => f.id === message.fontId);
    if (!requirement) throw new Error("Unknown font requirement.");
    session.busy = true; session.scratch = [];
    try {
      const candidates = [];
      for (const candidate of message.candidates) {
        const match = session.fonts.find(f => normalizeFont(f.family) === normalizeFont(candidate.family) && normalizeFont(f.style) === normalizeFont(candidate.style));
        const result = { ...candidate, available: Boolean(match), font: match || null };
        if (match && requirement.sample) {
          try {
            await figma.loadFontAsync(match); checkEditable(session);
            const node = figma.createText(); session.scratch.push(node);
            node.fontName = match; node.fontSize = 32; node.textAutoResize = "WIDTH_AND_HEIGHT";
            node.characters = requirement.sample.slice(0, 256); node.x = -100000; node.y = -100000;
            result.preview = await node.exportAsync({ format: "PNG", constraint: { type: "WIDTH", value: Math.min(600, Math.ceil(node.width)) } });
            checkEditable(session);
          } catch (error) { checkEditable(session); result.available = false; }
        }
        candidates.push(result);
      }
      return { type: "editable-font-candidates", candidates };
    } finally {
      for (const node of session.scratch) if (!node.removed) node.remove();
      session.scratch = []; session.busy = false;
    }
  }
  if (message.type === "editable-commit") {
    if (session.busy || !session.report || !session.frame || session.frame.removed) throw new Error("Preview the result before importing.");
    if (figma.currentPage !== session.page) throw new Error("The Figma page changed. Return to the original page and retry.");
    session.frame.name = `${session.title} · Page ${session.pageIndex + 1}`;
    session.frame.x = figma.viewport.center.x - session.scene.width / 2;
    session.frame.y = figma.viewport.center.y - session.scene.height / 2;
    session.frame.setPluginData(DRAFT_KEY, "");
    figma.currentPage.selection = [session.frame]; figma.viewport.scrollAndZoomIntoView([session.frame]);
    editableSessions.delete(session.id);
    const result = { type: "editable-completed", report: session.report };
    editableFinished.set(session.id, result);
    if (editableFinished.size > 20) editableFinished.delete(editableFinished.keys().next().value);
    return result;
  }
  throw new Error("Unknown editable operation.");
}

figma.ui.onmessage = async (message) => {
  if (!message || typeof message.type !== "string") return;
  if (message.type === "close-plugin") {
    for (const id of [...editableSessions.keys()]) cancelEditable(id);
    figma.closePlugin();
    return;
  }
  if (message.type === "open-external") {
    if (typeof message.url !== "string" || !message.url.startsWith("https://www.canva.com/api/oauth/authorize?")) {
      figma.ui.postMessage({ type: "import-failed", message: "Blocked an invalid external authorization URL." });
      return;
    }
    figma.openExternal(message.url);
    return;
  }
  try {
    if (message.type.startsWith("editable-")) {
      const result = await handleEditable(message);
      figma.ui.postMessage({ ...result, sessionId: message.sessionId, requestId: message.requestId });
      return;
    }
    if (message.type === "begin-page-image-import") {
      beginPageImageImport(message.sessionId, message.payload);
      figma.ui.postMessage({ type: "page-image-import-ready", sessionId: message.sessionId });
      return;
    }
    if (message.type === "append-page-image") {
      await appendPageImage(message.sessionId, message.pageId, message.imageBytes);
      figma.ui.postMessage({ type: "page-image-import-page", sessionId: message.sessionId, pageId: message.pageId });
      return;
    }
    if (message.type === "cancel-page-image-import") {
      cancelPageImageImport(message.sessionId);
      return;
    }
    let report;
    if (message.type === "finish-page-image-import") {
      report = finishPageImageImport(message.sessionId);
    } else {
      return;
    }
    const completion = { type: "import-completed", report };
    if (message.sessionId) completion.sessionId = message.sessionId;
    figma.ui.postMessage(completion);
    const detail = report.warnings.length ? ` ${report.warnings.length} warning(s).` : "";
    figma.notify(`Imported ${report.pages} Canva page(s) as images.${detail}`, { timeout: 5000 });
  } catch (error) {
    const text = error && error.message ? error.message : String(error);
    if (message.sessionId) cancelPageImageImport(message.sessionId);
    if (message.type.startsWith("editable-")) {
      cancelEditable(message.sessionId);
      figma.ui.postMessage({ type: "editable-failed", sessionId: message.sessionId, requestId: message.requestId, message: text });
      return;
    }
    figma.ui.postMessage({ type: "import-failed", sessionId: message.sessionId, message: text });
    figma.notify(`Import failed: ${text}`, { error: true, timeout: 6000 });
  }
};
