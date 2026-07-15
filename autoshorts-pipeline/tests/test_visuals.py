from src.visuals import score_candidate


def test_visual_candidate_scoring_prefers_relevant_vertical_media():
    relevant = {
        "candidate_id": "a", "title": "neutron star space animation", "tags": ["space", "star", "universe"],
        "width": 1080, "height": 1920, "media_type": "video",
    }
    irrelevant = {
        "candidate_id": "b", "title": "beer glass jewellery animal", "tags": ["beer", "ring", "squirrel"],
        "width": 1920, "height": 1080, "media_type": "video",
    }
    keywords = ["neutron", "star", "space"]
    negatives = ["beer", "jewellery", "animal"]
    assert score_candidate(relevant, keywords, negatives) > score_candidate(irrelevant, keywords, negatives)


def test_used_candidate_is_rejected():
    candidate = {"candidate_id": "used", "title": "ocean", "tags": ["ocean"], "width": 1080, "height": 1920, "media_type": "video"}
    assert score_candidate(candidate, ["ocean"], [], {"used"}) < 0
