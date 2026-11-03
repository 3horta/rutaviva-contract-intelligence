"""Real-provider spike. Not part of the app.

Reads the keys from the .env at the repo root. Never prints the keys.

Usage (from backend/):
    uv run --group spike python scripts/spike_providers.py gemini-models
    uv run --group spike python scripts/spike_providers.py gemini-llm --model <id-flash>
    uv run --group spike python scripts/spike_providers.py gemini-embed
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path

from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]
EMBEDDING_MODEL = os.getenv("SPIKE_EMBEDDING_MODEL", "gemini-embedding-001")
DIM = 768

QUESTION = "What penalty applies if the supplier delivers late?"
CONTEXT = (
    "CLAUSE 7. PENALTIES. For each day of delay in delivery, the supplier shall pay "
    "a penalty equal to 2% of the order value, up to a maximum of 10%."
)
TEXTS = [
    "Penalty clause for late delivery of tires.",
    "The fleet maintenance agreement renews automatically every year.",
    "The fuel price is adjusted monthly according to the official index.",
]


class SpikeAnswer(BaseModel):
    answer: str
    quote: str
    confidence: str


def load_env() -> None:
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def require_key(name: str) -> str:
    key = os.getenv(name, "")
    if not key:
        sys.exit(f"Missing {name} in {REPO_ROOT / '.env'}")
    return key


def norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True)) / (norm(a) * norm(b))


def prompt() -> str:
    return (
        "Answer using only the context. Return JSON with answer, quote (verbatim quote from "
        f"the context) and confidence (low|medium|high).\n\nContext:\n{CONTEXT}\n\n"
        f"Question: {QUESTION}"
    )


def check_answer(raw: str, latency: float) -> None:
    print(f"Latency: {latency:.2f} s")
    print("Raw response:", raw)
    parsed = SpikeAnswer.model_validate_json(raw)
    print("Valid JSON per the schema:", parsed.model_dump())
    print("Quote appears in the context:", parsed.quote in CONTEXT)


# --- Gemini ---


def gemini_models(_: argparse.Namespace) -> None:
    from google import genai

    client = genai.Client(api_key=require_key("GEMINI_API_KEY"))
    for m in client.models.list():
        actions = m.supported_actions or []
        if "generateContent" in actions or "embedContent" in actions:
            print(f"{m.name:55} {','.join(actions)}")


def gemini_llm(args: argparse.Namespace) -> None:
    import litellm

    litellm.suppress_debug_info = True
    start = time.perf_counter()
    resp = litellm.completion(
        model=f"gemini/{args.model}",
        messages=[{"role": "user", "content": prompt()}],
        response_format=SpikeAnswer,
        api_key=require_key("GEMINI_API_KEY"),
        timeout=30,
    )
    check_answer(resp.choices[0].message.content, time.perf_counter() - start)
    print("Tokens:", resp.usage.prompt_tokens, "in /", resp.usage.completion_tokens, "out")


def gemini_embed(_: argparse.Namespace) -> None:
    key = require_key("GEMINI_API_KEY")

    print(f"== SDK google-genai ({EMBEDDING_MODEL}, {DIM} dim) ==")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)
    vectors = {}
    for task in ("RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"):
        start = time.perf_counter()
        res = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=TEXTS,
            config=types.EmbedContentConfig(task_type=task, output_dimensionality=DIM),
        )
        vecs = [e.values for e in res.embeddings]
        vectors[task] = vecs
        print(
            f"{task}: batch of {len(TEXTS)} -> {len(vecs)} vectors, "
            f"dim={len(vecs[0])}, norm={norm(vecs[0]):.4f}, "
            f"{time.perf_counter() - start:.2f} s"
        )
    v = vectors["RETRIEVAL_DOCUMENT"][0]
    n = norm(v)
    print(f"Norm after L2 normalization: {norm([x / n for x in v]):.4f}")
    print(
        "Cosine doc vs. query for the same text: "
        f"{cosine(vectors['RETRIEVAL_DOCUMENT'][0], vectors['RETRIEVAL_QUERY'][0]):.4f}"
    )

    print(f"\n== LiteLLM (gemini/{EMBEDDING_MODEL}, dimensions={DIM}) ==")
    import litellm

    litellm.suppress_debug_info = True
    try:
        resp = litellm.embedding(
            model=f"gemini/{EMBEDDING_MODEL}",
            input=TEXTS,
            dimensions=DIM,
            task_type="RETRIEVAL_DOCUMENT",
            api_key=key,
        )
        lvecs = [d["embedding"] for d in resp.data]
        print(f"batch of {len(TEXTS)} -> {len(lvecs)} vectors, dim={len(lvecs[0])}")
        print("Dimension respected?", len(lvecs[0]) == DIM)
        print(
            "Cosine vs. the SDK vector (same text and task): "
            f"{cosine(lvecs[0], vectors['RETRIEVAL_DOCUMENT'][0]):.4f}"
        )
    except Exception as exc:  # noqa: BLE001 - spike: we want to see any failure
        print(f"LiteLLM failed: {type(exc).__name__}: {exc}")


def main() -> None:
    load_env()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(required=True)
    sub.add_parser("gemini-models").set_defaults(func=gemini_models)
    p = sub.add_parser("gemini-llm")
    p.add_argument("--model", required=True)
    p.set_defaults(func=gemini_llm)
    sub.add_parser("gemini-embed").set_defaults(func=gemini_embed)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
