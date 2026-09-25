"""Job Radar - Local Streamlit Review Queue and Application Dashboard."""

from pathlib import Path

import streamlit as st

from radar.db import (
    get_matches_for_dashboard,
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
tab_setup, tab_new, tab_saved, tab_applied, tab_interviewing, tab_archive = st.tabs(
    [
        "⚙️ Setup",
        f"📥 New Queue ({stats['new_matches']})",
        f"⭐ Saved ({stats['saved']})",
        f"🚀 Applied ({stats['applied']})",
        f"🎯 Interviewing ({stats['interviewing']})",
        "🗄️ All / Archive",
    ]
)

with tab_setup:
    st.subheader("Set up your search")
    st.caption("Upload your resume once, tell Job Radar what you're targeting, and it handles the rest. No config files to edit.")

    setup_config = load_config()
    setup_targets = setup_config.get("targets", {})

    with st.form("setup_form"):
        st.markdown("**Resume**")
        resume_file = st.file_uploader("Upload your resume (PDF)", type=["pdf"])
        col_a, col_b = st.columns(2)
        with col_a:
            github_username = st.text_input("GitHub username (optional)")
        with col_b:
            site_url = st.text_input("Portfolio / personal site (optional)")

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

        with st.spinner("Building your profile from resume / GitHub / site..."):
            build_profile(resume_path=resume_path, github=github_username or None, site=site_url or None)

        setup_targets["roles"] = [r.strip() for r in roles_text.split(",") if r.strip()]
        setup_targets["seniority"] = seniority_sel
        setup_targets["locations"] = [loc.strip() for loc in locations_text.split(",") if loc.strip()]
        setup_targets["max_years_experience"] = int(max_years)
        setup_config["targets"] = setup_targets
        setup_config["dealbreakers"] = [d.strip() for d in dealbreakers_text.split(",") if d.strip()]
        setup_config["avoid_companies"] = [a.strip() for a in avoid_text.split(",") if a.strip()]
        save_config(setup_config)

        st.success("Saved! Click '⚡ Run Discovery Pipeline Now' in the sidebar to search with your new settings.")
        st.rerun()

current_tab = "new"
if tab_saved._is_selected if hasattr(tab_saved, "_is_selected") else False:
    current_tab = "saved"

# Filters bar
f_col1, f_col2, f_col3 = st.columns([2, 1, 1])
with f_col1:
    search_query = st.text_input(
        "Search by company or role keyword",
        placeholder="e.g. AI, Backend, Cursor, Postman",
        label_visibility="collapsed",
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

        with st.container():
            st.markdown(
                f"""
            <div class="job-card">
                <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                    <div>
                        <span class="{score_class}">Fit Score: {score}/100</span>
                        <span class="source-tag" style="margin-left: 8px;">{m["source"]}</span>
                        {"" if link_healthy else '<span class="link-warning" style="margin-left: 8px;">⚠️ Verify Apply Link</span>'}
                        <h3 style="margin-top: 8px; margin-bottom: 4px;">{m["title"]}</h3>
                        <div style="font-size: 1.05rem; font-weight: 600; color: #334155;">
                            {m["company_name"]} · <span style="font-weight: 400; color: #64748B;">{m["opening_location"]} {(" (Remote)" if m["remote"] else "")}</span>
                        </div>
                    </div>
                </div>
                <div style="margin-top: 12px; padding: 10px; background-color: #F8FAFC; border-left: 4px solid #3B82F6; border-radius: 4px;">
                    <strong>Why it fits:</strong> {m["reason"]}
                </div>
            </div>
            """,
                unsafe_allow_html=True,
            )

            # Action and Details row
            btn_col1, btn_col2, btn_col3, btn_col4, btn_col5 = st.columns([2, 1.5, 1.5, 1.5, 1.5])

            with btn_col1:
                st.link_button("↗ Direct Apply Link", m["apply_url"], use_container_width=True, type="primary")

            with btn_col2:
                if m["status"] != "applied":
                    if st.button("Mark Applied", key=f"app_{m['match_id']}", use_container_width=True):
                        update_match_status(m["match_id"], "applied")
                        st.toast(f"Marked {m['company_name']} as Applied!")
                        st.rerun()
                else:
                    st.caption("✅ Applied")

            with btn_col3:
                if m["status"] == "new":
                    if st.button("Save for Later", key=f"save_{m['match_id']}", use_container_width=True):
                        update_match_status(m["match_id"], "saved")
                        st.rerun()
                elif m["status"] == "saved":
                    if st.button("Move to New", key=f"unsave_{m['match_id']}", use_container_width=True):
                        update_match_status(m["match_id"], "new")
                        st.rerun()

            with btn_col4:
                if m["status"] != "interviewing":
                    if st.button("Interviewing", key=f"int_{m['match_id']}", use_container_width=True):
                        update_match_status(m["match_id"], "interviewing")
                        st.toast("Updated to Interviewing stage!")
                        st.rerun()
                else:
                    st.caption("🎉 In Interview")

            with btn_col5:
                if m["status"] != "skipped":
                    if st.button("Skip", key=f"skip_{m['match_id']}", use_container_width=True):
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
                    if st.button("👍 Good Match", key=f"fb_good_{m['match_id']}"):
                        update_match_feedback(m["match_id"], "good")
                        st.toast("Feedback recorded!")
                        st.rerun()
                    if st.button("👎 Poor Fit", key=f"fb_bad_{m['match_id']}"):
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

with tab_archive:
    render_matches_list("all")
