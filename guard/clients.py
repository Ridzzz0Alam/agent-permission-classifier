"""Model clients for the local Ollama server and Ollama Cloud.

Both go through the response cache. A cache hit makes no request and costs nothing. Cloud calls
reserve their worst-case cost in the Budget before being sent, and are capped at 3 concurrent
requests (the Pro plan's limit). The API key is read from .env and is never logged, cached or put
into an exception message.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv

from guard.budget import Budget
from guard.cache import ResponseCache, cache_key

ROOT = Path(__file__).resolve().parent.parent
LOCAL_URL = "http://localhost:11434/api/chat"
CLOUD_URL = "https://ollama.com/api/chat"
LOCAL_MODEL = "llama3.2:3b"
CLOUD_MODEL = "deepseek-v4-pro:0813"
NUM_CTX, NUM_THREAD = 4096, 4
CHARS_PER_TOKEN_FLOOR = 2.5  # conservative token estimate for budget reservations (real is ~4)


@dataclass
class ChatResult:
    content: str
    thinking: str
    prompt_tokens: int
    cached_tokens: int
    output_tokens: int
    latency_s: float
    cost_usd: float
    from_cache: bool = False


class ClientError(RuntimeError):
    pass


class BaseClient:
    kind = "base"
    max_concurrency = 1

    def __init__(self, model: str, cache: ResponseCache | None = None):
        self.model = model
        self.cache = cache if cache is not None else ResponseCache()

    def payload(self, messages, num_predict, temperature, seed) -> dict:
        raise NotImplementedError

    def _send(self, payload: dict, max_output: int) -> ChatResult:
        raise NotImplementedError

    def chat(self, messages: list[dict], *, num_predict: int, temperature: float = 0.0, seed: int = 42) -> ChatResult:
        payload = self.payload(messages, num_predict, temperature, seed)
        key = cache_key({"kind": self.kind, **payload})
        hit = self.cache.get(key)
        if hit is not None:
            return ChatResult(**{k: hit[k] for k in ChatResult.__dataclass_fields__ if k != "from_cache"},
                              from_cache=True)
        res = self._send(payload, num_predict)
        self.cache.put(key, {"model": self.model, "kind": self.kind, **asdict(res)})
        return res


class LocalClient(BaseClient):
    kind = "local"
    max_concurrency = 1  # CPU-bound; parallel requests would only slow each other down

    def __init__(self, model: str = LOCAL_MODEL, cache: ResponseCache | None = None, url: str = LOCAL_URL):
        super().__init__(model, cache)
        self.url = url

    def payload(self, messages, num_predict, temperature, seed):
        return {"model": self.model, "messages": messages, "stream": False, "keep_alive": "30m",
                "options": {"temperature": temperature, "seed": seed, "num_predict": num_predict,
                            "num_ctx": NUM_CTX, "num_thread": NUM_THREAD}}

    def _send(self, payload, max_output):
        t0 = time.monotonic()
        try:
            r = requests.post(self.url, json=payload, timeout=1800)
        except requests.RequestException as e:
            raise ClientError(f"local Ollama unreachable at {self.url}: {type(e).__name__}") from None
        if r.status_code != 200:
            raise ClientError(f"local Ollama HTTP {r.status_code}: {r.text[:200]}")
        d = r.json()
        msg = d.get("message", {})
        return ChatResult(content=msg.get("content", ""), thinking=msg.get("thinking", "") or "",
                          prompt_tokens=d.get("prompt_eval_count") or 0,
                          cached_tokens=d.get("prompt_eval_cached_count") or 0,
                          output_tokens=d.get("eval_count") or 0,
                          latency_s=time.monotonic() - t0, cost_usd=0.0)


class CloudClient(BaseClient):
    kind = "cloud"
    max_concurrency = 3  # Ollama Cloud Pro plan limit
    RETRY_SLEEPS = (2, 5, 15)

    def __init__(self, model: str = CLOUD_MODEL, budget: Budget | None = None,
                 cache: ResponseCache | None = None, url: str = CLOUD_URL, api_key: str | None = None):
        super().__init__(model, cache)
        self.url = url
        self.budget = budget if budget is not None else Budget()
        self._sem = threading.BoundedSemaphore(self.max_concurrency)
        if api_key is None:
            load_dotenv(ROOT / ".env")
            api_key = os.environ.get("OLLAMA_API_KEY", "").strip()
        if not api_key:
            raise ClientError("OLLAMA_API_KEY is not set (expected in .env)")
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def __repr__(self):  # never show the key
        return f"CloudClient(model={self.model!r})"

    def payload(self, messages, num_predict, temperature, seed):
        return {"model": self.model, "messages": messages, "stream": False, "think": False,
                "options": {"temperature": temperature, "seed": seed, "num_predict": num_predict}}

    def _post(self, payload) -> requests.Response:
        last = "no attempt"
        for attempt, pause in enumerate((0, *self.RETRY_SLEEPS)):
            if pause:
                time.sleep(pause)
            try:
                r = requests.post(self.url, json=payload, headers=self._headers, timeout=180)
            except requests.RequestException as e:
                last = type(e).__name__
                continue
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        raise ClientError(f"cloud call failed: {last}")

    def _send(self, payload, max_output):
        chars = sum(len(m["content"]) for m in payload["messages"])
        rid = self.budget.reserve(self.model, int(chars / CHARS_PER_TOKEN_FLOOR) + 50, max_output)
        try:
            with self._sem:
                t0 = time.monotonic()
                r = self._post(payload)
                latency = time.monotonic() - t0
        except BaseException:
            self.budget.release(rid)  # nothing was billed (or we cannot know): free the reservation
            raise
        d = r.json()
        msg = d.get("message", {})
        p, c, o = d.get("prompt_eval_count") or 0, d.get("prompt_eval_cached_count") or 0, d.get("eval_count") or 0
        cost = self.budget.record(rid, self.model, p, c, o)
        return ChatResult(content=msg.get("content", ""), thinking=msg.get("thinking", "") or "",
                          prompt_tokens=p, cached_tokens=c, output_tokens=o, latency_s=latency, cost_usd=cost)


def make_client(which: str, budget: Budget | None = None, cache: ResponseCache | None = None) -> BaseClient:
    if which == "local":
        return LocalClient(cache=cache)
    if which == "cloud":
        return CloudClient(budget=budget, cache=cache)
    raise ValueError(f"unknown client {which!r} (use local or cloud)")
