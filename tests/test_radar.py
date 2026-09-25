"""Unit and integration tests for Job Radar components."""

import pytest
import respx
from httpx import Response

from radar.db import (
    count_feedback_entries,
    get_companies_overview,
    get_discovered_ats_boards,
    get_distinct_company_stages,
    get_feedback_examples,
    get_matches_for_dashboard,
    get_openings_for_company,
    get_or_create_company,
    init_db,
    insert_match,
    insert_opening,
    opening_exists_by_hash,
    record_discovered_ats_board,
    update_company_research,
    update_match_feedback,
    update_match_status,
)
from radar.filter import apply_filters, matches_target_roles
from radar.normalize import (
    compute_dedupe_hash,
    infer_seniority,
    normalize_location,
    normalize_title,
)
from radar.notifier import _sanitize
from radar.score import FitScorer, compute_heuristic_score
from radar.sources.ats import ATSSource
from radar.sources.base import RawOpening
from radar.sources.yc import YCStartupSource


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


def test_matches_target_roles_rejects_non_target_role():
    # Regression: matches_target_roles previously fell through to `return True`
    # unconditionally, making targets.roles a no-op filter.
    target_roles = ["AI engineer", "backend engineer"]
    assert matches_target_roles("AI Engineer Intern", target_roles) is True
    assert matches_target_roles("Backend Engineer", target_roles) is True
    assert matches_target_roles("Site Reliability Engineer", target_roles) is False
    assert matches_target_roles("Growth Engineering Manager", target_roles) is False


def test_dry_run_does_not_write_db_or_spend_budget(tmp_path, monkeypatch):
    # Regression: --dry-run must never write to the DB or call paid research/scoring.
    import run as run_module
    from radar.db import get_connection
    from radar.sources.base import RawOpening

    monkeypatch.chdir(tmp_path)

    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        "targets:\n  roles: [backend engineer]\n  seniority: [intern, fresher, junior]\n"
        "dealbreakers: []\navoid_companies: []\nwatchlist_ats: {}\n"
    )
    profile_json = tmp_path / "profile.json"
    profile_json.write_text('{"name": "Test", "roles_sought": ["backend engineer"], "skills": ["python"]}')

    class FakeATSSource:
        name = "ats"

        def __init__(self, watchlist_config=None):
            pass

        def discover(self, cursor=None):
            return (
                [
                    RawOpening(
                        company_name="Acme",
                        company_domain="acme.com",
                        title="Backend Engineer",
                        apply_url="https://acme.com/apply",
                        source="ats",
                        location="Remote",
                        remote=True,
                    )
                ],
                None,
            )

    def research_should_not_be_called(*args, **kwargs):
        raise AssertionError("dry-run must not call company research")

    def score_should_not_be_called(*args, **kwargs):
        raise AssertionError("dry-run must not call paid scoring")

    monkeypatch.setattr(run_module, "ATSSource", FakeATSSource)
    monkeypatch.setattr(run_module.CompanyResearcher, "research_company", research_should_not_be_called)
    monkeypatch.setattr(run_module.FitScorer, "score_fit", score_should_not_be_called)
    monkeypatch.setattr(run_module, "check_apply_link", lambda url: True)

    result = run_module.execute_pipeline(
        config_path=str(config_yaml),
        profile_path=str(profile_json),
        dry_run=True,
        single_source="ats",
    )

    assert result["new_openings"] == 1
    # DB file should exist (schema init) but contain no companies/openings/matches.
    conn = get_connection(tmp_path / "db.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM openings").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM api_usage").fetchone()[0] == 0
    conn.close()


def test_notifier_sanitize_strips_injection_characters():
    dirty = 'Acme"); Remove-Item C:\\ -Recurse `$env:evil'
    clean = _sanitize(dirty)
    assert '"' not in clean
    assert "`" not in clean
    assert "$" not in clean
    assert "'" not in clean


def test_yc_source_never_crawls_directly():
    # Hard rule: YC/Wellfound must only ever be reached through Tavily search
    # results, never crawled directly.
    source = YCStartupSource()
    openings, cursor = source.discover(cursor="anything")
    assert openings == []
    assert cursor == "anything"


def test_heuristic_score_reason_requires_full_role_phrase_match():
    # Regression: the role-match bonus used to credit a title as matching "AI
    # Engineer" whenever it merely contained the generic word "engineer", even
    # with no "ai" anywhere in the title. It must now require every word in the
    # target role phrase to appear.
    profile = {
        "roles_sought": ["AI Engineer", "Backend Engineer"],
        "skills": [],
    }
    config = {"targets": {}}

    unrelated = compute_heuristic_score(
        profile=profile,
        config=config,
        opening_title="Software Engineer, Billing",
        opening_location=None,
        is_remote=False,
        company_name="BigCo",
        company_research={},
        apply_url="https://bigco.com/apply",
    )
    assert "Direct match with target role 'ai engineer'" not in unrelated.reason
    assert "Direct match with target role 'backend engineer'" not in unrelated.reason

    matching = compute_heuristic_score(
        profile=profile,
        config=config,
        opening_title="AI Engineer Intern",
        opening_location=None,
        is_remote=False,
        company_name="GoodAI",
        company_research={},
        apply_url="https://goodai.com/apply",
    )
    assert "Direct match with target role 'ai engineer'" in matching.reason


def test_companies_overview_and_stage_filter(test_db):
    acme_id = get_or_create_company("Acme", "acme.com", db_path=test_db)
    update_company_research(
        acme_id,
        stage="Seed",
        funding="$2M",
        founders="Jane Doe",
        summary="Acme builds widgets.",
        sources=["https://acme.com"],
        db_path=test_db,
    )
    acme_opening = insert_opening(
        company_id=acme_id,
        title="Backend Engineer",
        seniority="junior",
        location="Remote",
        remote=True,
        apply_url="https://acme.com/apply",
        source="ats",
        dedupe_hash="companies-hash-1",
        db_path=test_db,
    )
    insert_match(
        opening_id=acme_opening,
        score=85,
        reason="great fit",
        sources=["https://acme.com/apply"],
        db_path=test_db,
    )

    beta_id = get_or_create_company("Beta", "beta.com", db_path=test_db)
    update_company_research(
        beta_id,
        stage="Series A",
        funding="$10M",
        founders="John Smith",
        summary="Beta builds gadgets.",
        sources=["https://beta.com"],
        db_path=test_db,
    )
    insert_opening(
        company_id=beta_id,
        title="AI Engineer",
        seniority="intern",
        location="India",
        remote=False,
        apply_url="https://beta.com/apply",
        source="ats",
        dedupe_hash="companies-hash-2",
        db_path=test_db,
    )

    assert set(get_distinct_company_stages(db_path=test_db)) == {"Seed", "Series A"}

    overview = get_companies_overview(db_path=test_db)
    assert len(overview) == 2
    # Sorted by best_score desc: Acme (scored 85) before Beta (unscored).
    assert overview[0]["name"] == "Acme"
    assert overview[0]["opening_count"] == 1
    assert overview[0]["match_count"] == 1
    assert overview[0]["best_score"] == 85
    assert overview[1]["name"] == "Beta"
    assert overview[1]["best_score"] is None

    seed_only = get_companies_overview(stage="Seed", db_path=test_db)
    assert [c["name"] for c in seed_only] == ["Acme"]

    beta_openings = get_openings_for_company(beta_id, db_path=test_db)
    assert len(beta_openings) == 1
    assert beta_openings[0]["title"] == "AI Engineer"
    assert beta_openings[0]["score"] is None
