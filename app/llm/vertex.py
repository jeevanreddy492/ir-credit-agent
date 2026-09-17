"""Vertex AI via the google-genai SDK. Auth is ADC: `gcloud auth application-default login` locally, Workload Identity on GKE."""
import asyncio
import functools

from google import genai
from google.genai import types

from app.config import settings


@functools.lru_cache(maxsize=1)
def client() -> genai.Client:
    return genai.Client(vertexai=settings.google_genai_use_vertexai,
                        project=settings.google_cloud_project or None,
                        location=settings.google_cloud_location)


async def embed(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    def _call():
        resp = client().models.embed_content(
            model=settings.embedding_model, contents=texts,
            config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=settings.embedding_dim))
        return [e.values for e in resp.embeddings]
    return await asyncio.to_thread(_call)


async def generate(prompt: str, system: str | None = None, json_mode: bool = False, temperature: float = 0.1) -> str:
    def _call():
        resp = client().models.generate_content(
            model=settings.llm_model, contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, temperature=temperature,
                                               response_mime_type="application/json" if json_mode else None))
        return resp.text or ""
    return await asyncio.to_thread(_call)
