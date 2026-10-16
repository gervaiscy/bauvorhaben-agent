"""Document-only MVP view. No progress events, completion state or overdue status.

The review packet is evidence-bound input, not a semantic truth oracle. Publication
requires an external review of the rendered artifact; this renderer always emits a draft.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import html
import json
from pathlib import Path
from customer_report import compile_report, fingerprint
from source_integrity import verify_citations
from document_planning import derive_planning

CATEGORIES = {'CONTRACT_SCOPE', 'CONDITIONAL', 'OPTION', 'EXCLUDED', 'DOCUMENT_GAP', 'DOCUMENT_RECORD'}
CONSEQUENCE_KINDS = {'CONTRACTUAL', 'PLANNING_IMPLICATION'}
LABELS = {'CONTRACT_SCOPE':'Vereinbarter Leistungsumfang', 'CONDITIONAL':'Bedingte Regelung',
           'OPTION':'Wahlmöglichkeit', 'EXCLUDED':'Nicht im Auftrag enthalten / ersetzt',
           'DOCUMENT_GAP':'Unterlagenabhängige Abgrenzung', 'DOCUMENT_RECORD':'Dokumentvermerk'}

def build_document_report(payload, proposal, review, timing_result=None):
    if payload.get('input_fingerprint') != fingerprint({k:v for k,v in payload.items() if k != 'input_fingerprint'}):
        raise ValueError('Input snapshot was modified after fingerprinting')
    if review.get('input_fingerprint') != payload['input_fingerprint'] or review.get('proposal_fingerprint') != fingerprint(proposal):
        raise ValueError('Document review belongs to another snapshot')
    if not review.get('reviewer') or not review.get('review_basis'):
        raise ValueError('Attributed document review required')
    # Reuse source/membership/graph validation, but never consume its live schedules,
    # questions or event state. Unknown time anchors are not customer questions.
    validation_payload = copy.deepcopy(payload)
    validation_payload.setdefault('settings', {})['events'] = {}
    validated = compile_report(validation_payload, proposal)
    if validated.get('status') == 'BLOCKED':
        raise ValueError(str(validated['errors']))
    registry = payload['source_registry']
    def sourced(item):
        if not isinstance(item, dict) or not isinstance(item.get('text'), str) or not item['text'].strip():
            raise ValueError('Sourced statement needs nonempty text')
        verify_citations(item.get('citations'), registry)
        item = copy.deepcopy(item)
        for c in item['citations']:
            c['pdf_pages'] = sorted({registry[c['source_key']][i]['pdf_page'] for i in c['line_ids']})
        return item
    annotations = review.get('units', [])
    by_id = {a.get('id'): a for a in annotations}
    ids = {u['id'] for u in proposal['units']}
    if len(by_id) != len(annotations) or set(by_id) != ids:
        raise ValueError('Every unit needs exactly one document-review record')
    units = []
    for u in proposal['units']:
        a = by_id[u['id']]
        if a.get('unit_fingerprint') != fingerprint(u):
            raise ValueError('Stale unit review: ' + u['id'])
        if a.get('category') not in CATEGORIES or not a.get('reason'):
            raise ValueError('Missing category/review reason: ' + u['id'])
        if u['scope']['status'] == 'DOES_NOT_APPLY' and a['category'] != 'EXCLUDED':
            raise ValueError('Excluded claim cannot become an active task')
        if u['kind'] == 'OPTION' and a['category'] not in {'OPTION', 'DOCUMENT_GAP', 'EXCLUDED'}:
            raise ValueError('Unselected option cannot become an obligation')
        if u['scope']['status'] == 'UNKNOWN' and a['category'] == 'CONTRACT_SCOPE':
            raise ValueError('Unresolved scope cannot become unconditional')
        for key in ('responsibility', 'performance'):
            if not a.get(key): raise ValueError('Missing role: ' + key)
        timing = [sourced(x) for x in a.get('timing', [])]
        consequences = []
        for c in a.get('consequences', []):
            if c.get('kind') not in CONSEQUENCE_KINDS:
                raise ValueError('Consequence must distinguish contract from inference')
            consequences.append(sourced(c))
        qualification = [sourced(x) for x in a.get('qualifications', [])]
        evidence = sourced({'text': u['action'], 'citations': u['citations'] + u['scope'].get('condition_citations', [])})
        units.append({'id': u['id'], 'name': u['name'], 'action': u['action'],
            'responsibility': sourced(a['responsibility']), 'performance': sourced(a['performance']),
            'category': a['category'], 'condition': u['scope'].get('condition'),
            'timing': timing, 'consequences': consequences, 'qualifications': qualification,
            'evidence': evidence['citations'], 'review_reason': a['reason'],
            'member_refs': copy.deepcopy(u['member_refs'])})
    result = {'profile': 'DOCUMENT_ONLY', 'schema_version': 1, 'status': 'REVIEW_DRAFT',
        'input_fingerprint': payload['input_fingerprint'], 'proposal_fingerprint': fingerprint(proposal),
        'reviewer': review['reviewer'], 'review_basis': review['review_basis'],
        'units': units, 'overview': [sourced(x) for x in review.get('overview', [])],
        'document_gaps': [sourced(x) for x in review.get('document_gaps', [])],
        'source_inventory': copy.deepcopy(review.get('source_inventory', [])),
        'coverage_limitations': copy.deepcopy(review.get('coverage_limitations', []))}
    result['planning'] = derive_planning(payload, proposal, review.get('planning'))
    if result['planning']:
        for row in result['planning']['deadlines']:
            row['citations'] = sourced({'text':'Berechnungsgrundlage','citations':row['citations']})['citations']
    if timing_result is not None:
        if (timing_result.get('digest') != fingerprint({k:v for k,v in timing_result.items() if k!='digest'})
            or timing_result.get('input_fingerprint') != payload['input_fingerprint']
            or timing_result.get('proposal_fingerprint') != fingerprint(proposal)):
            raise ValueError('Timing assessment snapshot mismatch')
        assessed=[x['unit_id'] for x in timing_result['units']]
        if len(assessed)!=len(set(assessed)) or set(assessed)!=ids:
            raise ValueError('Timing assessment must cover every unit exactly once')
        result['timing_assessment']=copy.deepcopy(timing_result)
        result['planning']=None  # The complete assessment replaces the old selected-unit projection.
        for assessment in result['timing_assessment']['units']:
            assessment['citations']=sourced({'text':'Zeitliche Einordnung','citations':assessment['citations']})['citations']
            for candidate in assessment['candidates']:
                for step in candidate['chain']:
                    step['citations']=sourced({'text':'Rechenweg','citations':step['citations']})['citations']
    result['digest'] = fingerprint(result)
    return result


def render_document_report(report):
    if report.get('profile') != 'DOCUMENT_ONLY' or report.get('digest') != fingerprint({k:v for k,v in report.items() if k != 'digest'}):
        raise ValueError('Invalid or modified document report')
    esc = lambda x: html.escape(str(x), quote=True)
    def evidence(citations):
        parts=[]
        for c in citations:
            name={'contract':'Bauvertrag / Anlagen','blb':'BLB'}.get(c['source_key'],c['source_key'])
            parts.append('<p><b>'+esc(name)+' · PDF-Seite '+esc(', '.join(map(str,c['pdf_pages'])))+'</b><br>'+esc(c['quote'])+'</p>')
        return '<details><summary>Belege</summary>'+''.join(parts)+'</details>'
    def statements(items):
        return ''.join('<p>'+('<b>Planungsfolge (abgeleitet): </b>' if x.get('kind')=='PLANNING_IMPLICATION' else '<b>Vertragliche Folge: </b>' if x.get('kind')=='CONTRACTUAL' else '')+esc(x['text'])+'</p>'+evidence(x['citations']) for x in items)
    out=['<!doctype html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Bauvo – Vertragliche Aufgaben und Fristen</title>',
      '<style>body{font:16px/1.55 system-ui,sans-serif;color:#20362f;max-width:1120px;margin:32px auto;padding:0 20px}h1{font-size:2rem}h2{margin-top:2em}.banner{background:#fff1ce;padding:14px;border-left:4px solid #b57718}.overview{border-left:3px solid #31846e;padding:4px 18px;margin:18px 0}.unit{border:1px solid #ccd9d2;border-radius:8px;padding:18px;margin:18px 0;overflow-wrap:anywhere}.unit h3{margin:8px 0}.roles{display:grid;grid-template-columns:1fr 1fr;gap:18px}.badge{font-size:.85rem;font-weight:600;color:#386650}details{font-size:.9rem;color:#475950;margin:8px 0}input,select{font:inherit;padding:9px;margin:5px 8px 5px 0;max-width:100%}.tools{position:sticky;top:0;background:#fff;padding:12px 0;border-bottom:1px solid #ccd9d2}small{color:#58675f}[hidden]{display:none!important}@media(max-width:640px){.roles{grid-template-columns:1fr}.tools{position:static}}@media print{.tools{display:none}.unit{break-inside:avoid}body{font-size:10pt;margin:0}.overview{break-inside:avoid}}</style></head><body>',
      '<h1>Wer macht was – und bis wann?</h1><p class="banner">Prüffassung · Auswertung von Bauvertrag, BLB und vorliegenden Anlagen</p>',
      '<p>Dieser Bericht beschreibt die dokumentierten Aufgaben, Zuständigkeiten, Fristen und Folgen. Er bewertet keinen tatsächlichen Projektfortschritt. Vertragliche Fristen bleiben erhalten; ableitbare Kalenderdaten ergänzen sie als Planungsziele zur Preisbindung.</p>',
      '<h2>Die wichtigsten Regeln</h2>']
    planning=report.get('planning')
    assessment=report.get('timing_assessment')
    assessed={x['unit_id']:x for x in assessment['units']} if assessment else {}
    names={u['id']:u['name'] for u in report['units']}
    def day(value):
        from datetime import date
        return date.fromisoformat(value).strftime('%d.%m.%Y')
    if planning:
        out.insert(-1,'<section class="overview"><h2>Planungsfristen zur Preisbindung</h2><p>Vertragsbaseline: <b>'+day(planning['baseline'])+'</b> → Preisbindungsgrenze / spätestes Startziel im Szenario: <b>'+day(planning['boundary'])+'</b>.</p><p>Die folgenden Termine werden rückwärts von diesem Startziel berechnet. Sie sind abgeleitete Planungsfristen, keine bestätigten Bautermine. Das Szenario reserviert die längeren belegten Vorlaufzeiten, enthält aber keinen zusätzlichen Sicherheitspuffer. Ein früherer geplanter Baubeginn zieht die Vorbereitungstermine entsprechend vor.</p><p>Die Preisbindung hängt von den vertraglichen Voraussetzungen ab; das Erreichen eines einzelnen Planungsziels garantiert sie nicht. Vergangene Daten werden nicht automatisch als versäumt bewertet.</p></section>')
    if assessment:
        from collections import Counter
        counts=Counter(x['classification'] for x in assessment['units'])
        coverage=assessment['coverage']
        if 'model_responses' in coverage:
            out.insert(-1,'<p class="banner">Unvollständiger Modelllauf: '+str(coverage['model_responses'])+' vollständige Modellantworten; '+str(coverage['missing_model_responses'])+' fehlen. '+str(coverage['unresolved'])+' zeitliche Einordnungen bleiben offen. Alle Positionen sind aufgeführt; offene Prüfungen bedeuten nicht, dass die Unterlagen keine Frist enthalten.</p>')
        out.insert(-1,'<section class="overview"><h2>Terminprüfung aller Arbeitspositionen</h2><p>'+str(len(assessed))+(' Positionen erfasst: ' if coverage.get('unresolved') or coverage.get('missing_model_responses') else ' Positionen geprüft: ')+str(counts['DERIVABLE'])+' ableitbar · '+str(counts['CONDITIONAL'])+' bedingt ableitbar · '+str(counts['NOT_DERIVABLE_FROM_SIGNING'])+' nicht aus Unterzeichnung datierbar.</p><p>Baseline '+day(assessment['baseline'])+' → Preisbindungsgrenze / Startziel '+(day(assessment['price_boundary']) if assessment['price_boundary'] else 'nicht aus den Unterlagen bestimmbar')+'. Berechnete Termine sind Planungsziele ohne zusätzlichen Sicherheitspuffer, keine Ist-Termine. Reine Reihenfolgen ergeben nur eine Obergrenze „spätestens bis“; sie enthalten keine behauptete Vorbereitungsdauer.</p><p>Gemini hat die Zeitbeziehungen vorgeschlagen; Python hat Quellenzuordnung, Graph und Datumsberechnung geprüft. Die semantische Freigabe bleibt erforderlich.</p></section>')
        out.insert(-1,'<section class="overview"><p>Die Preisbindung hängt von sämtlichen vertraglichen Voraussetzungen ab; das Erreichen einer einzelnen Planungsfrist garantiert sie nicht. Vergangene Planungsdaten werden nicht als versäumt bewertet. Ein früheres Startziel zieht die Vorbereitungstermine entsprechend vor.</p></section>')
    for item in report['overview']: out.append('<section class="overview">'+statements([item])+'</section>')
    out.append('<h2>Abgrenzungen in den Unterlagen</h2>')
    out.append(statements(report['document_gaps']) or '<p>Keine zusätzlichen Unterlagenlücken im Review vermerkt.</p>')
    out.append('<h2>Arbeits- und Leistungsregister</h2><p>„Verantwortlich“ benennt die vertragliche Zuständigkeit. Ein beauftragter Vermesser, Versorger oder Nachunternehmer kann die eigentliche Ausführung übernehmen. Wo keine eigene Frist oder Sanktion genannt ist, wird keine erfunden.</p><div class="tools"><label>Suche <input id="search" type="search" placeholder="z. B. Elektroplan oder Bauherr"></label><label>Anzeige <select id="filter"><option value="all">Alle Arbeitspositionen</option>')
    for k,v in LABELS.items():
        if k != 'DOCUMENT_RECORD': out.append('<option value="'+k+'">'+esc(v)+'</option>')
    out.append('</select></label><span id="count" aria-live="polite"></span></div>')
    for u in report['units']:
        if u['category']=='DOCUMENT_RECORD': continue
        out.append('<article class="unit" data-category="'+u['category']+'" id="'+esc(u['id'])+'"><span class="badge">'+esc(LABELS[u['category']])+'</span><h3>'+esc(u['name'])+'</h3><p>'+esc(u['action'])+'</p>')
        if u['condition']:out.append('<p><b>Bedingung:</b> '+esc(u['condition'])+'</p>')
        out.append('<div class="roles"><div><b>Verantwortlich</b><p>'+esc(u['responsibility']['text'])+'</p></div><div><b>Ausführung / Mitwirkung</b><p>'+esc(u['performance']['text'])+'</p></div></div>')
        out.append('<p><b>Wann?</b></p>'+ (statements(u['timing']) or '<p>Keine eigenständige Frist in den zugeordneten Klauseln ausgewiesen.</p>'))
        if u['id'] in assessed:
            a=assessed[u['id']]
            label={'DERIVABLE':'Aus Vertragsdatum ableitbar','CONDITIONAL':'Bedingt ableitbar – Alternativen beachten','NOT_DERIVABLE_FROM_SIGNING':'Nicht aus Vertragsdatum datierbar'}[a['classification']]
            if a.get('review_required'):label='Terminprüfung offen – kein Datum freigegeben'
            out.append('<section class="overview"><b>'+label+'</b><p>'+esc(a['relative_text'])+'</p>')
            for candidate in a['candidates']:
                forward_range=candidate['date']!=candidate['later_date'] and any(step['relation']=='AFTER' for step in candidate['chain'])
                if forward_range:
                    out.append('<p><b>Abgeleitete Zeitspanne: '+day(candidate['date'])+' bis '+day(candidate['later_date'])+'</b></p><p>Die extrahierte Regel enthält einen Bereich; daraus wird keine einzelne feste Frist behauptet.</p>')
                else:out.append('<p><b>'+('Spätestens bis zum Planungsziel: ' if candidate['sequence_only'] else 'Abgeleitete Planungsfrist: ')+day(candidate['date'])+'</b></p>')
                if candidate['sequence_only']:out.append('<p>Reihenfolge-Obergrenze: keine gesonderte Vorbereitungsdauer in dieser Beziehung angegeben.</p>')
                if candidate['later_date']!=candidate['date'] and not forward_range:out.append('<p>Mit kürzerem dokumentiertem Vorlauf: '+day(candidate['later_date'])+'. Für die Vorbereitung gilt das frühere Szenario.</p>')
                if candidate['conditions']:out.append('<p><b>Nur unter diesen Bedingungen:</b> '+esc(' · '.join(candidate['conditions']))+'</p>')
                out.append('<details><summary>Rechenweg und Quellen</summary>')
                for step in candidate['chain']:
                    offset=step['offset']
                    wording=('spätestens bis (keine Dauer angegeben)' if offset is None else
                        str(offset['min'])+('–'+str(offset['max']) if offset['max']!=offset['min'] else '')+' '+{'days':'Tage','weeks':'Wochen','months':'Monate'}[offset['unit']]+' '+('vor' if step['relation']=='BEFORE' else 'nach'))
                    anchor=step['anchor'];anchor_label=names.get(anchor[5:],anchor) if anchor.startswith('unit:') else {'planned_start':'Startziel an der Preisbindungsgrenze','price_boundary':'Preisbindungsgrenze','contract_signed':'Vertragsbaseline'}.get(anchor,anchor)
                    out.append('<p>'+esc(names.get(step['unit_id'],step['unit_id'])+': '+wording+' '+anchor_label)+'</p>'+evidence(step['citations']))
                    out.append('<p>'+esc(step['explanation'])+'</p>')
                out.append('</details>')
            if not a['candidates']:out.append('<p>'+esc(a['no_date_reason'])+'</p>')
            for missing in a['missing_anchors']:
                anchor=missing['anchor']
                label=('Arbeitsposition: '+names.get(anchor[5:],anchor[5:]) if anchor.startswith('unit:') else anchor.replace('event:','Ereignis: ',1))
                out.append('<p><b>Offener zeitlicher Bezug:</b> '+esc(label)+' – '+esc(missing['reason'])+'</p>')
            out.append('<details><summary>Begründung der Einordnung</summary><p>'+esc(a['reason'])+'</p>'+evidence(a['citations'])+'</details></section>')
        if planning:
            for row in planning['deadlines']:
                if row['unit_id']!=u['id']:continue
                out.append('<section class="overview"><b>Abgeleitete Planungsfrist: '+day(row['date'])+'</b><p>'+esc(row['scenario'])+'</p>')
                if row['shorter_duration_date']!=row['date']:
                    out.append('<p>Bei kürzerem belegtem Vorlauf: '+day(row['shorter_duration_date'])+'. Für die Vorbereitung empfehlen wir das frühere Planungsziel.</p>')
                steps=[]
                for step in row['derivation']:
                    if 'offset' in step:
                        offset=step['offset'];units_label={'days':'Tage','weeks':'Wochen','months':'Monate'}
                        steps.append(str(offset['min'])+' '+units_label[offset['unit']]+' relativ zum Planungsanker')
                    else:
                        steps.append(str(step['lag_days']['max'])+' Tage vor „'+names.get(step['to'],step['to'])+'“')
                out.append('<details><summary>Rechenweg</summary><p>'+esc(' → '.join(steps))+'</p></details>')
                out.append('<p>Rückwärts gerechnet vom Startziel '+day(planning['boundary'])+'; '+('gilt nur für diese Alternative.' if row['conditional_branch'] else 'kein zusätzlicher Puffer.')+'</p>'+evidence(row['citations'])+'</section>')
        out.append('<p><b>Mögliche Folgen</b></p>'+(statements(u['consequences']) or '<p>Keine gesonderte Folge in den zugeordneten Klauseln benannt. Übergreifende Regeln stehen oben.</p>'))
        out.append(statements(u['qualifications'])+evidence(u['evidence'])+'</article>')
    out.append('<h2>Prüfstand</h2><p>Quellenprüfung und technische Validierung durch '+esc(report['reviewer'])+'. Keine unabhängige fachliche Freigabe.</p><ul>')
    out.extend('<li>'+esc(x)+'</li>' for x in report['coverage_limitations'])
    out.append('</ul><script>const cards=[...document.querySelectorAll(".unit")];function filter(){const q=document.getElementById("search").value.toLocaleLowerCase("de");const c=document.getElementById("filter").value;let n=0;for(const x of cards){x.hidden=!(c==="all"||x.dataset.category===c)||!x.textContent.toLocaleLowerCase("de").includes(q);if(!x.hidden)n++;}document.getElementById("count").textContent=n+" Positionen";}document.getElementById("search").addEventListener("input",filter);document.getElementById("filter").addEventListener("change",filter);filter();</script></body></html>')
    return '\n'.join(out)


def publication_check(report, html_bytes, approval=None):
    """Approval is an externally supplied audit record, not authentication or expertise proof."""
    reasons=[]
    timing=report.get('timing_assessment') or {}
    if timing.get('coverage',{}).get('missing_model_responses'):reasons.append('TIMING_MODEL_COVERAGE_INCOMPLETE')
    if any(x.get('review_required') for x in timing.get('units',[])):reasons.append('UNRESOLVED_TIMING_REVIEW')
    if report.get('digest') != fingerprint({k:v for k,v in report.items() if k!='digest'}): reasons.append('REPORT_CHANGED')
    expected=render_document_report(report).encode('utf-8')
    if html_bytes != expected: reasons.append('ARTIFACT_CHANGED')
    sha=hashlib.sha256(html_bytes).hexdigest()
    if not isinstance(approval,dict): reasons.append('INDEPENDENT_REVIEW_REQUIRED')
    else:
        if approval.get('report_digest') != report['digest'] or approval.get('artifact_sha256') != sha: reasons.append('STALE_APPROVAL')
        for key in ('reviewed_by','reviewed_at','qualification','decision_reason'):
            if not approval.get(key): reasons.append('INCOMPLETE_APPROVAL:'+key)
        if approval.get('reviewed_by') == report.get('reviewer'): reasons.append('INDEPENDENT_REVIEW_REQUIRED')
        if approval.get('approved') is not True: reasons.append('NOT_APPROVED')
        if approval.get('visual_qa_passed') is not True: reasons.append('VISUAL_QA_REQUIRED')
    return {'publishable':not reasons,'reasons':reasons,'report_digest':report['digest'],'artifact_sha256':sha,
            'note':'Local audit gate; authentication and reviewer authorization belong to the deployment boundary.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',required=True,type=Path);p.add_argument('--proposal',required=True,type=Path)
    p.add_argument('--review',required=True,type=Path);p.add_argument('--output-dir',required=True,type=Path)
    p.add_argument('--timing-result',type=Path)
    a=p.parse_args()
    if a.output_dir.exists():p.error('Use a new output directory')
    r=build_document_report(json.loads(a.input.read_text()),json.loads(a.proposal.read_text()),json.loads(a.review.read_text()),json.loads(a.timing_result.read_text()) if a.timing_result else None)
    rendered=render_document_report(r).encode('utf-8');a.output_dir.mkdir(parents=True)
    for name,value in [('document-report.json',r),('publication-status.json',publication_check(r,rendered))]:
        (a.output_dir/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    (a.output_dir/'customer-report.html').write_bytes(rendered)
    print('Document-only draft:',len(r['units']),'units; no live project state consumed')

if __name__=='__main__':main()
