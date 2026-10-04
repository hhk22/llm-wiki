"""Check generated Wiki files and provenance without model or database calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from llm_wiki.wiki_content import WikiValidationError
from llm_wiki.wiki_validation import verify_wiki

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=ROOT / "sources")
    parser.add_argument("--wiki", type=Path, default=ROOT / "wiki")
    args = parser.parse_args()
    try:
        print(json.dumps(verify_wiki(args.sources, args.wiki), ensure_ascii=False, indent=2))
    except WikiValidationError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
