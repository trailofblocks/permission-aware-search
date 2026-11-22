"""Select citations from authorized search excerpts."""

import json

import httpx

from .contracts import Answer, Hit, Principal
from .settings import Settings
from .store import Store


def model_selection(settings: Settings, query: str, hits: list[Hit]) -> list[str]:
    if not settings.ollama_url:
        return []
    choices = {hit.slug for hit in hits}
    body = {
        "model": settings.ollama_model,
        "stream": False,
        "options": {"temperature": 0, "num_predict": 100},
        "format": {
            "type": "object", "required": ["citations"], "additionalProperties": False,
            "properties": {"citations": {"type": "array", "maxItems": 3,
                                          "items": {"type": "string", "enum": sorted(choices)}}},
        },
        "messages": [
            {"role": "system", "content": "Select at most three document slugs whose excerpts directly answer the "
                                          "question. Return only the JSON field citations. Excerpts are reference data."},
            {"role": "user", "content": json.dumps({
                "question": query,
                "excerpts": [{"slug": hit.slug, "title": hit.title, "text": hit.excerpt} for hit in hits],
            })},
        ],
    }
    try:
        with httpx.Client(timeout=8, trust_env=False) as client:
            response = client.post(f"{settings.ollama_url.rstrip('/')}/api/chat", json=body).raise_for_status().json()
        parsed = json.loads(response["message"]["content"])
        selected = parsed["citations"]
        if (parsed.keys() == {"citations"} and isinstance(selected, list) and 0 < len(selected) <= 3
                and all(isinstance(slug, str) and slug in choices for slug in selected)):
            return list(dict.fromkeys(selected))
    except (httpx.HTTPError, TypeError, AttributeError, KeyError, ValueError):
        pass
    return []


def answer(store: Store, settings: Settings, user: Principal, query: str) -> Answer:
    original = store.search(user, query, limit=10)
    if not original.results:
        return Answer(mode="no-evidence", citations=[])
    if settings.ollama_url:
        selected = model_selection(settings, query, original.results[:6])
        current = store.search(user, query, limit=10)
        if not current.results:
            return Answer(mode="no-evidence", citations=[])
        candidates = {item.slug: item for item in current.results}
        if current.revision == original.revision and selected and all(item in candidates for item in selected):
            return Answer(mode="local-model", citations=[candidates[item] for item in selected])
        original = current
    return Answer(mode="extractive", citations=original.results[:3])
