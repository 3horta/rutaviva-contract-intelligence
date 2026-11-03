"""Spike de proveedores reales (T0.2-T0.4). No forma parte de la app.

Lee las claves del .env de la raíz del repo. Nunca imprime las claves.

Uso (desde backend/):
    uv run --group spike python scripts/spike_providers.py gemini-models
    uv run --group spike python scripts/spike_providers.py gemini-llm --model <id-flash>
    uv run --group spike python scripts/spike_providers.py gemini-embed
    uv run --group spike python scripts/spike_providers.py groq-models
    uv run --group spike python scripts/spike_providers.py groq-llm --model <id-groq>
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.request
from pathlib import Path

from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]
EMBEDDING_MODEL = os.getenv("SPIKE_EMBEDDING_MODEL", "gemini-embedding-001")
DIM = 768

QUESTION = "¿Qué penalización aplica si el proveedor entrega tarde?"
CONTEXT = (
    "CLÁUSULA 7. PENALIZACIONES. Por cada día de retraso en la entrega, el proveedor "
    "pagará una penalización equivalente al 2% del valor del pedido, hasta un máximo del 10%."
)
TEXTS = [
    "Cláusula de penalización por retraso en la entrega de neumáticos.",
    "El contrato de mantenimiento de flota se renueva automáticamente cada año.",
    "El precio del combustible se ajusta mensualmente según el índice oficial.",
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
        sys.exit(f"Falta {name} en {REPO_ROOT / '.env'}")
    return key


def norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True)) / (norm(a) * norm(b))


def prompt() -> str:
    return (
        "Responde solo con el contexto. Devuelve JSON con answer, quote (cita literal del "
        f"contexto) y confidence (low|medium|high).\n\nContexto:\n{CONTEXT}\n\n"
        f"Pregunta: {QUESTION}"
    )


def check_answer(raw: str, latency: float) -> None:
    print(f"Latencia: {latency:.2f} s")
    print("Respuesta cruda:", raw)
    parsed = SpikeAnswer.model_validate_json(raw)
    print("JSON válido según el esquema:", parsed.model_dump())
    print("La cita aparece en el contexto:", parsed.quote in CONTEXT)


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
    print("Tokens:", resp.usage.prompt_tokens, "entrada /", resp.usage.completion_tokens, "salida")


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
            f"{task}: lote de {len(TEXTS)} -> {len(vecs)} vectores, "
            f"dim={len(vecs[0])}, norma={norm(vecs[0]):.4f}, "
            f"{time.perf_counter() - start:.2f} s"
        )
    v = vectors["RETRIEVAL_DOCUMENT"][0]
    n = norm(v)
    print(f"Norma tras normalizar L2: {norm([x / n for x in v]):.4f}")
    print(
        "Coseno doc vs. query del mismo texto: "
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
        print(f"lote de {len(TEXTS)} -> {len(lvecs)} vectores, dim={len(lvecs[0])}")
        print("¿Respeta la dimensión?", len(lvecs[0]) == DIM)
        print(
            "Coseno con el vector del SDK (mismo texto y tarea): "
            f"{cosine(lvecs[0], vectors['RETRIEVAL_DOCUMENT'][0]):.4f}"
        )
    except Exception as exc:  # noqa: BLE001 - spike: queremos ver cualquier fallo
        print(f"LiteLLM falló: {type(exc).__name__}: {exc}")


# --- Groq ---


def groq_models(_: argparse.Namespace) -> None:
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {require_key('GROQ_API_KEY')}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    for m in sorted(data["data"], key=lambda m: m["id"]):
        print(f"{m['id']:50} ctx={m.get('context_window')}")


def groq_llm(args: argparse.Namespace) -> None:
    import litellm

    litellm.suppress_debug_info = True
    start = time.perf_counter()
    resp = litellm.completion(
        model=f"groq/{args.model}",
        messages=[{"role": "user", "content": prompt()}],
        response_format={"type": "json_object"},
        api_key=require_key("GROQ_API_KEY"),
        timeout=30,
    )
    check_answer(resp.choices[0].message.content, time.perf_counter() - start)
    print("Tokens:", resp.usage.prompt_tokens, "entrada /", resp.usage.completion_tokens, "salida")


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
    sub.add_parser("groq-models").set_defaults(func=groq_models)
    p = sub.add_parser("groq-llm")
    p.add_argument("--model", required=True)
    p.set_defaults(func=groq_llm)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
