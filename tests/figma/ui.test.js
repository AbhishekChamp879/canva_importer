"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor(tagName = "div") {
    this.tagName = tagName;
    this.children = [];
    this.dataset = {};
    this.listeners = {};
    this.style = {};
    this.value = "";
    this.disabled = false;
    this.checked = false;
    this.textContent = "";
    this.classes = new Set();
    this.classList = {
      add: name => this.classes.add(name),
      remove: name => this.classes.delete(name),
      toggle: (name, enabled) => enabled ? this.classes.add(name) : this.classes.delete(name),
    };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  querySelectorAll(selector) {
    const all = this.children.flatMap(child => [child, ...child.querySelectorAll(selector)]);
    return all.filter(child => child.tagName === "input" && child.type === "checkbox"
      && (!selector.includes(":checked") || child.checked));
  }
}

async function main() {
  const html = fs.readFileSync(path.resolve(__dirname, "../../figma-plugin/ui.html"), "utf8");
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
  const pages = [
    { id: "one", index: 0, width: 800, height: 600, orientation: "landscape" },
    { id: "two", index: 1, width: 600, height: 800, orientation: "portrait" },
    { id: "three", index: 2, width: 700, height: 700, orientation: "square" },
  ].map(page => ({ ...page, thumbnail: "data:image/jpeg;base64,AA==",
    imageUrl: `/api/captures/capture/pages/${page.id}/image` }));
  const requests = [];
  const messages = [];
  let context;
  const sandbox = {
    document: {
      getElementById: id => elements[id] || null,
      createElement: tag => new Element(tag),
    },
    Option: class extends Element {
      constructor(text, value) { super("option"); this.textContent = text; this.value = value; }
    },
    AbortController, DOMException, Uint8Array, console,
    window: { setTimeout, clearTimeout },
    fetch: async (url, options = {}) => {
      const route = new URL(url).pathname;
      requests.push({ route, method: options.method || "GET" });
      let payload;
      if (route === "/api/canva/oauth/status") payload = { configured: false, connected: false };
      else if (route === "/api/capture-jobs") payload = { jobId: "capture-job" };
      else if (route === "/api/capture-jobs/capture-job") {
        payload = { status: "completed", progress: 100, captureId: "capture" };
      } else if (route === "/api/captures/capture") {
        payload = { captureId: "capture", title: "Example", pages };
      } else if (!pages.some(page => page.imageUrl === route)) {
        throw new Error("Unexpected request: " + route);
      }
      return { ok: true, status: 200, json: async () => payload,
        arrayBuffer: async () => Uint8Array.from([137, 80, 78, 71]).buffer };
    },
    parent: {
      postMessage({ pluginMessage: message }) {
        messages.push(message);
        const types = {
          "begin-page-image-import": "page-image-import-ready",
          "append-page-image": "page-image-import-page",
          "finish-page-image-import": "import-completed",
        };
        const type = types[message.type];
        if (!type) return;
        queueMicrotask(() => {
          context.window.onmessage({ data: { pluginMessage: {
            type, sessionId: message.sessionId, pageId: message.pageId,
            report: { mode: "page-images", pages: 2, warnings: [] },
          } } });
        });
      },
    },
  };
  context = vm.createContext(sandbox);
  const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/gi)];
  assert.equal(scripts.length, 1);
  vm.runInContext(scripts[0][1], context);
  assert.equal(elements.reconstruct, undefined);
  await vm.runInContext("refreshCanvaOAuthStatus()", context);
  assert.equal(elements["canva-connect"].disabled, false, "missing setup must remain retryable after backend restart");
  assert.equal(elements["canva-connect"].textContent, "Retry Canva setup");
  await vm.runInContext("connectCanva()", context);
  assert.ok(!requests.some(request => request.route.endsWith("/oauth/start")), "missing settings must not start OAuth");

  elements["canva-url"].value = "https://www.canva.com/design/ABC/token/view";
  await vm.runInContext("capturePreview()", context);
  for (let i = 0; i < 20 && elements.pages.children.length !== 3; i++) {
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.equal(elements.pages.children.length, 3, "capture must display all previews");
  const boxes = elements.pages.querySelectorAll('input[type="checkbox"]');
  boxes[1].checked = false;
  boxes[1].listeners.change();
  assert.equal(elements["selected-count"].textContent, "2 of 3 selected");
  assert.equal(elements["import-images"].disabled, false);

  await vm.runInContext("importPageImages()", context);
  const transferred = messages.filter(message => message.type === "append-page-image");
  assert.deepEqual(transferred.map(message => message.pageId), ["one", "three"]);
  assert.deepEqual(requests.filter(request => request.route.endsWith("/image"))
    .map(request => request.route), [pages[0].imageUrl, pages[2].imageUrl]);
  assert.equal(elements["activity-state"].textContent, "Completed");
  assert.equal(elements["job-percent"].textContent, "100%");
  assert.equal(elements["import-images"].disabled, false);
  assert.equal(elements["cancel-job"].disabled, true);
  assert.equal(elements.summary.children[0].textContent, "2 page(s) imported as images");
  assert.ok(requests.every(request => !request.route.includes("reconstruction")));
  console.log("Plugin UI capture, selection, and page-image import tests passed.");
}

main().catch(error => { console.error(error); process.exitCode = 1; });
