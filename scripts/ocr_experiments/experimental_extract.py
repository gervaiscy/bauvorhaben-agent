#!/usr/bin/env python3
"""Legacy OCR experiment entry point; never used by make extract/run."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'pipeline/extraction_support'), str(ROOT/'pipeline/pdf-extractor')]
from legacy_grouping_client import gemini_sdk, cached_group_call, parse_pages


def main():
    from google_pipeline import run
    run()


if __name__ == '__main__':
    main()
