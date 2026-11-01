"""Strict binding contract, archived real failure, safe diagnostics and no repair."""
import copy
import importlib.util
import itertools
import json
from pathlib import Path

import pytest
from app.models import Document, ResearchReport, ResearchTask, User
from app.report_schemas import WrittenParagraph, WrittenReport
from app.services import report_writing, reports, research
from sqlalchemy import select
from test_api import register
from test_mvp_workflow import mock_model, setup
from test_research import execute, get

ROOT = Path(__file__).resolve().parents[3]
if not (ROOT / 'evaluation/report-binding-fix/replay.py').is_file():
    pytest.skip('Private historical replay is not distributed with the public repository', allow_module_level=True)
spec = importlib.util.spec_from_file_location('binding_replay', ROOT / 'evaluation/report-binding-fix/replay.py')
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


@pytest.fixture
def saved_store(tmp_path):
    store = replay.create_store(tmp_path / 'historical-copy.db')
    try:
        yield store
    finally:
        store.engine.dispose()


def prepare(store):
    with store.session() as db:
        task = db.get(ResearchTask, replay.TID)
        user = db.get(User, task.user_id)
        base, eligible, answered = report_writing.prepare(db, user, task)
        return copy.deepcopy(task), base, eligible, answered


def write(store, invoke):
    task, *_ = prepare(store)
    return report_writing.write_saved_report(store, task.user_id, task.id, store.settings, invoke)


def test_original_provider_output_fails_both_legacy_and_new_without_any_modification(saved_store):
    original = replay.read_original()
    before = reports.digest(original)
    task, base, eligible, answered = prepare(saved_store)
    assert WrittenReport.model_validate(original)
    with saved_store.session() as db:
        user = db.get(User, task.user_id)
        versions = list(db.scalars(select(ResearchReport.id)))
        with pytest.raises(ValueError, match='invalid, conflicting or gap-bound'):
            replay.legacy_writer().apply_writing(db, user, base, eligible, answered, original, saved_store.settings)
        with pytest.raises(report_writing.ReportWritingError) as caught:
            report_writing.apply_writing(db, user, base, eligible, answered, original, saved_store.settings)
        assert caught.value.details == {'code': 'REPORT_BINDING_INVALID', 'rule': 'EVIDENCE_STEP_MISMATCH',
            'path': '/sections/findings/paragraphs/2', 'evidence_ids': ['e4'], 'step_ids': ['s3', 's4'],
            'allowed_step_ids': ['s2'], 'semantic_checked': False}
        assert list(db.scalars(select(ResearchReport.id))) == versions
    assert reports.digest(original) == before


def test_contract_contains_original_and_allowed_edges_safe_claims_and_portable_schema(saved_store):
    task, base, eligible, answered = prepare(saved_store)
    before = reports.digest({'evidence': task.evidence, 'steps': task.steps, 'result': task.result})
    payload, schema = report_writing.writing_contract(task, base, eligible, answered)
    assert payload['citation_scope']['allowed_evidence_steps']['e4'] == ['s2']
    assert payload['citation_scope']['allowed_evidence_steps']['e3'] == ['s2', 's3', 's4']
    source = next(e for e in payload['sources'] if e['id'] == 'e4')
    assert source['step_ids'] == source['allowed_step_ids'] == ['s2'] and 'feed reranking' in source['text']
    assert source['document_name'] and len(payload['research_claims']) == 8
    assert all(c['quality_status'] == 'NEEDS_REVIEW' for c in payload['research_claims'] if c['kind'] == 'synthesis')
    fields = schema['$defs']['WrittenParagraph']['properties']
    assert fields['evidence_ids']['items']['enum'] == [e['id'] for e in eligible]
    assert fields['step_ids']['items']['enum'] == sorted(answered) and fields['step_ids']['uniqueItems']
    assert set(fields) == {'text', 'evidence_ids', 'step_ids'}
    assert json.loads(json.dumps(schema)) == schema  # Same JSON schema parameter as existing adapters.
    assert '两方向同时满足' in report_writing.INSTRUCTION and '装饰性补列' in report_writing.INSTRUCTION
    assert reports.digest({'evidence': task.evidence, 'steps': task.steps, 'result': task.result}) == before


def test_cross_step_union_of_edges_is_legal_and_not_a_cartesian_requirement(saved_store):
    calls = []
    def invoke(*args, **kwargs):
        calls.append(True)
        return replay.mock_writing(), {}
    content = write(saved_store, invoke)
    assert len(calls) == 1 and content['generation']['invocation_kind'] == 'mock'
    assert content['validation']['status'] == 'VALID' and content['validation']['semantic_checked'] is False
    assert all(c['quality_status'] == 'NEEDS_REVIEW' for c in content['claims'] if c['id'].startswith('w'))
    assert len(content['sections']) == 8 and len([c for c in content['claims'] if c['id'].startswith('w')]) == 6


@pytest.mark.parametrize('mutation,rule', [
    ('unknown_evidence', 'EVIDENCE_ID_UNAVAILABLE'), ('unknown_step', 'STEP_ID_UNAVAILABLE'),
    ('reverse_edge', 'EVIDENCE_STEP_MISMATCH'), ('uncovered_step', 'STEP_WITHOUT_CITED_EVIDENCE'),
    ('duplicate_evidence', 'DUPLICATE_BINDING_ID'), ('duplicate_step', 'DUPLICATE_BINDING_ID'),
    ('empty_evidence', None), ('empty_steps', None), ('numeric', None), ('inline', None), ('coverage', None)])
def test_invalid_output_never_saves_or_repairs_old_versions(saved_store, mutation, rule):
    task, *_ = prepare(saved_store)
    with saved_store.session() as db:
        old = {r.id: r.content_sha256 for r in db.scalars(select(ResearchReport))}
    before = reports.digest(task.result)
    data = replay.mock_writing()
    p = data['sections'][0]['paragraphs'][0]
    if mutation == 'unknown_evidence':
        p['evidence_ids'] = ['e999']
    elif mutation == 'unknown_step':
        p['step_ids'] = ['s9']
    elif mutation == 'reverse_edge':
        p.update(evidence_ids=['e3', 'e4'], step_ids=['s3', 's4'])
    elif mutation == 'uncovered_step':
        p.update(evidence_ids=['e1'], step_ids=['s1', 's2'])
    elif mutation == 'duplicate_evidence':
        p['evidence_ids'].append('e1')
    elif mutation == 'duplicate_step':
        p['step_ids'] = ['s1', 's1']
    elif mutation == 'empty_evidence':
        p['evidence_ids'] = []
    elif mutation == 'empty_steps':
        p['step_ids'] = []
    elif mutation == 'numeric':
        p['text'] += '实验提升999%。'
    elif mutation == 'inline':
        p['text'] += '[1]'
    else:
        for section in data['sections']:
            for paragraph in section['paragraphs']:
                paragraph.update(evidence_ids=['e1'], step_ids=['s1'])
    original = copy.deepcopy(data)
    with pytest.raises(ValueError) as caught:
        write(saved_store, lambda *args, **kwargs: (data, {}))
    if rule:
        assert caught.value.details['rule'] == rule
        assert caught.value.details['path'] == '/sections/abstract/paragraphs/0'
    assert data == original
    with saved_store.session() as db:
        assert {r.id: r.content_sha256 for r in db.scalars(select(ResearchReport))} == old
        assert reports.digest(db.get(ResearchTask, task.id).result) == before


@pytest.mark.parametrize('boundary', ['gap', 'conflict', 'source_deleted'])
def test_gap_conflict_and_invalid_source_remain_rejected(saved_store, boundary):
    with saved_store.session() as db:
        original_versions = {r.id: r.content_sha256 for r in db.scalars(select(ResearchReport))}
        task = db.get(ResearchTask, replay.TID)
        if boundary == 'gap':
            steps = copy.deepcopy(task.steps)
            steps[3]['gap'] = 'Mock：缺少直接证据'
            task.steps = steps
        elif boundary == 'conflict':
            task.result = {**task.result, 'conflicts': [{'evidence_ids': ['e3'], 'reason': 'Mock未裁决冲突'}]}
        else:
            db.get(Document, task.evidence[0]['document_id']).status = 'deleting'
        db.commit()
    with pytest.raises(ValueError):
        write(saved_store, lambda *a, **kw: (replay.mock_writing(), {}))
    with saved_store.session() as db:
        assert {r.id: r.content_sha256 for r in db.scalars(select(ResearchReport))} == original_versions


def test_uncovered_safe_step_is_rejected_before_mock_invocation(saved_store):
    task, base, eligible, answered = prepare(saved_store)
    with pytest.raises(report_writing.ReportWritingError) as caught:
        report_writing.writing_contract(task, base, [e for e in eligible if e['id'] in {'e1', 'e2'}], answered)
    assert caught.value.code == 'REPORT_INPUT_BINDING_INVALID'
    assert caught.value.details['step_ids'] == ['s2', 's3', 's4']
    # Exercise the same preflight through _write, with an otherwise unchanged completed task.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(report_writing, 'prepare', lambda *a: (base, [eligible[0]], answered))
        with pytest.raises(report_writing.ReportWritingError):
            write(saved_store, lambda *a, **kw: pytest.fail('No invocation permitted on an impossible input graph'))


def test_diagnostics_do_not_echo_untrusted_text_or_non_id_values(saved_store):
    data = replay.mock_writing()
    data['sections'][0]['paragraphs'][0]['evidence_ids'] = ['private secret <script>']
    with pytest.raises(report_writing.ReportWritingError) as caught:
        write(saved_store, lambda *a, **kw: (data, {}))
    assert caught.value.details['evidence_ids'] == ['[invalid-id]']
    assert 'private secret' not in str(caught.value) and 'LangGraph' not in str(caught.value)


def test_two_way_binding_truth_table_matches_existing_rule_for_coherent_graphs():
    subsets = [('s1',), ('s2',), ('s1', 's2')]
    for left, right in itertools.product(subsets, repeat=2):
        by_id = {'e1': {'step_ids': list(left)}, 'e2': {'step_ids': list(right)}}
        steps = {s: {'evidence_ids': [e for e in by_id if s in by_id[e]['step_ids']]} for s in ('s1', 's2')}
        for eids, sids in itertools.product((['e1'], ['e2'], ['e1', 'e2']), subsets):
            old = (all(any(s in by_id[e]['step_ids'] for e in eids) for s in sids)
                   and all(set(by_id[e]['step_ids']) & set(sids) for e in eids))
            p = WrittenParagraph(text='Mock段落仅检验关联图规则，不进行语义事实支持判定。' * 3, evidence_ids=eids, step_ids=list(sids))
            try:
                report_writing.validate_paragraph_bindings(p, by_id, {'s1', 's2'}, steps, '/sections/findings/paragraphs/0')
                new = True
            except report_writing.ReportWritingError:
                new = False
            assert old == new


def test_report_workflow_exposes_safe_binding_diagnostic_and_does_not_retry(monkeypatch, request):
    monkeypatch.setattr(research, 'run_research_worker', lambda database, settings, stop: stop.wait(60))
    c = request.getfixturevalue('client')
    h = register(c)
    def transform(data):
        data['sections'][0]['paragraphs'][0]['evidence_ids'] = ['e999']
        return data
    calls = mock_model(monkeypatch, transform)
    tid, _ = setup(c, h)
    execute(c)
    task = get(c, h, tid)
    workflow = task['report_workflow']
    assert task['status'] == 'completed' and workflow['code'] == 'REPORT_BINDING_INVALID'
    assert workflow['details']['rule'] == 'EVIDENCE_ID_UNAVAILABLE'
    assert workflow['details']['evidence_ids'] == ['e999'] and '/sections/abstract/paragraphs/0' in workflow['message']
    assert len(calls) == 3 and not c.get('/api/research-reports', headers=h).json()
    # Real Runner.call with the existing adapter interface, but a local Mock model:
    # the schema remains serializable in the system message, with no new wire fields.
    schema = json.loads(calls[-1][0].split(' schema=', 1)[1])
    fields = schema['$defs']['WrittenParagraph']['properties']
    assert set(fields) == {'text', 'evidence_ids', 'step_ids'}
    assert fields['step_ids']['items']['enum'] == ['s1']
    assert calls[-1][1]['citation_scope']['allowed_step_evidence']['s1']
    assert calls[-1][2]['max_retries'] == 0
