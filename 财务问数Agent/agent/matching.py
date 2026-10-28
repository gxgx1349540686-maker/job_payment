import re
import unicodedata
from rapidfuzz import fuzz


def normalize(text):
    text = unicodedata.normalize("NFKC", str(text)).lower()
    return re.sub(r"[\s\-_—·（）()，,。:：/\\]", "", text).replace("泊寓home", "泊寓")


def candidates(buildings, project, building, limit=8):
    project, building = normalize(project), normalize(building)
    if not project and not building:
        return []
    ranked = []
    for row in buildings:
        p,b = normalize(row["project"]),normalize(row["building"])
        ps = fuzz.WRatio(project,p) if project else 0
        bs = fuzz.WRatio(building,b) if building else 0
        score = .55*ps+.45*bs if project and building else ps or bs
        if score>=42:
            ranked.append(dict(row,score=round(score,1),exact=bool(project and building and project==p and building==b)))
    ranked.sort(key=lambda r:r["score"],reverse=True)
    return ranked[:limit]
