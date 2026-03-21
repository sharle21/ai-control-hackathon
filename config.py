"""
config.py — Centralised constants for the AI Control Hackathon experiment.

Import this in TournamentRunner.py and AnalysisSuite.py instead of
inlining values. Change numbers here; everything else picks them up.
"""

from pathlib import Path

# ── Trusted monitor (Anthropic) ───────────────────────────────────────────────
ANTHROPIC_TRUSTED_MODEL = "anthropic/claude-opus-4-6"

# ── vLLM generator endpoints (H100, OpenAI-compatible) ───────────────────────
VLLM_ENDPOINTS: dict[str, str] = {
    "baseline_8b":   "http://localhost:11434/v1",
    "variant_a_8b":  "http://localhost:11434/v1",
    "variant_b_70b": "http://localhost:11434/v1",
}

VLLM_MODEL_NAMES: dict[str, str] = {
    "baseline_8b":   "llama3.1",
    "variant_a_8b":  "llama3.1",
    "variant_b_70b": "llama3.1",
}

MODEL_VARIANTS = list(VLLM_ENDPOINTS.keys())

# ── Generator output limits ───────────────────────────────────────────────────
GENERATOR_REASONING_MAX_CHARS = 3_000   # truncate reasoning before monitor sees it

# ── Circuit breaker (per-variant, thread-safe) ────────────────────────────────
VLLM_OOM_COOLDOWN_SECONDS = 60          # wait after CUDA OOM before retrying

# ── Parallelism ───────────────────────────────────────────────────────────────
VLLM_POOL_MAX_WORKERS      = 8          # H100 can pipeline multiple 8B requests
ANTHROPIC_POOL_MAX_WORKERS = 4          # Anthropic API concurrency cap

# ── Data directories ──────────────────────────────────────────────────────────
ROOT_DIR  = Path(__file__).parent
DATA_DIR  = ROOT_DIR / "data" / "runs"
STRESS_DIR = ROOT_DIR / "data" / "stress"
FIGURES_DIR = ROOT_DIR / "data" / "figures"
