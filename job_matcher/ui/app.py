"""
Job Matcher — Streamlit UI
Talks exclusively to the orchestrator at POST /run and GET /agents.
"""

import base64
import os
import time

import httpx
import streamlit as st

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")

st.set_page_config(
    page_title="Job Matcher AI",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
st.markdown("""
<style>
.score-bar-wrap { background:#e9ecef; border-radius:6px; height:10px; margin-top:4px; }
.score-bar      { height:10px; border-radius:6px; }
.agent-dot      { display:inline-block; width:10px; height:10px;
                  border-radius:50%; margin-right:6px; }
.tag            { display:inline-block; background:#e8f4fd; color:#1a73e8;
                  border-radius:12px; padding:2px 10px; font-size:0.78rem;
                  margin:2px 2px; }
.missing-tag    { background:#fdecea; color:#d32f2f; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def score_color(s: float) -> str:
    if s >= 75:
        return "#2e7d32"
    if s >= 50:
        return "#f57c00"
    return "#c62828"


def score_bar(label: str, value: float) -> None:
    color = score_color(value)
    st.markdown(
        f"**{label}** — {value:.0f} / 100"
        f'<div class="score-bar-wrap">'
        f'<div class="score-bar" style="width:{value}%;background:{color};"></div>'
        f"</div>",
        unsafe_allow_html=True,
    )


def tags(items: list, css_class: str = "tag") -> str:
    return " ".join(f'<span class="{css_class}">{t}</span>' for t in items)


def check_agents() -> dict:
    try:
        r = httpx.get(f"{ORCHESTRATOR_URL}/agents", timeout=5)
        return r.json()
    except Exception:
        return {}


def run_pipeline(payload: dict) -> dict:
    r = httpx.post(f"{ORCHESTRATOR_URL}/run", json=payload, timeout=300)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Sidebar — inputs
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("🎯 Job Matcher AI")
    st.caption("Multi-agent pipeline powered by Claude")
    st.divider()

    st.subheader("Your Profile")
    linkedin_url = st.text_input(
        "LinkedIn URL",
        placeholder="https://linkedin.com/in/yourname",
    )
    resume_text = st.text_area(
        "Paste resume text",
        height=160,
        placeholder="Copy-paste your resume here…",
    )
    uploaded_pdf = st.file_uploader("Or upload resume PDF", type=["pdf"])

    st.subheader("Job Preferences")
    desired_roles_raw = st.text_input(
        "Desired roles (comma-separated)",
        placeholder="Backend Engineer, Python Developer",
    )
    location = st.text_input("Location", placeholder="San Francisco, CA")
    remote_ok = st.checkbox("Remote OK", value=True)
    salary = st.number_input(
        "Salary expectation (USD / year)",
        min_value=0,
        max_value=1_000_000,
        step=5_000,
        value=0,
    )

    st.subheader("Output Settings")
    tone = st.selectbox("Cover letter tone", ["professional", "enthusiastic", "concise"])
    top_n = st.slider("Top N matches to return", 1, 10, 5)
    gen_n = st.slider("Generate full docs for top", 1, 5, 2)

    st.divider()
    run_btn = st.button("🚀 Run Pipeline", type="primary", use_container_width=True)

    # Agent status
    with st.expander("Agent status", expanded=False):
        if st.button("Refresh", key="refresh_agents"):
            st.session_state["agent_status"] = check_agents()
        status = st.session_state.get("agent_status", {})
        if not status:
            status = check_agents()
            st.session_state["agent_status"] = status
        for name, info in status.items():
            ok = "error" not in info
            dot = "🟢" if ok else "🔴"
            label = info.get("name", name) if ok else "unreachable"
            st.markdown(f"{dot} **{name}** — {label}")


# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------

if not run_btn:
    st.markdown("## Welcome to Job Matcher AI")
    st.markdown(
        "Fill in your profile on the left and click **Run Pipeline** to:\n\n"
        "1. Parse your profile\n"
        "2. Discover matching jobs\n"
        "3. Score and rank them\n"
        "4. Generate customised resumes, cover letters, gap analyses and interview prep kits"
    )
    st.info("Need all Docker containers running first — check Agent Status in the sidebar.")
    st.stop()

# ---------------------------------------------------------------------------
# Build request payload
# ---------------------------------------------------------------------------

desired_roles = [r.strip() for r in desired_roles_raw.split(",") if r.strip()]

if not linkedin_url and not resume_text and not uploaded_pdf:
    st.error("Provide at least a LinkedIn URL, resume text, or a PDF.")
    st.stop()

payload: dict = {
    "desired_roles": desired_roles,
    "location_override": location or None,
    "remote_ok": remote_ok,
    "salary_expectation_usd": salary if salary > 0 else None,
    "tone": tone,
    "top_n": top_n,
    "generate_docs_for_top_n": gen_n,
}
if linkedin_url:
    payload["linkedin_url"] = linkedin_url
if resume_text:
    payload["resume_text"] = resume_text
if uploaded_pdf:
    payload["resume_pdf_b64"] = base64.b64encode(uploaded_pdf.read()).decode()

# ---------------------------------------------------------------------------
# Call the orchestrator
# ---------------------------------------------------------------------------

with st.spinner("Pipeline running… this may take a minute"):
    start = time.time()
    try:
        result = run_pipeline(payload)
    except httpx.HTTPStatusError as e:
        st.error(f"Orchestrator error {e.response.status_code}: {e.response.text[:400]}")
        st.stop()
    except Exception as e:
        st.error(f"Could not reach orchestrator at {ORCHESTRATOR_URL}: {e}")
        st.stop()
    elapsed = time.time() - start

st.success(f"Pipeline complete in {elapsed:.1f}s")

profile       = result.get("profile", {})
top_matches   = result.get("top_matches", [])
resumes       = result.get("customized_resumes", [])
cover_letters = result.get("cover_letters", [])
gap_reports   = result.get("gap_reports", [])
interview_kits = result.get("interview_kits", [])
trace         = result.get("agent_trace", [])

# ---------------------------------------------------------------------------
# Agent trace
# ---------------------------------------------------------------------------

with st.expander("📋 Agent trace", expanded=False):
    for line in trace:
        st.markdown(f"`{line}`")

# ---------------------------------------------------------------------------
# Profile card
# ---------------------------------------------------------------------------

st.markdown("---")
st.markdown("## 👤 Parsed Profile")

col1, col2 = st.columns([2, 1])
with col1:
    st.markdown(f"### {profile.get('full_name', 'Unknown')}")
    st.markdown(f"*{profile.get('headline', profile.get('current_title', ''))}*")
    if profile.get("location"):
        st.markdown(f"📍 {profile['location']}")
    if profile.get("summary"):
        st.markdown(profile["summary"])
with col2:
    st.metric("Years of Experience", f"{profile.get('years_of_experience', 0):.1f}")
    skills = profile.get("skills", [])
    if skills:
        st.markdown("**Skills**")
        st.markdown(tags(skills[:20]), unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Top matches overview
# ---------------------------------------------------------------------------

st.markdown("---")
st.markdown(f"## 💼 Top {len(top_matches)} Job Matches")

for i, match in enumerate(top_matches):
    job = match.get("job", {})
    score = match.get("overall_score", 0)
    color = score_color(score)

    with st.container(border=True):
        h1, h2 = st.columns([4, 1])
        with h1:
            st.markdown(f"### {i+1}. {job.get('title', 'Unknown Role')} @ {job.get('company', '')}")
            st.markdown(
                f"📍 {job.get('location','')} &nbsp;|&nbsp; "
                f"{'🌐 Remote' if job.get('work_mode') == 'remote' else job.get('work_mode','')}"
                f"&nbsp;|&nbsp; Source: **{job.get('source','')}**",
                unsafe_allow_html=True,
            )
        with h2:
            st.markdown(
                f'<div style="text-align:center;font-size:2rem;color:{color};font-weight:700;">'
                f'{score:.0f}<span style="font-size:1rem;color:#666">/100</span></div>',
                unsafe_allow_html=True,
            )

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            score_bar("Skills", match.get("skills_match_score", 0))
        with c2:
            score_bar("Experience", match.get("experience_match_score", 0))
        with c3:
            score_bar("Location", match.get("location_match_score", 0))
        with c4:
            score_bar("Salary", match.get("salary_match_score", 0))

        if match.get("matching_skills"):
            st.markdown("**Matching skills:** " + tags(match["matching_skills"]), unsafe_allow_html=True)
        if match.get("missing_skills"):
            st.markdown("**Missing skills:** " + tags(match["missing_skills"], "tag missing-tag"), unsafe_allow_html=True)
        if match.get("match_rationale"):
            st.caption(match["match_rationale"])

        if job.get("apply_url"):
            st.link_button("Apply →", job["apply_url"])

# ---------------------------------------------------------------------------
# Per-match deep-dive tabs
# ---------------------------------------------------------------------------

if resumes or cover_letters or gap_reports or interview_kits:
    st.markdown("---")
    st.markdown("## 📄 Generated Documents")

    match_tabs = st.tabs(
        [
            f"{top_matches[i]['job'].get('title','Match '+str(i+1))} @ {top_matches[i]['job'].get('company','')}"
            for i in range(len(resumes))
        ]
        if top_matches else []
    )

    for i, tab in enumerate(match_tabs):
        with tab:
            sub = st.tabs(["📝 Resume", "✉️ Cover Letter", "🔍 Gap Analysis", "🎤 Interview Prep"])

            # ── Resume ──
            with sub[0]:
                res = resumes[i] if i < len(resumes) else {}
                if not res:
                    st.info("No resume generated for this match.")
                else:
                    r1, r2 = st.columns([3, 1])
                    with r1:
                        st.markdown(f"**{res.get('candidate_name','')}** — {res.get('target_title','')}")
                    with r2:
                        ats = res.get("ats_score_estimate", 0)
                        st.metric("ATS Score", f"{ats:.0f}/100")
                    if res.get("markdown_content"):
                        st.markdown(res["markdown_content"])
                    else:
                        st.markdown(f"**Summary:** {res.get('summary','')}")
                        for sec in res.get("sections", []):
                            st.markdown(f"### {sec.get('heading','')}")
                            st.markdown(sec.get("content", ""))
                    if res.get("keywords_added"):
                        st.markdown("**Keywords added:** " + tags(res["keywords_added"]), unsafe_allow_html=True)

            # ── Cover Letter ──
            with sub[1]:
                cl = cover_letters[i] if i < len(cover_letters) else {}
                if not cl:
                    st.info("No cover letter generated for this match.")
                else:
                    st.markdown(f"**Subject:** {cl.get('subject_line','')}")
                    st.markdown("---")
                    st.markdown(cl.get("body", ""))
                    st.markdown("---")
                    st.markdown(cl.get("call_to_action", ""))

            # ── Gap Analysis ──
            with sub[2]:
                gap = gap_reports[i] if i < len(gap_reports) else {}
                if not gap:
                    st.info("No gap analysis generated for this match.")
                else:
                    readiness = gap.get("overall_readiness_score", 0)
                    score_bar("Overall Readiness", readiness)
                    st.markdown("")

                    g1, g2 = st.columns(2)
                    with g1:
                        if gap.get("strengths"):
                            st.markdown("**Strengths**")
                            for s in gap["strengths"]:
                                st.markdown(f"- {s}")
                        if gap.get("quick_wins"):
                            st.markdown("**Quick Wins**")
                            for s in gap["quick_wins"]:
                                st.markdown(f"- {s}")
                    with g2:
                        if gap.get("experience_gaps"):
                            st.markdown("**Experience Gaps**")
                            for s in gap["experience_gaps"]:
                                st.markdown(f"- {s}")
                        if gap.get("long_term_actions"):
                            st.markdown("**Long-term Actions**")
                            for s in gap["long_term_actions"]:
                                st.markdown(f"- {s}")

                    if gap.get("skill_gaps"):
                        st.markdown("**Skill Gaps**")
                        for sg in gap["skill_gaps"]:
                            imp = sg.get("importance", "")
                            imp_color = {"critical": "🔴", "important": "🟡", "nice_to_have": "🟢"}.get(imp, "⚪")
                            with st.expander(f"{imp_color} {sg.get('skill','')} ({imp})"):
                                st.markdown(sg.get("how_to_acquire", ""))
                                if sg.get("estimated_time_weeks"):
                                    st.caption(f"Estimated time: {sg['estimated_time_weeks']} weeks")
                                if sg.get("resources"):
                                    st.markdown("Resources: " + ", ".join(sg["resources"]))

            # ── Interview Prep ──
            with sub[3]:
                kit = interview_kits[i] if i < len(interview_kits) else {}
                if not kit:
                    st.info("No interview prep generated for this match.")
                else:
                    st.markdown(f"### {kit.get('role_title','')} at {kit.get('company_name','')}")
                    if kit.get("company_research"):
                        with st.expander("Company Research"):
                            st.markdown(kit["company_research"])

                    if kit.get("questions"):
                        st.markdown("**Interview Questions**")
                        category_icons = {
                            "behavioral": "🧠", "technical": "💻", "situational": "🔮"
                        }
                        diff_colors = {"easy": "🟢", "medium": "🟡", "hard": "🔴"}
                        for q in kit["questions"]:
                            cat = q.get("category", "")
                            diff = q.get("difficulty", "")
                            icon = category_icons.get(cat, "❓")
                            dc = diff_colors.get(diff, "⚪")
                            with st.expander(f"{icon} {dc} {q.get('question','')}"):
                                st.markdown(f"**Sample answer:** {q.get('sample_answer','')}")
                                if q.get("tips"):
                                    st.markdown("**Tips:**")
                                    for tip in q["tips"]:
                                        st.markdown(f"- {tip}")

                    if kit.get("star_story_prompts"):
                        st.markdown("**STAR Story Prompts**")
                        for p in kit["star_story_prompts"]:
                            st.markdown(f"- {p}")

                    if kit.get("questions_to_ask_interviewer"):
                        st.markdown("**Questions to Ask the Interviewer**")
                        for p in kit["questions_to_ask_interviewer"]:
                            st.markdown(f"- {p}")

                    if kit.get("salary_negotiation_tips"):
                        with st.expander("💰 Salary Negotiation Tips"):
                            st.markdown(kit["salary_negotiation_tips"])

                    if kit.get("red_flags_to_watch"):
                        with st.expander("⚠️ Red Flags to Watch"):
                            for f in kit["red_flags_to_watch"]:
                                st.markdown(f"- {f}")
