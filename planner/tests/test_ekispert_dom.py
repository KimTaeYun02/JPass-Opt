"""Current public result layout is parsed without depending on a removed tag."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from planner.adapters.ekispert_dom import parse_dom_routes
from planner.adapters.ekispert_times import resolve_calendar_times
from planner.tests import test_ekispert_times as fixtures
from planner.tests.test_ekispert_times import snapshot


def current_course():
    return {"headerText":"経路1\n09:01 ⇒ 09:32\n（31分：乗車 16 分 その他 15 分）\n乗換:\n2回\n片道:\n513円\n",
            "elements":[{"kind":"point","names":["参宮橋"]},{"kind":"line","names":["小田急小田原線・新宿行\n前後のダイヤ"]},
                        {"kind":"point","names":["新宿"]},{"kind":"line","names":["ＪＲ中央線快速・東京行"]},
                        {"kind":"point","names":["四ツ谷"]},{"kind":"line","names":["東京メトロ南北線・王子神谷行"]},
                        {"kind":"point","names":["東大前"]}]}


class DomLayoutTests(unittest.TestCase):
    def test_current_label_only_fare_header_does_not_become_zero(self):
        parsed=parse_dom_routes({"courses":[current_course()]})
        self.assertEqual((parsed.routes[0].time,parsed.routes[0].fee),(31,513))
        self.assertEqual(parsed.routes[0].route[0].line,'小田急小田原線・新宿行')
        self.assertIsNone(parsed.routes[0].route[-1].line)
        for header in ('片道:\n\n513円','（31分：乗車16分）\n片道:','（31分：乗車16分）\n片道:513円\n片道:600円'):
            with self.assertRaises(ValueError):parse_dom_routes({'courses':[{**current_course(),'headerText':header}]})

    def test_earlier_layout_and_hour_duration_are_supported_without_deriving_times(self):
        course={**current_course(),'headerText':'09:01 ⇒ 10:32\n所要時間:1時間31分(乗車16分)\n片道:1,513円'}
        result=parse_dom_routes({'courses':[course]}).routes[0]
        self.assertEqual((result.time,result.fee),(91,1513))
        invalid=deepcopy(course);invalid['elements'].insert(1,{'kind':'point','names':['duplicate']})
        with self.assertRaises(ValueError):parse_dom_routes({'courses':[invalid]})

    def test_actual_failed_extraction_diagnostic_is_readable_offline(self):
        root=Path(__file__).resolve().parents[2]
        raw=json.loads((root/'sample/low-call/sequential/diagnostic-parser.json').read_text(encoding='utf-8'))
        self.assertIn('course-detail-header-total-time',raw['errorDetail'])
        courses=[]
        for course in raw['extractionDiagnostic']:
            first=next(field['text'] for field in course['fields'] if field['id']=='course-detail-point')
            elements=[]
            for field in course['fields']:
                if field['id']=='course-detail-point-station-name':elements.append({'kind':'point','names':[field['text']]})
                elif field['id']=='course-detail-line-name':elements.append({'kind':'line','names':[field['text']]})
            courses.append({'headerText':course['text'].split(first)[0],'elements':elements})
        parsed=parse_dom_routes({'courses':courses})
        self.assertEqual([(c.time,c.fee) for c in parsed.routes],[(31,513),(34,513),(38,345),(44,555)])
        # This diagnostic has no full result-calendar text: do not invent dated rides from it.

    def test_observed_short_departure_arrival_labels_are_supported(self):
        raw=snapshot()
        raw['pointTimes'][0]['rawText']='発\n09:04'
        raw['pointTimes'][1]['rawText']='着\n09:06'
        dated=resolve_calendar_times(raw)
        self.assertEqual(dated.arrivalAt,'2026-11-19T00:06:00Z')

    def test_actual_odaiba_failure_supports_spaced_hour_duration_offline(self):
        root=Path(__file__).resolve().parents[2]
        raw=json.loads((root/'sample/low-call/divergent/run-20261119/captures/02-snapshot.json').read_text(encoding='utf-8'))
        self.assertEqual(raw['errorDetail'],'ambiguous_route_duration_or_fare')
        self.assertIsNone(raw['absoluteTimes'])
        courses=[]
        for course in raw['extractionDiagnostic']:
            body=next(field['text'] for field in course['fields'] if field['id']=='course-detail-body')
            elements=[]
            for field in course['fields']:
                if field['id']=='course-detail-point-station-name':elements.append({'kind':'point','names':[field['text']]})
                elif field['id']=='course-detail-line-name':elements.append({'kind':'line','names':[field['text']]})
            courses.append({'headerText':course['text'][:course['text'].index(body)],'elements':elements})
        self.assertIn('（ 1時間 41分 ：',courses[3]['headerText'])
        parsed=parse_dom_routes({'courses':courses})
        self.assertEqual([(c.time,c.fee) for c in parsed.routes],[(54,901),(58,744),(50,890),(101,618)])
        self.assertTrue(all(c.route[0].station=='参宮橋' and c.route[-1].station=='テレコムセンター' for c in parsed.routes))
        for suffix in ('\n（ 1時間 41分 ：乗車76分）','\n片道:700円'):
            with self.assertRaises(ValueError):
                parse_dom_routes({'courses':[{**courses[3],'headerText':courses[3]['headerText']+suffix}]})
        # Saved diagnostics lack the result calendar. Parsing is not a dated capture.


class CompatCaptureTests(unittest.IsolatedAsyncioTestCase):
    setUp = fixtures.HeadedCaptureTests.setUp
    capture = fixtures.HeadedCaptureTests.capture
    async def test_missing_old_total_time_selector_uses_compat_dom_without_original_extractor(self):
        original_locator=self.page.locator
        class Empty:
            async def count(self):return 0
        self.page.locator=lambda selector:Empty() if selector=='[data-testid="course-detail-header-total-time"]' else original_locator(selector)
        self.dom['courses'][0].update(headerText='09:04 ⇒ 09:06\n（2分：乗車2分）\n片道:\n178円',
            elements=[{'kind':'point','names':['東大前']},{'kind':'line','names':['東京メトロ南北線']},{'kind':'point','names':['後楽園']}])
        async def forbidden(page):raise AssertionError('old extractor must not wait for the absent tag')
        self.crawler.extract_route=forbidden
        result=await self.capture('compat.json',headless=False)
        self.assertEqual(result['status'],'captured',result)
        self.assertEqual(result['extractionMethod'],'public-dom-layout-compat')
        self.assertEqual(result['payload']['routes'][0]['fee'],178)


if __name__=='__main__':unittest.main()
