"""Evidence-bound price-protection scenarios; never infer actual project events."""
import copy
from customer_report import compile_report
from source_integrity import verify_citations


def derive_planning(payload, proposal, config):
    if not config:
        return None
    if not payload.get('settings', {}).get('contract_signed_verified'):
        raise ValueError('Planning requires a confirmed contract-date baseline')
    if not config.get('boundary_anchor') or not isinstance(config.get('scenarios'), list):
        raise ValueError('Planning requires boundary and scenarios')
    units = {u['id']: u for u in proposal['units']}
    rules = {r['id']: r for r in proposal['rules']}
    rows=[]; boundaries=set(); seen=set()
    for scenario in config['scenarios']:
        sid=scenario.get('id')
        if not sid or sid in seen or not scenario.get('label'):
            raise ValueError('Planning scenarios need unique IDs and labels')
        seen.add(sid)
        selected=scenario.get('selections', [])
        if any(s not in units or units[s]['kind']!='OPTION' for s in selected):
            raise ValueError('Planning branch must reference an option')
        active=[]
        for rid in scenario['rule_ids']:
            r=rules.get(rid)
            if not r or r['kind']!='PLANNING_TARGET' or r['anchor'].startswith(('event:', 'unit:')):
                raise ValueError('Planning cannot consume actual event rules')
            active.append(copy.deepcopy(r))
        p=copy.deepcopy(payload);p['settings']['events']={};p['settings']['selections']=selected
        q=copy.deepcopy(proposal);q['rules']=active
        report=compile_report(p,q)
        if report['status']=='BLOCKED':raise ValueError(str(report['errors']))
        schedules=report['schedules']; conservative=schedules['conservative']; optimistic=schedules['optimistic']
        boundary=conservative['anchors'].get(config['boundary_anchor'])
        if not boundary:raise ValueError('Price boundary could not be derived')
        boundaries.add(boundary)
        for uid,day in conservative['targets'].items():
            if scenario.get('display_units') is not None and uid not in scenario['display_units']:continue
            # Show only selected options; excluded and unresolved scope remain undated.
            if units[uid]['kind']=='OPTION' and uid not in selected:continue
            proof=[];chain=[];current=uid;visited=set()
            while current not in visited:
                visited.add(current)
                propagation=conservative['propagation'].get(current)
                if propagation and propagation['successor']:
                    nxt=propagation['successor'];chain.append({'from':current,'to':nxt,'lag_days':propagation['lag_days']})
                    for group in units[nxt]['requirements']:
                        for edge in group['members']:
                            if edge['unit_id']==current:proof.extend(edge['citations'])
                    current=nxt
                else:
                    ds=conservative['deadlines'].get(current,[])
                    if ds:
                        d=min(ds,key=lambda d:d['date']);proof.extend(d['citations'])
                        chain.append({'rule_id':d['rule_id'],'anchor':d['anchor'],'offset':d['offset']})
                    break
            # Include the boundary's underlying contract rule, not only the local offset.
            for r in active:
                if r['target']==config['boundary_anchor']:proof.extend(r['citations'])
            verify_citations(proof,payload['source_registry'])
            rows.append({'unit_id':uid,'date':day,'shorter_duration_date':optimistic['targets'].get(uid,day),
                'scenario_id':sid,'scenario':scenario['label'],'conditional_branch':bool(selected),
                'kind':'DERIVED_PLANNING_DEADLINE','derivation':chain,'citations':proof})
    if len(boundaries)!=1:raise ValueError('Conflicting price boundaries')
    # Aliases are explicitly reviewed equivalent obligations, not guessed by text similarity.
    for alias in config.get('display_aliases',[]):
        if alias['source'] not in units or alias['target'] not in units:raise ValueError('Unknown planning alias')
        verify_citations(alias['citations'],payload['source_registry'])
        for row in list(rows):
            if row['unit_id']==alias['source'] and row['scenario_id'] in alias['scenario_ids']:
                clone=copy.deepcopy(row);clone['unit_id']=alias['target'];clone['citations']+=alias['citations'];rows.append(clone)
    return {'baseline':payload['contract_signed'],'boundary':boundaries.pop(),
        'basis':payload['settings'].get('contract_signed_basis',''), 'deadlines':rows,
        'method':'Backward planning from the price-protection boundary; conservative durations, no extra buffer. Alternative branches are scenarios, not selections made by the customer.'}
