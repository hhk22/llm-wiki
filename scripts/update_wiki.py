"""Update a built Wiki from added, modified or deleted sources (no vector DB)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from llm_wiki.answering import GenerationSettings
from llm_wiki.wiki_build import WikiBuildError
from llm_wiki.wiki_content import WikiValidationError
from llm_wiki.wiki_update import WikiUpdatePlan, update_wiki

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=ROOT / "sources")
    parser.add_argument("--wiki", type=Path, default=ROOT / "wiki")
    parser.add_argument("--dry-run", action="store_true", help="Show affected pages without API calls")
    parser.add_argument("--model", help="Default: the model used to build this Wiki")
    parser.add_argument("--interval", type=float, default=16)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    try:
        plan = WikiUpdatePlan(args.sources, args.wiki, model=args.model)
        if args.dry_run:
            report = plan.report()
        else:
            report = update_wiki(
                args.sources, args.wiki,
                settings=GenerationSettings(api_key=os.getenv("GEMINI_API_KEY", "").strip(),
                                            model=plan.model),
                interval=args.interval,
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (WikiBuildError, WikiValidationError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
