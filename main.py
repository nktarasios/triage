#!/usr/bin/env python3
"""
Safety event triage agent — CLI entry point.

Example:
    python main.py --input data/sgo_ads.csv --config config/nhtsa_sgo_ads.yaml \
        --output output/triaged.json --limit 25 --model qwen2.5:14b

Dry run without any LLM/Ollama (rules engine only):
    python main.py --input data/sgo_ads.csv --config config/nhtsa_sgo_ads.yaml \
        --output output/triaged.json --limit 25 --no-llm
"""
import argparse
import json
import logging
import sys
from pathlib import Path

from triage.llm_client import LLMClient, OllamaConnectionError, StubClient
from triage.pipeline import run_pipeline

logger = logging.getLogger("triage")


def main():
    parser = argparse.ArgumentParser(description="Local safety-event triage agent")
    parser.add_argument("--input", required=True, help="Path to input CSV")
    parser.add_argument("--config", required=True, help="Path to dataset YAML config")
    parser.add_argument("--output", required=True, help="Path to write results JSON")
    parser.add_argument("--limit", type=int, default=None, help="Max rows to process")
    parser.add_argument(
        "--model", default="qwen2.5:14b", help="Ollama model name"
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:11434/v1",
        help="Ollama OpenAI-compatible endpoint",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip the LLM call entirely; use rules-engine output only "
        "(good for testing the pipeline without Ollama running)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    llm_client = StubClient() if args.no_llm else LLMClient(args.model, args.base_url)

    skipped = []

    def on_progress(n, event_id):
        logger.info("  [%d] %s", n, event_id)

    def on_skip(row_number, exc):
        skipped.append(row_number)

    logger.info("Running pipeline: %s (limit=%s)", args.input, args.limit)
    try:
        results = run_pipeline(
            args.input,
            args.config,
            llm_client,
            limit=args.limit,
            on_progress=on_progress,
            on_skip=on_skip,
        )
    except OllamaConnectionError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)

    reportable_count = sum(1 for r in results if r["reportable"])
    logger.info(
        "Done. %d events processed, %d flagged reportable, %d rows skipped.",
        len(results),
        reportable_count,
        len(skipped),
    )
    logger.info("Results written to %s", args.output)


if __name__ == "__main__":
    main()
