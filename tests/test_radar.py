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
from radar.profile import (
    build_profile,
    extract_profile_with_llm,
    find_matching_keywords,
    load_profile,
    parse_resume_text,
    resume_text_looks_unreadable,
    save_profile,
)
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


def test_build_profile_overwrites_roles_sought_and_seniority(tmp_path):
    # Regression: the dashboard's Setup tab let you edit target roles/experience
    # level, but build_profile() never wrote them into profile.json — so the
    # sidebar and scoring kept using stale defaults no matter what you typed.
    profile_path = tmp_path / "profile.json"
    save_profile(
        {
            "name": "Test User",
            "roles_sought": ["AI Engineer", "Machine Learning Engineer"],
            "seniority": ["intern", "fresher"],
            "skills": ["Python"],
        },
        str(profile_path),
    )

    build_profile(
        roles_sought=["backend engineer", "data engineer"],
        seniority=["junior", "mid"],
        output=str(profile_path),
    )

    updated = load_profile(str(profile_path))
    assert updated["roles_sought"] == ["backend engineer", "data engineer"]
    assert updated["seniority"] == ["junior", "mid"]
    # Untouched fields survive.
    assert updated["name"] == "Test User"
    assert updated["skills"] == ["Python"]

    # Omitting roles_sought/seniority (e.g. CLI usage without the Setup form)
    # leaves whatever is already stored alone.
    build_profile(output=str(profile_path))
    unchanged = load_profile(str(profile_path))
    assert unchanged["roles_sought"] == ["backend engineer", "data engineer"]


class _FakeLLMClient:
    """Stands in for radar.llm.LLMClient in tests, no network involved."""

    def __init__(self, response_text=None):
        self._response_text = response_text

    def complete(self, prompt, system=None, prefer_quality=True):
        if self._response_text is None:
            return None, "none"
        return self._response_text, "claude"


def test_extract_profile_with_llm_parses_valid_json(monkeypatch):
    fake_json = (
        '{"headline": "AI engineer who ships", '
        '"skills": ["Rust", "gRPC"], '
        '"best_projects": [{"name": "Radar", "one_liner": "job matcher", "stack": ["python"], "url": null}], '
        '"proof_points": ["Built a 500-user tool solo"]}'
    )
    monkeypatch.setattr("radar.llm.LLMClient", lambda: _FakeLLMClient(fake_json))

    result = extract_profile_with_llm("some resume text mentioning Rust and gRPC")
    assert result is not None
    assert result["headline"] == "AI engineer who ships"
    assert "Rust" in result["skills"]
    assert result["best_projects"][0]["name"] == "Radar"
    assert result["proof_points"] == ["Built a 500-user tool solo"]


def test_extract_profile_with_llm_returns_none_when_no_llm_available(monkeypatch):
    monkeypatch.setattr("radar.llm.LLMClient", lambda: _FakeLLMClient(None))
    assert extract_profile_with_llm("some resume text") is None


def test_extract_profile_with_llm_returns_none_on_bad_json(monkeypatch):
    monkeypatch.setattr("radar.llm.LLMClient", lambda: _FakeLLMClient("not valid json {"))
    assert extract_profile_with_llm("some resume text") is None


def test_build_profile_merges_llm_extraction(tmp_path, monkeypatch):
    resume_file = tmp_path / "resume.txt"
    resume_file.write_text("Experienced with Python and Rust. Built Radar, a job matcher.")

    fake_json = (
        '{"headline": "Backend-leaning AI builder", '
        '"skills": ["Rust"], '
        '"best_projects": [{"name": "Radar", "one_liner": "job matcher", "stack": ["python"], "url": null}], '
        '"proof_points": ["Shipped to 500 users"]}'
    )
    monkeypatch.setattr("radar.llm.LLMClient", lambda: _FakeLLMClient(fake_json))

    profile_path = tmp_path / "profile.json"
    profile = build_profile(resume_path=str(resume_file), output=str(profile_path))

    assert profile["headline"] == "Backend-leaning AI builder"
    assert "Rust" in profile["skills"]
    assert "Python" in profile["skills"]  # keyword-spotting still runs alongside
    assert profile["best_projects"][0]["name"] == "Radar"
    assert profile["proof_points"] == ["Shipped to 500 users"]


def test_build_profile_falls_back_cleanly_when_llm_unavailable(tmp_path, monkeypatch):
    resume_file = tmp_path / "resume.txt"
    resume_file.write_text("Experienced with Python and FastAPI.")
    monkeypatch.setattr("radar.llm.LLMClient", lambda: _FakeLLMClient(None))

    profile_path = tmp_path / "profile.json"
    profile = build_profile(resume_path=str(resume_file), output=str(profile_path))

    # Keyword-spotting fallback still works; no crash, no LLM fields set.
    assert "Python" in profile["skills"]
    assert "FastAPI" in profile["skills"]
    assert profile["best_projects"] == []


def test_compute_heuristic_score_matches_skills_in_description_not_just_title():
    profile = {"roles_sought": [], "skills": ["kubernetes"], "best_projects": []}
    config = {"targets": {}}

    generic_title_result = compute_heuristic_score(
        profile=profile,
        config=config,
        opening_title="Platform Engineer",
        opening_location=None,
        is_remote=False,
        company_name="Acme",
        company_research={},
        apply_url="https://acme.com/apply",
        opening_description="You'll own our Kubernetes clusters and CI/CD pipelines.",
    )
    assert "Stack overlap in kubernetes" in generic_title_result.reason

    no_description_result = compute_heuristic_score(
        profile=profile,
        config=config,
        opening_title="Platform Engineer",
        opening_location=None,
        is_remote=False,
        company_name="Acme",
        company_research={},
        apply_url="https://acme.com/apply",
    )
    assert "kubernetes" not in no_description_result.reason.lower()


def test_compute_heuristic_score_credits_matching_project():
    profile = {
        "roles_sought": [],
        "skills": [],
        "best_projects": [{"name": "Radar", "stack": ["fastapi", "postgresql"]}],
    }
    config = {"targets": {}}

    result = compute_heuristic_score(
        profile=profile,
        config=config,
        opening_title="Backend Engineer",
        opening_location=None,
        is_remote=False,
        company_name="Acme",
        company_research={},
        apply_url="https://acme.com/apply",
        opening_description="Build APIs with FastAPI backed by PostgreSQL.",
    )
    assert "Project 'Radar'" in result.reason


def test_insert_opening_is_idempotent_on_duplicate_dedupe_hash(test_db):
    # Regression: a run that hit the same dedupe_hash twice (two raw items
    # from the same or different sources hashing identically) crashed the
    # entire pipeline with sqlite3.IntegrityError, losing every match already
    # stored earlier in that run. insert_opening/insert_match must be
    # idempotent instead: return the existing row's id rather than raising.
    company_id = get_or_create_company("Ramp", "ramp.com", db_path=test_db)
    dedupe_hash = "duplicate-hash-1"

    first_opening_id = insert_opening(
        company_id=company_id,
        title="Backend Engineer",
        seniority="junior",
        location="Remote",
        remote=True,
        apply_url="https://ramp.com/apply/1",
        source="ats",
        dedupe_hash=dedupe_hash,
        db_path=test_db,
    )
    first_match_id = insert_match(
        opening_id=first_opening_id,
        score=80,
        reason="first pass",
        sources=["https://ramp.com/apply/1"],
        db_path=test_db,
    )

    second_opening_id = insert_opening(
        company_id=company_id,
        title="Backend Engineer",
        seniority="junior",
        location="Remote",
        remote=True,
        apply_url="https://ramp.com/apply/2",
        source="ats",
        dedupe_hash=dedupe_hash,
        db_path=test_db,
    )
    second_match_id = insert_match(
        opening_id=second_opening_id,
        score=85,
        reason="second pass",
        sources=["https://ramp.com/apply/2"],
        db_path=test_db,
    )

    assert second_opening_id == first_opening_id
    assert second_match_id == first_match_id
    assert opening_exists_by_hash(dedupe_hash, db_path=test_db)


def test_pipeline_survives_one_bad_opening_and_still_finishes(tmp_path, monkeypatch):
    # Regression: an unhandled exception on a single opening used to abort
    # execute_pipeline entirely, so record_run_finish (and the run's stats)
    # never happened even though other openings had already been stored.
    import run as run_module
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
            good_one = RawOpening(
                company_name="Acme",
                company_domain="acme.com",
                title="Backend Engineer",
                apply_url="https://acme.com/apply",
                source="ats",
                location="Remote",
                remote=True,
            )
            good_two = RawOpening(
                company_name="Beta",
                company_domain="beta.com",
                title="Backend Engineer",
                apply_url="https://beta.com/apply",
                source="ats",
                location="Remote",
                remote=True,
            )
            return [good_one, good_two], None

    call_count = {"n": 0}
    real_research = run_module.CompanyResearcher.research_company

    def flaky_research(self, company_name, company_domain, db_path=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated unexpected failure on the first opening")
        kwargs = {} if db_path is None else {"db_path": db_path}
        return real_research(self, company_name, company_domain, **kwargs)

    monkeypatch.setattr(run_module, "ATSSource", FakeATSSource)
    monkeypatch.setattr(run_module.CompanyResearcher, "research_company", flaky_research)
    monkeypatch.setattr(run_module, "check_apply_link", lambda url: True)

    result = run_module.execute_pipeline(
        config_path=str(config_yaml),
        profile_path=str(profile_json),
        dry_run=False,
        single_source="ats",
    )

    # The second (good) opening was still stored and the run finished cleanly,
    # despite the first one blowing up.
    assert result["new_openings"] == 1
    assert len(result["errors"]) == 1
    assert "Acme" in result["errors"][0]


def test_find_matching_keywords_handles_symbol_suffixed_tokens():
    # Regression: plain \b fails on tokens ending in a non-word char like
    # "C++"/"C#" because \b needs a word/non-word transition that never
    # actually occurs at that boundary.
    text = "Experienced in C++ and C# for backend systems, plus Python."
    hits = find_matching_keywords(text, ["C++", "C#", "Python", "Rust"])
    assert hits == {"C++", "C#", "Python"}


def test_find_matching_keywords_is_case_insensitive_and_whole_token():
    text = "Built APIs with fastapi and used reactjs on the frontend."
    hits = find_matching_keywords(text, ["FastAPI", "React", "Go"])
    assert hits == {"FastAPI"}  # "React" must not fuzzy-match "reactjs"


def test_resume_text_looks_unreadable_for_short_extraction():
    assert resume_text_looks_unreadable("") is True
    assert resume_text_looks_unreadable("garbled \x00\x01") is True
    assert resume_text_looks_unreadable("x" * 199) is True
    assert resume_text_looks_unreadable("x" * 250) is False


def test_parse_resume_text_falls_back_to_byte_scan_when_pypdf_extracts_nothing(tmp_path, monkeypatch):
    # Simulates a scanned/image-only PDF where pypdf's extract_text() returns
    # empty strings for every page but the raw bytes still contain a
    # printable text run (e.g. from an embedded fallback layer).
    class FakePage:
        def extract_text(self):
            return ""

    class FakeReader:
        def __init__(self, path):
            self.pages = [FakePage()]
            self.is_encrypted = False

    monkeypatch.setitem(
        __import__("sys").modules,
        "pypdf",
        type("FakeModule", (), {"PdfReader": FakeReader}),
    )

    resume_pdf = tmp_path / "resume.pdf"
    padding = b"\x00" * 20
    embedded_text = b"Experienced Python and FastAPI backend engineer with three shipped projects"
    resume_pdf.write_bytes(b"%PDF-1.4\n" + padding + embedded_text + padding)

    text = parse_resume_text(str(resume_pdf))
    assert "Python" in text
    assert "FastAPI" in text


def test_parse_resume_text_falls_back_when_pypdf_raises_entirely(tmp_path, monkeypatch):
    # Regression: parse_resume_text() previously only caught ImportError, so a
    # corrupt file, unsupported encryption, or a malformed xref table (pypdf
    # raising PdfReadError/DependencyError/etc.) propagated uncaught, crashing
    # the whole Setup tab instead of falling back to the byte-scan.
    class ExplodingReader:
        def __init__(self, path):
            raise ValueError("simulated pypdf failure: malformed xref table")

    monkeypatch.setitem(
        __import__("sys").modules,
        "pypdf",
        type("FakeModule", (), {"PdfReader": ExplodingReader}),
    )

    resume_pdf = tmp_path / "resume.pdf"
    padding = b"\x00" * 20
    embedded_text = b"Experienced Python and FastAPI backend engineer with three shipped projects"
    resume_pdf.write_bytes(b"%PDF-1.4\n" + padding + embedded_text + padding)

    text = parse_resume_text(str(resume_pdf))  # must not raise
    assert "Python" in text
    assert "FastAPI" in text


def test_parse_resume_text_decrypts_empty_password_pdfs(tmp_path, monkeypatch):
    # Some resume exporters (Word, Canva, print-to-PDF drivers) set an empty
    # owner password to restrict editing; the content is still meant to be
    # readable, so parse_resume_text should try decrypt("") before giving up.
    class FakePage:
        def extract_text(self):
            return "Experienced Python and FastAPI backend engineer with three shipped projects." * 2

    class FakeReader:
        def __init__(self, path):
            self.pages = [FakePage()]
            self.is_encrypted = True
            self.decrypted = False

        def decrypt(self, password):
            assert password == ""
            self.decrypted = True

    monkeypatch.setitem(
        __import__("sys").modules,
        "pypdf",
        type("FakeModule", (), {"PdfReader": FakeReader}),
    )

    resume_pdf = tmp_path / "resume.pdf"
    resume_pdf.write_bytes(b"%PDF-1.4\n" + b"\x00" * 20)

    text = parse_resume_text(str(resume_pdf))
    assert "Python" in text
    assert "FastAPI" in text


def test_build_profile_uses_resume_text_override_and_flags_extraction_ok(tmp_path):
    output = tmp_path / "profile.json"
    resume_text = (
        "Jane Doe — backend engineer with Python, FastAPI, PostgreSQL, and Docker experience. "
        "Built and shipped three production services handling real user traffic, including a "
        "payments API processing thousands of requests per day and an internal analytics platform."
    )
    profile = build_profile(resume_text_override=resume_text, output=str(output))

    assert profile["resume_extraction_ok"] is True
    assert profile["resume_chars_extracted"] == len(resume_text)
    assert "Python" in profile["skills"]
    assert "FastAPI" in profile["skills"]
    assert "PostgreSQL" in profile["skills"]


def test_build_profile_flags_unreadable_extraction(tmp_path):
    output = tmp_path / "profile.json"
    profile = build_profile(resume_text_override="   ", output=str(output))
    assert profile["resume_extraction_ok"] is False
    assert profile["resume_chars_extracted"] == len("   ")


def test_min_score_to_store_discards_low_quality_matches(tmp_path, monkeypatch):
    # Regression: "quality over quantity" — a match scoring below the
    # configured min_score_to_store must be skipped entirely (not stored,
    # not counted, not alerted on), even though it would have passed every
    # other filter.
    import run as run_module
    from radar.db import get_connection
    from radar.sources.base import RawOpening

    monkeypatch.chdir(tmp_path)

    config_yaml = tmp_path / "config.yaml"
    config_yaml.write_text(
        "targets:\n  roles: [backend engineer]\n  seniority: [intern, fresher, junior]\n"
        "dealbreakers: []\navoid_companies: []\nwatchlist_ats: {}\n"
        "min_score_to_store: 95\n"
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

    monkeypatch.setattr(run_module, "ATSSource", FakeATSSource)
    monkeypatch.setattr(run_module, "check_apply_link", lambda url: True)

    result = run_module.execute_pipeline(
        config_path=str(config_yaml),
        profile_path=str(profile_json),
        dry_run=True,
        single_source="ats",
    )

    # Heuristic score for this opening tops out well under 95, so it must be
    # discarded rather than stored/counted.
    assert result["new_openings"] == 0

    conn = get_connection(tmp_path / "db.sqlite")
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 0
    conn.close()
