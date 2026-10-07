"""Editorial selection: which photographs go into each film.

Both films draw on the same restored library. Selection is a pure function of data: emotional scores,
technical quality, similar-shot pairs, the film profiles in config/films.json, and your pins. Nothing
here touches an image.
"""
import json

from . import catalog, paths

SCORES_FILE = paths.EDITORIAL_DIR / "scores.json"


def chapter_order(events: dict, results: list[dict]) -> list[str]:
    """Story order of the chapters: explicit in event_ranges.json, else order of first appearance."""
    if events.get("chapter_order"):
        return events["chapter_order"]
    seen = []
    for r in sorted(results, key=lambda r: r["capture_order"]):
        if r["category"] not in seen:
            seen.append(r["category"])
    return seen


def _quotas(total: int, available: dict[str, int], bridge_max: int) -> dict[str, int]:
    """Shots per chapter: tiny chapters (a bridge, like an invitation) get one; the rest share by size."""
    quotas = {c: 1 for c, n in available.items() if 0 < n <= bridge_max}
    big = {c: n for c, n in available.items() if n > bridge_max}
    remaining = max(total - sum(quotas.values()), 0)
    share = {c: remaining * n / max(sum(big.values()), 1) for c, n in big.items()}
    quotas.update({c: int(s) for c, s in share.items()})
    for c in sorted(big, key=lambda c: share[c] - int(share[c]), reverse=True)[: remaining - sum(int(s) for s in share.values())]:
        quotas[c] += 1
    return {c: min(q, available[c]) for c, q in quotas.items()}


def build(results: list[dict], metrics: dict, events: dict, duplicates: dict) -> dict:
    """Returns {"photos": {id: editorial record}, "films": {name: summary}, "chapter_order": [...]}."""
    profiles = catalog.load_config("films.json")
    stored = json.loads(SCORES_FILE.read_text(encoding="utf-8"))["scores"] if SCORES_FILE.exists() else {}
    candidates = [r for r in results if not r["override"].get("exclude")]
    order = chapter_order(events, results)

    sharp = sorted(metrics.get(r["id"], {}).get("sharpness") or 0 for r in candidates)
    rank = lambda v: sum(1 for s in sharp if s <= v) / max(len(sharp), 1)
    by_order = {r["capture_order"]: r["id"] for r in results}
    similar: dict[str, set[str]] = {}
    for p in duplicates.get("similar_shots", []):
        a, b = by_order.get(p["a"]), by_order.get(p["b"])
        if a and b:
            similar.setdefault(a, set()).add(b)
            similar.setdefault(b, set()).add(a)

    photos = {}
    for r in candidates:
        emotional = int(r["override"].get("emotional") or stored.get(r["id"]) or 3)
        quality = round(rank(metrics.get(r["id"], {}).get("sharpness") or 0), 3)
        photos[r["id"]] = {"emotional": emotional, "emotional_source": "you" if r["override"].get("emotional") else
                           "first pass" if r["id"] in stored else "default", "quality": quality, "films": {}}

    films = {}
    for name, prof in profiles["films"].items():
        w = prof["weights"]
        score = {i: w["emotional"] * p["emotional"] + w["quality"] * p["quality"] for i, p in photos.items()}
        pins = {r["id"]: r["override"].get(name) for r in candidates}
        available = {c: sum(1 for r in candidates if r["category"] == c and pins[r["id"]] != "out") for c in order}
        quotas = _quotas(round(prof["target_seconds"] / prof["nominal_shot_seconds"]), available, profiles["bridge_chapter_max_photos"])
        selected: set[str] = set()
        for chapter in order:
            pool = [r for r in candidates if r["category"] == chapter and pins[r["id"]] != "out"]
            chosen = [r["id"] for r in pool if pins[r["id"]] == "in"]
            for r in sorted(pool, key=lambda r: (-score[r["id"]], r["capture_order"])):
                if len(chosen) >= quotas.get(chapter, 0):
                    break
                if r["id"] in chosen:
                    continue
                if prof["avoid_similar_shots"] and similar.get(r["id"], set()) & set(chosen):
                    continue
                chosen.append(r["id"])
            selected |= set(chosen)
        ordered = [r for r in sorted(candidates, key=lambda r: (order.index(r["category"]), r["capture_order"])) if r["id"] in selected]
        for r in candidates:
            photos[r["id"]]["films"][name] = {"selected": r["id"] in selected, "pin": pins[r["id"]],
                                              "score": round(score[r["id"]], 2)}
        per_chapter = {c: sum(1 for r in ordered if r["category"] == c) for c in order}
        films[name] = {"label": prof["label"], "count": len(ordered), "by_chapter": per_chapter,
                       "estimated_seconds": round(len(ordered) * prof["nominal_shot_seconds"]),
                       "target_seconds": prof["target_seconds"], "order": [r["id"] for r in ordered]}
    return {"photos": photos, "films": films, "chapter_order": order}
