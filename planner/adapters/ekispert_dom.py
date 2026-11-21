"""Read public route fields in the current and earlier Ekispert result layouts."""

import re

from .ekispert import RawSearch


def parse_dom_routes(snapshot: dict) -> RawSearch:
    routes = []
    for course in snapshot["courses"]:
        header = course["headerText"]
        durations = re.findall(r"(?:所要時間\s*[:：]\s*|^[（(][ \t]*)((?:\d+時間[ \t]*)?\d+分)(?=[(（:：\s])", header, re.MULTILINE)
        fares = re.findall(r"片道\s*[:：]\s*([0-9,]+)\s*円", header)
        if len(durations) != 1 or len(fares) != 1:
            raise ValueError("ambiguous_route_duration_or_fare")
        duration = re.fullmatch(r"(?:(\d+)時間[ \t]*)?(\d+)分", durations[0])
        minutes = int(duration[1] or 0)*60+int(duration[2])
        points = []
        previous = "line"
        for element in course["elements"]:
            if element["kind"] == "point":
                if previous != "line" or len(element["names"]) != 1:
                    raise ValueError("ambiguous_station_or_point_order")
                points.append({"station":element["names"][0].strip(),"line":None})
                previous = "point"
            elif element["kind"] == "line":
                if previous != "point" or len(element["names"]) != 1:
                    raise ValueError("ambiguous_line_or_point_order")
                text = element["names"][0].strip().splitlines()
                if not text or not text[0].strip():
                    raise ValueError("missing_line_name")
                points[-1]["line"] = text[0].strip()
                previous = "line"
            else:
                raise ValueError("unknown_route_element")
        if previous != "point" or len(points) < 2:
            raise ValueError("missing_route_endpoints")
        routes.append({"time":minutes,"fee":int(fares[0].replace(",","")),"route":points})
    return RawSearch.model_validate({"routes":routes})


DOM_COURSE_SCRIPT = """() => ({
    bodyText:document.body.innerText,
    courses:[...document.querySelectorAll('[data-testid="course-detail"]')].map(section=>{
        const body=section.querySelector('[data-testid="course-detail-body"]');
        const text=section.innerText;
        const bodyText=body ? body.innerText : '';
        const start=bodyText ? text.indexOf(bodyText) : -1;
        const points=[...section.querySelectorAll('[data-testid="course-detail-point"]')];
        return {
            headerText:start >= 0 ? text.slice(0,start) : '',
            elements:body ? [...body.querySelectorAll('[data-testid="course-detail-point"], [data-testid="course-detail-line"]')].map(node=>{
                const kind=node.getAttribute('data-testid')==='course-detail-point'?'point':'line';
                const selector=kind==='point'?'[data-testid="course-detail-point-station-name"]':'[data-testid="course-detail-line-name"]';
                return {kind,names:[...node.querySelectorAll(selector)].map(n=>n.innerText)};
            }) : [],
            pointCount:points.length,
            stationNames:[...section.querySelectorAll('[data-testid="course-detail-point-station-name"]')].map(n=>n.innerText.trim()),
            pointTimes:points.flatMap((point,pointIndex)=>['arrival','departure'].flatMap(kind=>{
                const selector='[data-testid="course-detail-point-'+kind+'-time"]';
                return [...point.querySelectorAll(selector)].map(node=>({pointIndex,kind,rawText:node.innerText,selector}));
            }))
        };
    })
})"""
