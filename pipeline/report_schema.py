"""Constrained model output mirrors the report validator; source checks remain separate."""
import copy


def obj(properties, required=None):
    return {'type':'object','properties':properties,'required':required or list(properties),'additionalProperties':False}


def array(items): return {'type':'array','items':items}

STRING={'type':'string'}
NULLABLE={'anyOf':[STRING,{'type':'null'}]}
CITATION=obj({'source_key':STRING,'line_ids':array(STRING),'quote':STRING})
SCOPE=obj({'status':{'type':'string','enum':['APPLIES','UNKNOWN','DOES_NOT_APPLY']},
           'condition':NULLABLE,'condition_citations':array(CITATION),'reason':STRING})
LAG=obj({'min':{'type':'integer','minimum':0},'max':{'type':'integer','minimum':0}})
EDGE=obj({'unit_id':STRING,'lag_days':LAG,'citations':array(CITATION)})
GROUP=obj({'mode':{'type':'string','enum':['ALL','ANY']},'selected':NULLABLE,'members':array(EDGE)})
FIELDS={'name':STRING,'actor':STRING,'action':STRING,
        'kind':{'type':'string','enum':['OBLIGATION','MILESTONE','OPTION','EVENT']},
        'citations':array(CITATION),'scope':SCOPE,'requirements':array(GROUP),
        'reconciliation_notes':array(STRING)}
UNIT=obj({'id':STRING,'member_refs':array(STRING),**FIELDS})
CHANGES={'type':'object','properties':FIELDS,'additionalProperties':False}
RULE=obj({'id':STRING,'target':STRING,'anchor':STRING,
          'offset':obj({'unit':{'type':'string','enum':['days','weeks','months']},
                        'min':{'type':'integer'},'max':{'type':'integer'}}),
          'kind':{'type':'string','enum':['CONTRACTUAL','PLANNING_TARGET']},
          'requires_selection':NULLABLE,'citations':array(CITATION),'assumptions':array(STRING)})
PATCH=obj({'unit_updates':array(obj({'id':STRING,'changes':CHANGES})),
           'additional_units':array(UNIT),
           'merges':array(obj({'target_id':STRING,'source_ids':array(STRING),'reason':STRING,'citations':array(CITATION)})),
           'rules':array(RULE),'unresolved_questions':array(STRING)})


def patch_schema(requested_ids=None):
    schema=copy.deepcopy(PATCH)
    if requested_ids is not None:
        updates=schema['properties']['unit_updates']
        # Vertex's schema-state budget can reject a long ID enum multiplied by maxItems.
        # Ownership and membership are enforced deterministically after generation instead.
        del updates['items']['properties']['changes']['properties']['requirements']
        for key in ('additional_units','merges','rules'):
            schema['properties'][key]={'type':'array','items':STRING,'maxItems':0}
    return schema


def batch_schema():
    """Constrain detail responses; IDs, evidence and chronology still require validation."""
    return obj({'units':array(copy.deepcopy(UNIT)),'unresolved_questions':array(STRING)})
