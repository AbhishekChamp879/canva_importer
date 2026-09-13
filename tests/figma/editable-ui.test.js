"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
class Element {
  constructor(tag = "div") { this.tagName = tag; this.children = []; this.listeners = {}; this.dataset = {}; this.style = {}; this.value = ""; this.disabled = false; this.checked = false; this.textContent = ""; this.classes = new Set(); this.classList = { add: x => this.classes.add(x), remove: x => this.classes.delete(x), toggle: (x, on) => on ? this.classes.add(x) : this.classes.delete(x) }; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(event, fn) { this.listeners[event] = fn; }
  setAttribute(key, value) { this[key] = value; }
  querySelectorAll(selector) { return this.children.flatMap(n => [n, ...n.querySelectorAll(selector)]).filter(n => n.tagName === "input" && n.type === "checkbox" && (!selector.includes(":checked") || n.checked)); }
}
async function main() {
  const html = fs.readFileSync(path.join(__dirname, "../../figma-plugin/ui.html"), "utf8");
  const elements = Object.fromEntries([...html.matchAll(/\bid="([^"]+)"/g)].map(m => [m[1], new Element()]));
  const messages = [], requests = []; let context, connected = false, blockAsset = false;
  const scene = { version: 1, width: 600, height: 400, referenceAsset: "reference", nodes: [], fonts: [], assets: [{ id: "reference", width: 600, height: 400 }], warnings: [] };
  const report = { text: 1, vectors: 1, images: 0, fallbacks: 0, fonts: [], warnings: [] };
  const sandbox = { document: { getElementById: id => elements[id], createElement: tag => new Element(tag) },
    window: { setTimeout, clearTimeout }, console, AbortController, DOMException, Uint8Array, Blob, URL,
    Option: class extends Element { constructor(text, value) { super("option"); this.textContent = text; this.value = value; } },
    fetch: async (url, options = {}) => {
      const route = new URL(url).pathname; requests.push({ route, options });
      let payload;
      if (route === "/api/canva/oauth/status") payload = { configured: true, connected };
      else if (route === "/api/canva/designs") payload = { items: [] };
      else if (route === "/api/editable-jobs") payload = { jobId: "job" };
      else if (route === "/api/editable-jobs/job") payload = { status: options.method === "DELETE" ? "cancelled" : "completed", title: "Fixture", pageIndex: 0, fontAIConfigured: false };
      else if (route.endsWith("/scene")) payload = scene;
      else if (route.endsWith("/assets/reference")) {
        if (blockAsset) throw new Error("Asset expired");
      } else throw new Error("Unexpected network request " + route);
      return { ok: true, status: 200, json: async () => payload, arrayBuffer: async () => new Uint8Array([137, 80, 78, 71]).buffer };
    }, parent: { postMessage({ pluginMessage: message }) {
      messages.push(message);
      const types = { "editable-begin": "editable-ready", "editable-scene-chunk": "editable-scene-ready", "editable-asset": "editable-asset-ready", "editable-render": "editable-preview", "editable-commit": "editable-completed", "editable-cancel": "editable-cancelled" };
      if (!types[message.type]) return;
      queueMicrotask(() => context.window.onmessage({ data: { pluginMessage: { type: types[message.type], sessionId: message.sessionId, requestId: message.requestId, fonts: [], report, preview: [137, 80, 78, 71] } } }));
    } } };
  context = vm.createContext(sandbox); vm.runInContext([...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/gi)][0][1], context);
  vm.runInContext('renderPages({captureId:"capture", title:"Fixture", pages:[{id:"page",index:0,width:600,height:400,thumbnail:"data:image/png;base64,AA=="}]})', context);
  await vm.runInContext("startEditable()", context);
  assert.match(elements.activity.textContent, /Connect Canva/);
  assert.equal(elements.pages.querySelectorAll('input[type="checkbox"]:checked').length, 1, "connection must preserve selection");
  connected = true;
  await vm.runInContext("connectCanva()", context);
  assert.ok(!requests.some(r => r.route.endsWith("/oauth/disconnect")), "retrying stale disconnected status must not disconnect a restored session");
  await vm.runInContext("startEditable()", context);
  assert.equal(elements["editable-review"].classes.has("hidden"), false);
  assert.equal(elements["editable-commit"].disabled, false);
  assert.match(elements["editable-counts"].textContent, /1 editable text/);
  assert.ok(messages.some(m => m.type === "editable-scene-chunk"));
  assert.ok(!requests.some(r => r.route.includes("font-suggestions")), "normal imports must never trigger AI");
  await vm.runInContext("commitEditable()", context);
  assert.equal(elements["activity-state"].textContent, "Completed");
  assert.equal(elements["import-images"].disabled, false);
  blockAsset = true;
  await vm.runInContext("startEditable()", context);
  assert.equal(elements["activity-state"].textContent, "Editable import failed");
  assert.ok(messages.some(m => m.type === "editable-cancel"));
  assert.equal(elements["import-editable"].disabled, false);
  console.log("Editable UI connection, preview, commit, asset failure, rollback and no-implicit-AI tests passed.");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
