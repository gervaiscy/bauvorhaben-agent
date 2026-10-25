"""Offline tests for production transcription and isolated OCR experiments."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'pipeline/pdf-extractor'), str(ROOT/'pipeline/document-boundaries'),
               str(ROOT/'scripts/ocr_experiments'), str(ROOT/'pipeline/working-units/src'), str(Path(__file__).parent)]
