"""Job Radar - Local Streamlit Review Queue and Application Dashboard."""

import html
from pathlib import Path

import streamlit as st

from radar.db import (
    get_companies_overview,
    get_distinct_company_stages,
    get_matches_for_dashboard,
    get_openings_for_company,
    get_pipeline_stats,
    init_db,
    update_match_feedback,
    update_match_status,
)
from radar.profile import build_profile, load_profile
from run import execute_pipeline, load_config, save_config

SENIORITY_OPTIONS = ["intern", "fresher", "junior", "entry-level", "new grad", "mid", "senior"]

st.set_page_config(page_title="Job Radar", page_icon="🎯", layout="wide", initial_sidebar_state="expanded")

# Custom Styling
st.markdown(
    """
<style>
    .main-header {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E293B;
        margin-bottom: 0.2rem;
    }
    .sub-header {
        color: #64748B;
        margin-bottom: 1.5rem;
    }
    .score-badge-high {
        background-color: #DCFCE7;
        color: #166534;
        font-weight: 700;
        padding: 4px 10px;
        border-radius: 9999px;
        font-size: 0.95rem;
        border: 1px solid #86EFAC;
    }
    .score-badge-med {
        background-color: #FEF3C7;
        color: #92400E;
        font-weight: 700;
        padding: 4px 10px;
        border-radius: 9999px;
        font-size: 0.95rem;
        border: 1px solid #FCD34D;
    }
    .score-badge-low {
        background-color: #F1F5F9;
        color: #475569;
        font-weight: 700;
        padding: 4px 10px;
        border-radius: 9999px;
        font-size: 0.95rem;
    }
    .job-card {
        background-color: #FFFFFF;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 18px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .source-tag {
        background-color: #EFF6FF;
        color: #1D4ED8;
        padding: 2px 8px;
        border-radius: 6px;
        font-size: 0.8rem;
        font-weight: 600;
        text-transform: uppercase;
    }
    .link-warning {
        background-color: #FEE2E2;
        color: #991B1B;
        padding: 2px 8px;
        border-radius: 6px;
        font-size: 0.8rem;
        font-weight: 600;
    }
</style>
""",
    unsafe_allow_html=True,
)

# Initialize Database
init_db()

# Sidebar: Pipeline Controls, Stats & Profile
with st.sidebar:
    st.title("🎯 Job Radar")
    st.caption("Personal Autonomous Job Discovery Agent")
    st.divider()

    stats = get_pipeline_stats()
    st.subheader("📊 Pipeline Stats")
    col_s1, col_s2 = st.columns(2)
    col_s1.metric("Tracked", stats["total_openings"])
    col_s2.metric("New Fits", stats["new_matches"])

    col_s3, col_s4 = st.columns(2)
    col_s3.metric("Applied", stats["applied"])
    col_s4.metric("Interviews", stats["interviewing"])

    # Monthly API spend
    spent_cents = stats.get("monthly_cost_cents", 0.0)
    spent_dollars = spent_cents / 100.0
    st.progress(min(1.0, spent_dollars / 5.0), text=f"Est. Monthly API Spend: ${spent_dollars:.2f} / $5.00")

    st.divider()

    # Manual trigger button
    if st.button("⚡ Run Discovery Pipeline Now", use_container_width=True, type="primary"):
        with st.spinner("Discovering openings, researching companies & scoring fit..."):
            res = execute_pipeline()
            st.success(f"Run completed! Found {res['new_openings']} new openings.")
            st.rerun()

    if stats.get("last_run"):
        lr = stats["last_run"]
        st.caption(f"Last run: {lr.get('finished_at') or lr.get('started_at')} ({lr.get('new_openings', 0)} new)")

    st.divider()
    with st.expander("👤 Target Candidate Profile"):
        profile = load_profile()
        st.write(f"**{profile.get('name')}**")
        st.write(f"*{profile.get('headline')}*")
        st.write("**Top Skills:**")
        st.write(", ".join(profile.get("skills", [])[:8]))
        st.write("**Roles:**")
        st.write(", ".join(profile.get("roles_sought", [])))


# Main Screen
st.markdown('<div class="main-header">Job Radar — Review Queue</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-header">Ranked early-stage startup & tech openings with verified 1-click apply links.</div>',
    unsafe_allow_html=True,
)

# Tabs for Application Status
tab_setup, tab_new, tab_saved, tab_applied, tab_interviewing, tab_companies, tab_archive = st.tabs(
    [
        "⚙️ Setup",
        f"📥 New Queue ({stats['new_matches']})",
        f"⭐ Saved ({stats['saved']})",
        f"🚀 Applied ({stats['applied']})",
        f"🎯 Interviewing ({stats['interviewing']})",
        "🏢 Companies",
        "🗄️ All / Archive",
    ]
)

with tab_setup:
    st.subheader("Set up your search")
    st.caption("Upload your resume once, tell Job Radar what you're targeting, and it handles the rest. No config files to edit.")

    if "setup_message" in st.session_state:
        _kind, _msg = st.session_state.pop("setup_message")
        getattr(st, _kind)(_msg)

    setup_config = load_config()
    setup_targets = setup_config.get("targets", {})
    setup_profile_preview = load_profile()

    if setup_profile_preview.get("resume_chars_extracted"):
        with st.expander("📄 What was extracted from your resume last time"):
            extraction_note = (
                " (looks readable)"
                if setup_profile_preview.get("resume_extraction_ok")
                else " (looks too short — see warning above)"
            )
            st.caption(f"{setup_profile_preview['resume_chars_extracted']} characters extracted" + extraction_note)
            st.text(setup_profile_preview.get("resume_raw_summary", "")[:1000] or "(nothing extracted)")

    with st.form("setup_form"):
        st.markdown("**Resume**")
        resume_file = st.file_uploader("Upload your resume (PDF)", type=["pdf"])
        resume_text_pasted = st.text_area(
            "Or paste your resume text directly — use this if the PDF doesn't parse well "
            "(scanned copies and some Canva/Word exports have no readable text layer)",
            height=100,
            placeholder="Paste resume text here as a fallback...",
        )
        col_a, col_b = st.columns(2)
        with col_a:
            github_username = st.text_input("GitHub username (optional)", autocomplete="username")
        with col_b:
            site_url = st.text_input("Portfolio / personal site (optional)", autocomplete="url")

        st.divider()
        st.markdown("**Roles you're targeting** — comma-separated, add as many as you like")
        roles_text = st.text_area(
            "Roles",
            value=", ".join(setup_targets.get("roles", [])),
            label_visibility="collapsed",
            height=70,
        )

        seniority_sel = st.multiselect(
            "Experience level",
            options=SENIORITY_OPTIONS,
            default=[s for s in setup_targets.get("seniority", []) if s in SENIORITY_OPTIONS],
        )

        max_years = st.number_input(
            "Max years of experience a role can ask for (roles requiring more are auto-filtered out)",
            min_value=0,
            max_value=20,
            value=int(setup_targets.get("max_years_experience") or 2),
        )

        min_score_to_store = st.slider(
            "Minimum fit score to keep (higher = fewer but higher-quality matches)",
            min_value=0,
            max_value=100,
            value=int(setup_config.get("min_score_to_store") or 60),
            step=5,
        )

        st.markdown("**Locations** — comma-separated; include 'remote' if that's okay")
        locations_text = st.text_area(
            "Locations",
            value=", ".join(setup_targets.get("locations", [])),
            label_visibility="collapsed",
            height=70,
        )

        st.markdown("**Dealbreakers** — comma-separated phrases to auto-reject")
        dealbreakers_text = st.text_area(
            "Dealbreakers",
            value=", ".join(setup_config.get("dealbreakers", [])),
            label_visibility="collapsed",
            height=70,
        )

        st.markdown("**Companies to avoid** — comma-separated")
        avoid_text = st.text_area(
            "Avoid companies",
            value=", ".join(setup_config.get("avoid_companies", [])),
            label_visibility="collapsed",
            height=50,
        )

        submitted = st.form_submit_button("💾 Save & Build Profile", type="primary", use_container_width=True)

    if submitted:
        resume_path = None
        if resume_file is not None:
            Path("data").mkdir(exist_ok=True)
            resume_path = "data/resume.pdf"
            with open(resume_path, "wb") as f:
                f.write(resume_file.getbuffer())

        parsed_roles = [r.strip() for r in roles_text.split(",") if r.strip()]
        pasted_text = resume_text_pasted.strip() or None

        with st.spinner("Building your profile from resume / GitHub / site..."):
            updated_profile = build_profile(
                resume_path=resume_path,
                resume_text_override=pasted_text,
                github=github_username or None,
                site=site_url or None,
                roles_sought=parsed_roles,
                seniority=seniority_sel,
            )

        setup_targets["roles"] = parsed_roles
        setup_targets["seniority"] = seniority_sel
        setup_targets["locations"] = [loc.strip() for loc in locations_text.split(",") if loc.strip()]
        setup_targets["max_years_experience"] = int(max_years)
        setup_config["targets"] = setup_targets
        setup_config["dealbreakers"] = [d.strip() for d in dealbreakers_text.split(",") if d.strip()]
        setup_config["avoid_companies"] = [a.strip() for a in avoid_text.split(",") if a.strip()]
        setup_config["min_score_to_store"] = int(min_score_to_store)
        save_config(setup_config)

        # Streamlit clears one-shot st.success()/st.warning() calls on the
        # st.rerun() below, so stash the resume-extraction diagnostic in
        # session_state to show it after the rerun instead of losing it.
        if resume_path or pasted_text:
            chars = updated_profile.get("resume_chars_extracted", 0)
            if pasted_text:
                st.session_state["setup_message"] = ("success", f"Saved! Using your pasted resume text ({chars} characters).")
            elif updated_profile.get("resume_extraction_ok"):
                st.session_state["setup_message"] = (
                    "success",
                    f"Saved! Extracted {chars} characters from your resume — "
                    f"skills found: {', '.join(updated_profile.get('skills', [])[:8]) or 'none yet'}.",
                )
            else:
                st.session_state["setup_message"] = (
                    "warning",
                    f"Saved, but only {chars} characters came out of that PDF — it may be a scanned/image-based "
                    "file or use a font pypdf can't read. Try the 'paste your resume text' box above instead.",
                )
        else:
            st.session_state["setup_message"] = ("success", "Saved! Click '⚡ Run Discovery Pipeline Now' in the sidebar.")

        st.rerun()

# Filters bar
f_col1, f_col2, f_col3 = st.columns([2, 1, 1])
with f_col1:
    search_query = st.text_input(
        "Search by company or role keyword",
        placeholder="e.g. AI, Backend, Cursor, Postman",
        label_visibility="collapsed",
        autocomplete="off",
    )
with f_col2:
    min_score = st.slider("Min Fit Score", 0, 100, 50, step=5, label_visibility="collapsed")
with f_col3:
    remote_only = st.checkbox("Remote Only", value=False)


def render_matches_list(status_filter: str):
    matches = get_matches_for_dashboard(status=status_filter, min_score=min_score, limit=100)

    # Client-side search and remote filter
    if search_query:
        q = search_query.lower()
        matches = [m for m in matches if q in m["title"].lower() or q in m["company_name"].lower()]

    if remote_only:
        matches = [m for m in matches if m["remote"]]

    if not matches:
        st.info("No openings found in this view. Click '⚡ Run Discovery Pipeline Now' in the sidebar or adjust your filter.")
        return

    st.caption(f"Showing {len(matches)} matching roles sorted by Fit Score.")

    for m in matches:
        score = m["score"]
        score_class = "score-badge-high" if score >= 80 else ("score-badge-med" if score >= 60 else "score-badge-low")
        link_healthy = m.get("status_head_ok", 1) == 1

        # Job titles, company names, locations and reasons come from external sources
        # (job boards, HN comments, search results) — escape before injecting as HTML.
        safe_source = html.escape(str(m["source"]))
        safe_title = html.escape(str(m["title"]))
        safe_company = html.escape(str(m["company_name"]))
        safe_location = html.escape(str(m["opening_location"] or ""))
        safe_reason = html.escape(str(m["reason"]))

        # Built as a single line (no embedded blank/whitespace-only lines): a blank
        # line in the middle of a Markdown HTML block terminates the block early,
        # so the remaining tags get rendered as escaped text instead of HTML.
        warning_badge = "" if link_healthy else '<span class="link-warning" style="margin-left: 8px;">⚠️ Verify Apply Link</span>'
        # normalize_location() already turns a remote opening's location into the
        # literal string "Remote", so only append the "(Remote)" suffix when the
        # location text doesn't already say so (avoids "Remote (Remote)").
        remote_suffix = " (Remote)" if m["remote"] and "remote" not in (m["opening_location"] or "").lower() else ""
        job_card_html = (
            f'<div class="job-card">'
            f'<div style="display: flex; justify-content: space-between; align-items: flex-start;"><div>'
            f'<span class="{score_class}">Fit Score: {score}/100</span>'
            f'<span class="source-tag" style="margin-left: 8px;">{safe_source}</span>'
            f"{warning_badge}"
            f'<h3 style="margin-top: 8px; margin-bottom: 4px; color: #0F172A;">{safe_title}</h3>'
            f'<div style="font-size: 1.05rem; font-weight: 600; color: #334155;">'
            f'{safe_company} · <span style="font-weight: 400; color: #64748B;">{safe_location}{remote_suffix}</span>'
            f"</div></div></div>"
            f'<div style="margin-top: 12px; padding: 10px; background-color: #F8FAFC; border-left: 4px solid #3B82F6; border-radius: 4px; color: #1E293B;">'
            f"<strong>Why it fits:</strong> {safe_reason}"
            f"</div></div>"
        )

        with st.container():
            st.markdown(job_card_html, unsafe_allow_html=True)

            # Action and Details row
            btn_col1, btn_col2, btn_col3, btn_col4, btn_col5 = st.columns([2, 1.5, 1.5, 1.5, 1.5])

            with btn_col1:
                st.link_button("↗ Direct Apply Link", m["apply_url"], use_container_width=True, type="primary")

            with btn_col2:
                if m["status"] != "applied":
                    if st.button("Mark Applied", key=f"app_{status_filter}_{m['match_id']}", use_container_width=True):
                        update_match_status(m["match_id"], "applied")
                        st.toast(f"Marked {m['company_name']} as Applied!")
                        st.rerun()
                else:
                    st.caption("✅ Applied")

            with btn_col3:
                if m["status"] == "new":
                    if st.button("Save for Later", key=f"save_{status_filter}_{m['match_id']}", use_container_width=True):
                        update_match_status(m["match_id"], "saved")
                        st.rerun()
                elif m["status"] == "saved":
                    if st.button("Move to New", key=f"unsave_{status_filter}_{m['match_id']}", use_container_width=True):
                        update_match_status(m["match_id"], "new")
                        st.rerun()

            with btn_col4:
                if m["status"] != "interviewing":
                    if st.button("Interviewing", key=f"int_{status_filter}_{m['match_id']}", use_container_width=True):
                        update_match_status(m["match_id"], "interviewing")
                        st.toast("Updated to Interviewing stage!")
                        st.rerun()
                else:
                    st.caption("🎉 In Interview")

            with btn_col5:
                if m["status"] != "skipped":
                    if st.button("Skip", key=f"skip_{status_filter}_{m['match_id']}", use_container_width=True):
                        update_match_status(m["match_id"], "skipped")
                        st.rerun()

            # Company Intelligence Card & Sources Expander
            with st.expander(f"🏢 Company Intelligence Card — {m['company_name']}"):
                col_c1, col_c2, col_c3 = st.columns(3)
                col_c1.write(f"**Stage:** {m.get('stage') or 'Early Stage'}")
                col_c2.write(f"**Funding:** {m.get('funding') or 'Undisclosed'}")
                col_c3.write(f"**Founders:** {m.get('founders') or 'Undisclosed'}")

                st.write(f"**Overview:** {m.get('company_summary') or 'Tech company developing innovative software.'}")

                # Verified Source URLs
                st.write("**Verified Evidence & Citations:**")
                all_sources = list(set((m.get("match_sources") or []) + (m.get("company_sources") or [])))
                if all_sources:
                    for s in all_sources:
                        st.markdown(f"- [{s}]({s})")
                else:
                    st.markdown(f"- [{m['apply_url']}]({m['apply_url']})")

                # Feedback widget
                fb_col1, fb_col2 = st.columns([1, 4])
                with fb_col1:
                    cur_fb = m.get("feedback")
                    if st.button("👍 Good Match", key=f"fb_good_{status_filter}_{m['match_id']}"):
                        update_match_feedback(m["match_id"], "good")
                        st.toast("Feedback recorded!")
                        st.rerun()
                    if st.button("👎 Poor Fit", key=f"fb_bad_{status_filter}_{m['match_id']}"):
                        update_match_feedback(m["match_id"], "bad")
                        st.toast("Feedback recorded!")
                        st.rerun()
                with fb_col2:
                    if cur_fb:
                        st.caption(f"Recorded Feedback: {cur_fb.capitalize()}")

            st.write("")  # Spacing


with tab_new:
    render_matches_list("new")

with tab_saved:
    render_matches_list("saved")

with tab_applied:
    render_matches_list("applied")

with tab_interviewing:
    render_matches_list("interviewing")

with tab_companies:
    st.subheader("Companies")
    st.caption("Every company Job Radar has researched, filterable by stage.")

    stages = get_distinct_company_stages()
    stage_filter = st.selectbox("Filter by stage", options=["all", *stages], index=0)

    companies = get_companies_overview(stage=stage_filter)

    if not companies:
        st.info("No companies researched yet. Run the discovery pipeline to populate this page.")
    else:
        st.caption(f"Showing {len(companies)} compan{'y' if len(companies) == 1 else 'ies'}.")

        for c in companies:
            safe_name = html.escape(str(c["name"]))
            safe_stage = html.escape(str(c.get("stage") or "Unknown stage"))
            safe_location = html.escape(str(c.get("location") or ""))
            safe_summary = html.escape(str(c.get("summary") or "No summary available yet."))
            safe_funding = html.escape(str(c.get("funding") or "Undisclosed"))
            safe_founders = html.escape(str(c.get("founders") or "Undisclosed"))

            with st.container():
                header = f"**{safe_name}** · {safe_stage}"
                if safe_location:
                    header += f" · {safe_location}"
                if c.get("best_score") is not None:
                    header += f" — best fit {c['best_score']}/100"
                st.markdown(header)
                st.caption(f"{c['opening_count']} opening(s) tracked, {c['match_count']} scored")
                st.write(safe_summary)

                with st.expander(f"Details & openings — {safe_name}"):
                    col1, col2 = st.columns(2)
                    col1.write(f"**Funding:** {safe_funding}")
                    col2.write(f"**Founders:** {safe_founders}")

                    if c.get("sources"):
                        st.write("**Sources:**")
                        for s in c["sources"]:
                            st.markdown(f"- [{s}]({s})")

                    openings = get_openings_for_company(c["id"])
                    if openings:
                        st.write("**Openings:**")
                        for o in openings:
                            safe_title = html.escape(str(o["title"]))
                            score_txt = f"{o['score']}/100" if o.get("score") is not None else "unscored"
                            st.markdown(f"- [{safe_title}]({o['apply_url']}) — {score_txt} ({o.get('match_status') or 'new'})")

                st.divider()

with tab_archive:
    render_matches_list("all")
