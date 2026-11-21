"""Real, dated different-order schedules replay without new route searches."""

from datetime import timedelta
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from planner.collect_sequential import analyze
from planner.models import RailQuery, instant
from planner.replay import record_from_capture
from planner.sequential_benchmark import fastest_course


ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT/'sample/low-call/divergent-revalidation'
RUN = FOLDER/'run-20261119'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


class DivergentRevalidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_same_input_replays_with_correct_quality_and_call_counts(self):
        preflight = read(FOLDER/'preflight.json')
        source = (ROOT/preflight['selectedScenario']).read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(),preflight['expectedScenarioSha256'])
        self.assertEqual(source,(RUN/'scenario.json').read_bytes())
        acquisition = read(RUN/'acquisition.json')
        self.assertEqual(acquisition['status'],'complete')
        self.assertEqual(acquisition['routeSearchAttempts'],10)
        self.assertEqual(acquisition['searchReservations'],10)
        self.assertEqual(acquisition['httpRequestsObserved'],5058)
        self.assertFalse(acquisition['legacySharedWithV2'])
        self.assertEqual(acquisition['v2Order'],preflight['v2Order'])
        self.assertEqual(acquisition['retries'],0)
        self.assertEqual(acquisition['stationRequests'],0)
        self.assertEqual(acquisition['walkRequests'],0)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'analysis'
            with patch('socket.socket.connect',side_effect=AssertionError('offline attempted network')):
                comparison = await analyze(RUN,output)
            self.assertEqual(comparison,read(RUN/'analysis/comparison.json'))
            self.assertEqual(comparison['status'],'complete')
            self.assertEqual(comparison['searchesByProfile'],{'snapshot':6,'v2':2,'legacy-validation':2})
            self.assertEqual(comparison['replayHttpRequests'],0)
            self.assertEqual(comparison['v2ElapsedMinutesSaved'],-6)
            self.assertEqual(comparison['v2GrossFareSaved'],-199)
            self.assertFalse(comparison['passSavingsVerified'])
            for profile,key,order,elapsed,wait,journey,walk,fare in (
                ('v2','v2AtActualTimes',['nezu','meiji','odaiba'],379,1,93,45,1246),
                ('legacy-validation','legacyOrderAtActualTimes',['meiji','nezu','odaiba'],373,15,83,35,1047),
            ):
                metrics = comparison[key]
                self.assertEqual(metrics['order'],order)
                self.assertEqual(metrics['elapsedMinutes'],elapsed)
                self.assertEqual(metrics['initialWaitMinutes'],wait)
                self.assertEqual(metrics['rideMinutes'],journey)
                self.assertEqual(metrics['walkingMinutes'],walk)
                self.assertEqual(metrics['stayMinutes'],240)
                self.assertEqual(metrics['grossFare'],fare)
                self.assertEqual(elapsed,wait+journey+walk+240)
                result = read(output/f'{profile}-result.json')
                self.assertEqual(result['selected'],read(RUN/f'analysis/{profile}-result.json')['selected'])
                if profile=='v2':
                    candidates = result['candidates']['orders']
                    self.assertEqual(candidates[1]['order'],list(reversed(candidates[0]['order'])))
                    self.assertEqual(candidates[0]['geometricScore'],candidates[1]['geometricScore'])
                    self.assertEqual(candidates[2]['order'],acquisition['legacyFixed']['order'])
                self.assertEqual(result['ledger']['railAttempts'],2)
                self.assertEqual(result['ledger']['httpRequestsObserved'],0)
                dataset = read(output/f'{profile}-recording.json')
                self.assertEqual(dataset['provenance']['kind'],'captured-public-dom')
                self.assertEqual(dataset['provenance']['sourceSha256'],hashlib.sha256((RUN/'acquisition.json').read_bytes()).hexdigest())
                visits = result['selected']['visits']
                captures = [entry for entry in acquisition['captures'] if entry['profile']==profile]
                for index,leg in enumerate(result['selected']['legs']):
                    self.assertEqual(instant(leg['preparedAt']),instant(visits[index]['leaveAt'])+timedelta(minutes=leg['accessWalking']['minutes']))
                    self.assertGreaterEqual(instant(leg['departureAt']),instant(leg['preparedAt']))
                    self.assertGreater(instant(leg['arrivalAt']),instant(leg['departureAt']))
                    self.assertEqual(instant(visits[index+1]['arrivalAt']),instant(leg['arrivalAt'])+timedelta(minutes=leg['egressWalking']['minutes']))
                    self.assertEqual(leg['providerEvidence']['source'],'ekispert-captured-public-dom')
                    self.assertIsNone(leg['transfers'])
                    captured = read(RUN/captures[index]['file'])
                    usable = [(course,dated) for course,dated in zip(captured['payload']['routes'],captured['absoluteTimes'])
                              if instant(dated['departureAt'])>=instant(leg['preparedAt'])]
                    earliest = min(instant(dated['arrivalAt']) for _,dated in usable)
                    self.assertEqual(instant(leg['arrivalAt']),earliest)
                    fare = sum(unit['quotedTotal'] for unit in leg['fareUnits'])
                    self.assertEqual(fare,min(course['fee'] for course,dated in usable if instant(dated['arrivalAt'])==earliest))
                    self.assertTrue(any(dated['departureAt']==leg['departureAt'] and dated['arrivalAt']==leg['arrivalAt']
                                        and course['fee']==fare for course,dated in usable))
                    for unit in leg['fareUnits']:
                        self.assertIsNone(unit['baseFare'])
                        self.assertIsNone(unit['supplement'])

    @patch('socket.socket.connect',side_effect=AssertionError('offline attempted network'))
    async def test_all_capture_hashes_dated_candidates_and_original_fixed_optimizer(self, _connect):
        acquisition = read(RUN/'acquisition.json')
        edges = []
        for entry in acquisition['captures']:
            raw = (RUN/entry['file']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),entry['sha256'])
            capture = json.loads(raw)
            self.assertEqual(capture['status'],'captured')
            self.assertFalse(capture['headless'])
            self.assertEqual(capture['extractionMethod'],'public-dom-layout-compat')
            recording = record_from_capture(capture,RailQuery.model_validate(entry['query']))
            self.assertEqual(len(recording.absoluteTimes),4)
            for dated in recording.absoluteTimes:
                self.assertIsNotNone(dated)
                self.assertEqual(dated.evidence,'calendar_dom_clock')
            if entry['profile']=='snapshot':
                _,course = fastest_course(capture['payload'])
                edges.append({'from':entry['from'],'to':entry['to'],'source':'snapshot','result':course,
                              'stations':{'from':capture['query']['departure'],'to':capture['query']['arrival']}})
        self.assertEqual(sum(read(RUN/e['file'])['httpRequestsObserved'] for e in acquisition['captures']),5058)
        previous_path = list(sys.path)
        try:
            sys.path.insert(0,str(ROOT/'backend'))
            optimizer = importlib.import_module('app.optimizer')
            original = optimizer.optimize_visit_orders(['meiji','nezu','odaiba'],edges, limit=1)[0]
        finally:
            sys.path[:] = previous_path
        self.assertEqual(original['order'],acquisition['legacyFixed']['order'])
        self.assertEqual(original['order'],['meiji','nezu','odaiba'])
        self.assertEqual((original['time'],original['fee']),(79,1110))


if __name__ == '__main__':
    unittest.main()
