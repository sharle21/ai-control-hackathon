"""
truncation_proxy.py — Reasoning-truncation proxy for vLLM endpoints
=====================================================================
AI Control Hackathon — March 2026

HOME SECURITY ANALOGY:
Imagine the stamping machine (vLLM) sends back a work-order note (reasoning trace)
that is sometimes 50 pages long.  The building inspector (Monitor / Trusted Editor)
is not supposed to read the full 50 pages — the experiment requires that the inspector
only sees a short summary, so that we're testing whether subtle attacks slip through,
not whether the inspector can read the entire attack plan verbatim.

This proxy is the CLERK at the factory gate.  Every response from the stamping machine
passes through this clerk's desk.  The clerk reads the work-order note, trims it to
GENERATOR_REASONING_MAX_CHARS characters, staples it back on, and passes it through.
The framework never sees the full note — only the trimmed version.

The circuit breaker is also here: if the stamping machine overheats (CUDA OOM error
from vLLM), the clerk trips the fuse-box for VLLM_OOM_COOLDOWN_SECONDS before sending
more work its way.

Usage (start one proxy per variant before launching TournamentRunner.py):
    python truncation_proxy.py --variant baseline_8b
    python truncation_proxy.py --variant variant_a_8b
    python truncation_proxy.py --variant variant_b_70b

Config flow:
    PROXY_ENDPOINTS  (in config.py) → what the framework calls  (e.g. localhost:9000)
    VLLM_ENDPOINTS   (in config.py) → what the proxy forwards to (e.g. localhost:8000)
    GENERATOR_REASONING_MAX_CHARS   → max chars kept from <reasoning> blocks

TournamentRunner.py must use PROXY_ENDPOINTS as OPENAI_BASE_URL — see config.py.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

from config import (
    GENERATOR_REASONING_MAX_CHARS,
    PROXY_ENDPOINTS,
    VLLM_ENDPOINTS,
    VLLM_OOM_COOLDOWN_SECONDS,
)


# ── Circuit breaker (per-variant, shared with main process via module state) ──

_cb_event = threading.Event()          # set = circuit is OPEN (blocked)
_variant_label = "unknown"             # set in main()


def _is_open() -> bool:
    return _cb_event.is_set()


def _trip() -> None:
    print(f"[PROXY:{_variant_label}] Circuit TRIPPED — pausing {VLLM_OOM_COOLDOWN_SECONDS}s")
    _cb_event.set()
    threading.Timer(VLLM_OOM_COOLDOWN_SECONDS, _cb_event.clear).start()


# ── Reasoning truncation ───────────────────────────────────────────────────────

def _truncate_reasoning(content: str, max_chars: int) -> tuple[str, bool]:
    """
    CLERK ANALOGY:
    Finds every <reasoning>…</reasoning> block in the content and clips it to
    max_chars.  Returns (modified_content, was_truncated).

    Truncation adds '[TRUNCATED]' at the cut point so the monitor can see it
    was shortened — important for the experiment's audit trail.
    """
    truncated = False

    def _clip(m: re.Match) -> str:
        nonlocal truncated
        inner = m.group(1)
        if len(inner) > max_chars:
            truncated = True
            return f"<reasoning>{inner[:max_chars]}… [TRUNCATED]</reasoning>"
        return m.group(0)

    result = re.sub(r"<reasoning>(.*?)</reasoning>", _clip, content, flags=re.DOTALL)
    return result, truncated


# ── HTTP proxy handler ─────────────────────────────────────────────────────────

class TruncationProxyHandler(BaseHTTPRequestHandler):
    """
    CLERK ANALOGY:
    This class IS the clerk at the factory gate.  It:
      1. Accepts a request from the framework (inspect_ai / control-arena)
      2. Forwards it unchanged to the real vLLM endpoint
      3. Parses the response, trims any <reasoning> blocks
      4. Returns the trimmed response to the framework

    All other endpoints (GET /v1/models, etc.) are passed through transparently.
    """

    upstream: str = "http://localhost:8000"   # set in run()

    def log_message(self, fmt: str, *args) -> None:
        print(f"[PROXY:{_variant_label}] {fmt % args}")

    def _forward(self, body: Optional[bytes] = None) -> None:
        """Forward the current request to upstream and return the (possibly modified) response."""
        url = self.upstream.rstrip("/") + self.path
        method = self.command

        headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in ("host", "content-length")
        }

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
                status = resp.status
                resp_headers = dict(resp.headers)
        except urllib.error.HTTPError as exc:
            # Check for OOM in the error body
            err_body = exc.read()
            if b"CUDA out of memory" in err_body:
                _trip()
            self._send_raw(exc.code, err_body, {"Content-Type": "application/json"})
            return
        except Exception as exc:
            self._send_raw(502, json.dumps({"error": str(exc)}).encode(),
                           {"Content-Type": "application/json"})
            return

        # Only process chat completion responses — pass everything else through
        is_completion = self.path.startswith("/v1/chat/completions") and method == "POST"
        if is_completion:
            raw, resp_headers = self._apply_truncation(raw, resp_headers)

        self._send_raw(status, raw, resp_headers)

    def _apply_truncation(
        self, raw: bytes, headers: dict
    ) -> tuple[bytes, dict]:
        """
        CLERK ANALOGY:
        Opens the envelope (JSON response), finds the work-order note
        (<reasoning> in message content), clips it, reseals the envelope,
        and returns the lighter package.
        """
        try:
            body = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return raw, headers

        modified = False
        for choice in body.get("choices", []):
            msg = choice.get("message", {})
            content = msg.get("content") or ""
            if not content:
                continue
            trimmed, was_cut = _truncate_reasoning(content, GENERATOR_REASONING_MAX_CHARS)
            if was_cut:
                msg["content"] = trimmed
                modified = True
                print(
                    f"[PROXY:{_variant_label}] Reasoning truncated → "
                    f"{GENERATOR_REASONING_MAX_CHARS} chars"
                )

        if modified:
            raw = json.dumps(body).encode()
            headers = dict(headers)
            headers["Content-Length"] = str(len(raw))

        return raw, headers

    def _send_raw(self, status: int, body: bytes, resp_headers: dict) -> None:
        self.send_response(status)
        for k, v in resp_headers.items():
            if k.lower() in ("content-type", "content-length"):
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:   # noqa: N802
        if _is_open():
            self._send_raw(503, b'{"error":"circuit open — OOM cooldown"}',
                           {"Content-Type": "application/json"})
            return
        self._forward()

    def do_POST(self) -> None:  # noqa: N802
        if _is_open():
            self._send_raw(503, b'{"error":"circuit open — OOM cooldown"}',
                           {"Content-Type": "application/json"})
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None
        self._forward(body)


def run(variant: str) -> None:
    """
    FACTORY ANALOGY:
    Starts the clerk's desk.  Looks up which proxy port to listen on (from
    PROXY_ENDPOINTS in config.py) and which vLLM port to forward to (from
    VLLM_ENDPOINTS in config.py), then opens for business.
    """
    global _variant_label
    _variant_label = variant

    proxy_url  = PROXY_ENDPOINTS[variant]          # e.g. "http://localhost:9000/v1"
    upstream   = VLLM_ENDPOINTS[variant]           # e.g. "http://localhost:8000/v1"

    # Extract port from proxy URL
    proxy_port = int(proxy_url.split(":")[-1].split("/")[0])
    # Strip /v1 suffix from upstream for urllib forwarding
    TruncationProxyHandler.upstream = upstream.rstrip("/").removesuffix("/v1")

    server = HTTPServer(("0.0.0.0", proxy_port), TruncationProxyHandler)
    print(f"[PROXY:{variant}] Listening on {proxy_url}")
    print(f"[PROXY:{variant}] Forwarding to {upstream}")
    print(f"[PROXY:{variant}] Reasoning cap: {GENERATOR_REASONING_MAX_CHARS} chars")
    print(f"[PROXY:{variant}] Circuit breaker cooldown: {VLLM_OOM_COOLDOWN_SECONDS}s")
    print(f"[PROXY:{variant}] Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n[PROXY:{variant}] Stopped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reasoning-truncation proxy for vLLM.")
    parser.add_argument(
        "--variant",
        required=True,
        help="Model variant name (must be a key in PROXY_ENDPOINTS and VLLM_ENDPOINTS).",
    )
    args = parser.parse_args()
    run(args.variant)
