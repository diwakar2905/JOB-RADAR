"""Unit and integration tests for Job Radar components."""

import pytest
import respx
from httpx import Response

from radar.db import (
    count_feedback_entries,
    get_discovered_ats_boards,
    get_feedback_examples,
    get_matches_for_dashboard,
    get_or_create_company,
    init_db,
    insert_match,
    insert_opening,
    opening_exists_by_hash,
    record_discovered_ats_board,
    update_match_feedback,
    update_match_status,
)
from radar.filter import apply_filters
from radar.normalize import (
    compute_dedupe_hash,
    infer_seniority,
    normalize_location,
    normalize_title,
)
from radar.score import FitScorer, compute_heuristic_score
from radar.sources.ats import ATSSource
from radar.sources.base import RawOpening


@pytest.fixture
def test_db(tmp_path):
    db_file = tmp_path / "test_db.sqlite"
    init_db(db_file)
    return db_file


def test_db_lifecycle(test_db):
    company_id = get_or_create_company("TestCorp", "testcorp.com", db_path=test_db)
    assert company_id > 0

    h = compute_dedupe_hash("testcorp.com", "Software Engineer", "Remote")
    assert not opening_exists_by_hash(h, db_path=test_db)

    opening_id = insert_opening(
        company_id=company_id,
        title="Software Engineer",
        seniority="junior",
        location="Remote",
        remote=True,
        apply_url="https://testcorp.com/apply/1",
        source="test",
        dedupe_hash=h,
        db_path=test_db,
    )
    assert opening_id > 0
    assert opening_exists_by_hash(h, db_path=test_db)

    match_id = insert_match(
        opening_id=opening_id,
        score=88,
        reason="Strong Python stack match",
        sources=["https://testcorp.com/apply/1"],
        status="new",
        db_path=test_db,
    )
    assert match_id > 0

    matches = get_matches_for_dashboard(status="new", db_path=test_db)
    assert len(matches) == 1
    assert matches[0]["score"] == 88

    update_match_status(match_id, "applied", db_path=test_db)
    new_matches = get_matches_for_dashboard(status="new", db_path=test_db)
    applied_matches = get_matches_for_dashboard(status="applied", db_path=test_db)
    assert len(new_matches) == 0
    assert len(applied_matches) == 1


def test_normalization():
    assert normalize_title("Software Engineer - AI (Internship) [2026]") == "software engineer ai"

    loc, _is_remote = normalize_location("Bengaluru, Karnataka")
    assert "India" in loc

    _rem_loc, is_rem = normalize_location("Anywhere in the world", remote_hint=False)
    assert is_rem is True

    h1 = compute_dedupe_hash("postman.com", "Software Engineer", "Remote")
    h2 = compute_dedupe_hash("postman.com", "software engineer", "Remote")
    assert h1 == h2

    sen = infer_seniority("Deep Learning Research Intern")
    assert sen == "intern"
    sen_lead = infer_seniority("Staff Backend Architect")
    assert sen_lead == "staff"


def test_filter_dealbreakers():
    config = {
        "targets": {
            "roles": ["AI engineer", "backend engineer"],
            "seniority": ["intern", "fresher", "junior"],
        },
        "dealbreakers": ["unpaid", "on-site outside India", "5+ years"],
        "avoid_companies": ["Revature", "BlacklistedCo"],
    }

    # 1. Unpaid dealbreaker
    op_unpaid = RawOpening(
        company_name="StartupX",
        company_domain="startupx.com",
        title="AI Engineer Intern (Unpaid / Volunteer)",
        apply_url="https://startupx.com/apply",
        source="test",
    )
    res = apply_filters(op_unpaid, config)
    assert not res.passed
    assert "unpaid" in res.reason.lower()

    # 2. Avoided company
    op_avoid = RawOpening(
        company_name="Revature",
        company_domain="revature.com",
        title="Junior Software Engineer",
        apply_url="https://revature.com/apply",
        source="test",
    )
    res = apply_filters(op_avoid, config)
    assert not res.passed
    assert "avoid_companies" in res.reason.lower()

    # 3. On-site outside India
    op_sf = RawOpening(
        company_name="SFStartup",
        company_domain="sfstartup.com",
        title="AI Engineer",
        apply_url="https://sfstartup.com/apply",
        source="test",
        location="San Francisco, CA",
        remote=False,
    )
    res = apply_filters(op_sf, config)
    assert not res.passed
    assert "outside india" in res.reason.lower()

    # 4. Valid target match
    op_valid = RawOpening(
        company_name="GoodAI",
        company_domain="goodai.com",
        title="AI Engineer Intern",
        apply_url="https://goodai.com/apply",
        source="test",
        location="Bengaluru, India",
        remote=False,
    )
    res = apply_filters(op_valid, config)
    assert res.passed


def test_filter_max_years_experience_threshold():
    config = {
        "targets": {
            "roles": ["backend engineer"],
            "seniority": ["intern", "fresher", "junior"],
            "max_years_experience": 2,
        },
        "dealbreakers": [],
        "avoid_companies": [],
    }

    op_too_senior = RawOpening(
        company_name="BigCo",
        company_domain="bigco.com",
        title="Backend Engineer",
        apply_url="https://bigco.com/apply",
        source="test",
        description="Candidates should have 4 years of experience with distributed systems.",
    )
    res = apply_filters(op_too_senior, config)
    assert not res.passed
    assert "experience" in res.reason.lower()

    op_within_range = RawOpening(
        company_name="SmallCo",
        company_domain="smallco.com",
        title="Backend Engineer",
        apply_url="https://smallco.com/apply",
        source="test",
        description="1-2 years of experience preferred.",
    )
    res = apply_filters(op_within_range, config)
    assert res.passed


def test_heuristic_scoring():
    profile = {
        "name": "Diwakar Mishra",
        "roles_sought": ["AI Engineer", "Backend Engineer"],
        "skills": ["Python", "PyTorch", "FastAPI"],
        "seniority": ["intern", "fresher"],
    }
    config = {"targets": {"stages": ["seed", "series-a"]}}
    company_research = {"stage": "Seed", "sources": ["https://techcorp.ai"]}
    score_res = compute_heuristic_score(
        profile=profile,
        config=config,
        opening_title="AI Engineer Intern",
        opening_location="Bengaluru, India",
        is_remote=True,
        company_name="TechCorp",
        company_research=company_research,
        apply_url="https://techcorp.ai/jobs/1",
    )
    assert score_res.score >= 80
    assert len(score_res.sources) >= 1
    assert "https://techcorp.ai/jobs/1" in score_res.sources


@respx.mock
def test_ats_source_parses_greenhouse_lever_ashby():
    respx.get("https://boards-api.greenhouse.io/v1/boards/postman/jobs").mock(
        return_value=Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 1,
                        "title": "AI Engineer Intern",
                        "location": {"name": "Remote"},
                        "absolute_url": "https://job-boards.greenhouse.io/postman/jobs/1",
                        "updated_at": "2026-01-01T00:00:00Z",
                    }
                ]
            },
        )
    )
    respx.get("https://api.lever.co/v0/postings/hasura").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": "abc",
                    "text": "Backend Engineer",
                    "categories": {"location": "Bengaluru"},
                    "workplaceType": "on-site",
                    "hostedUrl": "https://jobs.lever.co/hasura/abc",
                    "createdAt": 1700000000000,
                }
            ],
        )
    )
    respx.get("https://api.ashbyhq.com/posting-api/job-board/cursor").mock(
        return_value=Response(
            200,
            json={
                "jobs": [
                    {
                        "id": "xyz",
                        "title": "Full-Stack Engineer",
                        "isRemote": True,
                        "jobUrl": "https://jobs.ashbyhq.com/cursor/xyz",
                        "publishedAt": "2026-01-01T00:00:00Z",
                    }
                ]
            },
        )
    )

    source = ATSSource(
        watchlist_config={
            "greenhouse": ["postman"],
            "lever": ["hasura"],
            "ashby": ["cursor"],
        }
    )
    openings, _cursor = source.discover()

    assert len(openings) == 3
    by_source = {o.source: o for o in openings}
    assert by_source["greenhouse"].title == "AI Engineer Intern"
    assert by_source["greenhouse"].remote is True
    assert by_source["lever"].apply_url == "https://jobs.lever.co/hasura/abc"
    assert by_source["ashby"].remote is True


@respx.mock
def test_ats_source_survives_one_failing_board():
    respx.get("https://boards-api.greenhouse.io/v1/boards/broken/jobs").mock(return_value=Response(500))
    source = ATSSource(watchlist_config={"greenhouse": ["broken"], "lever": [], "ashby": []})
    openings, _cursor = source.discover()
    assert openings == []


def test_dedupe_hash_reseen_opening_is_skipped(test_db):
    company_id = get_or_create_company("Hasura", "hasura.io", db_path=test_db)
    h = compute_dedupe_hash("hasura.io", "Backend Engineer", "Bengaluru")
    assert not opening_exists_by_hash(h, db_path=test_db)

    insert_opening(
        company_id=company_id,
        title="Backend Engineer",
        seniority="junior",
        location="Bengaluru",
        remote=False,
        apply_url="https://hasura.io/apply/1",
        source="lever",
        dedupe_hash=h,
        db_path=test_db,
    )
    assert opening_exists_by_hash(h, db_path=test_db)

    # A second discovery of the same opening must dedupe on the same hash.
    h_again = compute_dedupe_hash("hasura.io", "backend engineer", "Bengaluru")
    assert h_again == h
    assert opening_exists_by_hash(h_again, db_path=test_db)


def test_auto_discovery_ats_boards(test_db):
    assert get_discovered_ats_boards(db_path=test_db) == {"greenhouse": [], "lever": [], "ashby": []}

    record_discovered_ats_board("greenhouse", "cursor", db_path=test_db)
    record_discovered_ats_board("greenhouse", "cursor", db_path=test_db)  # duplicate, ignored
    record_discovered_ats_board("ashby", "modal", db_path=test_db)
    record_discovered_ats_board("carta", "unknown-platform", db_path=test_db)  # not an ATS we support

    discovered = get_discovered_ats_boards(db_path=test_db)
    assert discovered["greenhouse"] == ["cursor"]
    assert discovered["ashby"] == ["modal"]
    assert discovered["lever"] == []
    assert "carta" not in discovered


def test_feedback_calibration_examples(test_db):
    company_id = get_or_create_company("Acme", "acme.com", db_path=test_db)
    assert count_feedback_entries(db_path=test_db) == 0

    for i in range(6):
        opening_id = insert_opening(
            company_id=company_id,
            title=f"Role {i}",
            seniority="junior",
            location="Remote",
            remote=True,
            apply_url=f"https://acme.com/{i}",
            source="test",
            dedupe_hash=f"hash{i}",
            db_path=test_db,
        )
        match_id = insert_match(
            opening_id=opening_id,
            score=70 + i,
            reason=f"reason {i}",
            sources=["https://acme.com"],
            status="new",
            db_path=test_db,
        )
        update_match_feedback(match_id, "good" if i % 2 == 0 else "bad", db_path=test_db)

    assert count_feedback_entries(db_path=test_db) == 6
    examples = get_feedback_examples(limit=3, db_path=test_db)
    assert len(examples) == 3
    assert examples[0]["title"] == "Role 5"  # most recently updated first
    assert examples[0]["feedback"] in ("good", "bad")


def test_score_fit_includes_calibration_once_enough_feedback(test_db):
    scorer = FitScorer()

    # Below the calibration threshold: no block yet.
    assert scorer._get_calibration_block(db_path=test_db) == ""

    company_id = get_or_create_company("Acme", "acme.com", db_path=test_db)
    for i in range(5):
        opening_id = insert_opening(
            company_id=company_id,
            title=f"Role {i}",
            seniority="junior",
            location="Remote",
            remote=True,
            apply_url=f"https://acme.com/{i}",
            source="test",
            dedupe_hash=f"cal-hash{i}",
            db_path=test_db,
        )
        match_id = insert_match(
            opening_id=opening_id,
            score=70 + i,
            reason=f"reason {i}",
            sources=["https://acme.com"],
            status="new",
            db_path=test_db,
        )
        update_match_feedback(match_id, "good", db_path=test_db)

    # A fresh scorer picks up the now-sufficient feedback.
    fresh_scorer = FitScorer()
    block = fresh_scorer._get_calibration_block(db_path=test_db)
    assert "GOOD fit" in block
    assert "Role 4" in block
    # Cached for subsequent calls within the same scorer/run.
    assert fresh_scorer._get_calibration_block(db_path=test_db) is block
