"""Compare full/incremental updates in isolated directories (paid model calls)."""
import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from llm_wiki.answering import GenerationSettings
from llm_wiki.update_evaluation import compare_updates

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True, help="Sources used by the old Wiki")
    parser.add_argument("--after", type=Path, required=True, help="Changed sources")
    parser.add_argument("--wiki", type=Path, required=True, help="Old completed Wiki")
    parser.add_argument("--output", type=Path, required=True, help="New isolated comparison directory")
    parser.add_argument("--interval", type=float, default=16)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    previous = json.loads((args.wiki / "_build/manifest.json").read_text())
    settings = GenerationSettings(api_key=os.environ["GEMINI_API_KEY"],
                                  model=previous["settings"]["model"])
    report = compare_updates(args.before, args.after, args.wiki, args.output, settings,
                             interval=args.interval)
    print(f"{report['status']}: {args.output / 'report.json'}")


if __name__ == "__main__":
    main()
