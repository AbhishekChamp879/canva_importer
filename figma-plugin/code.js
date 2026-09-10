/* Canva to Editable Figma — dependency-free Figma sandbox runtime. */

figma.showUI(__html__, { width: 520, height: 420, themeColors: true });

const FALLBACK_FONT = { family: "Inter", style: "Regular" };
const imageCache = new Map();
const pageImageSessions = new Map();

function pageOrientation(width, height) {
  return Math.abs(width - height) / Math.max(width, height) <= 0.01 ? "square" : width > height ? "landscape" : "portrait";
}

function requireDesignDocument(document) {
  if (!document || document.schemaVersion !== 1 || !Array.isArray(document.pages) || !document.assets) {
    throw new Error("The converter returned an unsupported Design IR document.");
  }
  if (document.pages.length < 1) {
    throw new Error("A Design IR document must contain at least one page.");
  }
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, Number(value)));
}

function color(value) {
  const source = value || {};
  return {
    r: clamp(source.r || 0, 0, 1),
    g: clamp(source.g || 0, 0, 1),
    b: clamp(source.b || 0, 0, 1),
  };
}

function colorWithAlpha(value) {
  return { ...color(value), a: clamp(value && value.a == null ? 1 : value && value.a, 0, 1) };
}

function bytesFromBase64(value) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  const clean = String(value || "").replace(/^data:[^,]+,/, "").replace(/\s/g, "");
  if (!clean || clean.length % 4 === 1 || !/^[A-Za-z0-9+/]*={0,2}$/.test(clean)) {
    throw new Error("Image asset contains invalid base64 data.");
  }
  const output = [];
  let buffer = 0;
  let bits = 0;
  for (const character of clean) {
    if (character === "=") break;
    const index = alphabet.indexOf(character);
    if (index < 0) continue;
    buffer = (buffer << 6) | index;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      output.push((buffer >> bits) & 255);
    }
  }
  return new Uint8Array(output);
}

async function imageHash(assetId, assets, warnings) {
  if (imageCache.has(assetId)) return imageCache.get(assetId);
  const asset = assets[assetId];
  if (!asset) throw new Error(`Missing image asset: ${assetId}`);

  let image;
  if (asset.dataBase64) {
    image = figma.createImage(bytesFromBase64(asset.dataBase64));
  } else if (asset.url && typeof figma.createImageAsync === "function") {
    try {
      image = await figma.createImageAsync(String(asset.url));
    } catch (_error) {
      warnings.push(`Could not download URL asset ${assetId}.`);
      throw new Error(`Could not download image asset: ${assetId}`);
    }
  } else {
    throw new Error(`Asset ${assetId} has no embedded image data.`);
  }
  imageCache.set(assetId, image.hash);
  return image.hash;
}

function imageScaleMode(value) {
  return ({ fit: "FIT", crop: "CROP", tile: "TILE", fill: "FILL" })[String(value || "fill").toLowerCase()] || "FILL";
}

function validatedImageTransform(value) {
  if (!Array.isArray(value) || value.length !== 2 || value.some(row => !Array.isArray(row) || row.length !== 3)) {
    throw new Error("Image crop transform must be a 2x3 affine matrix.");
  }
  const normalized = value.map(row => row.map(number => Number(number)));
  if (normalized.some(row => row.some(number => !Number.isFinite(number) || Math.abs(number) > 1000000))) {
    throw new Error("Image crop transform contains an invalid value.");
  }
  return normalized;
}

async function paintFromFill(fill, assets, warnings) {
  if (!fill) return null;
  if (fill.type === "solid") {
    return { type: "SOLID", color: color(fill.color), opacity: clamp(fill.color && fill.color.a == null ? 1 : fill.color.a, 0, 1) };
  }
  if (fill.type === "image") {
    const scaleMode = imageScaleMode(fill.scaleMode);
    const paint = {
      type: "IMAGE",
      imageHash: await imageHash(fill.assetId, assets, warnings),
      scaleMode,
    };
    if (fill.transform != null) {
      if (scaleMode !== "CROP") throw new Error("Image transforms require crop scale mode.");
      paint.imageTransform = validatedImageTransform(fill.transform);
    }
    return paint;
  }
  if (fill.type === "linear-gradient" || fill.type === "radial-gradient") {
    if (!Array.isArray(fill.stops) || fill.stops.length < 2) throw new Error("Gradient fill requires at least two stops.");
    return {
      type: fill.type === "radial-gradient" ? "GRADIENT_RADIAL" : "GRADIENT_LINEAR",
      gradientStops: fill.stops.map(stop => ({
        position: clamp(stop.position, 0, 1),
        color: colorWithAlpha(stop.color),
      })),
      gradientTransform: fill.transform == null ? [[1, 0, 0], [0, 1, 0]] : validatedImageTransform(fill.transform),
    };
  }
  return null;
}

function effectsFromIr(effects, warnings) {
  const result = [];
  for (const effect of effects || []) {
    if (effect.type === "drop-shadow") {
      result.push({
        type: "DROP_SHADOW",
        color: colorWithAlpha(effect.color),
        offset: { x: Number(effect.offsetX || 0), y: Number(effect.offsetY || 0) },
        radius: Math.max(0, Number(effect.radius || 0)),
        spread: Number(effect.spread || 0),
        visible: effect.visible !== false,
        blendMode: "NORMAL",
      });
    } else if (effect.type === "layer-blur") {
      result.push({ type: "LAYER_BLUR", radius: Math.max(0.01, Number(effect.radius || 0)), visible: effect.visible !== false });
    } else {
      warnings.push(`Unsupported native effect: ${effect.type || "unknown"}.`);
    }
  }
  return result;
}

async function paints(fills, assets, warnings) {
  const result = [];
  for (const fill of fills || []) {
    try {
      const paint = await paintFromFill(fill, assets, warnings);
      if (paint) result.push(paint);
    } catch (error) {
      warnings.push(error.message || String(error));
    }
  }
  return result;
}

function setPluginMetadata(figmaNode, irNode) {
  figmaNode.setPluginData("canva-importer.ir-id", String(irNode.id || ""));
  figmaNode.setPluginData("canva-importer.z-index", String(irNode.zIndex == null ? 0 : irNode.zIndex));
  figmaNode.setPluginData("canva-importer.confidence", String(irNode.confidence == null ? 0 : irNode.confidence));
  if (irNode.source) {
    figmaNode.setPluginData("canva-importer.extraction", String(irNode.source.extraction || ""));
    figmaNode.setPluginData("canva-importer.source-type", String(irNode.source.sourceType || ""));
    if (irNode.source.canvaId) figmaNode.setPluginData("canva-importer.canva-id", String(irNode.source.canvaId));
  }
  if (irNode.reason) figmaNode.setPluginData("canva-importer.fallback-reason", String(irNode.reason));
}

function applyGeometry(figmaNode, irNode) {
  const box = irNode.box;
  if (typeof figmaNode.resize === "function") {
    figmaNode.resize(Math.max(0.01, box.width), Math.max(0.01, box.height));
  }
  figmaNode.x = box.x;
  figmaNode.y = box.y;
  figmaNode.rotation = Number(irNode.rotation || 0);
  figmaNode.opacity = clamp(irNode.opacity == null ? 1 : irNode.opacity, 0, 1);
  figmaNode.visible = irNode.visible !== false;
  figmaNode.locked = Boolean(irNode.locked);
  figmaNode.name = irNode.name || `${irNode.type} · ${irNode.id}`;
  setPluginMetadata(figmaNode, irNode);
}

async function loadFont(requested, report) {
  const font = {
    family: requested && requested.family ? requested.family : FALLBACK_FONT.family,
    style: requested && requested.style ? requested.style : FALLBACK_FONT.style,
  };
  const styleAliases = {
    "Semi Bold": ["Semibold", "Medium", "Bold"],
    "Semi Bold Italic": ["Semibold Italic", "Medium Italic", "Bold Italic", "Italic"],
    "Extra Bold": ["Extrabold", "Bold"],
    "Extra Bold Italic": ["Extrabold Italic", "Bold Italic", "Italic"],
    "Extra Light": ["Extralight", "Light", "Regular"],
    "Extra Light Italic": ["Extralight Italic", "Light Italic", "Italic"],
    "Black Italic": ["Heavy Italic", "Bold Italic", "Italic"],
  };
  const candidates = [font];
  for (const style of styleAliases[font.style] || []) candidates.push({ family: font.family, style });
  if (font.style !== "Regular") candidates.push({ family: font.family, style: font.style.includes("Italic") ? "Italic" : "Regular" });
  candidates.push({ family: FALLBACK_FONT.family, style: font.style }, FALLBACK_FONT);
  const unique = candidates.filter((candidate, index, all) => all.findIndex(other => other.family === candidate.family && other.style === candidate.style) === index);
  for (const candidate of unique) {
    try {
      await figma.loadFontAsync(candidate);
      if (candidate.family !== font.family || candidate.style !== font.style) {
        const requestedDescription = `${font.family} ${font.style}`;
        const substitution = `${requestedDescription} → ${candidate.family} ${candidate.style}`;
        if (!report.missingFonts.includes(requestedDescription)) report.missingFonts.push(requestedDescription);
        if (!report.fontSubstitutions.includes(substitution)) report.fontSubstitutions.push(substitution);
      }
      return candidate;
    } catch (_error) {
      // Try the next deterministic style/family candidate.
    }
  }
  throw new Error(`Could not load requested or fallback font: ${font.family} ${font.style}`);
}

async function createText(irNode, context) {
  const node = figma.createText();
  context.parent.appendChild(node);
  const runs = Array.isArray(irNode.runs) ? irNode.runs : [];
  const resolvedFonts = [];
  for (const run of runs) {
    resolvedFonts.push(await loadFont({ family: run.fontFamily, style: run.fontStyle }, context.report));
  }
  if (!resolvedFonts.length) resolvedFonts.push(await loadFont(FALLBACK_FONT, context.report));
  node.fontName = resolvedFonts[0];
  node.characters = String(irNode.text || "");
  node.textAutoResize = "NONE";
  node.resize(Math.max(0.01, irNode.box.width), Math.max(0.01, irNode.box.height));
  node.textAlignHorizontal = ({ center: "CENTER", right: "RIGHT", justify: "JUSTIFIED" })[irNode.horizontalAlign] || "LEFT";
  node.textAlignVertical = ({ center: "CENTER", bottom: "BOTTOM" })[irNode.verticalAlign] || "TOP";

  runs.forEach((run, index) => {
    const start = clamp(run.start, 0, node.characters.length);
    const end = clamp(run.end, start, node.characters.length);
    if (end <= start) return;
    node.setRangeFontName(start, end, resolvedFonts[index]);
    node.setRangeFontSize(start, end, Math.max(1, Number(run.fontSize || 12)));
    node.setRangeFills(start, end, [{ type: "SOLID", color: color(run.color), opacity: clamp(run.color && run.color.a == null ? 1 : run.color.a, 0, 1) }]);
    if (run.letterSpacing != null) node.setRangeLetterSpacing(start, end, { unit: "PIXELS", value: Number(run.letterSpacing) });
    if (run.lineHeight != null) node.setRangeLineHeight(start, end, { unit: "PIXELS", value: Math.max(1, Number(run.lineHeight)) });
    node.setRangeTextDecoration(start, end, ({ underline: "UNDERLINE", strikethrough: "STRIKETHROUGH" })[run.textDecoration] || "NONE");
  });
  if (Array.isArray(irNode.effects) && irNode.effects.length && "effects" in node) {
    node.effects = effectsFromIr(irNode.effects, context.report.warnings);
    context.report.nativeEffects += node.effects.length;
  }
  applyGeometry(node, irNode);
  return node;
}

async function createVisualNode(irNode, context) {
  let node;
  if (irNode.type === "ellipse") {
    node = figma.createEllipse();
  } else if (irNode.type === "vector" && irNode.svg) {
    node = figma.createNodeFromSvg(irNode.svg);
  } else if (irNode.type === "group") {
    node = figma.createFrame();
    node.fills = [];
    node.strokes = [];
    node.clipsContent = Boolean(irNode.clipsContent);
    node.layoutMode = "NONE";
  } else if (irNode.type === "image" || irNode.type === "rectangle" || irNode.type === "raster-fallback") {
    node = figma.createRectangle();
  } else {
    throw new Error(`Unsupported Design IR node type: ${irNode.type}`);
  }
  context.parent.appendChild(node);
  applyGeometry(node, irNode);

  if (irNode.type === "image") {
    if (irNode.source && irNode.source.extraction === "asset") context.report.originalAssets += 1;
    else context.report.croppedImages += 1;
  }
  if (irNode.type === "group" && irNode.clipsContent) context.report.clippedGroups += 1;

  if (irNode.type === "image" && irNode.fill) {
    try {
      node.fills = [await paintFromFill(irNode.fill, context.assets, context.report.warnings)];
    } catch (error) {
      node.fills = [{ type: "SOLID", color: { r: 0.85, g: 0.85, b: 0.85 } }];
      context.report.failedNodes += 1;
      const message = error.message || String(error);
      node.name = `⚠ ${node.name}`;
      node.setPluginData("canva-importer.render-error", message);
      context.report.warnings.push(`${irNode.id}: ${message}`);
    }
  } else if (irNode.type === "raster-fallback") {
    context.report.fallbacks += 1;
    try {
      const hash = await imageHash(irNode.assetId, context.assets, context.report.warnings);
      node.fills = [{ type: "IMAGE", imageHash: hash, scaleMode: "FILL" }];
    } catch (error) {
      node.fills = [{ type: "SOLID", color: { r: 1, g: 0.85, b: 0.85 } }];
      context.report.failedNodes += 1;
      const message = error.message || String(error);
      node.name = `⚠ ${node.name}`;
      node.setPluginData("canva-importer.render-error", message);
      context.report.warnings.push(`${irNode.id}: ${message}`);
    }
  } else if (irNode.type !== "group" && irNode.type !== "vector") {
    const warningCount = context.report.warnings.length;
    node.fills = await paints(irNode.fills, context.assets, context.report.warnings);
    if ((irNode.fills || []).length && !node.fills.length && context.report.warnings.length > warningCount) {
      context.report.failedNodes += 1;
      node.name = `⚠ ${node.name}`;
      node.setPluginData("canva-importer.render-error", "No valid fills could be rendered.");
    }
  }

  if (Array.isArray(irNode.fills) && irNode.fills.some(fill => fill && (fill.type === "linear-gradient" || fill.type === "radial-gradient"))) {
    context.report.gradientFills += 1;
  }
  if (Array.isArray(irNode.effects) && irNode.effects.length && "effects" in node) {
    node.effects = effectsFromIr(irNode.effects, context.report.warnings);
    context.report.nativeEffects += node.effects.length;
  }

  if (irNode.stroke && "strokes" in node) {
    node.strokes = [{ type: "SOLID", color: color(irNode.stroke.color), opacity: clamp(irNode.stroke.color && irNode.stroke.color.a == null ? 1 : irNode.stroke.color.a, 0, 1) }];
    node.strokeWeight = Number(irNode.stroke.weight || 0);
    if ("strokeAlign" in node) node.strokeAlign = String(irNode.stroke.align || "inside").toUpperCase();
  }
  if (irNode.cornerRadius != null && "cornerRadius" in node) node.cornerRadius = Math.max(0, Number(irNode.cornerRadius));

  if (irNode.type === "group") {
    const children = [...(irNode.children || [])].sort((a, b) => Number(a.zIndex || 0) - Number(b.zIndex || 0));
    for (const child of children) {
      await createIrNode(child, { ...context, parent: node });
    }
  }
  return node;
}

async function createIrNode(irNode, context) {
  try {
    if (!irNode || !irNode.box || !irNode.type) throw new Error("Node is missing type or geometry.");
    return irNode.type === "text" ? await createText(irNode, context) : await createVisualNode(irNode, context);
  } catch (error) {
    context.report.failedNodes += 1;
    context.report.warnings.push(`${irNode && irNode.id ? irNode.id : "unknown node"}: ${error.message || String(error)}`);
    const warning = figma.createText();
    context.parent.appendChild(warning);
    await figma.loadFontAsync(FALLBACK_FONT);
    warning.fontName = FALLBACK_FONT;
    warning.characters = `⚠ Failed layer: ${(irNode && (irNode.name || irNode.id)) || "unknown"}`;
    warning.fontSize = 12;
    warning.fills = [{ type: "SOLID", color: { r: 0.75, g: 0.1, b: 0.1 } }];
    warning.resize(Math.max(120, irNode && irNode.box ? irNode.box.width : 200), 28);
    warning.x = irNode && irNode.box ? irNode.box.x : 0;
    warning.y = irNode && irNode.box ? irNode.box.y : 0;
    warning.name = "Import warning";
    return warning;
  }
}

async function setPageBackground(frame, background, assets, report) {
  if (!background) {
    frame.fills = [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }];
    return;
  }
  try {
    const paint = await paintFromFill(background, assets, report.warnings);
    frame.fills = paint ? [paint] : [];
    if (background.type === "linear-gradient" || background.type === "radial-gradient") report.gradientFills += 1;
  } catch (error) {
    frame.fills = [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }];
    report.warnings.push(`Page background: ${error.message || String(error)}`);
  }
}

async function addQaReference(pageFrame, page, assets, report) {
  const assetId = page.qaReferenceAssetId;
  if (!assetId || !assets[assetId]) {
    report.warnings.push(`Page ${page.id} has no QA reference image.`);
    return;
  }
  const reference = figma.createFrame();
  pageFrame.appendChild(reference);
  reference.name = "QA Reference · hidden";
  reference.resize(page.width, page.height);
  reference.x = 0;
  reference.y = 0;
  reference.visible = false;
  reference.locked = true;
  reference.clipsContent = true;
  reference.setPluginData("canva-importer.qa-reference", "true");
  try {
    const hash = await imageHash(assetId, assets, report.warnings);
    reference.fills = [{ type: "IMAGE", imageHash: hash, scaleMode: "FILL" }];
  } catch (error) {
    reference.remove();
    report.warnings.push(`QA reference: ${error.message || String(error)}`);
  }
}

async function renderDocument(document, reconstructionJobId) {
  requireDesignDocument(document);
  imageCache.clear();
  const report = {
    mode: "editable", warnings: [], missingFonts: [], failedNodes: 0, fallbacks: 0,
    originalAssets: 0, croppedImages: 0, clippedGroups: 0,
    gradientFills: 0, nativeEffects: 0, fontSubstitutions: [],
    pages: document.pages.length, pageMetrics: [], figmaQaExports: 0,
    reconstructionJobId: reconstructionJobId ? String(reconstructionJobId) : null,
  };
  const createdFrames = [];
  const center = figma.viewport.center;
  const columns = Math.max(1, Math.min(5, Math.ceil(Math.sqrt(document.pages.length))));
  const maxWidth = document.pages.reduce((maximum, page) => Math.max(maximum, Number(page.width)), 1);
  const maxHeight = document.pages.reduce((maximum, page) => Math.max(maximum, Number(page.height)), 1);
  const gridWidth = columns * maxWidth + Math.max(0, columns - 1) * 80;
  const rows = Math.ceil(document.pages.length / columns);
  const gridHeight = rows * maxHeight + Math.max(0, rows - 1) * 80;
  const originX = center.x - gridWidth / 2;
  const originY = center.y - gridHeight / 2;

  try {
    for (const [pageIndex, page] of document.pages.entries()) {
      const frame = figma.createFrame();
      createdFrames.push(frame);
      frame.name = page.name || `Canva Page ${createdFrames.length}`;
      frame.resize(page.width, page.height);
      frame.x = originX + (pageIndex % columns) * (maxWidth + 80);
      frame.y = originY + Math.floor(pageIndex / columns) * (maxHeight + 80);
      frame.clipsContent = true;
      frame.layoutMode = "NONE";
      frame.setPluginData("canva-importer.document-id", String(document.id));
      frame.setPluginData("canva-importer.page-id", String(page.id));
      if (reconstructionJobId) frame.setPluginData("canva-importer.reconstruction-job-id", String(reconstructionJobId));
      frame.setPluginData("canva-importer.orientation", page.orientation || pageOrientation(page.width, page.height));
      const metrics = page.metrics || {};
      frame.setPluginData("canva-importer.native-coverage", String(metrics.nativeCoverage == null ? "" : metrics.nativeCoverage));
      frame.setPluginData("canva-importer.fallback-coverage", String(metrics.fallbackCoverage == null ? "" : metrics.fallbackCoverage));
      frame.setPluginData("canva-importer.exact-text-rate", String(metrics.exactTextRate == null ? "" : metrics.exactTextRate));
      frame.setPluginData("canva-importer.visual-similarity", String(metrics.visualSimilarity == null ? "" : metrics.visualSimilarity));
      frame.setPluginData("canva-importer.pixel-difference", String(metrics.pixelDifference == null ? "" : metrics.pixelDifference));
      frame.setPluginData("canva-importer.missing-region-rate", String(metrics.missingRegionRate == null ? "" : metrics.missingRegionRate));
      frame.setPluginData("canva-importer.duplicate-text-blocks", String(metrics.duplicateTextBlocks == null ? "" : metrics.duplicateTextBlocks));
      report.pageMetrics.push({
        pageId: String(page.id),
        pageName: frame.name,
        area: Number(page.width) * Number(page.height),
        nativeCoverage: Number(metrics.nativeCoverage || 0),
        fallbackCoverage: Number(metrics.fallbackCoverage || 0),
        exactTextRate: Number(metrics.exactTextRate || 0),
        visualSimilarity: Number(metrics.visualSimilarity || 0),
        pixelDifference: Number(metrics.pixelDifference || 0),
        missingRegionRate: Number(metrics.missingRegionRate || 0),
        duplicateTextBlocks: Number(metrics.duplicateTextBlocks || 0),
      });
      for (const font of metrics.missingFonts || []) {
        if (!report.missingFonts.includes(font)) report.missingFonts.push(font);
      }
      for (const warning of metrics.warnings || []) report.warnings.push(`${frame.name}: ${warning}`);
      await setPageBackground(frame, page.background, document.assets, report);

      const children = [...(page.children || [])].sort((a, b) => Number(a.zIndex || 0) - Number(b.zIndex || 0));
      for (const child of children) {
        await createIrNode(child, { parent: frame, assets: document.assets, report });
      }
      await addQaReference(frame, page, document.assets, report);
      if (reconstructionJobId && typeof frame.exportAsync === "function") {
        try {
          const imageBytes = await frame.exportAsync({
            format: "PNG",
            constraint: { type: "SCALE", value: 1 },
            colorProfile: "SRGB",
          });
          if (!(imageBytes instanceof Uint8Array) || imageBytes.length < 1 || imageBytes.length > 25 * 1024 * 1024) {
            throw new Error("Final Figma PNG is empty or exceeds the 25MB QA limit.");
          }
          report.figmaQaExports += 1;
          figma.ui.postMessage({
            type: "figma-qa-export",
            jobId: String(reconstructionJobId),
            pageId: String(page.id),
            pageName: frame.name,
            width: Number(page.width),
            height: Number(page.height),
            imageBytes,
          });
        } catch (error) {
          report.warnings.push(`Final Figma QA export for ${frame.name}: ${error.message || String(error)}`);
        }
      }
    }
  } catch (error) {
    for (const frame of createdFrames) {
      try { frame.remove(); } catch (_removeError) { /* best-effort transaction rollback */ }
    }
    throw error;
  }

  const totalArea = report.pageMetrics.reduce((sum, metric) => sum + metric.area, 0) || 1;
  report.nativeCoverage = report.pageMetrics.reduce((sum, metric) => sum + metric.nativeCoverage * metric.area, 0) / totalArea;
  report.fallbackCoverage = report.pageMetrics.reduce((sum, metric) => sum + metric.fallbackCoverage * metric.area, 0) / totalArea;
  report.exactTextRate = report.pageMetrics.reduce((sum, metric) => sum + metric.exactTextRate * metric.area, 0) / totalArea;
  report.visualSimilarity = report.pageMetrics.reduce((sum, metric) => sum + metric.visualSimilarity * metric.area, 0) / totalArea;
  report.pixelDifference = report.pageMetrics.reduce((sum, metric) => sum + metric.pixelDifference * metric.area, 0) / totalArea;
  report.missingRegionRate = report.pageMetrics.reduce((sum, metric) => sum + metric.missingRegionRate * metric.area, 0) / totalArea;
  report.duplicateTextBlocks = report.pageMetrics.reduce((sum, metric) => sum + metric.duplicateTextBlocks, 0);
  figma.currentPage.selection = createdFrames;
  figma.viewport.scrollAndZoomIntoView(createdFrames);
  return report;
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
  return { mode: "page-images", warnings: [], missingFonts: [], failedNodes: 0, fallbacks: 0, pages: session.pages.length };
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
    } else if (message.type === "import-design") {
      report = await renderDocument(message.document, message.jobId);
    } else {
      return;
    }
    const completion = { type: "import-completed", report };
    if (message.sessionId) completion.sessionId = message.sessionId;
    figma.ui.postMessage(completion);
    const detail = report.warnings.length ? ` ${report.warnings.length} warning(s).` : "";
    const mode = report.mode === "page-images" ? " as exact page images" : "";
    figma.notify(`Imported ${report.pages} Canva page(s)${mode}.${detail}`, { timeout: 5000 });
  } catch (error) {
    const text = error && error.message ? error.message : String(error);
    if (message.sessionId) cancelPageImageImport(message.sessionId);
    figma.ui.postMessage({ type: "import-failed", sessionId: message.sessionId, message: text });
    figma.notify(`Import failed: ${text}`, { error: true, timeout: 6000 });
  }
};
