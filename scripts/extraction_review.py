"""Archive boundary proposals and downstream evidence before recomputation."""
import shutil
import uuid

def prepare_regroup(run, source, read, write_json, sha):
    """Invalidate only derived grouping and dependent unit projection, preserving evidence."""
    if (run/'reconciliation/run-state.json').exists():
        raise ValueError('Reconciliation already started; use a fresh run for source changes')
    folder=run/'extraction'/source
    if not (folder/'pages.json').exists():raise ValueError('No page transcription to regroup')
    if read(folder/'pages.json').get('schema_version') not in (5,6):raise ValueError('Legacy extraction needs a fresh run')
    audit=run/'regrouping-previous'/uuid.uuid4().hex[:12];audit.mkdir(parents=True)
    preserved={}
    for name in ('documents.json','quality-report.json','source-review.html','complete.json'):
        path=folder/name
        if path.exists():shutil.copy2(path,audit/name);preserved[name]=sha(path)
    for stage in ('extract','units'):
        path=run/f'{stage}.done.json'
        if path.exists():shutil.copy2(path,audit/path.name);path.unlink()
    unit_output=run/'working-units'/source/'working-units.json'
    if unit_output.exists():shutil.move(unit_output,audit/'working-units-before-regroup.json')
    approval=run/'extraction-approvals'/(source+'.json')
    if approval.exists():shutil.move(approval,audit/'source-approval-before-regroup.json')
    (folder/'complete.json').unlink(missing_ok=True)
    write_json(audit/'audit.json',{'source':source,'before':preserved,'action':'Recompute document boundaries from retained PDF page transcription; no source text or prior evidence deleted.'})
    return audit
