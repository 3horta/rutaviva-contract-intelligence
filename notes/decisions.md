# Decisiones

## 2026-11-03 · Spikes de proveedores (Día 0, T0.2–T0.4)

Script: `backend/scripts/spike_providers.py` (grupo uv `spike`). Versiones: litellm 1.106.6,
google-genai 2.34.0. Ejecución desde `backend/`:
`uv run --group spike python scripts/spike_providers.py <comando>`.

### Entorno

- `cryptography` 50.x no publica wheel para macOS 12 x86_64 e intenta compilar con Rust (falla).
  Se fija `cryptography<49` en el grupo `spike` (48.0.1 tiene wheel). En Docker (Linux) no aplica.

### T0.2 · LLM Gemini con salida JSON por esquema (LiteLLM)

- Modelos Flash listados en AI Studio: `gemini-3.8-flash` (el más reciente), `gemini-3.5-flash`,
  `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, además de previews.
- `gemini-2.5-flash`: **404, ya no disponible para usuarios nuevos**.
- `gemini-3.8-flash`: **503 UNAVAILABLE ("high demand")** en 3 de 3 intentos (más un timeout de conexión).
- `gemini-3.5-flash`: OK con `response_format=<modelo Pydantic>`. JSON válido según el esquema,
  la cita literal aparece en el contexto. Latencia ≈ 4,4 s; 97 tokens de entrada / 296 de salida.
  En un intento posterior: 503.
- `gemini-3.5-flash-lite`: OK, JSON válido, cita correcta. Latencia ≈ 19,6 s (en un momento de
  carga); 97 / 92 tokens.
- **Decisión:** `LLM_MODEL=gemini/gemini-3.5-flash` como predeterminado para el modo real;
  `gemini-3.5-flash-lite` como alternativa. Ids solo por variable de entorno.
- **Implicación:** los 503 de Gemini son frecuentes. Tratar 503 (como 429) como **transitorio**
  con backoff, y tener Groq como segundo LLM configurable.

### T0.3 · Embeddings Gemini

- `gemini-embedding-001` con `output_dimensionality=768`:
  - Lote de 3 textos → 3 vectores de 768 (≈ 0,8–1,6 s por lote).
  - Norma antes de normalizar ≈ 0,59 (**no viene normalizado**); tras L2 = 1,0000.
  - `RETRIEVAL_DOCUMENT` vs. `RETRIEVAL_QUERY` del mismo texto: coseno 0,90, así que el tipo de tarea
    sí cambia el vector.
- LiteLLM (`gemini/gemini-embedding-001`, `dimensions=768`, `task_type=...`): respeta la dimensión
  y el tipo de tarea; coseno con el vector del SDK = 1,0000 (idéntico).
- `gemini-embedding-2` (probado por comparación): con el SDK, un lote de 3 textos devuelve **1 solo
  vector** (parece agregar los `contents`) y el tipo de tarea no cambia el vector (coseno 1,0).
  Con LiteLLM devuelve 3 vectores pero distintos de los del SDK (coseno 0,76). Comportamiento poco
  claro: descartado para el MVP.
- **Decisión:** `EMBEDDING_DIM=768` confirmado, modelo `gemini-embedding-001`, versión guardada como
  `gemini-embedding-001@768`. Normalizar con L2 siempre antes de guardar. LiteLLM sirve para
  embeddings (no hace falta el SDK `google-genai` en la app); se usa una sola librería.

### T0.4 · Groq

- Pendiente: falta crear `GROQ_API_KEY`.
