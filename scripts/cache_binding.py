"""Stage-scoped code fingerprints keep unrelated fixes from invalidating PDF work."""
import hashlib
import json

# Bump the affected protocol when orchestration changes a stage's input/output meaning.
PROTOCOL={stage:1 for stage in ('extract','units','reconcile','review','timing','report')}
PROTOCOL['extract']=22
PROTOCOL['units']=5
for stage in ('reconcile','review','timing','report'):PROTOCOL[stage]=2
FILES={
 'extract':['pipeline/pdf-extractor/*.py','pipeline/extraction_support/*.py','scripts/extraction_acceptance.py'],
 'units':['scripts/extraction_acceptance.py','pipeline/working-units/src/**/*.py','pipeline/enrich_working_units.py','pipeline/customer_report.py','pipeline/source_integrity.py'],
 'reconcile':['pipeline/batched_report.py','pipeline/customer_report.py','pipeline/enrich_working_units.py','pipeline/source_integrity.py','pipeline/report_schema.py'],
 'review':['pipeline/document_review.py','pipeline/document_report.py','pipeline/customer_report.py','pipeline/document_planning.py','pipeline/enrich_working_units.py','pipeline/source_integrity.py','pipeline/report_schema.py'],
 'timing':['pipeline/timing_assessment.py','pipeline/price_anchor.py','pipeline/customer_report.py','pipeline/enrich_working_units.py','pipeline/source_integrity.py','pipeline/report_schema.py'],
 'report':['pipeline/document_report.py','pipeline/document_planning.py','pipeline/customer_report.py','pipeline/source_integrity.py'],
}

def dependencies(root,stage):
    return {str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest()
            for pattern in FILES[stage] for path in root.glob(pattern) if path.is_file()}

def stage_binding(root,stage):
    value={'protocol':PROTOCOL[stage],'files':dependencies(root,stage)}
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
