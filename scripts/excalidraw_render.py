"""Render Excalidraw skeleton diagrams to .excalidraw files and 2x PNGs.

Uses headless Chromium (Playwright) with @excalidraw/excalidraw loaded from a CDN:
skeleton -> convertToExcalidrawElements -> diagrams/<name>.excalidraw
                                       -> exportToBlob (scale 2, white) -> figures/<name>.png
"""
import base64
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

EXCALIDRAW = "0.18.0"
PAGE = f"""<!doctype html><html><head><meta charset="utf-8">
<script>window.EXCALIDRAW_ASSET_PATH = "https://cdn.jsdelivr.net/npm/@excalidraw/excalidraw@{EXCALIDRAW}/dist/prod/";</script>
<script type="importmap">{{"imports": {{
  "react": "https://esm.sh/react@19.1.0",
  "react/jsx-runtime": "https://esm.sh/react@19.1.0/jsx-runtime",
  "react-dom": "https://esm.sh/react-dom@19.1.0",
  "react-dom/client": "https://esm.sh/react-dom@19.1.0/client"
}}}}</script>
<script type="module">
  import * as X from "https://esm.sh/@excalidraw/excalidraw@{EXCALIDRAW}?external=react,react-dom";
  window.X = X; window.ready = true;
</script></head><body></body></html>"""

JS_WARMUP = """async () => {
  const els = window.X.convertToExcalidrawElements([
    { type: "text", x: 0, y: 0, text: "warm up 0123", fontSize: 20, fontFamily: 5 },
    { type: "text", x: 0, y: 40, text: "warm up 0123", fontSize: 20, fontFamily: 3 }]);
  await window.X.exportToBlob({ elements: els, appState: { exportBackground: true }, files: {} });
  await document.fonts.ready;
  return document.fonts.check("20px Excalifont");
}"""

JS_RENDER = """async ([skel, bg]) => {
  const els = window.X.convertToExcalidrawElements(skel, { regenerateIds: false });
  const appState = { viewBackgroundColor: bg, exportBackground: true, exportWithDarkMode: false };
  const blob = await window.X.exportToBlob({ elements: els, appState: { ...appState, exportScale: 2 },
                                             files: {}, mimeType: "image/png", exportPadding: 30 });
  const buf = new Uint8Array(await blob.arrayBuffer());
  let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return { elements: els, png: btoa(bin) };
}"""


def render_all(diagrams: dict, src_dir: Path, png_dir: Path, bg: str = "#ffffff"):
    src_dir.mkdir(parents=True, exist_ok=True)
    png_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        # Playwright's own Chromium is not installed here; use the system Chrome.
        browser = p.chromium.launch(executable_path="/usr/bin/google-chrome")
        page = browser.new_page()
        page.set_content(PAGE)
        page.wait_for_function("window.ready === true", timeout=120_000)
        # Warm-up export so Excalifont is loaded before any text is measured.
        print("fonts:", page.evaluate(JS_WARMUP))
        for name, skel in diagrams.items():
            out = page.evaluate(JS_RENDER, [skel, bg])
            doc = {"type": "excalidraw", "version": 2, "source": "agent-permission-classifier/scripts/diagrams.py",
                   "elements": out["elements"],
                   "appState": {"viewBackgroundColor": bg, "gridSize": None}, "files": {}}
            (src_dir / f"{name}.excalidraw").write_text(json.dumps(doc, indent=1))
            (png_dir / f"{name}.png").write_bytes(base64.b64decode(out["png"]))
            print(f"rendered {name}: {len(out['elements'])} elements")
        browser.close()


if __name__ == "__main__":
    demo = [{"type": "rectangle", "x": 0, "y": 0, "width": 220, "height": 80, "backgroundColor": "#a5d8ff",
             "fillStyle": "solid", "roundness": {"type": 3}, "label": {"text": "Hello ring", "fontSize": 28}}]
    import tempfile
    d = Path(tempfile.gettempdir()) / "exc_test"
    render_all({"test": demo}, d, d)
