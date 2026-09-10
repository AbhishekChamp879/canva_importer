from __future__ import annotations

import asyncio
import base64
import io
import re
from typing import Any, Callable
from urllib.parse import urlparse

from PIL import Image

from ..config import Settings
from ..errors import AcquisitionError
from ..models import CapturedPage, DomImageHint, DomTextHint, new_id
from .browser_state import classify_browser_state
from .browser_runtime import PortableBrowserRuntime
from .coordinator import CaptureCoordinator
from .page_detection import screenshot_fingerprint, select_best_page_candidate
from .url_policy import is_canva_asset_host, is_valid_canva_url, is_valid_resolved_canva_url, resolve_canva_source_url


Progress = Callable[[str], None]
Cancelled = Callable[[], bool]


TEXT_HINT_SCRIPT = r"""
(pageBox) => {
  const layers = [];
  const logicalScale = Number(pageBox.logicalScale) || 1;
  const blacklist = new Set(['share','create with canva','download','present','more','open in canva','sign up','log in']);
  for (const el of document.querySelectorAll('span,p,h1,h2,h3,h4,h5,h6,[class*="text"],[role="heading"]')) {
    const text = (el.innerText || '').trim();
    if (text.length <= 1 || blacklist.has(text.toLowerCase()) || /^\d+\s*\/\s*\d+$/.test(text)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 5 || rect.height < 5 || rect.right < pageBox.x-10 || rect.left > pageBox.x+pageBox.width+10 || rect.bottom < pageBox.y-10 || rect.top > pageBox.y+pageBox.height+10) continue;
    const relY = rect.top-pageBox.y, relBottom = rect.bottom-pageBox.y;
    if (relY < -5 || relBottom > pageBox.height+5) continue;
    const style = getComputedStyle(el);
    if (style.opacity === '0' || style.visibility === 'hidden' || style.display === 'none') continue;
    layers.push({text,x:Math.max(0,rect.left-pageBox.x)*logicalScale,y:Math.max(0,rect.top-pageBox.y)*logicalScale,width:rect.width*logicalScale,height:rect.height*logicalScale,fontSize:(parseFloat(style.fontSize)||16)*logicalScale,fontFamily:style.fontFamily||'Inter',fontWeight:style.fontWeight||'400',fontStyle:style.fontStyle||'normal',color:style.color||'rgb(0,0,0)',textAlign:style.textAlign||'left',lineHeight:parseFloat(style.lineHeight)?parseFloat(style.lineHeight)*logicalScale:null,letterSpacing:parseFloat(style.letterSpacing)?parseFloat(style.letterSpacing)*logicalScale:null,textDecoration:style.textDecorationLine||'none'});
  }
  const result=[];
  for (const layer of layers) if (!result.some(existing => existing.text===layer.text && Math.abs(existing.x-layer.x)<30 && Math.abs(existing.y-layer.y)<30)) result.push(layer);
  return result.slice(0,200);
}
"""


IMAGE_HINT_SCRIPT = r"""
(pageBox) => {
  const layers=[];
  const logicalScale = Number(pageBox.logicalScale) || 1;
  const positionValue=(token,axis) => {
    const value=String(token||'').toLowerCase();
    if (value==='left'||value==='top') return 0;
    if (value==='right'||value==='bottom') return 1;
    if (value==='center') return 0.5;
    if (value.endsWith('%')) return Math.max(0,Math.min(1,(parseFloat(value)||50)/100));
    return 0.5;
  };
  for (const img of document.querySelectorAll('img')) {
    const src=img.currentSrc||img.src||img.getAttribute('data-src')||'';
    if (!src || src.startsWith('data:') || src.startsWith('blob:')) continue;
    const rect=img.getBoundingClientRect();
    if (rect.width<20 || rect.height<20 || rect.right<pageBox.x-10 || rect.left>pageBox.x+pageBox.width+10 || rect.bottom<pageBox.y-10 || rect.top>pageBox.y+pageBox.height+10) continue;
    // A page-sized IMG is commonly Canva's flattened viewer render, not an
    // editable source layer. Treat it as uncertain and preserve screenshot
    // pixels rather than re-introducing a full-page duplicate asset.
    if ((rect.width*rect.height)/(pageBox.width*pageBox.height) >= 0.92) continue;
    const relX=rect.left-pageBox.x, relRight=rect.right-pageBox.x;
    const relY=rect.top-pageBox.y, relBottom=rect.bottom-pageBox.y;
    // Partially clipped DOM images need additional crop geometry that this
    // hint contract cannot prove. Exclude them and retain screenshot fallback.
    if (relX < -5 || relRight > pageBox.width+5 || relY < -5 || relBottom > pageBox.height+5) continue;
    const style=getComputedStyle(img);
    if (style.opacity==='0'||style.visibility==='hidden'||style.display==='none') continue;
    const tokens=String(style.objectPosition||'50% 50%').trim().split(/\s+/);
    const px=positionValue(tokens[0], 'x');
    const py=positionValue(tokens[1]||tokens[0], 'y');
    const radii=[style.borderTopLeftRadius,style.borderTopRightRadius,style.borderBottomRightRadius,style.borderBottomLeftRadius].map(value=>parseFloat(value)||0);
    layers.push({src,x:Math.max(0,rect.left-pageBox.x)*logicalScale,y:Math.max(0,rect.top-pageBox.y)*logicalScale,width:rect.width*logicalScale,height:rect.height*logicalScale,alt:img.alt||'',naturalWidth:img.naturalWidth||null,naturalHeight:img.naturalHeight||null,objectFit:style.objectFit||'fill',objectPositionX:px,objectPositionY:py,cornerRadius:Math.min(...radii)*logicalScale});
  }
  const seen=new Set();
  return layers.filter(layer => {const key=`${Math.round(layer.x)}|${Math.round(layer.y)}|${Math.round(layer.width)}`;if(seen.has(key))return false;seen.add(key);return true;}).slice(0,100);
}
"""


class PublicCanvaAcquisitionProvider:
    id = "canva-public-url-python-v1"

    def __init__(self, settings: Settings, coordinator: CaptureCoordinator | None = None):
        self.settings = settings
        self.coordinator = coordinator or CaptureCoordinator(settings.capture_concurrency)

    def capture(self, url: str, progress: Progress | None = None, is_cancelled: Cancelled | None = None) -> tuple[str, list[CapturedPage]]:
        if not is_valid_canva_url(url):
            raise AcquisitionError("INVALID_SOURCE", "Use a Canva design/share URL or canva.link URL.")
        try:
            return asyncio.run(self._capture_with_timeout(url, progress or (lambda _: None), is_cancelled or (lambda: False)))
        except TimeoutError as error:
            raise AcquisitionError("SOURCE_UNAVAILABLE", "Canva capture exceeded the configured deadline.") from error
        except AcquisitionError:
            raise
        except Exception as error:
            raise self._map_error(error) from error

    async def _capture_with_timeout(self, url: str, progress: Progress, is_cancelled: Cancelled):
        async with asyncio.timeout(self.settings.capture_timeout_ms / 1000):
            self._ensure_not_cancelled(is_cancelled)
            resolved = await asyncio.to_thread(resolve_canva_source_url, url)
            progress("[resolving] Resolved Canva source URL.")
            self._ensure_not_cancelled(is_cancelled)
            # All equivalent /edit, /view, locale, bare-design, and short-link
            # inputs converge on this canonical key before concurrency control.
            with self.coordinator.acquire(resolved):
                return await self._capture_browser(resolved, progress, is_cancelled)

    async def _capture_browser(self, url: str, progress: Progress, is_cancelled: Cancelled) -> tuple[str, list[CapturedPage]]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:
            raise AcquisitionError("CAPTURE_FAILED", "Playwright is not installed. Run: pip install -r requirements.txt") from error

        launch_args: dict[str, Any] = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check", "--window-size=1920,1080"],
        }

        progress("[launching] Launching isolated Chrome capture.")
        self._ensure_not_cancelled(is_cancelled)
        async with async_playwright() as playwright:
            runtime = PortableBrowserRuntime(
                configured_executable=self.settings.browser_executable_path,
                auto_install=self.settings.browser_auto_install,
                install_timeout_seconds=self.settings.browser_install_timeout_seconds,
            )
            browser = await runtime.launch(playwright.chromium, launch_args, progress)
            try:
                context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    device_scale_factor=2,
                )
                await context.route("**/*", lambda route: route.abort() if route.request.resource_type == "media" else route.continue_())
                page = await context.new_page()
                self._ensure_not_cancelled(is_cancelled)
                progress("[loading] Loading Canva design.")
                response = None
                try:
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                except Exception as navigation_error:
                    navigation_state = await self._stable_page_snapshot(page)
                    if not navigation_state.get("hasDocument") or not navigation_state.get("title") or not is_valid_resolved_canva_url(navigation_state.get("url", "")):
                        raise navigation_error
                await self._dismiss_overlays(page)
                try:
                    await page.wait_for_load_state("networkidle", timeout=30_000)
                except Exception:
                    pass
                await asyncio.sleep(3)
                await self._dismiss_overlays(page)

                first = None
                for _ in range(6):
                    self._ensure_not_cancelled(is_cancelled)
                    first = await self._find_page_locator(page, 1)
                    if first is None:
                        first = await self._find_page_locator(page)
                    if first is not None:
                        break
                    await asyncio.sleep(1)
                snapshot = await self._stable_page_snapshot(page, bool(first))
                snapshot["responseStatus"] = response.status if response else None
                state, reason = classify_browser_state(snapshot)
                progress(f"[detecting] Access state: {state}.")
                if state not in {"ready", "loading"}:
                    raise self._state_error(state, reason)

                total = await self._detect_page_count(page)
                multiple_rendered_pages = await self._has_multiple_rendered_pages(page)
                pages: list[CapturedPage] = []
                captured_bytes_total = 0
                previous_navigation_fingerprint: str | None = None
                previous_locator_identity: str | None = None
                captured_fingerprints: dict[str, dict[str, Any]] = {}
                for page_number in range(1, total + 1):
                    self._ensure_not_cancelled(is_cancelled)
                    progress(f"[capturing] Capturing page {page_number} of {total}.")
                    # Canva's document/editor viewer renders multiple pages in one
                    # vertically virtualized canvas. Prefer its stable per-page DOM
                    # identity instead of pressing ArrowRight, which may only move
                    # focus or animate viewer chrome without changing the design.
                    navigation_proof: str | None = None
                    if page_number == 1:
                        locator = first
                    elif multiple_rendered_pages:
                        locator = await self._find_page_locator(page, page_number)
                    else:
                        locator = None
                    if locator is None and page_number > 1:
                        navigation_proof = await self._navigate_next(
                            page, page_number, previous_navigation_fingerprint, previous_locator_identity,
                        )
                        if navigation_proof is None:
                            raise AcquisitionError(
                                "CAPTURE_INCOMPLETE",
                                f"Canva reported {total} pages, but navigation stopped after {len(pages)}. No partial or duplicate import was returned.",
                            )
                        # Navigation has now been proven. Prefer the exact
                        # current page identity even in slideshow mode so a
                        # cached, hidden canvas cannot win generic scoring.
                        locator = await self._find_page_locator(page, page_number)
                        if locator is None:
                            locator = await self._find_page_locator(page)
                    if locator is None:
                        if page_number > 1:
                            raise AcquisitionError(
                                "MULTI_PAGE_CAPTURE_UNAVAILABLE",
                                "Canva exposed only the cover preview, so the remaining pages could not be captured without duplicating page 1.",
                            )
                        preview = await self._open_graph_preview(page)
                        if preview is None:
                            raise AcquisitionError("SOURCE_NOT_PUBLIC", 'Canva did not expose a public page preview. Set link access to "Anyone with the link" with view permission.')
                        width, height, screenshot = preview
                        text_hints: list[DomTextHint] = []
                        image_hints: list[DomImageHint] = []
                    else:
                        await locator.scroll_into_view_if_needed(timeout=10_000)
                        screenshot_bytes, box = await self._stable_locator_screenshot(locator)
                        if not box:
                            raise AcquisitionError("UNSUPPORTED_SOURCE", f"Page {page_number} has no fixed-size bounds.")
                        logical_scale = await self._logical_scale(locator)
                        width = round(box["width"] / logical_scale)
                        height = round(box["height"] / logical_scale)
                        if width > 8192 or height > 8192:
                            raise AcquisitionError("CAPTURE_LIMIT_EXCEEDED", f"Page {page_number} exceeds the 8192px logical dimension limit.")
                        previous_navigation_fingerprint = screenshot_fingerprint(screenshot_bytes)
                        screenshot_bytes = self._normalize_page_image(screenshot_bytes, width, height)
                        captured_fingerprint = screenshot_fingerprint(screenshot_bytes)
                        locator_identity = await self._locator_page_identity(locator)
                        previous_locator_identity = locator_identity
                        earlier = captured_fingerprints.get(captured_fingerprint)
                        if earlier and not self._duplicate_is_authoritative(
                            page_number,
                            await self._current_page_number(page) if navigation_proof == "page-number" else None,
                            locator_identity,
                            earlier.get("identity"),
                        ):
                            raise AcquisitionError(
                                "CAPTURE_DUPLICATE_PAGE",
                                f"Page {page_number} matched captured page {earlier['pageNumber']} without authoritative Canva page evidence. The import was stopped instead of repeating a page.",
                            )
                        captured_fingerprints.setdefault(captured_fingerprint, {"pageNumber": page_number, "identity": locator_identity})
                        screenshot = base64.b64encode(screenshot_bytes).decode("ascii")
                        hint_box = {**box, "logicalScale": 1 / logical_scale}
                        text_hints = [DomTextHint.model_validate(item) for item in await page.evaluate(TEXT_HINT_SCRIPT, hint_box)]
                        image_hints = [DomImageHint.model_validate(item) for item in await page.evaluate(IMAGE_HINT_SCRIPT, hint_box)]
                    if width > 8192 or height > 8192:
                        raise AcquisitionError("CAPTURE_LIMIT_EXCEEDED", f"Page {page_number} exceeds the 8192px logical dimension limit.")
                    if len(base64.b64decode(screenshot, validate=True)) > 25 * 1024 * 1024:
                        raise AcquisitionError("CAPTURE_LIMIT_EXCEEDED", f"Page {page_number} exceeds the 25MB image limit.")
                    captured_bytes_total += len(base64.b64decode(screenshot, validate=True))
                    if captured_bytes_total > self.settings.max_capture_bytes:
                        raise AcquisitionError(
                            "CAPTURE_LIMIT_EXCEEDED",
                            f"Captured pages exceed the configured {self.settings.max_capture_bytes // (1024 * 1024)}MB design limit.",
                        )
                    captured = CapturedPage(id=new_id(), index=page_number - 1, width=width, height=height, screenshotBase64=screenshot, textHints=text_hints, imageHints=image_hints)
                    pages.append(captured)
                    self._ensure_not_cancelled(is_cancelled)
                    progress(f"[capturing] Page {page_number}: {width}×{height} ({captured.orientation}).")
                if not pages:
                    raise AcquisitionError("UNSUPPORTED_SOURCE", "No fixed-size Canva pages could be captured.")
                progress(f"[completed] Captured {len(pages)} page(s).")
                return await page.title(), pages
            finally:
                await browser.close()

    @staticmethod
    def _ensure_not_cancelled(is_cancelled: Cancelled) -> None:
        if is_cancelled():
            raise AcquisitionError("CAPTURE_CANCELLED", "Capture cancelled.")

    async def _find_page_locator(self, page, page_number: int | None = None):
        if page_number is not None:
            zero_based = page_number - 1
            selectors = [
                f'[data-page-id="{zero_based}"]',
                f'[data-page-index="{zero_based}"]',
                f'[data-page-number="{page_number}"]',
                f'[role="group"][aria-label="Page {page_number}"]',
                f'[aria-label="Page {page_number}"]',
            ]
            for selector in selectors:
                matches = page.locator(selector)
                count = min(await matches.count(), 20)
                for index in range(count):
                    candidate = await self._nearest_fixed_page_box(matches.nth(index), page)
                    if candidate is not None:
                        return candidate

            # Some Canva builds use opaque page IDs. Their DOM order still
            # follows document order, so use the indexed page wrapper when the
            # full page list is present.
            indexed_pages = page.locator("[data-page-id]")
            indexed_count = await indexed_pages.count()
            if indexed_count >= page_number:
                candidate = await self._nearest_fixed_page_box(indexed_pages.nth(zero_based), page)
                if candidate is not None:
                    return candidate

            # Slideshow-style Canva viewers expose only the currently rendered
            # page (often permanently named data-page-id="0"). Returning a
            # generic viewer shell here would make the capture loop believe that
            # page N already exists and would skip ArrowRight navigation.
            return None

        selector = '[data-page-id],[data-page-index],[data-page-number],canvas,img,[role="img"],[class*="page"],[class*="slide"],[class*="design"],[class*="viewer"],[class*="render"]'
        locator = page.locator(selector)
        candidates: list[dict[str, Any]] = []
        count = min(await locator.count(), 1000)
        for index in range(count):
            item = locator.nth(index)
            box = await item.bounding_box()
            if not box:
                continue
            metadata = await item.evaluate("el => ({tagName:el.tagName,role:el.getAttribute('role')||undefined,ariaLabel:el.getAttribute('aria-label')||undefined,className:typeof el.className==='string'?el.className.slice(0,300):undefined,dataPageId:el.getAttribute('data-page-id'),dataPageIndex:el.getAttribute('data-page-index'),dataPageNumber:el.getAttribute('data-page-number')})")
            candidates.append({"id": str(index), **box, **metadata})
        selected = select_best_page_candidate(candidates, {"width": 1920, "height": 1080})
        return locator.nth(int(selected["id"])) if selected else None

    async def _has_multiple_rendered_pages(self, page) -> bool:
        """Distinguish a document canvas from a one-page-at-a-time viewer.

        Thumbnail and accessibility nodes must not count as rendered pages;
        otherwise they can be enlarged through their ancestors and captured as
        if they were the requested design page.
        """
        return bool(await page.evaluate(r"""() => {
          const roots = new Set();
          for (const marker of document.querySelectorAll('[data-page-id],[data-page-index],[data-page-number]')) {
            let candidate = marker;
            for (let depth = 0; depth < 7 && candidate; depth++, candidate = candidate.parentElement) {
              const rect = candidate.getBoundingClientRect();
              const aspect = rect.height ? rect.width / rect.height : 0;
              if (rect.width >= 200 && rect.width <= 8192 && rect.height >= 200 && rect.height <= 8192 && aspect >= 0.15 && aspect <= 6.5) {
                roots.add(candidate);
                break;
              }
            }
          }
          return roots.size > 1;
        }"""))

    async def _nearest_fixed_page_box(self, locator, page):
        """Return the closest useful page-sized element for an indexed marker."""
        candidate = locator
        for _ in range(7):
            try:
                box = await candidate.bounding_box()
            except Exception:
                return None
            if box:
                width, height = float(box["width"]), float(box["height"])
                aspect = width / height if height else 0
                # Indexed pages may be far below the viewport before scrolling,
                # so position must not be part of this validation.
                if 200 <= width <= 8192 and 200 <= height <= 8192 and 0.15 <= aspect <= 6.5:
                    return candidate
            candidate = candidate.locator("xpath=..")
        return None

    async def _stable_locator_screenshot(self, locator) -> tuple[bytes, dict[str, float]]:
        """Wait for a virtualized Canva page to render before saving its image."""
        latest: bytes | None = None
        latest_box: dict[str, float] | None = None
        previous: str | None = None
        stable_reads = 0
        for _ in range(12):
            await asyncio.sleep(0.5)
            try:
                box = await locator.bounding_box()
                if not box or box["width"] < 1 or box["height"] < 1:
                    continue
                image = await locator.screenshot(type="png", timeout=15_000)
            except Exception as error:
                if re.search(r"detached|not attached|not visible|timeout", str(error), re.I):
                    continue
                raise
            fingerprint = screenshot_fingerprint(image)
            stable_reads = stable_reads + 1 if fingerprint == previous else 0
            previous = fingerprint
            latest = image
            latest_box = box
            if stable_reads >= 1:
                return latest, latest_box
        if latest is not None and latest_box is not None:
            return latest, latest_box
        raise AcquisitionError("CAPTURE_FAILED", "Canva page content did not become visible before capture.")

    async def _logical_scale(self, locator) -> float:
        """Recover Canva's internal page scale so dimensions do not depend on viewer zoom."""
        try:
            value = await locator.evaluate(r"""el => {
              const candidates = el.querySelectorAll('[aria-label="Canvas content"],[role="group"][aria-label^="Page "]');
              for (const candidate of candidates) {
                const rect = candidate.getBoundingClientRect();
                const width = candidate.offsetWidth;
                const height = candidate.offsetHeight;
                if (!width || !height) continue;
                const scaleX = rect.width / width;
                const scaleY = rect.height / height;
                if (scaleX >= 0.05 && scaleX <= 4 && Math.abs(scaleX - scaleY) <= Math.max(0.02, scaleX * 0.05)) {
                  return (scaleX + scaleY) / 2;
                }
              }
              return 1;
            }""")
            scale = float(value or 1)
            return scale if 0.05 <= scale <= 4 else 1
        except Exception:
            return 1

    @staticmethod
    def _normalize_page_image(image_bytes: bytes, logical_width: int, logical_height: int) -> bytes:
        if logical_width <= 0 or logical_height <= 0:
            raise AcquisitionError("UNSUPPORTED_SOURCE", "Canva returned invalid logical page dimensions.")
        maximum_dimension = 8192
        output_scale = min(2.0, maximum_dimension / max(logical_width, logical_height))
        target = (max(1, round(logical_width * output_scale)), max(1, round(logical_height * output_scale)))
        with Image.open(io.BytesIO(image_bytes)) as source:
            source.load()
            normalized = source.convert("RGBA" if source.mode in {"RGBA", "LA"} else "RGB")
            if normalized.size != target:
                normalized = normalized.resize(target, Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            normalized.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    async def _stable_page_snapshot(self, page, has_page_candidate: bool = False) -> dict[str, Any]:
        last_error: Exception | None = None
        previous_url: str | None = None
        stable_reads = 0
        for _ in range(10):
            try:
                snapshot = await page.evaluate(
                    "has => ({url:location.href,title:document.title,bodyText:(document.body?.innerText||'').slice(0,10000),readyState:document.readyState,hasDocument:Boolean(document.documentElement),hasPageCandidate:has})",
                    has_page_candidate,
                )
                current_url = snapshot.get("url")
                if current_url == previous_url and snapshot.get("readyState") in {"interactive", "complete"}:
                    stable_reads += 1
                else:
                    stable_reads = 0
                previous_url = current_url
                if stable_reads >= 1:
                    return snapshot
            except Exception as error:
                last_error = error
                if not re.search(r"execution context was destroyed|navigation|target closed", str(error), re.I):
                    raise
            await asyncio.sleep(0.5)
        if last_error:
            raise AcquisitionError("SOURCE_UNAVAILABLE", f"Canva navigation did not stabilize: {last_error}") from last_error
        raise AcquisitionError("SOURCE_UNAVAILABLE", "Canva navigation did not stabilize before capture.")

    async def _detect_page_count(self, page) -> int:
        value = await page.evaluate(r"""() => {
          const totals = [];
          const addTotal = value => {
            const number = Number(value);
            if (Number.isInteger(number) && number >= 1 && number <= 10000) totals.push(number);
          };
          const inspect = text => {
            for (const match of String(text || '').matchAll(/(?:page\s*)?(\d+)\s*(?:\/|of)\s*(\d+)/gi)) {
              if (Number(match[1]) <= Number(match[2])) addTotal(match[2]);
            }
          };
          for (const el of document.querySelectorAll('[class*="page-indicator"],[class*="pageIndicator"],[class*="slide-count"],[aria-label*="page" i]')) {
            inspect(`${el.innerText || ''} ${el.getAttribute('aria-label') || ''}`);
          }
          const body = document.body?.innerText || '';
          for (const line of body.split('\n')) {
            if (/^\s*(?:page\s*)?\d+\s*(?:\/|of)\s*\d+\s*$/i.test(line)) inspect(line);
          }
          const indexed = new Set();
          for (const el of document.querySelectorAll('[data-page-index],[data-page-number]')) {
            const value = el.getAttribute('data-page-index') ?? el.getAttribute('data-page-number');
            if (value !== null) indexed.add(value);
          }
          if (indexed.size > 1) addTotal(indexed.size);
          const thumbs = document.querySelectorAll('[class*="thumbnail"],[class*="page-list"] > *,[class*="slide-list"] > *');
          if (thumbs.length > 1) addTotal(thumbs.length);
          return totals.length ? Math.max(...totals) : 1;
        }""")
        return max(1, int(value or 1))

    async def _navigate_next(
        self,
        page,
        expected_page_number: int,
        previous_page_fingerprint: str | None,
        previous_locator_identity: str | None,
    ) -> str | None:
        # Prefer Canva's explicit numbered page controls. Unlike a broad
        # "next" selector, these cannot accidentally activate an interactive
        # link embedded inside the user's design.
        page_controls = [
            f'[data-role="timeline-scene"][role="button"][aria-label="Page {expected_page_number}"]',
            f'button[aria-label="Page {expected_page_number}"]',
            f'[role="option"][aria-label="Page {expected_page_number}"]',
        ]
        for selector in page_controls:
            controls = page.locator(selector)
            for index in range(min(await controls.count(), 5)):
                control = controls.nth(index)
                try:
                    if not await control.is_visible() or not await control.is_enabled():
                        continue
                    await control.click(timeout=5_000)
                except Exception:
                    continue
                return await self._wait_for_expected_page(
                    page, expected_page_number, previous_page_fingerprint, previous_locator_identity,
                )

        # Do not select a generic or even exact "next page" button here. Canva
        # designs can contain interactive links with the same accessible label;
        # activating one can jump to an arbitrary page while looking like valid
        # viewer navigation. ArrowRight is handled by Canva's viewer itself.
        await page.keyboard.press("ArrowRight")
        return await self._wait_for_expected_page(
            page, expected_page_number, previous_page_fingerprint, previous_locator_identity,
        )

    async def _wait_for_expected_page(
        self,
        page,
        expected_page_number: int,
        previous_page_fingerprint: str | None,
        previous_locator_identity: str | None,
    ) -> str | None:
        """Require an authoritative page-number change when the viewer exposes one."""
        for _ in range(24):
            await asyncio.sleep(0.25)
            current = await self._current_page_number(page)
            if current is not None:
                if current == expected_page_number:
                    return "page-number"
                continue
            locator = await self._find_page_locator(page)
            if locator is None:
                continue
            try:
                current_identity = await self._locator_page_identity(locator)
                if self._identity_is_expected(current_identity, expected_page_number):
                    return "page-identity"
                if previous_locator_identity and current_identity and current_identity != previous_locator_identity:
                    return "page-identity"
                fingerprint = screenshot_fingerprint(await locator.screenshot(type="png", timeout=5_000))
            except Exception:
                continue
            if previous_page_fingerprint is not None and fingerprint != previous_page_fingerprint:
                return "visual-change"
        return None

    @staticmethod
    def _identity_is_expected(identity: str | None, expected_page_number: int) -> bool:
        return identity in {
            f"data-page-id:{expected_page_number - 1}",
            f"data-page-index:{expected_page_number - 1}",
            f"data-page-number:{expected_page_number}",
            f"aria-page:{expected_page_number}",
        }

    async def _current_page_number(self, page) -> int | None:
        value = await page.evaluate(r"""() => {
          const selectors = [
            '[class*="page-indicator"]',
            '[class*="pageIndicator"]',
            '[class*="slide-count"]',
            '[aria-live][aria-label*="page" i]'
          ];
          for (const el of document.querySelectorAll(selectors.join(','))) {
            const rect = el.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) continue;
            const text = `${el.innerText || ''} ${el.getAttribute('aria-label') || ''}`;
            const match = text.match(/(?:page\s*)?(\d+)\s*(?:\/|of)\s*(\d+)/i);
            if (match) return Number(match[1]);
          }
          for (const el of document.querySelectorAll('[aria-current="page"],[aria-selected="true"][aria-label*="page" i]')) {
            const text = `${el.innerText || ''} ${el.getAttribute('aria-label') || ''}`;
            const match = text.match(/page\s*(\d+)/i);
            if (match) return Number(match[1]);
          }
          const body = document.body?.innerText || '';
          const match = body.match(/(?:^|\n)\s*(\d+)\s*\/\s*(\d+)\s*(?:\n|$)/m);
          if (match) return Number(match[1]);
          for (const el of document.querySelectorAll('button[aria-label*="page" i],[role="button"][aria-label*="page" i]')) {
            const rect = el.getBoundingClientRect();
            if (rect.width <= 0 || rect.height <= 0) continue;
            const text = `${el.innerText || ''} ${el.getAttribute('aria-label') || ''}`;
            const numbered = text.match(/(?:page\s*)?(\d+)\s*(?:\/|of)\s*(\d+)/i);
            if (numbered) return Number(numbered[1]);
          }
          return null;
        }""")
        return int(value) if value is not None else None

    async def _locator_page_identity(self, locator) -> str | None:
        try:
            return await locator.evaluate(r"""el => {
              let candidate = el;
              for (let depth = 0; depth < 5 && candidate; depth++, candidate = candidate.parentElement) {
                for (const attribute of ['data-page-id', 'data-page-index', 'data-page-number']) {
                  const value = candidate.getAttribute(attribute);
                  if (value !== null && value !== '') return `${attribute}:${value}`;
                }
                const label = candidate.getAttribute('aria-label') || '';
                const match = label.match(/^Page\s+(\d+)(?:\s|$)/i);
                if (match) return `aria-page:${match[1]}`;
              }
              return null;
            }""")
        except Exception:
            return None

    @staticmethod
    def _duplicate_is_authoritative(
        expected_page_number: int,
        confirmed_page_number: int | None,
        current_identity: str | None,
        earlier_identity: str | None,
    ) -> bool:
        if confirmed_page_number == expected_page_number:
            return True
        return bool(current_identity and earlier_identity and current_identity != earlier_identity)

    async def _dismiss_overlays(self, page):
        selectors = ['button[data-testid="cookie-policy-dialog-accept-button"]','button[aria-label="Accept all cookies"]','button[aria-label="Accept cookies"]','[class*="cookie"] button','button[aria-label="Close"]','button[aria-label="Dismiss"]','[data-testid="close-button"]','button:has-text("Got it")','button:has-text("Accept")','button:has-text("OK")']
        for selector in selectors:
            try:
                button = page.locator(selector).first
                if await button.count() and await button.is_visible():
                    await button.click(timeout=1000)
            except Exception:
                pass

    async def _open_graph_preview(self, page):
        metadata = await page.evaluate("() => ({url:document.querySelector('meta[property=\"og:image\"]')?.content||'',width:Number(document.querySelector('meta[property=\"og:image:width\"]')?.content),height:Number(document.querySelector('meta[property=\"og:image:height\"]')?.content)})")
        parsed = urlparse(metadata.get("url", ""))
        if parsed.scheme != "https" or not is_canva_asset_host(parsed):
            return None
        response = await page.context.request.get(metadata["url"], timeout=30_000, fail_on_status_code=False)
        final_url = urlparse(response.url)
        content_type = response.headers.get("content-type", "").lower()
        if not response.ok or final_url.scheme != "https" or not is_canva_asset_host(final_url) or not content_type.startswith("image/"):
            return None
        image_bytes = await response.body()
        if not image_bytes or len(image_bytes) > 25 * 1024 * 1024:
            return None
        try:
            with Image.open(io.BytesIO(image_bytes)) as image:
                decoded_width, decoded_height = image.size
                image.verify()
        except Exception:
            return None
        width = int(metadata.get("width") or decoded_width)
        height = int(metadata.get("height") or decoded_height)
        if width <= 0 or height <= 0:
            return None
        return width, height, base64.b64encode(image_bytes).decode("ascii")

    @staticmethod
    def _state_error(state: str, reason: str) -> AcquisitionError:
        code = {"login-required": "AUTH_REQUIRED", "private": "SOURCE_NOT_PUBLIC", "challenge": "SOURCE_CHALLENGED", "rate-limited": "SOURCE_RATE_LIMITED", "unsupported": "UNSUPPORTED_SOURCE"}.get(state, "CAPTURE_FAILED")
        return AcquisitionError(code, reason)

    @staticmethod
    def _map_error(error: Exception) -> AcquisitionError:
        message = str(error) or "Canva capture failed."
        if re.search(r"login|required|sign in", message, re.I):
            return AcquisitionError("AUTH_REQUIRED", "This Canva design requires a signed-in session; the public-URL MVP cannot access it.")
        if re.search(r"captcha|verify you are human|security check", message, re.I):
            return AcquisitionError("SOURCE_CHALLENGED", "Canva blocked the automated public capture with a security challenge.")
        if re.search(r"rate limit|429", message, re.I):
            return AcquisitionError("SOURCE_RATE_LIMITED", "Canva rate-limited public capture. Wait before retrying.")
        if re.search(r"not public|request access|private|403", message, re.I):
            return AcquisitionError("SOURCE_NOT_PUBLIC", 'Canva could not be opened anonymously. Set link access to "Anyone with the link" with view permission.')
        if re.search(r"timeout|timed out|navigation", message, re.I):
            return AcquisitionError("SOURCE_UNAVAILABLE", f"Canva did not finish loading: {message}")
        return AcquisitionError("CAPTURE_FAILED", message)
