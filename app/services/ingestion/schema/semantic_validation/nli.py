"""Pretrained NLI cross-encoder (premise=evidence, hypothesis=claim). GPU mặc định, fallback CPU."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.services.ingestion.schema.semantic_validation.models import (
    RULE_VERSION,
    NliScores,
)

logger = logging.getLogger(__name__)

_RETRY_AFTER_SECONDS = 60.0


class NliUnavailableError(RuntimeError):
    """Model không load được; gate fail-closed về UNCERTAIN (rồi LLM fallback)."""


@dataclass(frozen=True)
class NliPair:
    premise: str
    hypothesis: str


class NliVerifier(Protocol):
    model_version: str

    def verify_batch(self, pairs: Sequence[NliPair]) -> list[NliScores]: ...


class TransformersNliVerifier:
    """HuggingFace sequence-classification NLI model, lazy-loaded, batch inference."""

    def __init__(
        self,
        model_name: str,
        *,
        device: str = "auto",
        batch_size: int = 16,
        max_length: int = 512,
    ) -> None:
        self.model_version = model_name
        self._device_pref = device.casefold()
        self._batch_size = max(1, batch_size)
        self._max_length = max_length
        self._lock = threading.Lock()
        self._tokenizer = None
        self._model = None
        self._device = "cpu"
        self._label_index: dict[str, int] = {}
        self._failed_at: float | None = None
        self._error = ""

    # -- loading ---------------------------------------------------------
    def _resolve_device(self, torch) -> str:
        if self._device_pref in {"auto", "cuda", "gpu"} and torch.cuda.is_available():
            return "cuda"
        if self._device_pref not in {"auto", "cpu", "cuda", "gpu"}:
            return self._device_pref
        return "cpu"

    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._model is not None:
                return
            if self._failed_at and time.monotonic() - self._failed_at < _RETRY_AFTER_SECONDS:
                raise NliUnavailableError(self._error)
            try:
                import torch
                from transformers import (
                    AutoModelForSequenceClassification,
                    AutoTokenizer,
                )

                tokenizer = AutoTokenizer.from_pretrained(self.model_version)
                model = AutoModelForSequenceClassification.from_pretrained(self.model_version)
                model.eval()
                labels = {str(v).casefold(): int(k) for k, v in model.config.id2label.items()}
                index = {
                    key: next(i for name, i in labels.items() if key in name)
                    for key in ("entail", "neutral", "contradict")
                }
                device = self._resolve_device(torch)
                try:
                    model.to(device)
                except Exception:
                    logger.warning("NLI_DEVICE_FALLBACK device=%s -> cpu", device, exc_info=True)
                    device = "cpu"
                    model.to(device)
            except Exception as exc:
                self._failed_at = time.monotonic()
                self._error = f"{type(exc).__name__}: {exc}"
                logger.error("NLI_LOAD_FAILED model=%s error=%s", self.model_version, self._error)
                raise NliUnavailableError(self._error) from exc
            self._tokenizer, self._model = tokenizer, model
            self._device, self._label_index = device, index
            self._failed_at = None
            logger.info("NLI_LOADED model=%s device=%s", self.model_version, device)

    # -- inference -------------------------------------------------------
    def _forward(self, batch: Sequence[NliPair], device: str) -> list[NliScores]:
        import torch

        encoded = self._tokenizer(
            [p.premise for p in batch],
            [p.hypothesis for p in batch],
            truncation="only_first",
            max_length=self._max_length,
            padding=True,
            return_tensors="pt",
        ).to(device)
        with torch.inference_mode():
            probs = torch.softmax(self._model(**encoded).logits.float(), dim=-1).cpu().tolist()
        idx = self._label_index
        return [
            NliScores(row[idx["entail"]], row[idx["neutral"]], row[idx["contradict"]])
            for row in probs
        ]

    def _infer(self, batch: Sequence[NliPair]) -> list[NliScores]:
        try:
            return self._forward(batch, self._device)
        except RuntimeError:  # CUDA OOM / kernel error -> CPU
            if not self._device.startswith("cuda"):
                raise
            import torch

            logger.warning("NLI_CUDA_ERROR switching to cpu", exc_info=True)
            torch.cuda.empty_cache()
            self._model.to("cpu")
            self._device = "cpu"
            return self._forward(batch, "cpu")

    def verify_batch(self, pairs: Sequence[NliPair]) -> list[NliScores]:
        if not pairs:
            return []
        self._ensure_loaded()
        order = sorted(range(len(pairs)), key=lambda i: len(pairs[i].premise))
        results: list[NliScores | None] = [None] * len(pairs)
        for start in range(0, len(order), self._batch_size):
            chunk = order[start : start + self._batch_size]
            for position, scores in zip(chunk, self._infer([pairs[i] for i in chunk])):
                results[position] = scores
        return [r for r in results if r is not None]


class CachingNliVerifier:
    """LRU cache: SHA256(model + claim + evidence + rule version)."""

    def __init__(self, inner: NliVerifier, max_entries: int = 4096) -> None:
        self._inner = inner
        self._max = max_entries
        self._cache: OrderedDict[str, NliScores] = OrderedDict()
        self._lock = threading.Lock()
        self.model_version = inner.model_version

    def _key(self, pair: NliPair) -> str:
        raw = "\x1f".join((self.model_version, pair.hypothesis, pair.premise, RULE_VERSION))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def verify_batch(self, pairs: Sequence[NliPair]) -> list[NliScores]:
        keys = [self._key(p) for p in pairs]
        with self._lock:
            known = {k: self._cache[k] for k in keys if k in self._cache}
            for key in known:
                self._cache.move_to_end(key)
        missing: dict[str, NliPair] = {k: p for k, p in zip(keys, pairs) if k not in known}
        if missing:
            computed = self._inner.verify_batch(list(missing.values()))
            with self._lock:
                for key, scores in zip(missing, computed):
                    self._cache[key] = known[key] = scores
                while len(self._cache) > self._max:
                    self._cache.popitem(last=False)
        return [known[k] for k in keys]
