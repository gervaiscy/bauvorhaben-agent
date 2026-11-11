"""One attempt limit across OCR, document boundaries, analysis and subprocesses."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bauvo_working_units.call_budget import BudgetedModel, FileCallBudget, CallLimitExceeded
from bauvo_working_units.call_budget_ledger import CallLedger, ENVIRONMENT_KEY, LedgerLimitExceeded

ROOT = Path(__file__).resolve().parents[3]


def module(name, path):
    spec=importlib.util.spec_from_file_location(name, path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


class SharedBudgetTests(unittest.TestCase):
    def test_ocr_boundary_retry_and_analysis_share_one_limit_before_any_extra_sdk_call(self):
        ocr=module('budget_ocr',ROOT/'pipeline/pdf-extractor/gemini_ocr.py')
        grouping=module('budget_grouping',ROOT/'pipeline/document-boundaries/grouping_client.py')
        class Transient(Exception):
            code=503
        with TemporaryDirectory() as tmp:
            ledger=CallLedger.create(Path(tmp)/'budget.json',2)
            sdk=SimpleNamespace(models=SimpleNamespace(generate_content=Mock(side_effect=Transient())))
            ocr_sdk=SimpleNamespace(models=SimpleNamespace(generate_content=Mock(return_value=SimpleNamespace(model_dump=lambda **kw:{'candidates':[]}))))
            with patch.dict(os.environ,{ENVIRONMENT_KEY:str(ledger.path)}),patch.dict(sys.modules,{'document_grouping':SimpleNamespace(RESPONSE_SCHEMA={})}),patch('google.genai.Client',return_value=sdk) as factory,patch.object(grouping.time,'sleep'):
                ocr.GeminiImageOCR('unused',client=ocr_sdk).process(b'not-real-image')
                with self.assertRaises(LedgerLimitExceeded):
                    grouping.gemini_sdk('unused','global','fixture',[{'text':'Synthetic document'}])
            self.assertEqual(ocr_sdk.models.generate_content.call_count,1)
            self.assertEqual(sdk.models.generate_content.call_count,1)
            self.assertEqual(factory.call_args.kwargs['http_options'].retry_options.attempts,1)
            budget=FileCallBudget(ledger)
            with self.assertRaises(CallLimitExceeded):
                BudgetedModel(lambda:self.fail('Client constructed after shared limit'),budget).extract('PAGE_TIMING.')
            self.assertEqual(budget.used,2)

    def test_child_processes_observe_the_parent_attempt_count(self):
        with TemporaryDirectory() as tmp:
            ledger=CallLedger.create(Path(tmp)/'budget.json',2)
            budget=FileCallBudget(ledger);budget.consume()
            env={**os.environ,ENVIRONMENT_KEY:str(ledger.path),'PYTHONPATH':str(ROOT/'pipeline/working-units/src')}
            script='from bauvo_working_units.call_budget_ledger import consume_from_environment; consume_from_environment("synthetic-child")'
            subprocess.run([sys.executable,'-c',script],env=env,check=True,capture_output=True)
            blocked=subprocess.run([sys.executable,'-c',script],env=env,capture_output=True)
            self.assertNotEqual(blocked.returncode,0)
            self.assertIn(b'Provider call limit reached',blocked.stderr)
            self.assertEqual(budget.used,2)

    def test_invalid_ledger_is_rejected_and_existing_ledgers_cannot_be_reinitialized(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'budget.json';ledger=CallLedger.create(path,1)
            with self.assertRaises(FileExistsError):CallLedger.create(path,20)
            path.write_text(json.dumps({'limit':True,'used':0}))
            with self.assertRaisesRegex(ValueError,'Invalid provider call ledger'):ledger.access(consume=True)
