"""Synthetic acquisition controls and honest fixed-time/sequential comparison."""

import asyncio
from copy import deepcopy
from datetime import timedelta
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from planner.collect_sequential import acquire, analyze
from planner.models import instant, iso
from planner.sequential_benchmark import (Scenario, compare_schedules, fastest_course, fixed_time_order,
                                         geometric_order, schedule_metrics)

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT/"sample/low-call/sequential/scenario.json"


def load_scenario():
    return Scenario.model_validate_json(SCENARIO.read_text(encoding="utf-8"))


class FakeCollector:
    """All route times here are synthetic test values, never real capture evidence."""
    def __init__(self, *, blocked_at=None, hang=False, change_options=False, mismatch_query=False,
                 partial_at=None, change_method=False):
        self.calls = []
        self.started = asyncio.Event()
        self.blocked_at, self.hang = blocked_at, hang
        self.change_options, self.mismatch_query = change_options, mismatch_query
        self.partial_at, self.change_method = partial_at, change_method

    async def __call__(self, crawler, *, departure, arrival, prepared_at, output, timeout_seconds, headless):
        self.calls.append((departure,arrival,prepared_at))
        assert headless is False and 0 < timeout_seconds <= 90
        prepared=instant(prepared_at)
        duration={('東大前','後楽園'):1,('後楽園','参宮橋'):1,('参宮橋','後楽園'):5,('後楽園','東大前'):5}.get((departure,arrival),20)
        codes={'東大前':'29075','後楽園':'22675','参宮橋':'22702'}
        at=prepared+timedelta(minutes=2)
        result={'schemaVersion':'ekispert-capture/1','status':'captured','stopReason':None,
                'query':{'departure':departure,'arrival':arrival,'preparedAt':iso(prepared),'options':{}},
                'payload':{'routes':[{'time':duration,'fee':178,'route':[{'station':departure,'line':'synthetic-test-line'},{'station':arrival,'line':None}]}]},
                'absoluteTimes':[{'departureAt':iso(at),'arrivalAt':iso(at+timedelta(minutes=duration)),'evidence':'dom-time-datetime'}],
                'stationCodes':{'depCode':codes[departure],'arrCode':codes[arrival]},'providerOptions':{'ticketType':'IC'},
                'routeSearchAttempts':1,'httpRequestsObserved':17,'headless':False,'retries':0,
                'extractionMethod':'synthetic-test-parser'}
        if self.partial_at == len(self.calls):result['status']='partial'
        if self.change_method and len(self.calls)==2:result['extractionMethod']='another-synthetic-parser'
        if self.change_options and len(self.calls)==2:
            result['providerOptions']['ticketType']='cash'
        if self.mismatch_query:
            result['query']['preparedAt']=iso(prepared+timedelta(seconds=1))
        if self.blocked_at == len(self.calls):
            result.update(status='blocked',stopReason='http_429',payload=None,absoluteTimes=None)
        self.started.set()
        try:
            if self.hang:
                await asyncio.Event().wait()
        except asyncio.CancelledError:
            result.update(status='failed',stopReason='cancelled')
            raise
        finally:
            with Path(output).open('x',encoding='utf-8') as stream:
                stream.write(json.dumps(result,ensure_ascii=False))
        return result


class StaticComparisonTests(unittest.TestCase):
    def test_scenario_and_geometric_order_are_fixed_before_matrix(self):
        scenario=load_scenario()
        self.assertEqual(geometric_order(scenario),['meiji','tokyo-dome','nezu'])
        for mutate in (lambda s:s['request']['places'][0].update(anchorRole='start'),
                       lambda s:s['request']['options'].update(allowCache=True),
                       lambda s:s['bindings'][0]['place'].update(name='different'),
                       lambda s:s['request'].update(startAt='2026-11-20T10:00:00+09:00')):
            raw=scenario.model_dump(mode='json');mutate(raw)
            with self.assertRaises(ValueError):Scenario.model_validate(raw)

    def test_fastest_selection_duration_then_fare_then_original_index(self):
        base={'time':5,'fee':200,'route':[{'station':'A','line':'test'},{'station':'B','line':None}]}
        index,selected=fastest_course({'routes':[base,{**base,'time':4,'fee':300},{**base,'time':4,'fee':250},{**base,'time':None}]})
        self.assertEqual((index,selected['time'],selected['fee']),(2,4,250))
        with self.assertRaises(ValueError):fastest_course({'routes':[{**base,'fee':None}]})

    def test_complete_matrix_required_and_unknown_result_not_reported_as_zero(self):
        with self.assertRaisesRegex(ValueError,'incomplete_snapshot_matrix'):fixed_time_order(['a','b','c'],[])
        self.assertIsNone(schedule_metrics(None)['grossFare'])
        self.assertIsNone(schedule_metrics({'completion':'partial'})['elapsedMinutes'])
        comparison=compare_schedules(None,None,None)
        self.assertEqual(comparison['status'],'partial')
        self.assertIsNone(comparison['v2ElapsedMinutesSaved'])
        self.assertEqual(comparison['timingOutcome'],'unknown')


class AcquisitionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.path=Path(folder.name)
        self.scenario=load_scenario()

    async def test_acquire_and_offline_replay_keep_stays_walks_waits_and_actual_counters(self):
        fake=FakeCollector()
        run=self.path/'run'
        report=await acquire(self.scenario,run,None,collector=fake)
        self.assertEqual(report['status'],'complete',report)
        self.assertEqual(len(fake.calls),10)
        self.assertEqual(report['routeSearchAttempts'],10)
        self.assertEqual(report['httpRequestsObserved'],170)
        self.assertEqual(report['stationRequests'],0)
        self.assertEqual(report['legacyFixed']['order'],['nezu','tokyo-dome','meiji'])
        self.assertFalse(report['legacySharedWithV2'])
        self.assertEqual(report['v2Order'],['meiji','tokyo-dome','nezu'])
        # Initial stay60 + access15, then actual arrival+egress+stay90+access8.
        self.assertEqual(instant(fake.calls[6][2]),instant('2026-11-20T10:15:00+09:00'))
        self.assertEqual(instant(fake.calls[7][2]),instant('2026-11-20T12:08:00+09:00'))
        with patch('requests.sessions.Session.request',side_effect=AssertionError('offline network forbidden')):
            comparison=await analyze(run,self.path/'analysis')
        self.assertEqual(comparison['status'],'complete')
        self.assertEqual(comparison['timingOutcome'],'longer')
        self.assertEqual(comparison['v2ElapsedMinutesSaved'],-8)
        self.assertEqual(comparison['v2AtActualTimes']['initialWaitMinutes'],4)
        self.assertEqual(comparison['v2AtActualTimes']['stayMinutes'],210)
        self.assertEqual(comparison['v2AtActualTimes']['walkingMinutes'],36)
        self.assertEqual(comparison['v2AtActualTimes']['rideMinutes'],10)
        self.assertEqual(comparison['v2AtActualTimes']['elapsedMinutes'],260)
        self.assertEqual(comparison['replayHttpRequests'],0)
        # Cross-check the read-only original Held-Karp implementation on identical costs.
        sys.path.insert(0,str(ROOT/'backend'))
        optimizer=importlib.import_module('app.optimizer')
        edges=[]
        for entry in report['captures'][:6]:
            stored=json.loads((run/entry['file']).read_text(encoding='utf-8'))
            _,result=fastest_course(stored['payload'])
            edges.append({'from':entry['from'],'to':entry['to'],'source':'snapshot','result':result,
                          'stations':{'from':stored['query']['departure'],'to':stored['query']['arrival']}})
        original=optimizer.optimize_visit_orders(['nezu','tokyo-dome','meiji'],edges, limit=1)[0]
        self.assertEqual(original['order'],report['legacyFixed']['order'])
        self.assertEqual(original['time'],report['legacyFixed']['displayedTravelMinutes'])
        self.assertEqual(original['fee'],report['legacyFixed']['grossFare'])

    async def test_same_order_shares_one_schedule_with_explicit_acquisition_counts(self):
        fake=FakeCollector()
        # Reverse geometry by changing the declared test coordinates and keeping bindings equal.
        raw=self.scenario.model_dump(mode='json')
        for place in raw['request']['places']:
            place['clientPlaceId']={'nezu':'a','tokyo-dome':'b','meiji':'c'}[place['clientPlaceId']]
        for binding in raw['bindings']:
            binding['place']['clientPlaceId']={'nezu':'a','tokyo-dome':'b','meiji':'c'}[binding['place']['clientPlaceId']]
        scenario=Scenario.model_validate(raw)
        report=await acquire(scenario,self.path/'shared',None,collector=fake)
        self.assertEqual(report['status'],'complete',report)
        self.assertTrue(report['legacySharedWithV2'])
        self.assertEqual(len(fake.calls),8)
        comparison=await analyze(self.path/'shared',self.path/'shared-analysis')
        self.assertEqual(comparison['timingOutcome'],'same')
        self.assertEqual(comparison['searchesByProfile']['legacy-validation'],0)

    async def test_budget_exhaustion_and_429_are_durable_without_hidden_retry(self):
        for name,fake,limit,expected,used in [('cap',FakeCollector(),1,'search_budget_exhausted',1),
                                            ('blocked',FakeCollector(blocked_at=2),10,'http_429',2),
                                            ('options',FakeCollector(change_options=True),10,'provider_options_changed',2),
                                            ('query',FakeCollector(mismatch_query=True),10,'ValueError',1)]:
            report=await acquire(self.scenario,self.path/name,None,collector=fake,max_searches=limit)
            self.assertEqual(report['status'],'partial')
            self.assertEqual(report['stopReason'],expected)
            self.assertEqual(report['searchReservations'],used)
            self.assertEqual(len(fake.calls),used)
            saved=json.loads((self.path/name/'acquisition.json').read_text(encoding='utf-8'))
            self.assertEqual(saved,report)
            with self.assertRaises(FileExistsError):
                await acquire(self.scenario,self.path/name,None,collector=fake)
            self.assertEqual(len(fake.calls),used)

    async def test_deadline_cancels_inflight_search_and_preserves_committed_attempt(self):
        fake=FakeCollector(hang=True)
        report=await acquire(self.scenario,self.path/'deadline',None,collector=fake,budget_seconds=.02)
        self.assertEqual(report['stopReason'],'deadline_exceeded')
        self.assertEqual(report['searchReservations'],1)
        self.assertEqual(report['routeSearchAttempts'],1)
        self.assertEqual(len(fake.calls),1)

    async def test_partial_sequential_capture_never_advances_to_next_visit(self):
        fake=FakeCollector(partial_at=7)
        report=await acquire(self.scenario,self.path/'partial-times',None,collector=fake)
        self.assertEqual(report['status'],'partial')
        self.assertEqual(report['stopReason'],'sequential_capture_has_unresolved_times')
        self.assertEqual(len(fake.calls),7)
        self.assertEqual(report['sequences']['v2']['legs'],[])

    async def test_parser_method_change_stops_entire_run(self):
        fake=FakeCollector(change_method=True)
        report=await acquire(self.scenario,self.path/'method-change',None,collector=fake)
        self.assertEqual(report['stopReason'],'extraction_method_changed')
        self.assertEqual(len(fake.calls),2)
        self.assertEqual(report['extractionMethod'],'synthetic-test-parser')

    async def test_cancellation_persists_report_and_does_not_dispatch_next_query(self):
        fake=FakeCollector(hang=True)
        task=asyncio.create_task(acquire(self.scenario,self.path/'cancel',None,collector=fake))
        await fake.started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        report=json.loads((self.path/'cancel/acquisition.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'],'cancelled')
        self.assertEqual(report['routeSearchAttempts'],1)
        self.assertEqual(report['searchReservations'],1)

    async def test_modified_capture_hash_is_rejected_before_replay(self):
        fake=FakeCollector()
        run=self.path/'hash'
        report=await acquire(self.scenario,run,None,collector=fake)
        entry=next(e for e in report['captures'] if e['profile']=='v2')
        with (run/entry['file']).open('a',encoding='utf-8') as stream:stream.write(' ')
        with self.assertRaisesRegex(ValueError,'capture_hash_mismatch'):
            await analyze(run,self.path/'hash-analysis')

    def test_cli_is_offline_by_default_and_dry_run_needs_no_backend(self):
        base=[sys.executable,'-m','planner.collect_sequential','--scenario',str(SCENARIO)]
        env={**os.environ,'PYTHONUTF8':'1'}
        result=subprocess.run(base,cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=10)
        self.assertEqual(result.returncode,2)
        result=subprocess.run(base+['--dry-run'],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['mode'],'offline-dry-run')
        result=subprocess.run(base+['--dry-run','--max-searches','11'],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=10)
        self.assertEqual(result.returncode,2)


class SavedSequentialTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_three_place_schedule_matches_recorded_trace_and_original_optimizer(self):
        run=ROOT/'sample/low-call/sequential/run-20261119-compat'
        acquisition=json.loads((run/'acquisition.json').read_text(encoding='utf-8'))
        self.assertEqual(acquisition['status'],'complete')
        self.assertEqual(acquisition['routeSearchAttempts'],8)
        self.assertTrue(acquisition['legacySharedWithV2'])
        with tempfile.TemporaryDirectory() as folder:
            with patch('socket.socket.connect',side_effect=AssertionError('offline replay attempted network')):
                comparison=await analyze(run,Path(folder)/'analysis')
            replay=json.loads((Path(folder)/'analysis/v2-result.json').read_text(encoding='utf-8'))
            self.assertEqual(replay['replayProvenance']['kind'],'captured-public-dom')
            self.assertTrue(all(leg['providerEvidence']['source']=='ekispert-captured-public-dom' for leg in replay['selected']['legs']))
            self.assertEqual(comparison['timingOutcome'],'same')
            self.assertEqual(comparison['v2ElapsedMinutesSaved'],0)
            self.assertEqual(comparison['v2AtActualTimes']['initialWaitMinutes'],14)
            self.assertEqual(comparison['v2AtActualTimes']['rideMinutes'],31)
            self.assertEqual(comparison['v2AtActualTimes']['walkingMinutes'],36)
            self.assertEqual(comparison['v2AtActualTimes']['stayMinutes'],210)
            self.assertEqual(comparison['v2AtActualTimes']['elapsedMinutes'],291)
            self.assertEqual(comparison['v2AtActualTimes']['grossFare'],691)
            self.assertEqual(comparison['replayHttpRequests'],0)
            self.assertEqual(comparison['searchesByProfile'],{'snapshot':6,'v2':2,'legacy-validation':0})
        sys.path.insert(0,str(ROOT/'backend'))
        optimizer=importlib.import_module('app.optimizer')
        edges=[]
        for entry in acquisition['captures']:
            if entry['profile']!='snapshot':continue
            stored=json.loads((run/entry['file']).read_text(encoding='utf-8'))
            _,result=fastest_course(stored['payload'])
            edges.append({'from':entry['from'],'to':entry['to'],'source':'snapshot','result':result,
                          'stations':{'from':stored['query']['departure'],'to':stored['query']['arrival']}})
        original=optimizer.optimize_visit_orders(['nezu','tokyo-dome','meiji'],edges, limit=1)[0]
        self.assertEqual(original['order'],acquisition['legacyFixed']['order'])
        self.assertEqual((original['time'],original['fee']),(29,701))


if __name__ == '__main__':unittest.main()
