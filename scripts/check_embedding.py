"""Verify Gemini embedding connectivity without PostgreSQL.

Run: uv run python scripts/check_embedding.py
"""

from __future__ import annotations

import math

from dotenv import load_dotenv

from llm_wiki.embedding import EmbeddingSettings, GeminiEmbeddingProvider


def cosine_similarity(left: list[float], right: list[float]) -> float:
    dot_product = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return dot_product / (left_norm * right_norm)


def main() -> None:
    load_dotenv()
    settings = EmbeddingSettings.from_env()
    provider = GeminiEmbeddingProvider(settings)

    query = provider.embed_query("배포 금지 시간은 언제인가요?")
    relevant = provider.embed_document(
        "목요일 오후와 공휴일 전날에는 프로덕션 배포를 하지 않는다.",
        title="배포 가이드 v30",
    )
    unrelated = provider.embed_document(
        "로컬 개발 환경은 Docker Compose로 실행한다.",
        title="온보딩 FAQ",
    )

    print(f"model={settings.model}")
    print(f"dimensions={len(query)}")
    print(f"relevant_similarity={cosine_similarity(query, relevant):.4f}")
    print(f"unrelated_similarity={cosine_similarity(query, unrelated):.4f}")


if __name__ == "__main__":
    main()
