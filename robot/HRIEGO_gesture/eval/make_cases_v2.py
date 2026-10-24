"""cases_v2.json = cases.json (unchanged v1 set) + 2 left GUIDANCE cases. Run from eval/."""
import json
cases = json.load(open("cases.json"))
for k, r in enumerate(("GUEST", "REGULAR"), 1):
    cases.append({"case_id": f"guide_point_left_{k}", "state": "GUIDANCE", "rank": r, "scenario": "reception_meeting_left", "expected": "POINT_LEFT"})
json.dump(cases, open("cases_v2.json", "w"), ensure_ascii=False, indent=1)
print(len(cases))
