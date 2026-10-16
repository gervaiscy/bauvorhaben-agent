"""Run each test suite in isolation, including actual PDF rasterization."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
commands = [
    ([sys.executable, '-m', 'unittest', 'discover', '-s', str(root), '-p', 'test*.py', '-v'], None),
    ([sys.executable, '-m', 'unittest', 'discover', '-s', str(root/'working-units/tests'), '-v'], str(root/'working-units/src')),
    ([sys.executable, '-m', 'unittest', 'discover', '-s', str(root/'pdf-extractor'), '-v'], None),
]
failed = False
for command, pythonpath in commands:
    env = dict(os.environ)
    if pythonpath: env['PYTHONPATH'] = pythonpath
    failed |= subprocess.run(command, cwd=root, env=env).returncode != 0
raise SystemExit(1 if failed else 0)
