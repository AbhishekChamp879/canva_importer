from __future__ import annotations

import re
from typing import Any


def classify_browser_state(snapshot: dict[str, Any]) -> tuple[str, str]:
    text = f"{snapshot.get('title', '')}\n{snapshot.get('bodyText', '')}".lower()
    status = snapshot.get("responseStatus")
    if status == 429 or re.search(r"too many requests|rate limit|try again later", text):
        return "rate-limited", "Canva rate-limited the capture browser."
    if status == 401 or re.search(r"log in to canva|sign in to canva|continue with google", text):
        return "login-required", "The design requires a Canva login."
    if re.search(r"captcha|verify you are human|checking your browser|security check|unusual traffic", text):
        return "challenge", "Canva presented an anti-automation challenge."
    if status == 403 or re.search(r"request access|you don't have access|design is private|access denied", text):
        return "private", "The design is not publicly accessible."
    if re.search(r"canva docs|whiteboard|video design", text):
        return "unsupported", "This Canva design type is outside the fixed-size MVP."
    if snapshot.get("hasPageCandidate"):
        return "ready", "A fixed-size Canva page is visible."
    if snapshot.get("readyState") == "loading" or "loading" in text:
        return "loading", "Canva is still rendering the design."
    return "error", "Canva loaded without exposing a fixed-size design page."

