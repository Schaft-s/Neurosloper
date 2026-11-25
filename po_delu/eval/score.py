import json
from pathlib import Path
root=Path(__file__).resolve().parent
gold=json.loads((root/"labels.json").read_text())
for name in ["skill_run","plain_run"]:
    output=json.loads((root/(name+".json")).read_text())
    assert set(output)==set(gold)
    pairs=[(label,output[k]["decision"]) for k,label in gold.items()]
    print(name,{"tp":pairs.count(("flag","flag")),"fp":sum(a!="flag" and b=="flag" for a,b in pairs),
                "fn":sum(a=="flag" and b!="flag" for a,b in pairs),"match":sum(a==b for a,b in pairs),"n":len(pairs)})
annotations=json.loads((root/"contextual_labels.json").read_text())
for name in ["contextual_skill","contextual_plain"]:
    output=json.loads((root/(name+".json")).read_text())
    assert set(annotations)==set(output)
    matches=exact=positives=0
    for k,v in annotations.items():
        expected=next(iter(v),"keep")
        matches+=output[k]["decision"]==expected
        if expected=="flag":
            positives+=1
            exact+=v[expected]==output[k]["quote"]
    print(name,dict(match=matches,n=len(annotations),exact_positive_quotes=exact,positive_quotes=positives))
gold=json.loads((root/"mixed_labels.json").read_text())
for name in ["mixed_skill","mixed_plain"]:
    out=json.loads((root/(name+".json")).read_text())
    actual={(k,q) for k,quotes in out.items() for q in quotes}
    expected={(k,q) for k,quotes in gold.items() for q in quotes}
    print(name,dict(tp=len(actual&expected),fp=len(actual-expected),fn=len(expected-actual)))
