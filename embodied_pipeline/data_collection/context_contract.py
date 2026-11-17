"""Strict public-input projection for reviewed natural-language collections.

Two profiles exist. ``VERSION`` is the original one and is unchanged: it validates
the public context against the node set the worker was given, which for a complete
graph contains the user-location anchor.

``CROPPED_VERSION`` is an opt-in profile for a task whose graph was cropped to a
route before the worker saw it. There the anchor object may legitimately be outside
the supplied subgraph, and three things must not happen in that case: the authored
dialogue must not be dropped, the user location must not be rewritten to whatever
room the crop kept, and no robot location may be invented. So the anchor and its
room annotation are checked once, at filter prepare time, against the complete
source graph, and that check is carried as an attestation which this module
re-verifies. When the anchor is still present in the supplied nodes the original
node-level checks run as well; when it is absent that fact is recorded and the
context is forwarded unchanged.

Re-verification is not the same as reading a label. An attestation says which
complete graph it was computed against, and that claim is only worth something if
it is tied to the graph this run actually filtered. So the cropped profile also
requires the attested ``full_graph_sha256`` to be a well-formed digest and to equal
both the digest in the task's ``topology_task_context`` and the digest in the
filtering provenance the caller has already validated, and it requires
``verified_at_stage`` to be the one stage that is allowed to produce the
attestation. A forged stage label, a digest from another graph, a missing or blank
digest, or an attestation that cannot be cross-checked against anything is refused
rather than forwarded.
"""
import copy
import json
from .catalog import _rooms

VERSION='reviewed_user_context_v1'
CROPPED_VERSION='reviewed_user_context_cropped_scope_v1'
PROFILES=(VERSION,CROPPED_VERSION)
ATTESTATION_KEY='public_context_source_graph_attestation'
ATTESTATION_KEYS={'verified_at_stage','verified_against','full_graph_sha256','anchor_object_id','anchor_room_name','anchor_active','room_annotation'}
#: The only stage allowed to produce the attestation, and the only scope it may
#: claim. They are constants here so that a task cannot invent a stage that sounds
#: authoritative, and so that the value written by
#: :func:`source_graph_attestation` and the value demanded by
#: :func:`public_context_cropped` cannot drift apart.
ATTESTATION_STAGE='topology_filter.prepare'
ATTESTATION_SCOPE='complete_source_graph_before_cropping'
_HEX_DIGITS=frozenset('0123456789abcdef')
CONTEXT_KEYS={'source','user_location','user_activity','dialogue_history'}
SPATIAL_KEYS={'location_source','user_exact_position','user_heading','robot_location','room_status','near_semantics','deixis_policy','history_scope'}
BOUNDARY=(
 ' The user location is an explicitly authored scenario assumption, not perceived human pose. '
 'User and robot are different entities. A user near a landmark is not at its bbox centre; '
 'do not infer exact coordinates, orientation, robot location, ownership, pointing, free surface '
 'space, room adjacency or handover capability. Use supplied user location for here/me, but '
 'preserve explicit referents from supplied dialogue history for there/that item. '
 'History is user speech, not executed robot actions. Preserve unresolved referents and '
 'uncertainty instead of inventing targets or facts; existing output and binding protocols apply.')
CROPPED_BOUNDARY=(
 ' The object the user is standing near may be outside the supplied subgraph, because the graph '
 'was cropped to a route. That is not evidence that the object or the room is absent, it does '
 'not move the speaker, and it does not tell you where the robot is. The robot location remains '
 'unknown and unmeasured. Do not replace the stated user room with a room that happens to be in '
 'the supplied subgraph, and do not treat the user position as a robot position.')

def _shape_head(task):
    """Field-shape checks that do not depend on the supplied node set."""
    mi=task.get('model_input')
    if not isinstance(mi,dict) or mi.get('instruction')!=task.get('instruction'):
        raise ValueError('Reviewed model input instruction is missing or differs')
    context=mi.get('conversation_context');spatial=mi.get('public_spatial_context')
    if not isinstance(context,dict) or set(context)-CONTEXT_KEYS or not {'source','user_location','user_activity'}<=set(context):
        raise ValueError('Unexpected public conversation fields')
    if context.get('source')!='scenario_assumption_not_observation':
        raise ValueError('Speaker location must be marked as authored, not observed')
    if task.get('conversation_context')!=context or task.get('public_spatial_context')!=spatial:
        raise ValueError('Public context copies differ')
    location=context['user_location']
    if not isinstance(location,dict) or set(location)!={'room_name','near_scene_element_id'}:
        raise ValueError('Invalid user location fields')
    return mi,context,spatial,location

def _anchor_checks(task,nodes,location):
    """Node-level checks: the anchor exists, is active and matches its annotation."""
    by={n['id']:n for n in nodes};anchor=location['near_scene_element_id']
    if type(anchor) is not int or anchor not in by or by[anchor].get('active',True) is False:
        raise ValueError('User location anchor absent or inactive')
    rooms,provenance=_rooms(task['scene_id'],by)
    if location['room_name'] is not None and rooms.get(anchor)!=location['room_name']:
        raise ValueError('User room has no existing annotation for this anchor')
    if task['scene_id']=='d3_619' and location['room_name'] is not None:
        raise ValueError('D3-619 room membership is unknown')
    return anchor,rooms,provenance

def _shape_tail(task,mi,context,spatial):
    """Field-shape checks that follow the node-level checks."""
    if not isinstance(context['user_activity'],str) or not context['user_activity'].strip():
        raise ValueError('User activity must be nonempty text')
    history=context.get('dialogue_history',[])
    if not isinstance(history,list):raise ValueError('History must be a list')
    for turn in history:
        if not isinstance(turn,dict) or set(turn)!={'speaker','text'} or turn['speaker']!='user' or not isinstance(turn['text'],str) or not turn['text'].strip():
            raise ValueError('Invalid user history')
    messages=mi.get('context_messages',[])
    if not isinstance(messages,list) or any(not isinstance(m,dict) or set(m)!={'role','content'} or m['role']!='user' for m in messages):
        raise ValueError('Invalid original context messages')
    if history!=[{'speaker':m['role'],'text':m['content']} for m in messages]:
        raise ValueError('Dialogue projection changed or lost history')
    if not isinstance(spatial,dict) or set(spatial)!=SPATIAL_KEYS:
        raise ValueError('Unexpected public spatial fields')
    if spatial['user_exact_position'] is not None or spatial['user_heading'] is not None or spatial['robot_location']!={'status':'unknown','same_as_user':None}:
        raise ValueError('Unknown physical poses were invented')
    for key in SPATIAL_KEYS-{'user_exact_position','user_heading','robot_location'}:
        if not isinstance(spatial[key],str) or not spatial[key].strip():raise ValueError('Invalid spatial text field')
    return copy.deepcopy(context),copy.deepcopy(spatial)

def public_context(task,nodes):
    """Reject unexpected fields rather than silently leaking reviewer metadata."""
    mi,context,spatial,location=_shape_head(task)
    _anchor_checks(task,nodes,location)
    return _shape_tail(task,mi,context,spatial)

def source_graph_attestation(task,nodes,full_graph_sha256):
    """Run the node-level checks once against a complete graph and record the result.

    Called by the offline filter at prepare time, where the complete source graph is
    still available. It verifies, it does not assert: a failure raises instead of
    being written down as satisfied.
    """
    full_graph_sha256=_digest(full_graph_sha256,'attested complete-graph digest')
    mi,context,spatial,location=_shape_head(task)
    anchor,rooms,provenance=_anchor_checks(task,nodes,location)
    _shape_tail(task,mi,context,spatial)
    return {'verified_at_stage':ATTESTATION_STAGE,
            'verified_against':ATTESTATION_SCOPE,
            'full_graph_sha256':full_graph_sha256,
            'anchor_object_id':anchor,
            'anchor_room_name':rooms.get(anchor),
            # _anchor_checks above raises unless the anchor exists and is active.
            'anchor_active':True,
            'room_annotation':copy.deepcopy(provenance)}

def _digest(value,label):
    """A sha256 hex digest, or raise. Blank, short and non-hex values are refused."""
    if not isinstance(value,str) or len(value)!=64 or not set(value)<=_HEX_DIGITS:
        raise ValueError('The '+label+' must be a 64 character lowercase sha256 digest')
    return value

def _cross_check_digest(attested,task,verified_provenance):
    """Tie the attested digest to the graph this run actually filtered.

    Returns ``(checked_against, matches_task_context, matches_verified_provenance)``.
    The attestation's own wording is never accepted as evidence on its own: at least
    one independent digest must be available here and every available digest must be
    the same one.
    """
    checked=[]
    context=task.get('topology_task_context')
    matches_context=False
    if isinstance(context,dict):
        declared=_digest(context.get('full_graph_sha256'),
                         'topology_task_context.full_graph_sha256')
        if declared!=attested:
            raise ValueError('The public context attestation was computed against another '
                             'complete graph than the one the task context declares')
        matches_context=True
        checked.append('task.topology_task_context.full_graph_sha256')
    matches_provenance=False
    if verified_provenance is not None:
        if not isinstance(verified_provenance,dict):
            raise ValueError('Verified filtering provenance must be an object')
        verified=_digest(verified_provenance.get('full_graph_sha256'),
                         'verified filtering provenance full_graph_sha256')
        if verified!=attested:
            raise ValueError('The public context attestation was computed against another '
                             'complete graph than the one this run filtered')
        matches_provenance=True
        checked.append('verified_input_filtering_provenance.full_graph_sha256')
    if not checked:
        raise ValueError('The cropped-scope public context profile needs a verified digest to '
                         'compare the attestation with; a self-reported attestation is not a '
                         'verification')
    return checked,matches_context,matches_provenance

def public_context_cropped(task,nodes,verified_provenance=None):
    """Opt-in cropped-scope profile: attested anchor, context forwarded unchanged.

    ``verified_provenance`` is the filtering provenance the caller has already
    validated for this run. :func:`attach` always supplies it for a cropped task.
    """
    attestation=task.get(ATTESTATION_KEY)
    if not isinstance(attestation,dict) or set(attestation)!=ATTESTATION_KEYS:
        raise ValueError('The cropped-scope public context profile requires a complete '+ATTESTATION_KEY)
    if attestation.get('verified_against')!=ATTESTATION_SCOPE:
        raise ValueError('The anchor attestation must come from the complete source graph')
    if attestation.get('verified_at_stage')!=ATTESTATION_STAGE:
        raise ValueError('The anchor attestation must be the one produced by '+ATTESTATION_STAGE
                         +'; another stage label is not evidence that the check ran')
    attested=_digest(attestation.get('full_graph_sha256'),'attested complete-graph digest')
    checked,matches_context,matches_provenance=_cross_check_digest(attested,task,
                                                                  verified_provenance)
    mi,context,spatial,location=_shape_head(task)
    if attestation.get('anchor_object_id')!=location['near_scene_element_id']:
        raise ValueError('The attestation describes another user-location anchor')
    if attestation.get('anchor_room_name')!=location['room_name']:
        raise ValueError('The attested room annotation differs from the stated user room')
    if attestation.get('anchor_active') is not True:
        raise ValueError('The attested anchor was not active in the complete graph')
    present=any(n.get('id')==location['near_scene_element_id'] for n in nodes)
    if present:
        # Still in scope, so the original node-level checks apply unchanged.
        _anchor_checks(task,nodes,location)
    stated,projected=_shape_tail(task,mi,context,spatial)
    return stated,projected,{'anchor_object_id':location['near_scene_element_id'],
        'anchor_present_in_supplied_subgraph':present,
        'anchor_checked_against':'supplied_subgraph_and_attestation' if present else 'attestation_only',
        'attestation':copy.deepcopy(attestation),
        'attested_full_graph_sha256':attested,
        'attested_verified_at_stage':ATTESTATION_STAGE,
        'source_graph_digest_cross_checked_against':checked,
        'digest_matches_task_topology_context':matches_context,
        'digest_matches_verified_filtering_provenance':matches_provenance,
        'self_reported_attestation_label_not_accepted_as_verification':True,
        'dialogue_history_forwarded_unchanged':True,
        'user_location_rewritten':False,
        'user_location_used_as_robot_location':False,
        'robot_location_invented':False,
        'absence_from_subgraph_is_not_absence_from_environment':True}

def attach(task,nodes,contract):
    marker=task.get('collection_public_context_version')
    if marker is None:
        if 'conversation_context' in task or 'model_input' in task:
            raise ValueError('Context-bearing task requires the reviewed-context collection entry; refusing to silently discard context')
        return False
    if marker==CROPPED_VERSION:
        # The caller validates the filtering provenance for this run before attaching
        # context, so the attested digest is compared with a checked value, not with
        # the task's own restatement of it.
        provenance=contract.get('input_filtering_provenance')
        if not isinstance(provenance,dict):
            raise ValueError('The cropped-scope public context profile requires the verified '
                             'input_filtering_provenance of this run before the context is '
                             'forwarded')
        context,spatial,scope=public_context_cropped(task,nodes,verified_provenance=provenance)
        contract['conversation_context']=context
        contract['public_spatial_context']=spatial
        contract['factual_boundary']+=BOUNDARY+CROPPED_BOUNDARY
        contract['public_context_version']=CROPPED_VERSION
        contract['public_context_anchor_scope']=scope
        return True
    if marker!=VERSION:raise ValueError('Unsupported public context version')
    context,spatial=public_context(task,nodes)
    contract['conversation_context']=context
    contract['public_spatial_context']=spatial
    contract['factual_boundary']+=BOUNDARY
    contract['public_context_version']=VERSION
    return True
