"""Local UI-layout fixture only. Figma replies are mocked; no Canva or AI traffic.

Run: .venv/Scripts/python tests/figma/preview_server.py
Open http://127.0.0.1:8766. Ctrl+C stops the fixture server.
"""
from __future__ import annotations
import base64
import json
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
from test_editable import pdf_fixture
from canva_converter.pdf_worker import convert


def main():
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        (folder / "fixture.pdf").write_bytes(pdf_fixture())
        convert(folder / "fixture.pdf", folder, 600, 400)
        scene = json.loads((folder / "scene.json").read_text())
        encoded = {a["id"]: base64.b64encode((folder / (a["id"] + ".png")).read_bytes()).decode() for a in scene["assets"]}
        report = {"text": 1, "vectors": 1, "images": 0, "fallbacks": 0, "warnings": ["UI fixture: the bridge is mocked. This preview does not validate actual Figma rendering."],
                  "fonts": [{"id": scene["fonts"][0]["id"], "matched": {"family": "Arial", "style": "Regular"}, "substituted": False}]}
        script = """
const fixtureScene = SCENE, fixtureAssets = ASSETS;
window.fetch = async (url, options = {}) => {
 const path = new URL(url).pathname; let value;
 if (path.endsWith('/oauth/status')) value = {configured:true,connected:true};
 else if (path.endsWith('/designs')) value = {items:[]};
 else if (path === '/api/editable-jobs') value = {jobId:'fixture'};
 else if (path.endsWith('/scene')) value = fixtureScene;
 else if (path.includes('/assets/')) { const bytes = Uint8Array.from(atob(fixtureAssets[path.split('/').pop()]), c => c.charCodeAt(0)); return {ok:true,arrayBuffer:async()=>bytes.buffer}; }
 else value = {status:'completed',title:'UI fixture',pageIndex:0,fontAIConfigured:false};
 return {ok:true,status:200,json:async()=>value};
};
""".replace("SCENE", json.dumps(scene)).replace("ASSETS", json.dumps(encoded))
        html = (ROOT / "figma-plugin/ui.html").read_text(encoding="utf-8")
        html = html.replace('<script>', '<script>' + script, 1)
        page = {"captureId": "fixture", "title": "PDF typography fixture", "pages": [{"id": "page", "index": 0, "width": 600, "height": 400, "thumbnail": "data:image/png;base64," + encoded["reference"]}]}
        html = html.replace('</body>', '<script>renderPages(' + json.dumps(page) + '); startEditable();</script></body>')
        wrapper = """<!doctype html><html><body style="margin:0;background:#202020;color:white;font:14px Arial">
<p>UI layout fixture only — mocked Figma responses</p><iframe id="plugin" src="/plugin" style="width:520px;height:850px;border:0"></iframe>
<script>
window.addEventListener('message', event => {
 const m=event.data.pluginMessage; if(!m)return;
 const map={'editable-begin':'editable-ready','editable-scene-chunk':'editable-scene-ready','editable-asset':'editable-asset-ready','editable-render':'editable-preview','editable-commit':'editable-completed','editable-cancel':'editable-cancelled'};
 if(!map[m.type])return;
 event.source.postMessage({pluginMessage:{type:map[m.type],sessionId:m.sessionId,requestId:m.requestId,fonts:[{family:'Arial',style:'Regular'},{family:'Inter',style:'Regular'}],report:REPORT,preview:Uint8Array.from(atob(PREVIEW),c=>c.charCodeAt(0))}},event.origin);
});
</script></body></html>""".replace("REPORT", json.dumps(report)).replace("PREVIEW", json.dumps(encoded["reference"]))
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = html if self.path == "/plugin" else wrapper
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body.encode())
            def log_message(self, *_args): pass
        server = ThreadingHTTPServer(("127.0.0.1", 8766), Handler)
        print("UI-only fixture at http://127.0.0.1:8766", flush=True)
        try: server.serve_forever()
        except KeyboardInterrupt: pass
        finally: server.server_close()


if __name__ == "__main__": main()
