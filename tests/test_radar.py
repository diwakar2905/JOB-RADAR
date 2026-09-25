"""Unit and integration tests for Job Radar components."""

import pytest
import respx
from httpx import Response

from radar.db import (
    get_matches_for_dashboard,
    get_or_create_company,
    init_db,
    insert_match,
    insert_opening,
    opening_exists_by_hash,
    update_match_status,
)
from radar.filter import apply_filters
from radar.normalize import (
    compute_dedupe_hash,
    infer_seniority,
    normalize_location,
    normalize_title,
)
from radar.score import compute_heuristic_score
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
