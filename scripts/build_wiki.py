"""Build the initial Wiki without changing existing keyword/vector/hybrid retrieval."""

from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from llm_wiki.answering import GenerationSettings
from llm_wiki.wiki_build import WikiBuilder, WikiBuildError
from llm_wiki.wiki_content import WikiValidationError

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=ROOT / "sources")
    parser.add_argument("--output", type=Path, default=ROOT / "wiki")
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument("--model", help="Wiki generation model; existing answer settings stay unchanged")
    parser.add_argument("--interval", type=float, default=16)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--accept-code-change", action="store_true",
        help="With --resume, record changed generator code and revalidate cached results; "
             "sources, policy, schemas, and model must match",
    )
    args = parser.parse_args()
    if args.accept_code_change and not args.resume:
        parser.error("--accept-code-change requires --resume")
    load_dotenv(ROOT / ".env")
    settings = GenerationSettings.from_env()
    if args.model:
        settings = GenerationSettings(api_key=settings.api_key, model=args.model.strip())
    try:
        builder = WikiBuilder(
            args.sources, args.output, settings, batch_size=args.batch_size,
            interval=args.interval, resume=args.resume,
            accept_code_change=args.accept_code_change,
        )
        print(builder.build())
    except (WikiBuildError, WikiValidationError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
