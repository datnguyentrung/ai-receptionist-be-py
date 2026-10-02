"""Markdown preprocessing and text cleanup."""

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class PreprocessResult:
    processed_text: str
    is_valid: bool
    warnings: tuple[str, ...] = ()


class MarkdownPreprocessor:
    """Clean and validate markdown text before chunking."""

    def preprocess(self, text: str) -> PreprocessResult:
        if not text:
            return PreprocessResult(processed_text="", is_valid=False)

        normalized = unicodedata.normalize("NFKC", text)
        normalized = normalized.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
        
        # Remove trailing spaces and extra blank lines
        lines = [line.rstrip() for line in normalized.splitlines()]
        cleaned = "\n".join(lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

        # Check character validity
        valid_chars = sum(c.isprintable() or c in "\n\t" for c in cleaned)
        is_valid = (valid_chars / len(cleaned) >= 0.85) if cleaned else False

        return PreprocessResult(
            processed_text=cleaned,
            is_valid=is_valid,
        )


__all__ = ["MarkdownPreprocessor", "PreprocessResult"]
