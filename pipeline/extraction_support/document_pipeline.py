"""Compatibility command for the document-boundaries phase."""
from pathlib import Path
import runpy
import sys

if __name__ == '__main__':
    target = Path(__file__).resolve().parents[1] / 'document-boundaries'
    sys.path.insert(0, str(target))
    runpy.run_path(str(target / 'document_pipeline.py'), run_name='__main__')
