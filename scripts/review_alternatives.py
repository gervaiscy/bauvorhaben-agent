"""Source-backed alternative proposals for the independent review, never progress tracking."""
from html import escape


def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def array(items):
    return {'type':'array','items':items}


TEXT = {'type':'string'}
NULL_TEXT = {'anyOf':[TEXT,{'type':'null'}]}
EVIDENCE = obj({'source_key':TEXT,'document_id':TEXT,'pdf_page':{'type':'integer'},
                'quote':TEXT,'relation':{'type':'string','enum':['SUPPORTS','CONFLICTS','CONTEXT']}})
ACTION = obj({'id':TEXT,'unit_id':NULL_TEXT,'action':TEXT,'actor':NULL_TEXT,'performed_by':NULL_TEXT,
              'applicability':{'type':'string','enum':['CONDITIONAL']},'condition':TEXT,'evidence':array(EVIDENCE)})
ROUTE = obj({'id':TEXT,'label':TEXT,'operator':{'type':'string','enum':['ALL_OF']},
             'actions':array(ACTION),'timing':obj({'answer':TEXT,'evidence':array(EVIDENCE)}),
             'evidence':array(EVIDENCE)})
GROUP = obj({'id':TEXT,'requirement':TEXT,'target_unit_ids':array(TEXT),
             'operator':{'type':'string','enum':['ANY_OF']},'routes':array(ROUTE),
             'selection':obj({'status':{'type':'string','enum':['NOT_ESTABLISHED','DOCUMENTED']},
                              'route_id':NULL_TEXT,'evidence':array(EVIDENCE)}),
             'evidence':array(EVIDENCE),'uncertainties':array(TEXT)})

INSTRUCTIONS = '''Erfasse zusätzlich alternative_groups für ausdrücklich belegte Wege zur Erfüllung
einer Anforderung: ANY_OF zwischen den Wegen (mindestens ein Weg genügt), ALL_OF für die
zusammengehörigen Schritte innerhalb eines Weges. Erfasse keine Wahlmöglichkeiten aus Werbung
oder bloßen Fähigkeiten. Jede Gruppe muss mindestens eine Zielaufgabe dieses Batches betreffen.
task_catalog enthält nur Kennungen, Namen und Fundstellen zur Zuordnung, keine früheren Bewertungen.
Verknüpfe vorhandene Aufgaben über unit_id aus diesem Katalog, auch wenn sie zu einem anderen Batch
gehören. Fehlt ein konkreter Schritt, beschreibe ihn als bedingten Aktionsvorschlag mit unit_id null.
Ergänze keine unbelegten Schritte. Aktionsvorschläge gelten CONDITIONAL, wenn der jeweilige Weg
gewählt wird; nenne die Verantwortlichen und Ausführenden nur, soweit belegt.
Unterscheide den Verzicht auf eine bisherige Pflicht von einer weiterhin ausdrücklich wählbaren
Alternative. Reaktiviere eine aufgehobene Pflicht nicht als unbedingte Aufgabe.
Bewahre je Weg seine eigene vollständige Zeitregel und Belege; übertrage keine Frist auf andere Wege.
Erhalte zusätzliche Voraussetzungen und Ausnahmen in den Bedingungen der betroffenen Aktionen.
NOT_STATED, wenn für den Weg keine Zeitregel belegt ist; AMBIGUOUS bei ungeklärter Zuordnung.
Belege die Wahlbeziehung, jeden Weg und jeden Aktionsvorschlag mit Quellzitaten.
Eine Auswahl ist nur DOCUMENTED, wenn die Unterlagen sie ausdrücklich belegen; zitiere diese
Entscheidung gesondert. Sonst NOT_ESTABLISHED mit route_id null und leerem Auswahlbeleg.
Leite eine Auswahl nicht aus früheren Anwendbarkeitsbewertungen, der Dokumentanwesenheit oder
dem zuerst genannten Weg ab. Erhalte echte Unsicherheiten und Konflikte.
Ohne belegte Alternativen: alternative_groups: []. Originalaufgaben werden nicht geändert.
'''


def validate(groups, expected_ids, task_catalog, page_text):
    """Called after JSON-schema checks. Only bindings/consistency, never interpretation approval."""
    findings=[]
    known={task['unit_id']:task for task in task_catalog}
    group_ids=set()

    def issue(path, message):
        findings.append(path+': '+message)

    def evidence(items, path, required=True):
        if required and not any(c['relation']=='SUPPORTS' for c in items):
            issue(path,'missing supporting evidence')
        for i,citation in enumerate(items):
            key=(citation['source_key'],citation['document_id'],citation['pdf_page'])
            quote=' '.join(citation['quote'].split())
            if not quote or quote not in ' '.join(page_text.get(key,'').split()):
                issue(f'{path}/{i}','quote or source not found')

    for i,group in enumerate(groups):
        path=f'alternative_groups/{i}'
        if not group['id'].strip() or group['id'] in group_ids:
            issue(path,'empty or duplicate group ID')
        group_ids.add(group['id'])
        targets=group['target_unit_ids']
        if not targets or len(targets)!=len(set(targets)) or not set(targets)<=set(expected_ids):
            issue(path,'missing, duplicate or out-of-batch group targets')
        if not group['requirement'].strip():issue(path,'missing requirement')
        if len(group['routes'])<2:issue(path,'ANY_OF needs at least two routes')
        evidence(group['evidence'],path+'/evidence')
        route_ids=set();action_ids=set()
        for j,route in enumerate(group['routes']):
            route_path=f'{path}/routes/{j}'
            if not route['id'].strip() or route['id'] in route_ids:issue(route_path,'empty or duplicate route ID')
            route_ids.add(route['id'])
            if not route['label'].strip():issue(route_path,'missing route label')
            if not route['actions']:issue(route_path,'ALL_OF needs at least one action')
            evidence(route['evidence'],route_path+'/evidence')
            timing=route['timing']
            if not timing['answer'].strip():issue(route_path+'/timing','missing timing answer')
            evidence(timing['evidence'],route_path+'/timing/evidence',timing['answer'] not in ('NOT_STATED','AMBIGUOUS'))
            unit_ids=set()
            for k,action in enumerate(route['actions']):
                action_path=f'{route_path}/actions/{k}'
                if not action['id'].strip() or action['id'] in action_ids:issue(action_path,'empty or duplicate action ID')
                action_ids.add(action['id'])
                if action['unit_id'] is not None:
                    if action['unit_id'] not in known:issue(action_path,'unknown task reference')
                    elif known[action['unit_id']].get('locator_status','VALID')!='VALID':
                        issue(action_path,'task reference has unresolved source locators')
                    if action['unit_id'] in unit_ids:issue(action_path,'duplicate task within route')
                    unit_ids.add(action['unit_id'])
                if not action['action'].strip() or not action['condition'].strip():issue(action_path,'missing conditional action')
                evidence(action['evidence'],action_path+'/evidence')
        selection=group['selection']
        if selection['status']=='NOT_ESTABLISHED':
            if selection['route_id'] is not None or selection['evidence']:issue(path+'/selection','unknown selection must not choose a route')
        else:
            if selection['route_id'] not in route_ids:issue(path+'/selection','selected route is unknown')
            evidence(selection['evidence'],path+'/selection/evidence')
    return findings


def render_checklist(records):
    """Local, read-only view of separate model proposals. Escape all model-controlled text."""
    def text(value):return escape(str(value))
    def evidence(items):
        return ''.join('<li>'+text(c['source_key'])+' / '+text(c['document_id'])+' / PDF '+
                       text(c['pdf_page'])+': '+text(c['quote'])+'</li>' for c in items)
    parts=['<!doctype html><html lang="de"><meta charset="utf-8"><title>Alternative Wege</title>',
           '<h1>Alternative Wege – ungeprüfte Vorschläge</h1>',
           '<p>Keine Auswahl oder Korrektur wird automatisch übernommen. Keine Fortschrittsanzeige.</p>']
    for record in records:
        parts.append('<section><h2>Batch '+text(record['batch_id'])+'</h2>')
        if record['validation_findings']:
            parts.append('<p>Vorschlag mit offenen Struktur- oder Belegbefunden; siehe alternatives.json.</p><ul>')
            parts.extend('<li>'+text(f)+'</li>' for f in record['validation_findings'])
            parts.append('</ul></section>');continue
        for group in record['alternative_groups']:
            parts.append('<h3>'+text(group['requirement'])+'</h3><p>Mindestens ein Weg genügt (ANY_OF).</p>')
            selection=group['selection']
            parts.append('<p>Auswahl: '+('Nicht aus den Unterlagen festgestellt.' if selection['status']=='NOT_ESTABLISHED'
                else 'Dokumentiert laut Modellvorschlag: '+text(selection['route_id']))+'</p>')
            parts.append('<ul>'+evidence(group['evidence'])+evidence(selection['evidence'])+'</ul>')
            for route in group['routes']:
                parts.append('<h4>'+text(route['label'])+'</h4><p>Bei Wahl dieses Weges gehören alle Schritte zusammen (ALL_OF).</p><ul>')
                for action in route['actions']:
                    parts.append('<li>'+text(action['action'])+' — bedingt: '+text(action['condition'])+
                        '<br>Verantwortlich: '+text(action['actor'] or 'Unbekannt')+
                        '; Ausführung: '+text(action['performed_by'] or 'Unbekannt')+
                        '<br>'+('Aufgabe: '+text(action['unit_id']) if action['unit_id'] is not None
                                 else 'Neue bedingte Aufgabe vorgeschlagen')+
                        '<ul>'+evidence(action['evidence'])+'</ul></li>')
                parts.append('</ul><p>Zeitregel: '+text(route['timing']['answer'])+'</p><ul>'+
                             evidence(route['evidence'])+evidence(route['timing']['evidence'])+'</ul>')
            if group['uncertainties']:
                parts.append('<p>Offen: '+text(' '.join(group['uncertainties']))+'</p>')
        if not record['alternative_groups']:parts.append('<p>Keine alternativen Wege vorgeschlagen.</p>')
        parts.append('</section>')
    parts.append('</html>')
    return '\n'.join(parts)
