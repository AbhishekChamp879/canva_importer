/* Canva to Figma Pages — page-image import runtime. */

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

figma.ui.onmessage = async (message) => {
  if (!message || typeof message.type !== "string") return;
  if (message.type === "close-plugin") {
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
    figma.ui.postMessage({ type: "import-failed", sessionId: message.sessionId, message: text });
    figma.notify(`Import failed: ${text}`, { error: true, timeout: 6000 });
  }
};
