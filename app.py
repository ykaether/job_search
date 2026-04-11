"""
Job Search Execution Tool
Goal: help decide what to do next, fast.
"""

import streamlit as st
import anthropic
import json
import os
import re
import io
import socket
import hashlib
import bcrypt
import requests
import xml.etree.ElementTree as ET
import pandas as pd
import uuid
from html import escape
from datetime import date, datetime, timedelta
from deep_translator import GoogleTranslator
import qrcode
import pdfplumber

@st.cache_data(show_spinner=False)
@st.cache_data(show_spinner=False, max_entries=1000)
def translate_to_jp(text: str) -> str:
    if not text or not text.strip():
        return text
    try:
        return GoogleTranslator(source="auto", target="ja").translate(text)
    except Exception:
        return text  # 失敗したら原文を返す

def t_text(text: str) -> str:
    """現在の言語設定に応じてテキストを翻訳（JPなら日本語に）"""
    if st.session_state.get("lang") == "JP":
        return translate_to_jp(text)
    return text

def t_list(items: list) -> list:
    if st.session_state.get("lang") == "JP":
        return [translate_to_jp(i) for i in items if i]
    return items

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"

def get_streamlit_port():
    try:
        from streamlit import config as _cfg
        return int(_cfg.get_option("server.port"))
    except Exception:
        return 8501

def get_public_base_url() -> str:
    """Prefer the current public host (Render/ngrok/proxy), fall back to local URL."""
    _ip = get_local_ip()
    _port = get_streamlit_port()
    _local_base = f"http://{_ip}:{_port}"
    _fwd_host = ""
    try:
        if hasattr(st, "context") and hasattr(st.context, "headers"):
            _fwd_host = (st.context.headers.get("x-forwarded-host", "")
                         or st.context.headers.get("x-original-host", ""))
    except Exception:
        pass
    if _fwd_host and _fwd_host not in (_ip, f"{_ip}:{_port}", "localhost", f"localhost:{_port}"):
        return f"https://{_fwd_host}"
    return _local_base

def make_qr_image(url: str):
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

BASE_DIR   = os.path.dirname(__file__)
USERS_FILE = os.path.join(BASE_DIR, "users.json")

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE) as f:
            return json.load(f)
    return [{"id": "default", "name": "Me"}]

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

def get_user_record(user_id: str):
    users = load_users()
    return next((u for u in users if u.get("id") == user_id), None)

def get_user_lang(user_id: str, default: str = "EN") -> str:
    user = get_user_record(user_id)
    if not user:
        return default
    lang = user.get("lang", default)
    return lang if lang in ("EN", "JP") else default

def save_user_lang(user_id: str, lang: str):
    if not user_id or user_id == "guest" or lang not in ("EN", "JP"):
        return
    users = load_users()
    changed = False
    for user in users:
        if user.get("id") == user_id and user.get("lang") != lang:
            user["lang"] = lang
            changed = True
            break
    if changed:
        save_users(users)

def user_data_file(user_id):
    # 既存のdata.jsonはdefaultユーザーのデータとして使う
    if user_id == "default":
        return os.path.join(BASE_DIR, "data.json")
    return os.path.join(BASE_DIR, f"data_{user_id}.json")

SESSIONS_FILE = os.path.join(BASE_DIR, "sessions.json")

def load_sessions() -> dict:
    if os.path.exists(SESSIONS_FILE):
        with open(SESSIONS_FILE) as f:
            return json.load(f)
    return {}

def save_sessions(sessions: dict):
    with open(SESSIONS_FILE, "w") as f:
        json.dump(sessions, f)

def create_session(user_id: str) -> str:
    token = uuid.uuid4().hex
    sessions = load_sessions()
    # clean up expired
    now = datetime.now()
    sessions = {t: v for t, v in sessions.items()
                if datetime.fromisoformat(v["expires"]) > now}
    sessions[token] = {
        "user_id": user_id,
        "expires": (now + timedelta(days=30)).isoformat()
    }
    save_sessions(sessions)
    return token

def validate_session(token: str):
    """Returns user_id if token is valid, else None."""
    if not token:
        return None
    sessions = load_sessions()
    entry = sessions.get(token)
    if not entry:
        return None
    if datetime.fromisoformat(entry["expires"]) < datetime.now():
        return None
    return entry["user_id"]

def delete_session(token: str):
    sessions = load_sessions()
    sessions.pop(token, None)
    save_sessions(sessions)

def get_current_user_id():
    return st.session_state.get("user_id", None)

def is_demo_user() -> bool:
    """Guest user is treated as a read-only public demo."""
    return get_current_user_id() == "guest"

def demo_notice():
    st.info("Demo mode: browsing is enabled, but saving, editing, and AI actions require login.", icon="🔒")

def login_as(user_id: str):
    """Set session state and issue a persistent URL token."""
    for _k in ["active_profile_id", "sidebar_profile_sel"]:
        st.session_state.pop(_k, None)
    st.session_state["user_id"] = user_id
    st.session_state["lang"] = get_user_lang(user_id, st.session_state.get("lang", "EN"))
    token = create_session(user_id)
    st.session_state["_session_token"] = token
    st.query_params["t"] = token

def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()

def check_password(user: dict, pw: str) -> bool:
    stored = user.get("password_hash", "")
    if not stored:
        return False
    try:
        return bcrypt.checkpw(pw.encode(), stored.encode())
    except Exception:
        return False

GUEST_USER = {"id": "guest", "name": "Guest"}

LOGO_PATH = os.path.join(os.path.dirname(__file__), "logo.png")

def render_login():
    """ログイン画面。メール+パスワード方式。"""
    _ll = T.get(st.session_state.get("lang", "EN"), T["EN"])

    # ── 言語切り替え ──────────────────────────────────────────
    _lc1, _lc2 = st.columns([5, 1])
    _lv = st.session_state.get("lang", "EN")
    _ln = _lc2.radio("", ["EN", "JP"], index=["EN","JP"].index(_lv),
                     horizontal=True, label_visibility="collapsed", key="login_lang_radio")
    if _ln != _lv:
        st.session_state["lang"] = _ln
        st.rerun()
    _ll = T.get(st.session_state.get("lang", "EN"), T["EN"])

    if os.path.exists(LOGO_PATH):
        import base64 as _b64l
        _logo_b64 = _b64l.b64encode(open(LOGO_PATH, "rb").read()).decode()
        st.markdown(f'<div style="text-align:center;margin-bottom:4px"><img src="data:image/png;base64,{_logo_b64}" width="220"/></div>', unsafe_allow_html=True)
    else:
        st.markdown('<h1 style="text-align:center">KoaFlux</h1>', unsafe_allow_html=True)
    st.markdown(
        f'<p style="text-align:center;font-size:16px;color:#64748b;margin:0 0 20px 0">{_ll["login_tagline"]}</p>',
        unsafe_allow_html=True
    )
    st.markdown("---")

    st.subheader(_ll["login_heading"])
    with st.form("login_form"):
        login_email = st.text_input("Email", key="login_email", placeholder="you@gmail.com").strip().lower()
        login_pw    = st.text_input("Password", type="password", key="login_pw")
        login_submitted = st.form_submit_button("Login", type="primary", use_container_width=True)

    if login_submitted:
        if not login_email:
            st.error(_ll["email_required"])
        else:
            users = load_users()
            login_user = next((u for u in users if u.get("email", "").lower() == login_email), None)
            if login_user is None:
                st.error(_ll["email_not_found"])
            elif not check_password(login_user, login_pw):
                st.error(_ll["wrong_password"])
            else:
                login_as(login_user["id"])
                st.rerun()

    st.markdown("---")

    with st.expander("📝 New Account / 新規登録"):
        _reg_email = st.text_input("Email", key="reg_email", placeholder="you@gmail.com").strip().lower()
        _reg_name  = st.text_input("Display name", key="reg_name", placeholder="e.g. Taro")
        _reg_pw1   = st.text_input("Password", type="password", key="reg_pw1")
        _reg_pw2   = st.text_input(_ll["confirm_pw"], type="password", key="reg_pw2")
        if st.button(_ll["register_btn"], key="reg_create_btn", type="primary"):
            if not _reg_email:
                st.error(_ll["email_required"])
            elif not _reg_name.strip():
                st.error(_ll["name_required"])
            elif not _reg_pw1:
                st.error(_ll["pw_required"])
            elif _reg_pw1 != _reg_pw2:
                st.error(_ll["pw_mismatch"])
            else:
                _reg_users = load_users()
                if any(u.get("email", "").lower() == _reg_email for u in _reg_users):
                    st.error(_ll["email_taken"])
                else:
                    _reg_id = hashlib.md5(_reg_email.encode()).hexdigest()[:12]
                    _reg_users.append({
                        "id":            _reg_id,
                        "name":          _reg_name.strip(),
                        "email":         _reg_email,
                        "password_hash": hash_password(_reg_pw1),
                        "lang":          st.session_state.get("lang", "EN"),
                    })
                    save_users(_reg_users)
                    login_as(_reg_id)
                    st.rerun()

    st.markdown("---")

    _guest_label = "👤 " + _ll["guest_label"]
    if st.button(_guest_label):
        guest_file = os.path.join(BASE_DIR, "data_guest.json")
        with open(guest_file, "w") as f:
            json.dump({"pipeline": [], "profiles": [], "discovered": []}, f)
        st.session_state["user_id"] = "guest"
        st.rerun()

    _mig_label = _ll["migrate_label"]
    with st.expander(_mig_label, expanded=False):
        st.caption(_ll["migrate_caption"])
        _all_users = load_users()
        _legacy = [u for u in _all_users if not u.get("email") and u["id"] != "guest"]
        if not _legacy:
            st.info(_ll["no_migrate"])
        else:
            _leg_name = st.selectbox("User", [u["name"] for u in _legacy], key="legacy_sel")
            _leg_user = next((u for u in _legacy if u["name"] == _leg_name), None)
            _has_old_pw = _leg_user and _leg_user.get("password_sha256")
            if not _has_old_pw:
                st.warning(_ll["no_pw_set"])
            else:
                _leg_old_pw = st.text_input("旧パスワード / Old password", type="password", key="leg_old_pw")
                _leg_email  = st.text_input("新しいメールアドレス / New email", key="leg_email", placeholder="you@gmail.com").strip().lower()
                _leg_pw1    = st.text_input(_ll["new_pw"], type="password", key="leg_pw1")
                _leg_pw2    = st.text_input(_ll["confirm_pw"], type="password", key="leg_pw2")
                _mig_btn = _ll["migrate_btn"]
                if st.button(_mig_btn, type="primary", key="leg_migrate_btn"):
                    import hashlib as _hl
                    _old_hash = _hl.sha256(_leg_old_pw.encode()).hexdigest()
                    if _old_hash != _leg_user["password_sha256"]:
                        st.error(_ll["wrong_password"])
                    elif not _leg_email:
                        st.error(_ll["email_required"])
                    elif any(u.get("email","").lower() == _leg_email for u in _all_users if u["id"] != _leg_user["id"]):
                        st.error(_ll["email_in_use"])
                    elif not _leg_pw1:
                        st.error(_ll["pw_required"])
                    elif _leg_pw1 != _leg_pw2:
                        st.error(_ll["pw_mismatch"])
                    else:
                        _lu = load_users()
                        for u in _lu:
                            if u["id"] == _leg_user["id"]:
                                u["email"]         = _leg_email
                                u["password_hash"] = hash_password(_leg_pw1)
                                u["lang"]          = st.session_state.get("lang", "EN")
                                u.pop("password_sha256", None)
                        save_users(_lu)
                        login_as(_leg_user["id"])
                        st.rerun()



PROFILE = """
[Example — replace with your own profile]
Japanese sales professional based in Southeast Asia. 6 years of B2B sales and account management experience, primarily in manufacturing and trading sectors. Managing a portfolio of Japanese and local corporate clients across Vietnam and Thailand. Strong at relationship building, negotiation, and coordinating between Japanese HQ and local teams. Native Japanese, business-level English, basic Vietnamese.
Currently based in Ho Chi Minh City. Targeting roles in Singapore or Bangkok — sales, account management, business development, or regional coordinator roles at Japanese companies, trading firms, or mid-size B2B businesses operating in ASEAN.
"""

SCREEN_PROMPT = """You are a job screener for a senior Japanese business professional targeting Singapore/APAC.

Candidate: Senior BD / Strategy / GTM / AI transformation / BizOps. 8+ years. Business-tech hybrid — targets tech, SaaS, consulting, Japanese MNC, or regional enterprise. NOT targeting local SMEs.

Screen each job. Return ONLY this JSON:
{{
  "results": [
    {{"id": "<id>", "verdict": "Worth|Skip", "reason": "<5 words max>"}}
  ]
}}

Worth if: role type fits (BD, strategy, GTM, transformation, solutions consulting, enablement, BizOps, partnerships, program management, commercial, AI adoption) AND company is plausibly regional/enterprise (tech, SaaS, MNC, consulting, startup with regional scope, Japanese company, global brand).

Skip if ANY of:
- Pure engineering / coding / data science / DevOps
- Pure HR / recruiting / payroll
- Pure supply chain / warehouse / logistics
- Pure finance / accounting / insurance sales
- Clinical healthcare / nursing
- Junior only (coordinator, assistant, entry-level specialist)
- Domestic-only SME with no regional scope (cleaning company, local retail chain, local F&B, property agent, local construction)
- Financial advisory / insurance / wealth management at non-bank SME

When company is unknown or ambiguous, lean Worth.

Jobs to screen:
{jobs}
"""

SEARCH_BRIEF_PROMPT = """You are a sharp career strategist specializing in senior professional job searches across Southeast Asia, particularly Singapore.

Candidate profile:
{profile}

Evaluated jobs so far (titles and scores only, no company names):
{evaluated_summary}

Based on what has scored well (Go/Stretch) and what hasn't (Skip), generate a concrete SEA-focused search strategy.

Return ONLY this JSON (no markdown, no explanation):
{{
  "insight": "<2-3 sentences: what pattern explains the high/low scores, and what this strategy is optimizing for>",
  "target_titles": ["<title 1>", "<title 2>", "<title 3>", "<title 4>", "<title 5>"],
  "avoid_titles": ["<title to avoid 1>", "<title to avoid 2>"],
  "company_types": ["<e.g. Series B-D tech, Japanese MNC SEA HQ, global consulting, SaaS vendor APAC>"],
  "must_keywords": ["<keyword 1>", "<keyword 2>", "<keyword 3>"],
  "avoid_keywords": ["<keyword 1>", "<keyword 2>"],
  "platform_searches": {{
    "LinkedIn": ["<search string 1>", "<search string 2>"],
    "JobStreet": ["<search string 1>"],
    "MyCareersFuture": ["<search string 1>"],
    "JobsDB": ["<search string 1>"],
    "Glints": ["<search string 1>"],
    "REERACOEN": ["<search string focused on Japanese-connected roles>"]
  }},
  "target_companies": ["<specific company or company type worth checking directly — e.g. Grab, Sea Group, Japanese trading company SEA HQ>"]
}}

SEA market context to apply:
- Singapore is the primary target: EP (Employment Pass) eligibility matters for senior roles
- Japanese MNCs with SEA HQs in Singapore are a strong channel for this candidate
- High-growth tech companies (Grab, Sea, Gojek, Shopee ecosystem) have BD/strategy/GTM roles
- Consulting firms (McKinsey, BCG, Bain, Kearney) and boutique strategy firms have APAC roles
- Global SaaS vendors (Salesforce, SAP, Workday) have APAC GTM/strategy roles
- REERACOEN specializes in Japanese-connected roles across SEA
- MyCareersFuture is Singapore government portal — good for EP-eligible senior roles
- Glints covers startups and growth-stage companies across SEA

Seniority calibration — IMPORTANT:
- Target the IC-to-lead layer, NOT the manager-of-managers layer
- Preferred title tiers: Lead, Senior, Team Lead, Principal, Associate Director, Assistant Manager — NOT Director or Head of
- "Manager" in Singapore often means individual contributor with ownership, not people manager — this is acceptable
- Avoid: Head of X, VP, Director (too senior to break in), Coordinator/Specialist (too junior)
- The sweet spot is: high-ownership individual contributor or small-team lead, regionally scoped, business/commercial/GTM focus

Be specific and actionable. All search strings should be copy-pasteable.
"""

EVAL_PROMPT = """You are a sharp career advisor for a senior Japanese business professional in Southeast Asia.

Candidate summary:
{profile}

Job Description:
{jd}

Return ONLY this JSON (no markdown, no explanation):
{{
  "decision": "Go|Stretch|Explore|Skip",
  "reason": "<one sentence: the single most important reason for this decision>",
  "trajectory_fit": <0|1|2>,
  "core_strength_match": <0|1|2>,
  "attraction": <0|1|2>,
  "competitiveness": <0|1|2>,
  "secondary_strength_used": "<null, or one sentence: whether L&D / enablement / digital platform advisory / program leadership play a supporting role>",
  "practical_constraint": "<null, or one sentence: any location / visa / timing barrier — null if no significant constraint>",
  "risk": "Low|Medium|High",
  "fit_bullets": ["<fit point 1>", "<fit point 2>", "<fit point 3>"],
  "main_risk": "<one sentence: biggest risk or gap>",
  "gap_note": "<one sentence: is the gap a GENUINE skill gap, or likely covered but not stated in profile?>",
  "direction_warning": "<null, or one sentence: flag if the role uses some of the candidate's strengths or is attractive but may pull away from the intended long-term path — state which direction it pulls toward>",
  "company_name": "<company name extracted from JD, or empty string if not found>",
  "job_title": "<job title extracted from JD, or empty string if not found>"
}}

Evaluate FOUR main axes independently. Do NOT collapse them into one blended judgment.

trajectory_fit (0=wrong direction, 1=partial/adjacent, 2=strong alignment):
  Target path: business-tech hybrid where AI accelerates business — BD, GTM, strategy, ops transformation, commercial advisory.
  2: clearly on this path.
  1: adjacent — could drift (e.g. ops-heavy without strategic scope, L&D without AI/business angle).
  0: pulls candidate into HR/L&D specialist, pure training delivery, or other tracks diverging from business-tech hybrid.
  IMPORTANT: A role can be doable or attractive yet still score 0. Do not conflate.

core_strength_match (0=not used, 1=partially used, 2=central to role):
  Core strengths: BizDev, strategy, AI-enabled execution, GTM, commercial advisory, BizOps.
  Score whether these are actually the daily substance of the role — not just whether the title sounds aligned.

attraction (0=low, 1=moderate, 2=high):
  Score based on: AI proximity, opportunity to build or apply AI skills, learning environment, technically strong team, work that is personally compelling.
  Independent of trajectory. A role can score high here and low on trajectory.

competitiveness (0=unlikely to win, 1=possible with strong application, 2=strong candidate):
  How realistically can the candidate win this role given JD requirements, seniority level, and profile?
  2: profile is a natural fit — requirements match well, no major gaps.
  1: plausible — some gaps exist but experience is transferable and hiring bar is realistic.
  0: structural mismatch — role requires credentials, domain expertise, or seniority level the candidate does not have.

Decision rules:
  Go      = trajectory_fit=2 AND core_strength_match>=1 AND competitiveness>=1
  Stretch = trajectory_fit>=1 AND (core_strength_match>=1 OR competitiveness>=1) — worth applying despite gaps
  Explore = role does not qualify for Go/Stretch, but has learning / networking / market intelligence value worth tracking
  Skip    = trajectory_fit=0 AND no explore value, OR clearly wrong industry/role, OR competitiveness=0 with no redeeming value

direction_warning: set whenever trajectory_fit<=1 but core_strength_match>=1 or attraction>=1.
  Signal: "you'd be good at this or want it, but it pulls you toward [X] instead of your target path."
  Set to null if trajectory_fit=2.

Calibration:
  Year requirements: "5+ years" = real bar ~3 years. Apply realistic hiring bar, not face value.
  Transferable experience: BD/sales/bizops/strategy counts toward CS, AM, Partnerships, Solutions Consulting.
  Profile coverage: if profile is brief, assume relevant experience exists but is unstated — flag in gap_note, don't penalize.
"""

STATUS_OPTIONS = ["Not Applied", "Applied", "Interview", "Offer", "Rejected", "Skip"]
SOURCE_DEFAULTS = ["LinkedIn", "Indeed", "REERACOEN", "JobStreet", "JobsDB", "MyCareersFuture", "Glints"]

def get_source_options(data=None):
    d = data or load_data()
    custom = d.get("custom_sources", [])
    return [""] + SOURCE_DEFAULTS + [s for s in custom if s not in SOURCE_DEFAULTS] + ["Other"]

def add_custom_source(name: str):
    d = load_data()
    custom = d.get("custom_sources", [])
    if name and name not in SOURCE_DEFAULTS and name != "Other" and name not in custom:
        custom.append(name)
        d["custom_sources"] = custom
        save_data(d)

DECISION_CHIP_COLOR = {"Go": "#27ae60", "Stretch": "#e67e22", "Explore": "#3b82f6", "Skip": "#e74c3c"}
RISK_BADGE = {"Low": "🟢 Low", "Medium": "🟡 Medium", "High": "🔴 High"}

def decision_chips(active):
    parts = []
    for d in ["Go", "Stretch", "Explore", "Skip"]:
        if d == active:
            c = DECISION_CHIP_COLOR[d]
            parts.append(f'<span style="background:{c};color:white;padding:2px 10px;border-radius:12px;font-weight:bold;font-size:13px">{d}</span>')
        else:
            parts.append(f'<span style="background:#f0f0f0;color:#aaa;padding:2px 10px;border-radius:12px;font-size:13px">{d}</span>')
    return " ".join(parts)

# V3: new axes
DIM_LABEL = {
    "trajectory_fit":     "Trajectory",
    "core_strength_match": "Core Strength",
    "attraction":          "Attraction",
    "competitiveness":     "Competitiveness",
}
# V2: previous axes
DIM_LABEL_V2 = {
    "trajectory_fit":       "Trajectory",
    "strength_utilization": "Strengths",
    "personal_attraction":  "Attraction",
    "practical_fit":        "Practical",
}
def _bar_color(ratio):
    """0.0–1.0 → 色"""
    if ratio < 0.35:   return "#ef4444"
    if ratio < 0.65:   return "#f59e0b"
    return "#22c55e"

def score_bar_html(score, max_val=2):
    """整数(0/1/2)または小数 → CSSプログレスバー"""
    try:
        v = float(score)
    except (TypeError, ValueError):
        v = 0.0
    ratio = max(0.0, min(1.0, v / max_val))
    pct   = round(ratio * 100)
    color = _bar_color(ratio)
    return (
        f'<div style="display:inline-block;background:#e5e7eb;border-radius:3px;'
        f'height:8px;width:80px;vertical-align:middle">'
        f'<div style="background:{color};width:{pct}%;height:100%;border-radius:3px"></div>'
        f'</div>'
    )

def total_bar_html(total, max_val=8):
    """0–8 → CSSプログレスバー（横幅広め）"""
    if total is None:
        return ""
    ratio = max(0.0, min(1.0, total / max_val))
    pct   = round(ratio * 100)
    color = _bar_color(ratio)
    return (
        f'<div style="display:inline-block;background:#e5e7eb;border-radius:3px;'
        f'height:8px;width:120px;vertical-align:middle">'
        f'<div style="background:{color};width:{pct}%;height:100%;border-radius:3px"></div>'
        f'</div>'
    )

# V3 (new)
DIMS     = ["trajectory_fit", "core_strength_match", "attraction", "competitiveness"]
# V2
DIMS_V2  = ["trajectory_fit", "strength_utilization", "personal_attraction", "practical_fit"]
# V1 legacy
DIMS_LEGACY = ["role_fit", "capability_fit", "market_value", "practical_fit"]

def _detect_version(entry):
    if entry.get("core_strength_match") is not None:   return "v3"
    if entry.get("trajectory_fit") is not None:         return "v2"
    return "v1"

def get_total(entry):
    v = _detect_version(entry)
    dims = {"v3": DIMS, "v2": DIMS_V2, "v1": DIMS_LEGACY}[v]
    scores = [entry.get(d) for d in dims]
    if any(s is None for s in scores):
        return None
    return sum(scores)

def render_dimensions(result):
    v = _detect_version(result)
    dim_map = {
        "v3": DIM_LABEL,
        "v2": DIM_LABEL_V2,
        "v1": {"role_fit": "Role Fit", "capability_fit": "Capability",
               "market_value": "Mkt Value", "practical_fit": "Practical"},
    }[v]

    total = get_total(result)
    if total is not None:
        score_100 = round(total / 8 * 100)
        st.markdown(
            f'<span style="font-size:13px;color:#666">Overall</span> '
            f'<strong>{score_100}pts</strong> &nbsp; {total_bar_html(total)}',
            unsafe_allow_html=True
        )
        st.markdown("---")

    # direction_warning 最優先
    warning = result.get("direction_warning")
    if warning and str(warning).lower() not in ("null", "none", ""):
        st.warning("⚠ **" + L["direction_alert"] + ":** " + warning)

    # 4軸ミニバー
    rows_html = []
    for key, label in dim_map.items():
        score = result.get(key) or 0
        bar = score_bar_html(score)
        rows_html.append(
            f'<div style="display:flex;align-items:center;gap:10px;margin:3px 0">'
            f'<span style="width:110px;font-size:12px;color:#555;flex-shrink:0">{label}</span>'
            f'{bar}'
            f'<span style="font-size:11px;color:#aaa;margin-left:6px">{score}/2</span>'
            f'</div>'
        )
    st.markdown("\n".join(rows_html), unsafe_allow_html=True)

    # V3フラグ表示
    if v == "v3":
        flags = []
        sec = result.get("secondary_strength_used")
        if sec and str(sec).lower() not in ("null", "none", ""):
            flags.append(f'<span style="font-size:11px;color:#3b82f6">◆ {sec}</span>')
        prac = result.get("practical_constraint")
        if prac and str(prac).lower() not in ("null", "none", ""):
            flags.append(f'<span style="font-size:11px;color:#f59e0b">⚑ {prac}</span>')
        if flags:
            st.markdown("<br>".join(flags), unsafe_allow_html=True)

def get_score_theme(score):
    if score is None or score < 0:
        return {"fg": "#94a3b8", "bg": "#f8fafc", "border": "#cbd5e1"}
    if score >= 75:
        return {"fg": "#16a34a", "bg": "#dcfce7", "border": "#22c55e"}
    if score >= 50:
        return {"fg": "#d97706", "bg": "#fef3c7", "border": "#f59e0b"}
    return {"fg": "#dc2626", "bg": "#fee2e2", "border": "#ef4444"}

def score_pill_html(score, prominent=False):
    theme = get_score_theme(score)
    text = f"{score}pts" if score is not None and score >= 0 else "—"
    font_size = "22px" if prominent else "16px"
    padding = "8px 16px" if prominent else "6px 12px"
    radius = "14px" if prominent else "12px"
    border = "3px" if prominent else "2px"
    shadow = "0 10px 24px rgba(15,23,42,0.10)" if prominent else "none"
    min_width = "112px" if prominent else "88px"
    return (
        f'<div style="display:inline-flex;align-items:center;justify-content:center;'
        f'min-width:{min_width};padding:{padding};border-radius:{radius};'
        f'background:{theme["bg"]};color:{theme["fg"]};border:{border} solid {theme["border"]};'
        f'font-size:{font_size};font-weight:800;letter-spacing:0.3px;box-shadow:{shadow}">'
        f'{text}</div>'
    )

def score_square_html(score, size=48, font_size=18):
    theme = get_score_theme(score)
    text = str(score) if score is not None and score >= 0 else "—"
    return (
        f'<div style="background:{theme["bg"]};color:{theme["fg"]};'
        f'font-size:{font_size}px;font-weight:800;width:{size}px;height:{size}px;'
        f'border-radius:10px;display:flex;align-items:center;justify-content:center;'
        f'flex-shrink:0;border:2px solid {theme["border"]}">{text}</div>'
    )
STATUS_BADGE = {
    "Not Applied": "⚪", "Applied": "🔵",
    "Interview": "🟡", "Offer": "🟢", "Rejected": "🔴", "Skip": "⛔"
}

# ── Data ──────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _load_data_from_disk(user_id: str, _cache_bust: int = 0):
    """Disk read cached by user_id. Invalidated via _cache_bust counter in save_data."""
    f = user_data_file(user_id)
    if os.path.exists(f):
        with open(f) as fp:
            d = json.load(fp)
        if "profiles" not in d:
            old_text = d.pop("profile", PROFILE)
            d["profiles"] = [{"id": "default", "name": "Default", "text": old_text}]
            d["active_profile_id"] = "default"
        return d
    return {
        "pipeline": [],
        "profiles": [{"id": "default", "name": "Default", "text": PROFILE}],
        "active_profile_id": "default",
    }

def load_data():
    uid = get_current_user_id()
    bust = st.session_state.get(f"_data_bust_{uid}", 0)
    return _load_data_from_disk(uid, bust)

def backup_file(user_id):
    return os.path.join(BASE_DIR, f"data_{user_id}_undo.json")

def save_data(data):
    if is_demo_user():
        return
    uid = get_current_user_id()
    f = user_data_file(uid)
    if os.path.exists(f):
        import shutil
        shutil.copy2(f, backup_file(uid))
    with open(f, "w") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    # Increment bust counter so _load_data_from_disk cache miss on next call
    bust_key = f"_data_bust_{uid}"
    st.session_state[bust_key] = st.session_state.get(bust_key, 0) + 1

def undo_save():
    uid = get_current_user_id()
    bf = backup_file(uid)
    if os.path.exists(bf):
        import shutil
        shutil.copy2(bf, user_data_file(uid))
        return True
    return False

# ── Evaluation ───────────────────────────────────────────────────

def get_profiles(data=None):
    d = data or load_data()
    return d.get("profiles", [{"id": "default", "name": "Default", "text": PROFILE}])

def get_active_profile_id(data=None):
    d = data or load_data()
    return st.session_state.get("active_profile_id") or d.get("active_profile_id", "default")

def get_profile(data=None):
    d = data or load_data()
    pid = get_active_profile_id(d)
    profiles = get_profiles(d)
    match = next((p for p in profiles if p["id"] == pid), None)
    return match["text"] if match else (profiles[0]["text"] if profiles else PROFILE)

def anonymize_profile(text: str, custom_replacements: dict = None) -> str:
    """プロフィールを匿名化して送信用テキストを生成"""
    # 自動ルール：地名・国名を地域名に抽象化
    AUTO_RULES = [
        # 都市名 → 地域名
        (r"\bHo Chi Minh( City)?\b", "Southeast Asia"),
        (r"\bHCMC\b", "Southeast Asia"),
        (r"\bSaigon\b", "Southeast Asia"),
        (r"\bHanoi\b", "Southeast Asia"),
        (r"\bVietnam\b", "Southeast Asia"),
        (r"\bBangkok\b", "Southeast Asia"),
        (r"\bThailand\b", "Southeast Asia"),
        (r"\bJakarta\b", "Southeast Asia"),
        (r"\bIndonesia\b", "Southeast Asia"),
        (r"\bKuala Lumpur\b", "Southeast Asia"),
        (r"\bKL\b", "Southeast Asia"),
        (r"\bMalaysia\b", "Southeast Asia"),
        (r"\bManila\b", "Southeast Asia"),
        (r"\bPhilippines\b", "Southeast Asia"),
        # Singapore は目標地なので残す
    ]
    result = text
    for pattern, replacement in AUTO_RULES:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    # ユーザー定義の置換
    if custom_replacements:
        for original, replacement in custom_replacements.items():
            if original.strip():
                result = result.replace(original, replacement)
    return result

def get_profile_name(pid, data=None):
    d = data or load_data()
    profiles = get_profiles(d)
    match = next((p for p in profiles if p["id"] == pid), None)
    return match["name"] if match else pid

def generate_search_brief(api_key, data):
    client = anthropic.Anthropic(api_key=api_key)
    pipeline = data.get("pipeline", [])
    evaluated = [e for e in pipeline if e.get("eval_decision")]

    # 会社名なし・タイトルとスコアのみ送信
    summary_lines = []
    for e in evaluated:
        decision = e.get("eval_decision", "")
        role = e.get("role", "")
        total = get_total(e)
        score_str = f"{round(total/8*100)}pts" if total is not None else "N/A"
        traj = e.get("trajectory_fit", e.get("role_fit", "?"))
        reason = e.get("eval_reason", "")
        summary_lines.append(f"- [{decision}] {role} | {score_str} | Trajectory:{traj} | {reason}")

    evaluated_summary = "\n".join(summary_lines) if summary_lines else "No evaluations yet."
    profile = get_anonymized_profile(data)

    prompt = SEARCH_BRIEF_PROMPT.format(profile=profile, evaluated_summary=evaluated_summary)
    msg = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    return json.loads(m.group() if m else raw)

def get_anonymized_profile(data=None):
    d = data or load_data()
    raw = get_profile(d)
    custom = d.get("anon_replacements", {})
    return anonymize_profile(raw, custom)

def job_id(url):
    return hashlib.md5(url.encode()).hexdigest()[:12]

def fetch_indeed_rss(query, angle, max_results=20):
    try:
        url = f"https://www.indeed.com/rss?q={requests.utils.quote(query)}&l=Singapore&sort=date&limit={max_results}"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        root = ET.fromstring(r.content)
        jobs = []
        for item in root.findall(".//item")[:max_results]:
            link  = item.findtext("link", "")
            title = item.findtext("title", "")
            company = ""
            for tag in item:
                if "company" in tag.tag.lower():
                    company = tag.text or ""
            pub = item.findtext("pubDate", "")[:16] if item.findtext("pubDate") else ""
            if link and title:
                jobs.append({"id": job_id(link), "title": title, "company": company,
                             "url": link, "source": "Indeed", "date": pub,
                             "search_angle": angle, "status": "new"})
        return jobs
    except Exception:
        return []

def fetch_mcf_jobs(query, angle, max_results=20):
    try:
        url = "https://api.mycareersfuture.gov.sg/v2/jobs"
        params = {"search": query, "sort": "new_posting_date", "limit": max_results}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        jobs = []
        for item in data.get("results", [])[:max_results]:
            link    = f"https://www.mycareersfuture.gov.sg/job/{item.get('uuid','')}"
            title   = item.get("title", "")
            company = item.get("postedCompany", {}).get("name", "")
            pub     = item.get("metadata", {}).get("createdAt", "")[:10]
            if title:
                jobs.append({"id": job_id(link), "title": title, "company": company,
                             "url": link, "source": "MyCareersFuture", "date": pub,
                             "search_angle": angle, "status": "new"})
        return jobs
    except Exception:
        return []

def get_discovery_angles(data):
    """プロフィール・Search Briefから検索角度を自動生成（シンプルなクエリ）"""
    brief = data.get("search_brief", {})
    core_titles = brief.get("target_titles", [])
    angles = []

    # Core: シンプルなタイトルのみ（ジョブボードはシンプルな方がヒットする）
    core_defaults = ["business development manager", "strategy operations manager",
                     "GTM manager", "digital transformation manager"]
    cores = core_titles[:4] if core_titles else core_defaults
    for t in cores[:4]:
        angles.append(("core", t))

    # Adjacent
    adj_defaults = ["solutions consultant", "revenue operations"]
    adjs = core_titles[4:6] if len(core_titles) > 4 else adj_defaults
    for t in adjs[:2]:
        angles.append(("adjacent", t))

    # Blind spot
    for t in ["chief of staff", "head of partnerships", "strategic initiatives manager"]:
        angles.append(("blind_spot", t))

    # Company-first
    angles.append(("company_first", "AI transformation Singapore"))
    angles.append(("company_first", "sales enablement APAC"))

    return angles

ANGLE_LABEL = {
    "core":         ("🟢", "Core match",    "Your primary target role type"),
    "adjacent":     ("🟡", "Adjacent",       "Related — worth a look"),
    "blind_spot":   ("🔵", "Blind spot",     "You may not search this — but it could fit"),
    "company_first":("🟠", "Company angle",  "Surfaced from target company type, not title"),
}

def run_discovery(data):
    """全角度から求人を取得して統合"""
    angles = get_discovery_angles(data)
    all_jobs = []
    existing_ids = {j["id"] for j in data.get("discovered", [])}

    for angle_type, query in angles:
        jobs  = fetch_indeed_rss(query, angle_type, max_results=10)
        jobs += fetch_mcf_jobs(query, angle_type, max_results=8)
        all_jobs += jobs

    # 重複除外（同セッション内・既存）
    seen = set()
    new_jobs = []
    for j in all_jobs:
        if j["id"] not in existing_ids and j["id"] not in seen:
            seen.add(j["id"])
            new_jobs.append(j)
    return new_jobs

def screen_jobs(jobs, api_key, data, batch_size=15):
    """タイトルのみでAIスクリーニング（Haiku使用・バッチ処理）"""
    client = anthropic.Anthropic(api_key=api_key)
    profile = get_anonymized_profile(data)

    for i in range(0, len(jobs), batch_size):
        batch = jobs[i:i + batch_size]
        jobs_text = "\n".join([f"- id:{j['id']} | {j['title']} @ {j['company']}" for j in batch])
        prompt = SCREEN_PROMPT.format(profile=profile[:800], jobs=jobs_text)
        try:
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1200,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = msg.content[0].text.strip()
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            result = json.loads(m.group() if m else raw)
            verdict_map = {r["id"]: r for r in result.get("results", [])}
            for j in batch:
                v = verdict_map.get(j["id"], {})
                j["screen_verdict"] = v.get("verdict", "Worth")  # パース失敗時はWorth扱い
                j["screen_reason"]  = v.get("reason", "")
        except Exception:
            for j in batch:
                j["screen_verdict"] = "Worth"
                j["screen_reason"]  = "screening error — review manually"
    return jobs

def fetch_linkedin_jd(url: str) -> dict:
    """Fetch job info from a LinkedIn job URL. Returns dict with company, title, jd, url, or error."""
    import requests
    from bs4 import BeautifulSoup as _BS

    m = re.search(r'linkedin\.com/jobs/(?:view|collections)/(?:[^/]+-)?(\d+)', url)
    if not m:
        m = re.search(r'(\d{8,})', url)
    if not m:
        return {"error": "URLからJob IDを取得できませんでした。LinkedIn求人のURLを貼り付けてください。"}

    job_id = m.group(1)
    guest_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xhtml;q=0.9,*/*;q=0.8",
    }
    try:
        r = requests.get(guest_url, headers=headers, timeout=10)
        if r.status_code != 200:
            return {"error": f"LinkedIn returned status {r.status_code}. JDを手動で貼り付けてください。"}
        soup = _BS(r.text, "html.parser")
        title_el   = soup.select_one("h2.top-card-layout__title")
        company_el = soup.select_one("a.topcard__org-name-link") or soup.select_one(".topcard__flavor a")
        desc_el    = soup.select_one("div.description__text") or soup.select_one("section.description")
        title   = title_el.text.strip()   if title_el   else ""
        company = company_el.text.strip() if company_el else ""
        jd      = desc_el.get_text(separator="\n").strip() if desc_el else ""
        logo_el  = soup.select_one("img.artdeco-entity-image")
        logo_b64 = ""
        if logo_el:
            _lsrc = (logo_el.get("src") or "").strip()
            if _lsrc.startswith("http"):
                try:
                    import base64 as _b64
                    _lr = requests.get(_lsrc, headers=headers, timeout=6)
                    if _lr.status_code == 200:
                        _mime = _lr.headers.get("Content-Type", "image/png").split(";")[0]
                        logo_b64 = f"data:{_mime};base64,{_b64.b64encode(_lr.content).decode()}"
                except Exception:
                    pass
        if not jd:
            return {"error": "JDの取得に失敗しました。LinkedInへのログインが必要な場合があります。手動で貼り付けてください。"}
        return {"company": company, "title": title, "jd": jd, "url": url, "logo_url": logo_b64}
    except Exception as e:
        return {"error": f"Fetch失敗: {e}"}


def evaluate(jd_text, api_key, lang="EN"):
    client = anthropic.Anthropic(api_key=api_key)
    prompt = EVAL_PROMPT.format(profile=get_anonymized_profile(), jd=jd_text[:4000])
    msg = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    return json.loads(m.group() if m else raw)

# ── App ──────────────────────────────────────────────────────────

# ── Translations ─────────────────────────────────────────────────
T = {
    "EN": {
        "title": "Opportunity Tracker",
        "tab_pipeline": "Pipeline",
        "tab_eval": "Evaluate & Apply",
        "tab_list": "List",
        "goal": "Goal & Funnel",
        "target_offers": "Target offers needed for decision",
        "filter": "Filter by Status",
        "sort": "Sort by",
        "notes_placeholder": "Notes, interview schedule, contact name...",
        "save_note": "Save note",
        "update": "Update",
        "re_eval": "Re-eval",
        "export": "Export CSV",
        "save_profile": "Save Profile",
        "api_settings": "API Settings",
        "my_profile": "My Profile",
        "goal_msg": lambda needed, gap, weeks, pace: f"Need **{needed} more offer(s)**. At ~7.5% conversion, approximately **{gap} more applications** needed. **{weeks} week(s)** left — pace of **{pace}/week**.",
        "goal_done": lambda t: f"Goal of {t} offer(s) reached. Ready to decide.",
        "conversion_note": "Conversion assumptions: Application→Interview 25%, Interview→Offer 30% (senior APAC reference)",
        "no_entries": "No roles tracked yet. Add via Evaluate tab or below.",
        "no_match": "No entries match the current filter.",
        "confirm_del": "Delete this entry?",
        "add_manual": "+ Add role manually",
        "company": "Company", "role": "Role", "status": "Status",
        "next_action": "Next Action", "url": "URL",
        "eval_detail": "Notes / Eval detail",
        "re_eval_help": "Re-evaluate with saved JD",
        "no_jd": "No JD saved",
        "company_hint": "Company",
        "role_hint": "Role",
        "url_hint": "URL (optional)",
        "source_hint": "Source",
        "job_id_hint": "Job ID (optional)",
        "salary_hint": "Salary (optional)",
        "jd_hint": "Paste the full JD here...",
        "evaluate_btn": "Evaluate",
        "why_fit": "Why you fit:",
        "main_risk": "Main risk:",
        "saved_ok": "Saved to pipeline",
        "skip_saved": "Saved as Skip",
        # login
        "email_required": "Please enter your email address",
        "email_not_found": "This email is not registered. Please sign up below.",
        "wrong_password": "Incorrect password",
        "name_required": "Please enter a display name",
        "pw_required": "Please enter a password",
        "pw_mismatch": "Passwords do not match",
        "email_taken": "This email is already registered",
        "login_tagline": "Paste a job URL. See your fit instantly.",
        "login_heading": "Login",
        "register_btn": "Register & Login",
        "direction_alert": "Direction alert",
        "undo_btn": "↩ Undo",
        "undo_help": "Undo last save",
        "delete_all_guest": "🗑 Delete all my data & Logout",
        "create_user_btn": "Create User",
        "user_created": "Created: {name}",
        "user_exists": "Already exists",
        "delete_profile_btn": "🗑 Delete",
        "anon_caption": "Auto-replaced before sending to API",
        "anon_add_btn": "Add",
        "anon_custom_label": "**Custom replacements (original → API text)**",
        "new_blank_btn": "+ Blank",
        "new_copy_btn": "+ Copy",
        "create_btn": "Create",
        "reeval_all_btn": "🔄 Re-evaluate All",
        "reeval_confirm": lambda n, cost: f"Re-evaluate **{n}** entries.\n\nEstimated cost: **~${cost}** (~$0.07/entry)\n\nContinue?",
        "reeval_proceed": "Yes, proceed",
        "reeval_done": lambda n: f"{n} entries re-evaluated",
        "api_not_set": "API key not set",
        "guest_label": "Guest",
        "pdf_error": "PDF error: {e}",
        "save_btn": "💾 Save",
        "entries_count": lambda f, t: f"{f} / {t} entries",
        "save_status_help": "Save status",
        "edit_help": "Edit fields",
        "error_prefix": "Error",
        "sidebar_add_user": "+ Add user",
        "migrate_label": "🔧 Migrate existing account",
        "migrate_caption": "Verify with your old password, then set new email and password.",
        "no_migrate": "No accounts to migrate",
        "no_pw_set": "No password set. Please register a new account.",
        "migrate_btn": "Migrate & Login",
        "profile_toggle": "👤 Profile",
        "pop_register_label": "Register",
        "direction_label": "Direction",
        "risk_label": "Risk",
        "add_to_pipeline": "Add to Pipeline",
        "added": "Added",
        "balanced_eval": lambda go, skip: f"Balanced evaluation pattern (Go {go}%, Skip {skip}%). Keep going.",
        "generated_at": "Generated",
        "eval_count_brief": lambda n: f"Evaluated: {n} (min 3 to generate)",
        "no_jobs_discovery": "No jobs yet. Press Run Discovery.",
        "screen_unscreened_btn": "⚡ Screen unscreened",
        "unscreened_count": lambda n: f"Unscreened: {n} → use ⚡ Screen unscreened",
        "run_discovery_hint": "▶ Press Run Discovery to fetch and screen jobs automatically.",
        "no_search_brief_warn": "No Search Brief yet. Generate one in the Pipeline tab for better results.",
        "fetch_done_n": lambda n: f"{n} jobs fetched and screened",
        "no_new_jobs": "No new jobs (all already fetched)",
        "maybe_fit_caption": "You may not search these titles — but they could fit your profile.",

        # profile / account
        "profile_saved": "Saved",
        "email_saved": "Email saved",
        "pw_changed": "Password changed",
        "wrong_current_pw": "Current password is incorrect",
        "new_pw_required": "Please enter a new password",
        "email_in_use": "This email is already in use",
        "profile_label": "Profile text",
        "new_profile": "New profile",
        "account_settings": "Account settings",
        "save_email": "Save email",
        "change_pw": "Change password",
        "current_pw": "Current password",
        "new_pw": "New password",
        "confirm_pw": "Confirm",
        # fetch
        "fetch_btn": "Fetch",
        "fetching": "Fetching from LinkedIn...",
        "fetch_url_required": "Please enter a URL",
        "fetch_done": "Fetched",
        "fetch_failed": "Fetch failed",
        # actions
        "re_evaluating": "Re-evaluating...",
        "eval_error": "Error",
        "confirm_del_yes": "Yes",
        "confirm_del_no": "No",
        "clear": "Clear",
        "logout": "Logout",
        "save_status": "Save status",
        "edit_fields": "Edit fields",
        "delete": "Delete",
        "save_changes": "Save changes",
        "cancel": "Cancel",
        # overview
        "filter_status": "Filter by status",
        "filter_decision": "Filter by decision",
        "export_csv": "Export CSV",
        "no_pipeline": "**Start by filling in your profile.**\n\nOpen the **👤 Profile** toggle above, enter your background and target roles, then go to **Evaluate & Apply** to paste a job description.",
        # cv upload
        "cv_upload": "Upload resume / CV (PDF)",
        "cv_caption": "Text will be extracted and applied to your profile",
        "cv_extracted": "Extracted ({n} chars)",
        "cv_no_text": "Could not extract text (may be an image-based PDF)",
        "cv_apply": "Apply to profile",
        "cv_preview": "Preview",
        # misc
        "anonymization": "Anonymization",
        "send_preview": "Send preview",
        "smartphone_qr": "Smartphone QR",
        "qr_access_help": "**Cannot access from smartphone?**\n\nLaunch Streamlit in network mode:\n\n```\nstreamlit run app.py --server.address=0.0.0.0\n```",
        "tab_overview": "Overview",
        "tab_discover": "🧪 Discover",
        "tab_insights": "Insights",
        "tab_saved": "🔖 Saved",
        "login_btn": "Login",
        "bookmark": "Bookmark",
        "unbookmark": "Remove bookmark",
        "no_saved": "No saved jobs yet. Tap 🔖 on any card to save it.",
        # pattern insights
        "pattern_stats_caption": lambda n: f"Auto-calculated from {n} evaluated entries (no API cost)",
        "insight_skip_high": lambda pct: f"⚠ **{pct}% are Skip** — your search category or target criteria may be off. Try adjusting keywords or role category.",
        "insight_weakest_dim": lambda dim, score: f"📉 **{dim} is your weakest axis** (avg {score:.1f}/2) — this is dragging down many evaluations. Focus on roles or experience that strengthen this angle.",
        "insight_apply_gap": lambda go_c, applied, pct: f"📌 **{go_c} Go decisions, only {applied} applied** ({pct}%) — good roles are stalling in the pipeline. Move to action.",
        "insight_go_low": lambda pct: f"🔍 **Only {pct}% Go rate** — scores are generally low. Adjusting role type, industry, or region may improve match quality.",
        "insight_trajectory_gap": "💡 **High Competitiveness but low Trajectory** — you're finding winnable roles but they're off your target direction. Narrow toward roles more aligned with your career axis.",
        "strategic_signals": "Strategic Signals:",
        # search brief
        "search_brief_expander": "Search Brief — AI Search Strategy",
        "brief_target_titles": "Target Titles",
        "brief_company_types": "Target Company Types",
        "brief_target_companies": "Spotlight Companies",
        "brief_avoid_titles": "Avoid Titles",
        "brief_must_keywords": "Must Keywords",
        "brief_avoid_keywords": "Avoid Keywords",
        "brief_platform_queries": "Platform Search Queries (copy-paste)",
        "brief_generate_btn": "✨ Generate Search Brief",
        "brief_regenerate_btn": "🔄 Regenerate",
        "brief_help": "Requires 3+ evaluated entries + API key",
        "brief_spinner": "Analyzing... (company names not sent to API)",
        # discover tab
        "discover_subheader": "Today's Opportunities",
        "discover_beta_info": "⚠️ Beta: RSS-based (MCF/Indeed) — tends to surface SME roles. For target companies (Grab, Salesforce, Japanese MNCs, etc.), manual LinkedIn search is more reliable. Use your Search Brief queries.",
        "run_discovery_help": "Fetch jobs + AI screening in one click",
        "clear_list_help": "Clear list",
        "screen_unscreened_help": "Re-screen only unscreened entries (use after manual add or interrupted run)",
        "spinner_angles": "Generating search angles from profile...",
        "spinner_fetching_jobs": "Fetching from Indeed & MyCareersFuture...",
        "spinner_screening": lambda n: f"AI screening {n} jobs...",
        "angles_caption": lambda labels: "Search angles: " + " / ".join(labels),
        # deadline calendar
        "deadline_calendar_title": "April Deadline Calendar",
        "urgency_1week": "🔴 Less than 1 week left",
        "urgency_2week": "🟡 2 weeks left",
        "urgency_days": lambda d: f"{d} days left",
        "disc_strong_matches": lambda n: f"### 🟢 Strong matches ({n})",
        "disc_worth_look": lambda n: f"### 🟡 Worth a look ({n})",
        "disc_blind_spot": lambda n: f"### 🔵 Overlooked / Blind spot ({n})",
        "disc_eval_btn": "→ Eval",
        # eval tab
        "eval_subheader": "Evaluate & Save to Pipeline",
        "profile_in_use": lambda name: f"Profile in use: **{name}** — change via sidebar",
        "pdf_loaded": lambda name: f"PDF loaded · pdfs/{name}",
        "eval_spinner": "Evaluating...",
        "saved_with_decision": lambda d: f"Saved to pipeline ({d})",
        # missing companies
        "missing_companies_header": "🔍 What you may be missing",
        "missing_companies_caption": "These company types/names appeared in your Search Brief but have no entries in your pipeline yet.",
        # pipeline no entries
        "no_pipeline_jp_text": "**Start by filling in your profile.**\n\nOpen the **👤 Profile** toggle above, enter your background and target roles, then go to **Evaluate & Apply** to paste a job description.",
    },
    "JP": {
        "title": "Opportunity Tracker",
        "tab_pipeline": "パイプライン",
        "tab_eval": "評価・応募",
        "tab_list": "一覧",
        "goal": "目標・ファネル",
        "target_offers": "意思決定に必要な内定数",
        "filter": "ステータスで絞り込み",
        "sort": "並び替え",
        "notes_placeholder": "メモ、面接日程、担当者名など...",
        "save_note": "メモ保存",
        "update": "更新",
        "re_eval": "再評価",
        "export": "CSVエクスポート",
        "save_profile": "プロファイル保存",
        "api_settings": "API設定",
        "my_profile": "マイプロファイル",
        "goal_msg": lambda needed, gap, weeks, pace: f"あと **{needed}件** の内定が必要。転換率（約7.5%）を元にすると、さらに **約{gap}件** の応募が必要。残り **{weeks}週間** で週 **{pace}件ペース**。",
        "goal_done": lambda t: f"目標 {t} 件達成。意思決定できる状態です。",
        "conversion_note": "転換率の前提: 応募→面接 25%、面接→内定 30%（シニアAPACロールの参考値）",
        "no_entries": "まだ求人がありません。評価タブから追加してください。",
        "no_match": "該当する求人がありません。",
        "confirm_del": "削除しますか？",
        "add_manual": "+ 手動で追加",
        "company": "会社", "role": "ポジション", "status": "ステータス",
        "next_action": "次のアクション", "url": "URL",
        "eval_detail": "メモ・評価詳細",
        "re_eval_help": "保存済みJDで再評価",
        "no_jd": "JD未保存",
        "company_hint": "会社名",
        "role_hint": "ポジション名",
        "url_hint": "URL（任意）",
        "source_hint": "媒体",
        "job_id_hint": "求人ID（任意）",
        "salary_hint": "給与（任意）",
        "jd_hint": "求人票をここに貼り付け...",
        "evaluate_btn": "評価する",
        "why_fit": "なぜ合うか:",
        "main_risk": "主なリスク:",
        "saved_ok": "パイプラインに保存しました",
        "skip_saved": "Skipとして保存しました",
        # login
        "email_required": "メールアドレスを入力してください",
        "email_not_found": "このメールアドレスは登録されていません。下の「新規登録」から登録してください。",
        "wrong_password": "パスワードが違います",
        "name_required": "表示名を入力してください",
        "pw_required": "パスワードを入力してください",
        "pw_mismatch": "パスワードが一致しません",
        "email_taken": "このメールアドレスはすでに登録されています",
        "login_tagline": "求人URLを貼るだけ。AIがあなたとの相性を即判定。",
        "login_heading": "ログイン",
        "register_btn": "登録してログイン",
        "direction_alert": "方向性アラート",
        "undo_btn": "↩ 元に戻す",
        "undo_help": "直前の保存を元に戻す",
        "delete_all_guest": "🗑 データを全削除してログアウト",
        "create_user_btn": "ユーザー作成",
        "user_created": "作成しました: {name}",
        "user_exists": "既に存在します",
        "delete_profile_btn": "🗑 削除",
        "anon_caption": "APIに送信する前に自動で置換されます",
        "anon_add_btn": "追加",
        "anon_custom_label": "**カスタム置換（元の語 → 送信時の表現）**",
        "new_blank_btn": "+ ブランク",
        "new_copy_btn": "+ コピー",
        "create_btn": "作成",
        "reeval_all_btn": "🔄 全件再評価",
        "reeval_confirm": lambda n, cost: f"**{n}件**を再評価します。\n\n推定コスト: **約${cost}**（1件あたり~$0.07）\n\n続けますか？",
        "reeval_proceed": "はい、実行",
        "reeval_done": lambda n: f"{n}件を再評価しました",
        "api_not_set": "APIキーが設定されていません",
        "guest_label": "ゲスト",
        "pdf_error": "PDF読み込みエラー: {e}",
        "save_btn": "💾 保存",
        "entries_count": lambda f, t: f"{f} / {t} 件",
        "save_status_help": "ステータスを保存",
        "edit_help": "編集",
        "error_prefix": "エラー",
        "sidebar_add_user": "+ ユーザー追加",
        "migrate_label": "🔧 既存ユーザーの移行",
        "migrate_caption": "旧パスワードで本人確認後、メールと新しいパスワードを設定してください。",
        "no_migrate": "移行が必要なアカウントはありません",
        "no_pw_set": "旧パスワードが未設定です。新規登録してください。",
        "migrate_btn": "移行してログイン",
        "profile_toggle": "👤 プロフィール",
        "pop_register_label": "新規登録",
        "direction_label": "方向性",
        "risk_label": "リスク",
        "add_to_pipeline": "パイプラインに追加",
        "added": "追加しました",
        "balanced_eval": lambda go, skip: f"バランスの取れた評価パターンです（Go {go}%、Skip {skip}%）。このまま継続しましょう。",
        "generated_at": "生成日時",
        "eval_count_brief": lambda n: f"評価済み: {n}件（3件以上で生成可能）",
        "no_jobs_discovery": "まだ求人がありません。「Run Discovery」を押してください。",
        "screen_unscreened_btn": "⚡ スクリーニング未実施を処理",
        "unscreened_count": lambda n: f"未スクリーニング: {n}件 → ⚡ Screen unscreened で分類",
        "run_discovery_hint": "▶ Run Discovery を押すと求人取得からスクリーニングまで自動実行します。",
        "no_search_brief_warn": "Search Briefがまだありません。Overviewタブで生成すると精度が上がります。",
        "fetch_done_n": lambda n: f"{n}件取得・スクリーニング完了",
        "no_new_jobs": "新着なし（すべて取得済み）",
        "maybe_fit_caption": "検索キーワードとは違うかもしれませんが、プロフィールに合う可能性があります。",

        # profile / account
        "profile_saved": "保存しました",
        "email_saved": "メールアドレスを保存しました",
        "pw_changed": "パスワードを変更しました",
        "wrong_current_pw": "現在のパスワードが違います",
        "new_pw_required": "新しいパスワードを入力してください",
        "email_in_use": "このメールアドレスはすでに使われています",
        "profile_label": "プロフィールテキスト",
        "new_profile": "新規プロフィール",
        "account_settings": "アカウント設定",
        "save_email": "メールを保存",
        "change_pw": "パスワード変更",
        "current_pw": "現在のパスワード",
        "new_pw": "新しいパスワード",
        "confirm_pw": "確認",
        # fetch
        "fetch_btn": "Fetch",
        "fetching": "LinkedInから取得中...",
        "fetch_url_required": "URLを入力してください",
        "fetch_done": "取得完了",
        "fetch_failed": "Fetch失敗",
        # actions
        "re_evaluating": "再評価中...",
        "eval_error": "エラー",
        "confirm_del_yes": "はい",
        "confirm_del_no": "いいえ",
        "clear": "クリア",
        "logout": "ログアウト",
        "save_status": "ステータス保存",
        "edit_fields": "編集",
        "delete": "削除",
        "save_changes": "変更を保存",
        "cancel": "キャンセル",
        # overview
        "filter_status": "ステータスで絞り込み",
        "filter_decision": "評価で絞り込み",
        "export_csv": "CSVエクスポート",
        "no_pipeline": "**まずプロフィールを入力してください。**\n\n上の **👤 Profile** トグルを開き、自分の職歴・スキル・希望条件を記入してから、**Evaluate & Apply** タブで求人のJDを貼り付けて評価を開始してください。",
        # cv upload
        "cv_upload": "履歴書・CVをアップロード（PDF）",
        "cv_caption": "PDFからテキストを抽出してプロフィール欄に反映します",
        "cv_extracted": "抽出完了（{n}文字）",
        "cv_no_text": "テキストを抽出できませんでした（画像PDFの可能性があります）",
        "cv_apply": "プロフィール欄に反映",
        "cv_preview": "プレビュー",
        # misc
        "anonymization": "匿名化設定",
        "send_preview": "送信プレビュー",
        "smartphone_qr": "スマートフォン QR",
        "qr_access_help": "**スマホからアクセスできない場合:**\n\nStreamlitをネットワーク公開モードで起動してください:\n\n```\nstreamlit run app.py --server.address=0.0.0.0\n```",
        "tab_overview": "概要",
        "tab_discover": "🧪 求人探索",
        "tab_insights": "インサイト",
        "tab_saved": "🔖 保存済み",
        "login_btn": "ログイン",
        "bookmark": "保存",
        "unbookmark": "保存を解除",
        "no_saved": "保存済みの求人がありません。カードの 🔖 で保存できます。",
        # pattern insights
        "pattern_stats_caption": lambda n: f"※ 評価済み{n}件のデータから自動計算（APIコスト不要）",
        "insight_skip_high": lambda pct: f"⚠ **{pct}%がSkip** — 検索カテゴリかターゲット条件がズレている可能性があります。検索キーワードや職種カテゴリを見直してみてください。",
        "insight_weakest_dim": lambda dim, score: f"📉 **{dim}が最も低い**（平均{score:.1f}/2）— このギャップが多くの求人で足を引っ張っています。この観点を補強できる求人や実績に絞ると効率的です。",
        "insight_apply_gap": lambda go_c, applied, pct: f"📌 **Go判定{go_c}件中、実応募は{applied}件**（{pct}%）— 良い求人がパイプラインで止まっています。次のアクションを実行に移しましょう。",
        "insight_go_low": lambda pct: f"🔍 **Goが{pct}%と低い** — 全体的にスコアが伸びていません。職種・業界・地域の絞り込みを変えると質が上がる可能性があります。",
        "insight_trajectory_gap": "💡 **Competitivenessは高いがTrajectoryが低い** — 勝てそうな求人を選べているが、方向性がズレています。より軸に合った求人に絞るとGo率が上がるかもしれません。",
        "strategic_signals": "Strategic Signals:",
        # search brief
        "search_brief_expander": "Search Brief — AI検索戦略",
        "brief_target_titles": "狙うタイトル",
        "brief_company_types": "ターゲット企業タイプ",
        "brief_target_companies": "注目企業・直接確認先",
        "brief_avoid_titles": "避けるタイトル",
        "brief_must_keywords": "必須キーワード",
        "brief_avoid_keywords": "避けるキーワード",
        "brief_platform_queries": "プラットフォーム別検索クエリ（コピペ用）",
        "brief_generate_btn": "✨ Search Briefを生成",
        "brief_regenerate_btn": "🔄 再生成",
        "brief_help": "評価済み3件以上 + APIキーが必要",
        "brief_spinner": "分析中...（会社名はAPIに送信されません）",
        # discover tab
        "discover_subheader": "今日の求人",
        "discover_beta_info": "⚠️ Beta: MCF・IndeedのRSSベースのため、SME求人が多く出ます。ターゲット企業（Grab、Salesforce、日系MNC等）はLinkedInで手動検索が実態に合っています。Search Briefのクエリを活用してください。",
        "run_discovery_help": "求人取得 + AIスクリーニングを一括実行",
        "clear_list_help": "リストをクリア",
        "screen_unscreened_help": "未スクリーニング分だけ再スクリーニング（手動追加・途中停止後に使用）",
        "spinner_angles": "プロフィールから検索角度を生成中...",
        "spinner_fetching_jobs": "Indeed & MyCareersFutureから取得中...",
        "spinner_screening": lambda n: f"{n}件をAIスクリーニング中...",
        "angles_caption": lambda labels: "検索角度: " + " / ".join(labels),
        # deadline calendar
        "deadline_calendar_title": "4月 Deadline カレンダー",
        "urgency_1week": "🔴 残り1週間を切っています",
        "urgency_2week": "🟡 残り2週間",
        "urgency_days": lambda d: f"残り {d} 日",
        "disc_strong_matches": lambda n: f"### 🟢 マッチ度高 ({n})",
        "disc_worth_look": lambda n: f"### 🟡 検討の価値あり ({n})",
        "disc_blind_spot": lambda n: f"### 🔵 見落としがち ({n})",
        "disc_eval_btn": "→ 評価",
        # eval tab
        "eval_subheader": "評価・パイプライン保存",
        "profile_in_use": lambda name: f"使用中プロフィール: **{name}** — サイドバーから変更",
        "pdf_loaded": lambda name: f"PDF読込済 · pdfs/{name}",
        "eval_spinner": "評価中...",
        "saved_with_decision": lambda d: f"パイプラインに保存しました（{d}）",
        # missing companies
        "missing_companies_header": "🔍 まだパイプラインにない企業",
        "missing_companies_caption": "Search Briefに含まれる企業・タイプのうち、まだパイプラインに追加されていないものです。",
        # pipeline no entries
        "no_pipeline_jp_text": "**まずプロフィールを入力してください。**\n\n上の **👤 Profile** トグルを開き、自分の職歴・スキル・希望条件を記入してから、**Evaluate & Apply** タブで求人のJDを貼り付けて評価を開始してください。",
    }
}

st.set_page_config(page_title="KoaFlux", layout="wide")

st.markdown("""
<style>
/* ── Layout ── */
[data-testid="stAppViewContainer"] > section > div:first-child,
[data-testid="stMain"] > div:first-child,
.block-container {
    padding-top: 0.5rem !important;
    max-width: 1400px !important;
    padding-left: 2rem !important;
    padding-right: 2rem !important;
    margin-left: auto !important;
    margin-right: auto !important;
}
header[data-testid="stHeader"] { display: none !important; }

/* ── Base font ── */
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
    font-size: 15px !important;
    color: #1a1a1a !important;
    -webkit-font-smoothing: antialiased !important;
}

/* Keep Streamlit/Material icon fonts intact */
.material-symbols-rounded,
.material-symbols-outlined,
.material-icons {
    font-family: "Material Symbols Rounded", "Material Symbols Outlined", "Material Icons" !important;
    font-weight: normal !important;
    font-style: normal !important;
    line-height: 1 !important;
    letter-spacing: normal !important;
    text-transform: none !important;
    display: inline-block !important;
    white-space: nowrap !important;
    word-wrap: normal !important;
    direction: ltr !important;
    -webkit-font-smoothing: antialiased !important;
}

/* ── Body text ── */
p, .stMarkdown p, .stText, div[data-testid="stMarkdownContainer"] p {
    font-size: 15px !important;
    line-height: 1.65 !important;
    color: #2d2d2d !important;
}

/* ── Headings ── */
h1 { font-size: 22px !important; font-weight: 700 !important; color: #111 !important; }
h2 { font-size: 19px !important; font-weight: 700 !important; color: #111 !important; }
h3 { font-size: 16px !important; font-weight: 600 !important; color: #1a1a1a !important; }

/* ── Captions ── */
small, .stCaption, [data-testid="stCaptionContainer"] p,
div[data-testid="stCaptionContainer"] {
    font-size: 13px !important;
    line-height: 1.5 !important;
    color: #6b7280 !important;
}

/* ── Metric ── */
[data-testid="stMetricLabel"] { font-size: 12px !important; color: #6b7280 !important; }
[data-testid="stMetricValue"] { font-size: 22px !important; font-weight: 700 !important; color: #111 !important; }

/* ── Tab labels ── */
button[role="tab"] {
    font-size: 14px !important;
    font-weight: 500 !important;
    color: #4b5563 !important;
}
button[role="tab"][aria-selected="true"] {
    font-weight: 600 !important;
    color: #2563eb !important;
}

/* ── Expander header ── */
[data-testid="stExpander"] summary p {
    font-size: 15px !important;
    font-weight: 500 !important;
    color: #1a1a1a !important;
}
.header-top-row-marker,
.header-menu-panel-marker,
.dp-title-close-row-marker,
.detail-score-row-marker { display: none !important; }
[data-testid="stVerticalBlock"]:has(.header-top-row-marker) [data-testid="stHorizontalBlock"] {
    align-items: flex-start !important;
    margin-top: 0 !important;
    flex-wrap: nowrap !important;
}
[data-testid="stVerticalBlock"]:has(.header-top-row-marker) [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {
    min-width: 0 !important;
    flex: 1 1 auto !important;
}
[data-testid="stVerticalBlock"]:has(.header-top-row-marker) [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2),
[data-testid="stVerticalBlock"]:has(.header-top-row-marker) [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {
    flex: 0 0 auto !important;
    min-width: 42px !important;
    width: 42px !important;
    align-self: flex-start !important;
}
/* Override: profile button columns must not inherit header 42px width */
[data-testid="stVerticalBlock"]:has(.profile-save-row-marker) [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
[data-testid="stVerticalBlock"]:has(.profile-new-row-marker) [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
[data-testid="stVerticalBlock"]:has(.profile-create-row-marker) [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    flex: 1 1 0% !important;
    width: auto !important;
    min-width: 0 !important;
}
[data-testid="stVerticalBlock"]:has(.header-top-row-marker) [data-testid="stButton"] button {
    min-width: 42px !important;
    width: 42px !important;
    height: 42px !important;
    padding: 0 !important;
    display: inline-flex !important;
    align-items: center !important;
    justify-content: center !important;
}
[data-testid="stVerticalBlock"]:has(.header-menu-panel-marker) [data-testid="stRadio"] > div {
    justify-content: center !important;
}

/* ── Widget labels ── */
label[data-testid="stWidgetLabel"] p {
    font-size: 13px !important;
    font-weight: 500 !important;
    color: #374151 !important;
}

/* ── Inputs ── */
input, textarea, [data-baseweb="input"] input {
    font-size: 15px !important;
}

/* ── Buttons ── */
button[kind="secondary"] { font-size: 14px !important; font-weight: 500 !important; }
button[kind="primary"]   { font-size: 14px !important; font-weight: 600 !important; color: #fff !important; background-color: #2563eb !important; border-color: #2563eb !important; }
button[kind="primary"] p { color: #fff !important; }
button[kind="primary"]:disabled { background-color: #93b4f5 !important; border-color: #93b4f5 !important; color: #fff !important; }

/* ── Sidebar ── */
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] label { font-size: 14px !important; }

/* ── Multiselect tags ── */
span[data-baseweb="tag"] {
    background-color: #dbeafe !important;
    color: #1d4ed8 !important;
    border: none !important;
    font-size: 13px !important;
    font-weight: 500 !important;
}
span[data-baseweb="tag"] svg { fill: #1d4ed8 !important; }

/* ── Info / warning / success boxes ── */
[data-testid="stAlert"] p { font-size: 14px !important; line-height: 1.55 !important; }

/* ── Action row: only inside detail panel header ── */
[data-testid="stVerticalBlock"]:has(.dp-header-marker) [data-testid="stColumns"] {
    flex-wrap: nowrap !important;
    gap: 4px !important;
}
[data-testid="stVerticalBlock"]:has(.dp-header-marker) [data-testid="stColumns"] > [data-testid="stColumn"] {
    min-width: 0 !important;
    flex-shrink: 1 !important;
}
[data-testid="stVerticalBlock"]:has(.dp-header-marker) [data-testid="stColumns"] > [data-testid="stColumn"] > div > div > div > button {
    padding: 0.3rem 0.2rem !important;
    min-width: 0 !important;
    width: 100% !important;
    font-size: 16px !important;
    white-space: nowrap !important;
    overflow: hidden !important;
}

/* ── Mobile: no horizontal overflow ── */
@media (max-width: 640px) {
    [data-testid="stAppViewContainer"],
    [data-testid="stMainBlockContainer"],
    section[data-testid="stSidebar"] ~ div {
        overflow-x: hidden !important;
        max-width: 100vw !important;
    }
    [data-testid="stSelectbox"] > div > div {
        max-width: 100% !important;
        overflow: hidden !important;
    }
    [data-baseweb="select"] span {
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        max-width: 100% !important;
    }
    /* EN/JP toggle: center on mobile */
    [data-testid="stRadio"] > div {
        justify-content: center !important;
    }
    [data-testid="stVerticalBlock"]:has(.desktop-header-row-marker),
    [data-testid="stHorizontalBlock"]:has(.desktop-header-row-marker),
    [data-testid="stVerticalBlock"]:has(.desktop-header-controls-marker),
    [data-testid="stColumn"]:has(.desktop-header-controls-marker) {
        display: none !important;
    }
    [data-testid="stVerticalBlock"]:has(.mobile-header-row-marker),
    [data-testid="stHorizontalBlock"]:has(.mobile-header-row-marker) {
        display: block !important;
        margin-bottom: 4px !important;
    }
    [data-testid="stVerticalBlock"]:has(.mobile-header-row-marker) [data-testid="stHorizontalBlock"],
    [data-testid="stHorizontalBlock"]:has(.mobile-header-row-marker) {
        display: flex !important;
        align-items: center !important;
        gap: 8px !important;
    }
    [data-testid="stVerticalBlock"]:has(.mobile-header-row-marker) [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {
        min-width: 0 !important;
        flex: 1 1 auto !important;
    }
    [data-testid="stVerticalBlock"]:has(.mobile-header-row-marker) [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {
        flex: 0 0 44px !important;
        width: 44px !important;
        min-width: 44px !important;
        display: flex !important;
        justify-content: flex-end !important;
    }
    /* Card buttons: keep left-aligned, bold first line via strong title */
    *:has(.pipeline-card-marker) + * button[kind="secondary"],
    *:has(.pipeline-card-marker) + * button[kind="primary"] {
        font-weight: 600 !important;
    }
    /* Evaluate fetch row: stack URL input and full-width Fetch on mobile */
    [data-testid="stHorizontalBlock"]:has(.eval-fetch-row-marker) {
        display: flex !important;
        flex-direction: column !important;
        align-items: stretch !important;
        gap: 8px !important;
    }
    [data-testid="stHorizontalBlock"]:has(.eval-fetch-row-marker) > [data-testid="stColumn"] {
        width: 100% !important;
        max-width: 100% !important;
        min-width: 0 !important;
        flex: 1 1 100% !important;
    }
    [data-testid="stHorizontalBlock"]:has(.eval-fetch-row-marker) > [data-testid="stColumn"] > div {
        width: 100% !important;
        max-width: 100% !important;
    }
    [data-testid="stHorizontalBlock"]:has(.eval-fetch-row-marker) [data-testid="stButton"] {
        width: 100% !important;
        max-width: 100% !important;
    }
    [data-testid="stHorizontalBlock"]:has(.eval-fetch-row-marker) [data-testid="stButton"] button {
        width: 100% !important;
        max-width: 100% !important;
        min-height: 42px !important;
    }
    /* Compact action rows on mobile */
    /* Profile button pairs: no wrap, touch-friendly */
    [data-testid="stVerticalBlock"]:has(.profile-save-row-marker) [data-testid="stButton"] button,
    [data-testid="stVerticalBlock"]:has(.profile-new-row-marker) [data-testid="stButton"] button,
    [data-testid="stVerticalBlock"]:has(.profile-create-row-marker) [data-testid="stButton"] button {
        white-space: nowrap !important;
        min-height: 44px !important;
    }
    /* Eval action row: Evaluate wide, Clear fixed */
    [data-testid="stHorizontalBlock"]:has(.eval-action-row-marker) > [data-testid="stColumn"]:first-child {
        flex: 1 1 auto !important;
        min-width: 0 !important;
    }
    [data-testid="stHorizontalBlock"]:has(.eval-action-row-marker) > [data-testid="stColumn"]:last-child {
        flex: 0 0 88px !important;
        width: 88px !important;
        min-width: 88px !important;
    }
    [data-testid="stHorizontalBlock"]:has(.eval-action-row-marker) [data-testid="stButton"] button {
        width: 100% !important;
        min-height: 42px !important;
    }
}
.desktop-header-controls-marker,
.mobile-header-menu-marker,
.desktop-header-row-marker,
.mobile-header-row-marker { display: none !important; }
.eval-fetch-row-marker,
.eval-main-row-marker,
.eval-meta-row-marker,
.eval-action-row-marker,
.profile-save-row-marker,
.profile-new-row-marker,
.profile-create-row-marker { display: none !important; }
[data-testid="stVerticalBlock"]:has(.mobile-header-row-marker) {
    display: none !important;
}
[data-testid="stVerticalBlock"]:has(.desktop-header-row-marker) {
    display: block !important;
}
[data-testid="stHorizontalBlock"]:has(.overview-filter-marker) {
    gap: 12px !important;
    margin-bottom: 8px !important;
}
[data-testid="stHorizontalBlock"]:has(.overview-filter-marker) > [data-testid="stColumn"] {
    min-width: 0 !important;
}
[data-testid="stHorizontalBlock"]:has(.overview-filter-marker) details {
    border: 1px solid #dbe4f0 !important;
    border-radius: 12px !important;
    background: #f8fafc !important;
}
[data-testid="stHorizontalBlock"]:has(.overview-filter-marker) details summary {
    font-weight: 700 !important;
}

/* ── Allow sticky: parent horizontal block must not clip ── */
[data-testid="stHorizontalBlock"]:has(.detail-panel-marker) {
    overflow: visible !important;
    align-items: flex-start !important;
    gap: 14px !important;
}
/* ── List column: clip overflow so cards stay in their lane ── */
[data-testid="stHorizontalBlock"]:has(.detail-panel-marker) > [data-testid="stColumn"]:not(:has(.detail-panel-marker)) {
    overflow: hidden !important;
    min-width: 0 !important;
}

/* ── PC detail panel card (sticky, flex column for inner scroll) ── */
[data-testid="stColumn"]:has(.detail-panel-marker) {
    position: sticky !important;
    top: 10px !important;
    height: calc(100vh - 30px) !important;
    overflow: hidden !important;
    align-self: flex-start !important;
    background: #f8fafc !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 12px !important;
    padding: 0 !important;
    display: flex !important;
    flex-direction: column !important;
}
/* Inner vertical block wrapping both header and body containers */
[data-testid="stColumn"]:has(.detail-panel-marker) > div > [data-testid="stVerticalBlock"] {
    display: flex !important;
    flex-direction: column !important;
    height: 100% !important;
    min-height: 0 !important;
    overflow: hidden !important;
    position: relative !important;
}
.dp-header-marker, .dp-body-marker { display: none !important; }
.dp-top-controls-row-marker { display: none !important; }
/* Header container: fixed, no scroll, with proper padding */
[data-testid="stVerticalBlock"]:has(.dp-header-marker) {
    flex-shrink: 0 !important;
    padding: 16px 20px 12px !important;
    border-bottom: 1px solid #e2e8f0 !important;
    background: #f8fafc !important;
    position: relative !important;
}
/* Body container: scrollable with proper padding */
[data-testid="stVerticalBlock"]:has(.dp-body-marker) {
    flex: 1 !important;
    min-height: 0 !important;
    overflow-y: auto !important;
    padding: 16px 20px 20px !important;
}
/* Action buttons: compact with balanced spacing */
[data-testid="stVerticalBlock"]:has(.dp-header-marker) [data-testid="stHorizontalBlock"] {
    gap: 6px !important;
    margin-top: 8px !important;
}
[data-testid="stVerticalBlock"]:has(.dp-header-marker) [data-testid="stHorizontalBlock"]:has(.dp-title-close-row-marker),
[data-testid="stVerticalBlock"]:has(.dp-header-marker) [data-testid="stHorizontalBlock"]:has(.detail-score-row-marker) {
    margin-top: 0 !important;
    margin-bottom: 10px !important;
    align-items: flex-start !important;
    width: 100% !important;
    overflow: visible !important;
}
[data-testid="stVerticalBlock"]:has(.dp-header-marker) [data-testid="stHorizontalBlock"]:has(.dp-title-close-row-marker) > [data-testid="stColumn"]:first-child,
[data-testid="stVerticalBlock"]:has(.dp-header-marker) [data-testid="stHorizontalBlock"]:has(.detail-score-row-marker) > [data-testid="stColumn"]:first-child {
    flex: 1 1 auto !important;
    width: auto !important;
}
[data-testid="stVerticalBlock"]:has(.dp-header-marker) [data-testid="stHorizontalBlock"]:has(.dp-title-close-row-marker) > [data-testid="stColumn"]:last-child,
[data-testid="stVerticalBlock"]:has(.dp-header-marker) [data-testid="stHorizontalBlock"]:has(.detail-score-row-marker) > [data-testid="stColumn"]:last-child {
    flex: 0 0 auto !important;
    width: auto !important;
    min-width: fit-content !important;
    display: flex !important;
    justify-content: flex-end !important;
    overflow: visible !important;
}
/* Close button: styled compact in its column */
[data-testid="stVerticalBlock"]:has(.dp-header-marker) [data-testid="stButton"]:has(button[kind="secondary"]:not([data-testid])) button,
[data-testid="stVerticalBlock"]:has(.dp-header-marker) > div > [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:last-child [data-testid="stButton"] button {
    padding: 2px 6px !important;
    border-radius: 50% !important;
    font-size: 15px !important;
    line-height: 1 !important;
    min-width: 30px !important;
    width: 100% !important;
    justify-content: center !important;
    text-align: center !important;
    background: #e5e7eb !important;
    border: none !important;
    color: #374151 !important;
}
[data-testid="stVerticalBlock"]:has(.dp-header-marker) [data-testid="stButton"]:not(:last-of-type) button {
    font-size: 12px !important;
    padding: 4px 6px !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    justify-content: center !important;
    text-align: center !important;
}
/* Mobile: emoji only — clip text after first character */
@media (max-width: 640px) {
    [data-testid="stVerticalBlock"]:has(.dp-header-marker) [data-testid="stHorizontalBlock"] [data-testid="stButton"] button {
        padding: 6px 2px !important;
        overflow: hidden !important;
    }
    [data-testid="stVerticalBlock"]:has(.dp-header-marker) [data-testid="stHorizontalBlock"] [data-testid="stButton"] button p {
        display: block !important;
        width: 1.4em !important;
        max-width: 1.4em !important;
        overflow: hidden !important;
        white-space: nowrap !important;
        text-overflow: clip !important;
        margin: 0 auto !important;
    }
}
/* Force ALL column rows inside detail panel to stay horizontal */
[data-testid="stColumn"]:has(.detail-panel-marker) [data-testid="stHorizontalBlock"],
[data-testid="stColumn"]:has(.detail-panel-marker) [data-testid="stColumns"] {
    display: flex !important;
    flex-direction: row !important;
    flex-wrap: nowrap !important;
    gap: 6px !important;
    width: 100% !important;
}
[data-testid="stColumn"]:has(.detail-panel-marker) [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
[data-testid="stColumn"]:has(.detail-panel-marker) [data-testid="stColumns"] > [data-testid="stColumn"] {
    min-width: 0 !important;
    flex: 1 1 0 !important;
    overflow: hidden !important;
}
/* Detail panel column buttons: full width, centered (non-header) */
[data-testid="stColumn"]:has(.detail-panel-marker) [data-testid="stButton"] button {
    min-width: 0 !important;
    width: 100% !important;
}
/* Mobile: stack columns vertically, detail panel on top */
@media (max-width: 640px) {
    html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
        font-size: 17px !important;
    }
    p, .stMarkdown p, .stText, div[data-testid="stMarkdownContainer"] p {
        font-size: 17px !important;
        line-height: 1.72 !important;
    }
    h1 { font-size: 34px !important; line-height: 1.2 !important; }
    h2 { font-size: 28px !important; line-height: 1.25 !important; }
    h3 { font-size: 22px !important; line-height: 1.3 !important; }
    small, .stCaption, [data-testid="stCaptionContainer"] p,
    div[data-testid="stCaptionContainer"] {
        font-size: 15px !important;
        line-height: 1.55 !important;
    }
    button[role="tab"] {
        font-size: 18px !important;
    }
    [data-testid="stExpander"] summary p {
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
    }
    label[data-testid="stWidgetLabel"] p {
        font-size: 15px !important;
    }
    input, textarea, [data-baseweb="input"] input {
        font-size: 17px !important;
    }
    [data-testid="stButton"] button {
        font-size: 17px !important;
    }
    [data-testid="stMetricValue"] {
        font-size: 28px !important;
    }
    [data-testid="stHorizontalBlock"]:has(.pipeline-card-row-marker) {
        gap: 8px !important;
        padding: 10px 12px !important;
        border-radius: 12px !important;
    }
    [data-testid="stVerticalBlock"]:has(.header-top-row-marker) [data-testid="stHorizontalBlock"] {
        gap: 6px !important;
        align-items: flex-start !important;
        margin-top: -18px !important;
    }
    [data-testid="stVerticalBlock"]:has(.header-top-row-marker) [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-child(2),
    [data-testid="stVerticalBlock"]:has(.header-top-row-marker) [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {
        margin-top: 0 !important;
        align-self: flex-start !important;
    }
    [data-testid="stVerticalBlock"]:has(.header-top-row-marker) [data-testid="stButton"] {
        display: flex !important;
        align-items: flex-start !important;
        justify-content: flex-end !important;
    }
    /* Stack columns vertically */
    [data-testid="stHorizontalBlock"]:has(.detail-panel-marker) {
        flex-direction: column !important;
        align-items: stretch !important;
    }
    /* Detail column: full-screen mobile overlay */
    [data-testid="stColumn"]:has(.detail-panel-marker) {
        order: -1 !important;
        position: fixed !important;
        inset: 0 !important;
        top: 0 !important;
        left: 0 !important;
        right: 0 !important;
        bottom: 0 !important;
        height: 100dvh !important;
        width: 100vw !important;
        max-width: 100vw !important;
        overflow: hidden !important;
        align-self: stretch !important;
        background: white !important;
        border: none !important;
        border-radius: 0 !important;
        padding: 0 !important;
        margin: 0 !important;
        display: flex !important;
        flex-direction: column !important;
        z-index: 999 !important;
        box-shadow: none !important;
    }
    /* On mobile: inner vertical block fills overlay */
    [data-testid="stColumn"]:has(.detail-panel-marker) > div > [data-testid="stVerticalBlock"] {
        display: flex !important;
        flex-direction: column !important;
        height: 100dvh !important;
        min-height: 0 !important;
        overflow: hidden !important;
    }
    [data-testid="stVerticalBlock"]:has(.dp-header-marker) {
        padding: 18px 18px 12px !important;
        border-bottom: 1px solid #e2e8f0 !important;
        flex-shrink: 0 !important;
        background: white !important;
        position: relative !important;
    }
    [data-testid="stVerticalBlock"]:has(.dp-header-marker) [data-testid="stHorizontalBlock"]:has(.dp-title-close-row-marker),
    [data-testid="stVerticalBlock"]:has(.dp-header-marker) [data-testid="stHorizontalBlock"]:has(.detail-score-row-marker) {
        margin-bottom: 12px !important;
        min-height: 48px !important;
    }
    [data-testid="stVerticalBlock"]:has(.dp-body-marker) {
        flex: 1 !important;
        min-height: 0 !important;
        overflow-y: auto !important;
        -webkit-overflow-scrolling: touch !important;
        padding: 18px 18px 28px !important;
        background: white !important;
    }
    /* List column remains underneath overlay */
    [data-testid="stHorizontalBlock"]:has(.detail-panel-marker) > [data-testid="stColumn"]:not(:has(.detail-panel-marker)) {
        order: 1 !important;
        width: 100% !important;
        overflow: visible !important;
    }
    /* Keep action buttons horizontal inside detail panel */
    [data-testid="stColumn"]:has(.detail-panel-marker) [data-testid="stHorizontalBlock"],
    [data-testid="stColumn"]:has(.detail-panel-marker) [data-testid="stColumns"] {
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        gap: 2px !important;
        width: 100% !important;
    }
    [data-testid="stColumn"]:has(.detail-panel-marker) [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
    [data-testid="stColumn"]:has(.detail-panel-marker) [data-testid="stColumns"] > [data-testid="stColumn"] {
        min-width: 0 !important;
        flex: 1 1 0 !important;
        overflow: hidden !important;
        width: auto !important;
    }
    [data-testid="stColumn"]:has(.detail-panel-marker) [data-testid="stButton"] button {
        min-width: 0 !important;
        width: 100% !important;
    }
    /* Close button: larger on mobile */
    [data-testid="stVerticalBlock"]:has(.dp-header-marker) > div > [data-testid="stHorizontalBlock"] [data-testid="stColumn"]:last-child [data-testid="stButton"] button {
        border-radius: 999px !important;
        font-size: 20px !important;
        line-height: 1 !important;
        justify-content: center !important;
        min-height: 44px !important;
        min-width: 44px !important;
    }
}
.detail-panel-marker { display: none !important; }
/* ── Pipeline card buttons ── */
.pipeline-card-marker { display: none !important; }
/* Scoped to stVerticalBlock that has card markers but NOT detail-panel-marker
   (prevents bleeding into the outer page stVerticalBlock when both panels coexist) */
[data-testid="stVerticalBlock"]:has(.pipeline-card-marker):not(:has(.detail-panel-marker)) [data-testid="stButton"] button {
    text-align: left !important;
    justify-content: flex-start !important;
    align-items: flex-start !important;
    height: auto !important;
    white-space: pre-line !important;
    border-radius: 10px !important;
    padding: 10px 14px !important;
    line-height: 1.55 !important;
    font-size: 14px !important;
    font-weight: 400 !important;
    width: 100% !important;
    display: flex !important;
    flex-direction: column !important;
}
[data-testid="stVerticalBlock"]:has(.pipeline-card-marker):not(:has(.detail-panel-marker)) [data-testid="stButton"] button > * {
    text-align: left !important;
    justify-content: flex-start !important;
    align-items: flex-start !important;
    width: 100% !important;
}
[data-testid="stVerticalBlock"]:has(.pipeline-card-marker):not(:has(.detail-panel-marker)) [data-testid="stButton"] button p {
    text-align: left !important;
    width: 100% !important;
    margin: 0 !important;
}
[data-testid="stVerticalBlock"]:has(.pipeline-card-marker):not(:has(.detail-panel-marker)) [data-testid="stButton"] button[kind="secondary"] {
    background: #fff !important;
    border: 1.5px solid #e5e7eb !important;
    box-shadow: 0 1px 2px rgba(0,0,0,0.05) !important;
    color: #111 !important;
}
[data-testid="stVerticalBlock"]:has(.pipeline-card-marker):not(:has(.detail-panel-marker)) [data-testid="stButton"] button[kind="primary"] {
    background: #eff6ff !important;
    border: 2px solid #2563eb !important;
    color: #1e3a8a !important;
}
.pipeline-card-row-marker { display: none !important; }
.pipeline-card-selected-marker { display: none !important; }
.overview-card-open-marker { display: none !important; }
[data-testid="stHorizontalBlock"]:has(.pipeline-card-row-marker) {
    align-items: stretch !important;
    gap: 10px !important;
    border: 1.5px solid #dbe4f0 !important;
    border-radius: 14px !important;
    background: #fff !important;
    padding: 12px 14px !important;
    margin-bottom: 10px !important;
}
[data-testid="stHorizontalBlock"]:has(.pipeline-card-selected-marker) {
    border-color: #2563eb !important;
    background: #eff6ff !important;
    box-shadow: 0 0 0 1px rgba(37,99,235,0.08) !important;
}
[data-testid="stHorizontalBlock"]:has(.pipeline-card-row-marker) > [data-testid="stColumn"] {
    min-width: 0 !important;
}
[data-testid="stVerticalBlock"]:has(.overview-card-open-marker) {
    position: relative !important;
}
[data-testid="stVerticalBlock"]:has(.overview-card-open-marker) > div > [data-testid="stButton"] {
    position: absolute !important;
    inset: 0 !important;
    z-index: 5 !important;
}
[data-testid="stVerticalBlock"]:has(.overview-card-open-marker) > div > [data-testid="stButton"] button {
    width: 100% !important;
    height: 100% !important;
    min-height: 84px !important;
    opacity: 0 !important;
    border: none !important;
    background: transparent !important;
    box-shadow: none !important;
    padding: 0 !important;
}
[data-testid="stHorizontalBlock"]:has(.pipeline-card-row-marker) [data-testid="stButton"] button {
    border: none !important;
    box-shadow: none !important;
    background: transparent !important;
    padding: 0 !important;
    min-height: 0 !important;
}
[data-testid="stHorizontalBlock"]:has(.pipeline-card-row-marker) [data-testid="stButton"] button[kind="secondary"],
[data-testid="stHorizontalBlock"]:has(.pipeline-card-row-marker) [data-testid="stButton"] button[kind="primary"] {
    border: none !important;
    box-shadow: none !important;
    background: transparent !important;
    color: #111 !important;
}
[data-testid="stHorizontalBlock"]:has(.pipeline-card-row-marker) [data-testid="stButton"] button p {
    line-height: 1.55 !important;
}
</style>
""", unsafe_allow_html=True)

# ── Auth gate — restore from URL token or auto guest ─────────────
if not get_current_user_id():
    _url_token = st.query_params.get("t", "")
    _restored_uid = validate_session(_url_token) if _url_token else None
    if _restored_uid:
        st.session_state["user_id"] = _restored_uid
        st.session_state["_session_token"] = _url_token
        st.session_state["lang"] = get_user_lang(_restored_uid, st.session_state.get("lang", "EN"))
    else:
        if _url_token:
            st.query_params.pop("t", None)
        guest_file = os.path.join(BASE_DIR, "data_guest.json")
        if not os.path.exists(guest_file):
            with open(guest_file, "w") as _gf:
                json.dump({"pipeline": [], "profiles": [], "discovered": []}, _gf)
        st.session_state["user_id"] = "guest"
    st.rerun()

# ── Language toggle ───────────────────────────────────────────────
if "lang" not in st.session_state:
    st.session_state["lang"] = "EN"
L = T[st.session_state["lang"]]

uid = get_current_user_id()
has_backup = os.path.exists(backup_file(uid))
demo_mode = is_demo_user()

with st.sidebar:
    # ── User info & logout ───────────────────────────────────────
    users = load_users()
    _uid  = get_current_user_id()
    _uname = next((u["name"] for u in users if u["id"] == _uid), _uid)
    is_guest = (_uid == "guest")

    sl1, sl2, sl3 = st.columns([3, 1, 1])
    _uemail = next((u.get("email","") for u in users if u["id"] == _uid), "")
    sl1.markdown(f"**👤 {_uname}**" + (f"<br><small style='color:#94a3b8'>{_uemail}</small>" if _uemail else ""), unsafe_allow_html=True)
    if sl2.button(L["logout"], key="logout_btn"):
        delete_session(st.session_state.get("_session_token", ""))
        st.query_params.pop("t", None)
        for _k in ["user_id", "active_profile_id", "sidebar_profile_sel", "_session_token"]:
            st.session_state.pop(_k, None)
        st.rerun()
    if sl3.button(L["undo_btn"], help=L["undo_help"], disabled=not has_backup, key="undo_btn"):
        if undo_save():
            st.rerun()


    if is_guest:
        if st.button(L["delete_all_guest"], type="primary", key="guest_wipe"):
            guest_file = os.path.join(BASE_DIR, "data_guest.json")
            if os.path.exists(guest_file):
                os.remove(guest_file)
            for _k in ["user_id", "active_profile_id", "sidebar_profile_sel"]:
                st.session_state.pop(_k, None)
            st.rerun()

    with st.expander(L["sidebar_add_user"], expanded=False):
        new_user_name = st.text_input("Name", key="new_user_name")
        if st.button(L["create_user_btn"], disabled=demo_mode) and new_user_name.strip():
            new_id = new_user_name.strip().lower().replace(" ", "_")
            user_ids = [u["id"] for u in users]
            if new_id not in user_ids:
                users.append({"id": new_id, "name": new_user_name.strip()})
                save_users(users)
                st.success(L["user_created"].format(name=new_user_name))
                st.rerun()
            else:
                st.warning(L["user_exists"])

    # api_key を先に取得（Re-evaluate All の disabled 判定に使用）
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    st.markdown("---")
    with st.expander(L["my_profile"], expanded=False):
        if demo_mode:
            demo_notice()
        d_prof = load_data()
        profiles = get_profiles(d_prof)
        if not profiles:
            profiles = [{"id": "default", "name": "Default", "text": PROFILE}]
        profile_names = [p["name"] for p in profiles]
        profile_ids   = [p["id"]   for p in profiles]
        active_pid = get_active_profile_id(d_prof)
        active_idx = profile_ids.index(active_pid) if active_pid in profile_ids else 0

        # ── 既存プロフィール編集 ──────────────────────────────
        sel_name = st.selectbox("Edit profile", profile_names, index=active_idx,
                                key="sidebar_profile_sel")
        sel_pid = profile_ids[profile_names.index(sel_name) if sel_name in profile_names else 0]

        if sel_pid != st.session_state.get("active_profile_id"):
            st.session_state["active_profile_id"] = sel_pid

        sel_profile = next(p for p in profiles if p["id"] == sel_pid)
        edited_name = st.text_input("Name", value=sel_profile["name"], key="prof_name_input")
        edited_text = st.text_area("Text", value=sel_profile["text"], height=220,
                                    label_visibility="collapsed")

        save_col, del_col = st.columns([2, 1])
        if save_col.button(L["save_profile"], key="save_prof_btn", disabled=demo_mode):
            d = load_data()
            for p in d["profiles"]:
                if p["id"] == sel_pid:
                    p["name"] = edited_name.strip() or p["name"]
                    p["text"] = edited_text
            d["active_profile_id"] = sel_pid
            save_data(d)
            st.success(L["profile_saved"])
            st.rerun()
        if len(profiles) > 1:
            if del_col.button(L["delete_profile_btn"], key="del_prof_btn", disabled=demo_mode):
                d = load_data()
                d["profiles"] = [p for p in d["profiles"] if p["id"] != sel_pid]
                d["active_profile_id"] = d["profiles"][0]["id"]
                st.session_state["active_profile_id"] = d["active_profile_id"]
                save_data(d)
                st.rerun()

        st.markdown("---")

        # ── 匿名化設定 ────────────────────────────────────────
        with st.expander(L["anonymization"], expanded=False):
            st.caption(L["anon_caption"])
            d_anon = load_data()
            anon_reps = d_anon.get("anon_replacements", {})

            # カスタム置換の編集
            st.markdown(L["anon_custom_label"])
            anon_items = list(anon_reps.items())
            for idx, (orig, rep) in enumerate(anon_items):
                ac1, ac2, ac3 = st.columns([3, 3, 1])
                new_orig = ac1.text_input("元", value=orig, key=f"anon_orig_{idx}", label_visibility="collapsed")
                new_rep  = ac2.text_input("置換後", value=rep, key=f"anon_rep_{idx}", label_visibility="collapsed")
                if ac3.button("✕", key=f"anon_del_{idx}", disabled=demo_mode):
                    del anon_reps[orig]
                    d_anon["anon_replacements"] = anon_reps
                    save_data(d_anon)
                    st.rerun()
                else:
                    if new_orig != orig or new_rep != rep:
                        anon_reps.pop(orig, None)
                        anon_reps[new_orig] = new_rep

            an1, an2 = st.columns(2)
            new_anon_orig = an1.text_input("+ 追加", key="anon_new_orig", placeholder="例: 株式会社XX")
            new_anon_rep  = an2.text_input("→", key="anon_new_rep", placeholder="例: Japanese MNC",
                                            label_visibility="collapsed")
            if st.button(L["anon_add_btn"], key="anon_add_btn", disabled=demo_mode):
                if new_anon_orig.strip():
                    anon_reps[new_anon_orig.strip()] = new_anon_rep.strip()
                    d_anon["anon_replacements"] = anon_reps
                    save_data(d_anon)
                    st.rerun()

            # 送信プレビュー
            preview_text = get_anonymized_profile(d_anon)
            with st.expander(L["send_preview"], expanded=False):
                st.text_area("APIに送信されるプロフィール", value=preview_text,
                              height=150, disabled=True, label_visibility="collapsed")

        st.markdown("---")

        # ── 新規プロフィール作成 ──────────────────────────────
        st.caption(L["new_profile"])
        nc1, nc2 = st.columns(2)
        if nc1.button(L["new_blank_btn"], key="new_blank_btn", disabled=demo_mode):
            st.session_state["adding_profile"] = "blank"
        if nc2.button(L["new_copy_btn"], key="new_copy_btn", disabled=demo_mode):
            st.session_state["adding_profile"] = "copy"

        adding = st.session_state.get("adding_profile")
        if adding in ("blank", "copy"):
            if adding == "copy":
                copy_src_name = st.selectbox("Copy from", profile_names, key="copy_src_sel")
                copy_src = next(p for p in profiles if p["name"] == copy_src_name)
            new_prof_name = st.text_input("Profile name", key="new_prof_name",
                                           placeholder="e.g. AI Angle")
            if adding == "blank":
                new_prof_text = st.text_area("Profile text", height=150, key="new_prof_text",
                                              label_visibility="collapsed",
                                              placeholder="Write your profile here...")
            cc1, cc2 = st.columns(2)
            if cc1.button(L["create_btn"], key="create_prof_btn", disabled=demo_mode):
                if new_prof_name.strip():
                    d = load_data()
                    new_id = new_prof_name.strip().lower().replace(" ", "_") + f"_{len(d['profiles'])}"
                    base_text = copy_src["text"] if adding == "copy" else new_prof_text
                    d["profiles"].append({"id": new_id, "name": new_prof_name.strip(), "text": base_text})
                    d["active_profile_id"] = new_id
                    st.session_state["active_profile_id"] = new_id
                    del st.session_state["adding_profile"]
                    save_data(d)
                    st.rerun()
            if cc2.button(L["cancel"], key="cancel_prof_btn"):
                del st.session_state["adding_profile"]
                st.rerun()

        st.markdown("---")
        d_check = load_data()
        entries_with_jd = [(i, e) for i, e in enumerate(d_check.get("pipeline", [])) if e.get("jd_text")]
        n_jd = len(entries_with_jd)
        est_cost = round(n_jd * 0.07, 2)

        if st.button(L["reeval_all_btn"], disabled=demo_mode or not (n_jd > 0 and api_key)):
            st.session_state["confirm_reeval"] = True

        if st.session_state.get("confirm_reeval"):
            st.warning(L["reeval_confirm"](n_jd, est_cost))
            cy, cn = st.columns(2)
            if cy.button(L["reeval_proceed"], key="reeval_yes"):
                del st.session_state["confirm_reeval"]
                d = load_data()
                entries_with_jd = [(i, e) for i, e in enumerate(d["pipeline"]) if e.get("jd_text")]
                progress = st.progress(0)
                active_pid_bulk = get_active_profile_id(d)
                for step, (i, entry) in enumerate(entries_with_jd):
                    try:
                        r = evaluate(entry["jd_text"], api_key, lang=st.session_state.get("lang", "EN"))
                        d["pipeline"][i].update({
                            "eval_decision":           r.get("decision"),
                            "eval_reason":             r.get("reason", ""),
                            "trajectory_fit":          r.get("trajectory_fit"),
                            "core_strength_match":     r.get("core_strength_match"),
                            "attraction":              r.get("attraction"),
                            "competitiveness":         r.get("competitiveness"),
                            "secondary_strength_used": r.get("secondary_strength_used", ""),
                            "practical_constraint":    r.get("practical_constraint", ""),
                            "risk":                    r.get("risk", ""),
                            "fit_bullets":             r.get("fit_bullets", []),
                            "eval_risk":               r.get("main_risk", ""),
                            "gap_note":                r.get("gap_note", ""),
                            "direction_warning":       r.get("direction_warning", ""),
                            "updated":                 datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "eval_profile_id":         active_pid_bulk,
                        })
                        progress.progress((step + 1) / len(entries_with_jd))
                    except Exception:
                        pass
                save_data(d)
                st.success(L["reeval_done"](len(entries_with_jd)))
                st.rerun()
            if cn.button(L["cancel"], key="reeval_no"):
                del st.session_state["confirm_reeval"]
                st.rerun()

    st.markdown("---")
    # ── Smartphone QR ────────────────────────────────────────────
    with st.expander(L["smartphone_qr"], expanded=False):
        _sb_base = get_public_base_url()
        _sb_tok  = st.session_state.get("_session_token","")
        _sb_url  = f"{_sb_base}?t={_sb_tok}" if _sb_tok else _sb_base
        st.image(make_qr_image(_sb_url), width=180)
        st.caption(_sb_url)
        st.info(L["qr_access_help"])

    st.markdown("---")
    # ── API Settings ─────────────────────────────────────────────
    with st.expander(L["api_settings"], expanded=False):
        api_key = st.text_input("Anthropic API Key", type="password",
                                 value=api_key, key="api_key_input")
    if not api_key:
        st.warning(L["api_not_set"])

def render_qr_panel(prefix: str):
    _base_url = get_public_base_url()
    _sess_tok = st.session_state.get("_session_token", "")
    _qr_url = f"{_base_url}?t={_sess_tok}" if _sess_tok else _base_url
    st.image(make_qr_image(_qr_url), width=180)
    st.caption(_qr_url)
    if not _sess_tok:
        st.warning("ログイン後にQRを開くとセッション付きURLになります")

def render_account_panel(prefix: str, label_map, has_backup_flag: bool):
    _acct_uid = get_current_user_id()
    _acct_users = load_users()
    _is_guest = (_acct_uid == "guest")

    if _is_guest:
        st.caption(label_map["guest_label"])
        st.markdown("---")
        st.markdown(f"**{label_map['login_heading']}**")
        with st.form(f"{prefix}_login_form"):
            _pop_email = st.text_input("Email", placeholder="you@gmail.com", key=f"{prefix}_email").strip().lower()
            _pop_pw = st.text_input("Password", type="password", key=f"{prefix}_pw")
            _pop_login = st.form_submit_button(label_map["login_btn"], use_container_width=True, type="primary")
        if _pop_login:
            _pu = next((u for u in _acct_users if u.get("email", "").lower() == _pop_email), None)
            if _pu and check_password(_pu, _pop_pw):
                login_as(_pu["id"])
                st.rerun()
            else:
                st.error(label_map["email_not_found"] if not _pu else label_map["wrong_password"])

        st.markdown("---")
        with st.expander("📝 " + label_map["pop_register_label"]):
            _pr_email = st.text_input("Email", key=f"{prefix}_reg_email", placeholder="you@gmail.com").strip().lower()
            _pr_name = st.text_input("Name", key=f"{prefix}_reg_name", placeholder="e.g. Taro")
            _pr_pw1 = st.text_input("Password", type="password", key=f"{prefix}_reg_pw1")
            _pr_pw2 = st.text_input(label_map["confirm_pw"], type="password", key=f"{prefix}_reg_pw2")
            if st.button(label_map["register_btn"], key=f"{prefix}_reg_btn", type="primary"):
                if not _pr_email:
                    st.error(label_map["email_required"])
                elif not _pr_name.strip():
                    st.error(label_map["name_required"])
                elif not _pr_pw1:
                    st.error(label_map["pw_required"])
                elif _pr_pw1 != _pr_pw2:
                    st.error(label_map["pw_mismatch"])
                elif any(u.get("email", "").lower() == _pr_email for u in _acct_users):
                    st.error(label_map["email_taken"])
                else:
                    _pr_id = hashlib.md5(_pr_email.encode()).hexdigest()[:12]
                    _acct_users.append({
                        "id": _pr_id,
                        "name": _pr_name.strip(),
                        "email": _pr_email,
                        "password_hash": hash_password(_pr_pw1),
                        "lang": st.session_state.get("lang", "EN"),
                    })
                    save_users(_acct_users)
                    login_as(_pr_id)
                    st.rerun()
    else:
        _acct_uname = next((u["name"] for u in _acct_users if u["id"] == _acct_uid), _acct_uid)
        _acct_email = next((u.get("email", "") for u in _acct_users if u["id"] == _acct_uid), "")
        st.markdown(f"**{_acct_uname}**")
        if _acct_email:
            st.caption(_acct_email)
        st.markdown("---")
        if has_backup_flag and st.button(label_map["undo_btn"], key=f"{prefix}_undo_btn"):
            if undo_save():
                st.rerun()
        if st.button(label_map["logout"], key=f"{prefix}_logout_btn", use_container_width=True):
            delete_session(st.session_state.get("_session_token", ""))
            st.query_params.pop("t", None)
            for _k in ["user_id", "active_profile_id", "sidebar_profile_sel", "_session_token"]:
                st.session_state.pop(_k, None)
            st.rerun()

def truncate_single_line(text: str, max_chars: int = 28) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"

def entry_score_100(entry):
    _total = get_total(entry)
    return round(_total / 8 * 100) if _total is not None else -1

def build_entry_scan_parts(entry):
    _score_n = entry_score_100(entry)
    _decision = entry.get("eval_decision", "") or "—"
    _status = entry.get("status", "Not Applied")
    _company = entry.get("company", "—")
    _role = entry.get("role", "—")
    _url = entry.get("url", "")
    _reason = entry.get("eval_reason", "").strip()
    _loc = " / ".join(p for p in [entry.get("country", ""), entry.get("city", "")] if p)
    _salary = entry.get("salary", "")
    _source = entry.get("source", "")
    _meta = "  ·  ".join(p for p in [_loc, _salary, _source] if p)
    _preview = (_reason[:85] + "…" if len(_reason) > 85 else _reason) if _reason else ""
    return {
        "score_n": _score_n,
        "decision": _decision,
        "status": _status,
        "status_icon": STATUS_BADGE.get(_status, "⚪"),
        "company": _company,
        "role": _role,
        "title": _role or _company,
        "url": _url,
        "meta": _meta,
        "preview": _preview,
    }

def render_scan_card_row(parts):
    _preview_text = parts["preview"] or "  ·  ".join(p for p in [parts["meta"]] if p)
    _meta_line = (
        f'<div style="font-size:12px;color:#9ca3af;margin-top:2px">{parts["meta"]}</div>'
        if parts["meta"] else ""
    )
    _preview_line = (
        f'<div style="font-size:13px;color:#6b7280;overflow:hidden;display:-webkit-box;'
        f'-webkit-line-clamp:2;-webkit-box-orient:vertical;margin-top:3px">{_preview_text}</div>'
        if _preview_text else ""
    )
    st.markdown(
        f'<div style="display:flex;align-items:flex-start;gap:12px;padding:6px 0">'
        f'{score_square_html(parts["score_n"], size=52, font_size=20)}'
        f'<div style="flex:1;min-width:0">'
        f'<div style="font-size:16px;font-weight:600;color:#111;white-space:nowrap;overflow:hidden;'
        f'text-overflow:ellipsis">{parts["title"]}</div>'
        f'<div style="font-size:13px;color:#595959;white-space:nowrap;overflow:hidden;'
        f'text-overflow:ellipsis;margin-top:1px">{parts["company"]}</div>'
        f'{_meta_line}{_preview_line}</div>'
        f'<div style="text-align:right;flex-shrink:0;font-size:13px;color:#9ca3af">'
        f'{parts["status_icon"]}<br><span style="font-size:11px">{parts["decision"]}</span></div>'
        f'</div>',
        unsafe_allow_html=True
    )

def render_pipeline_scan_list(entries, empty_label: str, count_label: str | None = None, key_prefix: str = "scan"):
    if not entries:
        st.info(empty_label)
        return

    if count_label:
        st.caption(count_label)

    for _idx, _entry in entries:
        _parts = build_entry_scan_parts(_entry)
        render_scan_card_row(_parts)
        _lc1, _lc2 = st.columns([5, 1])
        if _parts["url"]:
            _lc1.markdown(f"[→ Apply on Company / Job Site]({_parts['url']})")
        _is_saved = _entry.get("bookmarked", False)
        if _lc2.button("⭐" if _is_saved else "☆", key=f"{key_prefix}_star_{_idx}",
                       help=L["unbookmark"] if _is_saved else L["bookmark"],
                       use_container_width=True,
                       type="primary" if _is_saved else "secondary",
                       disabled=demo_mode):
            _d = load_data()
            _d["pipeline"][_idx]["bookmarked"] = not _is_saved
            save_data(_d)
            st.rerun()
        st.markdown("---")

def render_pipeline_dense_list(entries, empty_label: str, count_label: str | None = None):
    if not entries:
        st.info(empty_label)
        return
    if count_label:
        st.caption(count_label)

    _rows = []
    for _idx, _entry in entries:
        _parts = build_entry_scan_parts(_entry)
        _location = " / ".join(p for p in [_entry.get("country", ""), _entry.get("city", "")] if p) or "—"
        _rows.append(
            "<tr>"
            f"<td>{escape(str(_parts['score_n'])) if _parts['score_n'] >= 0 else '—'}</td>"
            f"<td>{escape(_parts['company'])}</td>"
            f"<td>{escape(_parts['title'])}</td>"
            f"<td>{escape(_parts['status'])}</td>"
            f"<td>{escape(_parts['decision'])}</td>"
            f"<td>{escape(_entry.get('source', '') or '—')}</td>"
            f"<td>{escape(_location)}</td>"
            "</tr>"
        )

    st.markdown(
        "<div style='overflow-x:auto;border:1px solid #e5e7eb;border-radius:14px;background:#fff'>"
        "<table style='width:100%;min-width:760px;border-collapse:collapse;font-size:14px'>"
        "<thead>"
        "<tr style='background:#f8fafc;color:#475569'>"
        "<th style='padding:12px 10px;text-align:left;border-bottom:1px solid #e5e7eb'>Score</th>"
        "<th style='padding:12px 10px;text-align:left;border-bottom:1px solid #e5e7eb'>Company</th>"
        "<th style='padding:12px 10px;text-align:left;border-bottom:1px solid #e5e7eb'>Role</th>"
        "<th style='padding:12px 10px;text-align:left;border-bottom:1px solid #e5e7eb'>Status</th>"
        "<th style='padding:12px 10px;text-align:left;border-bottom:1px solid #e5e7eb'>Decision</th>"
        "<th style='padding:12px 10px;text-align:left;border-bottom:1px solid #e5e7eb'>Source</th>"
        "<th style='padding:12px 10px;text-align:left;border-bottom:1px solid #e5e7eb'>Location</th>"
        "</tr>"
        "</thead>"
        "<tbody>"
        + "".join(_rows)
        + "</tbody></table></div>",
        unsafe_allow_html=True,
    )

data = load_data()
if "header_menu_open" not in st.session_state:
    st.session_state["header_menu_open"] = False

def render_brand(_col):
    if os.path.exists(LOGO_PATH):
        _col.image(LOGO_PATH, width=120)
    else:
        _col.markdown('<p style="font-size:18px;font-weight:700;margin:0 0 4px 0;color:#1E293B">KoaFlux</p>', unsafe_allow_html=True)

with st.container():
    st.markdown('<span class="header-top-row-marker"></span>', unsafe_allow_html=True)
    _title_col, _undo_col, _menu_col = st.columns([6, 1, 1])
    render_brand(_title_col)
    if _undo_col.button("↶", key="header_undo_btn", help=L["undo_help"], disabled=not has_backup):
        if undo_save():
            st.rerun()
    if _menu_col.button("☰", key="header_menu_toggle_btn"):
        st.session_state["header_menu_open"] = not st.session_state.get("header_menu_open", False)
        st.rerun()

if st.session_state.get("header_menu_open"):
    with st.container(border=True):
        st.markdown('<span class="header-menu-panel-marker"></span>', unsafe_allow_html=True)
        _mh1, _mh2 = st.columns([1, 0.14])
        _mh1.markdown("**Menu**")
        if _mh2.button("✕", key="header_menu_close_btn"):
            st.session_state["header_menu_open"] = False
            st.rerun()
        _menu_lang_val = st.session_state["lang"]
        _lang_left, _lang_mid, _lang_right = st.columns([1, 2.4, 1])
        with _lang_mid:
            _menu_lang_new = st.radio(
                "Language",
                ["EN", "JP"],
                index=["EN", "JP"].index(_menu_lang_val),
                key="header_menu_lang_radio",
                horizontal=True,
            )
        if _menu_lang_new != _menu_lang_val:
            st.session_state["lang"] = _menu_lang_new
            save_user_lang(get_current_user_id(), _menu_lang_new)
            st.rerun()
        st.markdown("---")
        st.markdown("**Me**")
        _menu_uid = get_current_user_id()
        _menu_users = load_users()
        _menu_user = next((u for u in _menu_users if u["id"] == _menu_uid), None)
        if _menu_user and _menu_uid != "guest":
            st.markdown(f"**{_menu_user.get('name', _menu_uid)}**")
            if _menu_user.get("email"):
                st.caption(_menu_user.get("email"))
            if st.button(L["logout"], key="header_menu_logout_btn", use_container_width=True):
                delete_session(st.session_state.get("_session_token", ""))
                st.query_params.pop("t", None)
                for _k in ["user_id", "active_profile_id", "sidebar_profile_sel", "_session_token"]:
                    st.session_state.pop(_k, None)
                st.session_state["header_menu_open"] = False
                st.rerun()
        else:
            render_account_panel("header_menu_account", L, has_backup)
        st.markdown("---")
        st.markdown(f"**{L['smartphone_qr']}**")
        render_qr_panel("header_menu_qr")

if demo_mode:
    demo_notice()

# ── Profile toggle (main area, mobile-friendly) ───────────────────────────
_mp_data = load_data()
_mp_profiles = get_profiles(_mp_data)
if not _mp_profiles:
    _mp_profiles = [{"id": "default", "name": "Default", "text": PROFILE}]
_mp_pid = get_active_profile_id(_mp_data)
_mp_pname = next((p["name"] for p in _mp_profiles if p["id"] == _mp_pid), _mp_profiles[0]["name"])
_mp_label_name = truncate_single_line(_mp_pname, 18)

with st.expander(L["profile_toggle"] + f": {_mp_label_name}", expanded=False):
    _mp_names = [p["name"] for p in _mp_profiles]
    _mp_ids   = [p["id"]   for p in _mp_profiles]
    _mp_ai    = _mp_ids.index(_mp_pid) if _mp_pid in _mp_ids else 0

    _mp_sel_name = st.selectbox("Profile", _mp_names, index=_mp_ai, key="mp_sel")
    _mp_sel_pid  = _mp_ids[_mp_names.index(_mp_sel_name) if _mp_sel_name in _mp_names else 0]
    if _mp_sel_pid != st.session_state.get("active_profile_id"):
        st.session_state["active_profile_id"] = _mp_sel_pid

    _mp_sel_prof = next(p for p in _mp_profiles if p["id"] == _mp_sel_pid)
    _mp_edit_name = st.text_input("Name", value=_mp_sel_prof["name"], key="mp_edit_name")

    # ── 履歴書PDFアップロード ──────────────────────────────────
    with st.expander("📄 " + L["cv_upload"], expanded=False):
        st.caption(L["cv_caption"])
        _cv_pdf = st.file_uploader("PDF", type="pdf", key=f"cv_pdf_{_mp_sel_pid}",
                                    label_visibility="collapsed")
        if _cv_pdf is not None:
            _cv_hash = hashlib.md5(_cv_pdf.getvalue()).hexdigest()
            if st.session_state.get("cv_last_hash") != _cv_hash:
                try:
                    with pdfplumber.open(_cv_pdf) as _pdf:
                        _cv_text = "\n".join(p.extract_text() or "" for p in _pdf.pages).strip()
                    if _cv_text:
                        st.session_state["cv_extracted"] = _cv_text
                        st.session_state["cv_last_hash"] = _cv_hash
                        st.success(L["cv_extracted"].format(n=len(_cv_text)))
                    else:
                        st.warning(L["cv_no_text"])
                except Exception as _ex:
                    st.error(L["pdf_error"].format(e=_ex))
            _cv_extracted = st.session_state.get("cv_extracted", "")
            if _cv_extracted:
                _ov1, _ov2 = st.columns(2)
                if _ov1.button(L["cv_apply"], key="cv_apply_btn", type="primary"):
                    st.session_state["mp_edit_text"] = _cv_extracted
                    st.rerun()
                if _ov2.button(L["cv_preview"], key="cv_preview_btn"):
                    st.session_state["cv_show_preview"] = not st.session_state.get("cv_show_preview", False)
                if st.session_state.get("cv_show_preview"):
                    st.text_area("抽出テキスト", value=_cv_extracted, height=200,
                                  disabled=True, label_visibility="collapsed")

    _mp_edit_text = st.text_area("Profile text", value=_mp_sel_prof["text"], height=220,
                                  key="mp_edit_text", label_visibility="visible")

    st.markdown('<span class="profile-save-row-marker"></span>', unsafe_allow_html=True)
    _mpc1, _mpc2 = st.columns(2)
    _save_label = "保存" if st.session_state.get("lang","EN")=="JP" else "Save"
    _del_label  = "削除" if st.session_state.get("lang","EN")=="JP" else "Delete"
    if _mpc1.button(_save_label, key="mp_save_btn", use_container_width=True):
        _d = load_data()
        for _p in _d["profiles"]:
            if _p["id"] == _mp_sel_pid:
                _p["name"] = _mp_edit_name.strip() or _p["name"]
                _p["text"] = _mp_edit_text
        _d["active_profile_id"] = _mp_sel_pid
        save_data(_d)
        st.success(L["profile_saved"])
        st.rerun()
    if len(_mp_profiles) > 1:
        if _mpc2.button(_del_label, key="mp_del_btn", use_container_width=True):
            _d = load_data()
            _d["profiles"] = [_p for _p in _d["profiles"] if _p["id"] != _mp_sel_pid]
            _d["active_profile_id"] = _d["profiles"][0]["id"]
            st.session_state["active_profile_id"] = _d["active_profile_id"]
            save_data(_d)
            st.rerun()

    st.markdown("---")
    st.caption(L["new_profile"])
    st.markdown('<span class="profile-new-row-marker"></span>', unsafe_allow_html=True)
    _mpn1, _mpn2 = st.columns(2)
    _blank_label = "ブランク" if st.session_state.get("lang","EN")=="JP" else "Blank"
    _copy_label  = "コピー"   if st.session_state.get("lang","EN")=="JP" else "Copy"
    if _mpn1.button(_blank_label, key="mp_new_blank", use_container_width=True):
        st.session_state["mp_adding"] = "blank"
    if _mpn2.button(_copy_label, key="mp_new_copy", use_container_width=True):
        st.session_state["mp_adding"] = "copy"

    _mp_adding = st.session_state.get("mp_adding")
    if _mp_adding in ("blank", "copy"):
        if _mp_adding == "copy":
            _mp_copy_src_name = st.selectbox("Copy from", _mp_names, key="mp_copy_src")
            _mp_copy_src = next(p for p in _mp_profiles if p["name"] == _mp_copy_src_name)
        _mp_new_name = st.text_input("Profile name", key="mp_new_name", placeholder="e.g. AI Angle")
        if _mp_adding == "blank":
            _mp_new_text = st.text_area("Profile text", height=120, key="mp_new_text",
                                         label_visibility="collapsed", placeholder="Write your profile here...")
        st.markdown('<span class="profile-create-row-marker"></span>', unsafe_allow_html=True)
        _mpc3, _mpc4 = st.columns(2)
        if _mpc3.button(L["create_btn"], key="mp_create_btn", use_container_width=True):
            if _mp_new_name.strip():
                _d = load_data()
                _new_id = _mp_new_name.strip().lower().replace(" ", "_") + f"_{len(_d['profiles'])}"
                _base_text = _mp_copy_src["text"] if _mp_adding == "copy" else _mp_new_text
                _d["profiles"].append({"id": _new_id, "name": _mp_new_name.strip(), "text": _base_text})
                _d["active_profile_id"] = _new_id
                st.session_state["active_profile_id"] = _new_id
                del st.session_state["mp_adding"]
                save_data(_d)
                st.rerun()
        if _mpc4.button(L["cancel"], key="mp_cancel_btn", use_container_width=True):
            del st.session_state["mp_adding"]
            st.rerun()

    st.markdown("---")
    with st.expander(L["anonymization"], expanded=False):
        st.caption(L["anon_caption"])
        _d_anon = load_data()
        _anon_reps = _d_anon.get("anon_replacements", {})
        st.markdown(L["anon_custom_label"])
        for _ai2, (_orig, _rep) in enumerate(list(_anon_reps.items())):
            _ac1, _ac2, _ac3 = st.columns([3, 3, 1])
            _new_orig = _ac1.text_input("元", value=_orig, key=f"mp_anon_orig_{_ai2}", label_visibility="collapsed")
            _new_rep  = _ac2.text_input("置換後", value=_rep, key=f"mp_anon_rep_{_ai2}", label_visibility="collapsed")
            if _ac3.button("✕", key=f"mp_anon_del_{_ai2}"):
                del _anon_reps[_orig]
                _d_anon["anon_replacements"] = _anon_reps
                save_data(_d_anon)
                st.rerun()
            elif _new_orig != _orig or _new_rep != _rep:
                _anon_reps.pop(_orig, None)
                _anon_reps[_new_orig] = _new_rep
        _an1, _an2 = st.columns(2)
        _new_anon_orig = _an1.text_input("+ 追加", key="mp_anon_new_orig", placeholder="例: 株式会社XX")
        _new_anon_rep  = _an2.text_input("→", key="mp_anon_new_rep", placeholder="例: Japanese MNC",
                                          label_visibility="collapsed")
        if st.button(L["anon_add_btn"], key="mp_anon_add_btn"):
            if _new_anon_orig.strip():
                _anon_reps[_new_anon_orig.strip()] = _new_anon_rep.strip()
                _d_anon["anon_replacements"] = _anon_reps
                save_data(_d_anon)
                st.rerun()
        with st.expander(L["send_preview"], expanded=False):
            st.text_area("APIに送信されるプロフィール", value=get_anonymized_profile(_d_anon),
                          height=150, disabled=True, label_visibility="collapsed")

    st.markdown("---")
    # ── アカウント設定（メール・パスワード変更） ──────────────────
    with st.expander("⚙️ " + L["account_settings"], expanded=False):
        _acc_users = load_users()
        _acc_uid   = get_current_user_id()
        _acc_user  = next((u for u in _acc_users if u["id"] == _acc_uid), None)
        if _acc_user:
            _acc_email_cur = _acc_user.get("email", "")
            st.markdown(f"**Email**")
            _acc_email_new = st.text_input("Email", value=_acc_email_cur, key="acc_email_input",
                                            placeholder="you@gmail.com").strip().lower()
            if st.button(L["save_email"], key="acc_email_save"):
                if _acc_email_new and _acc_email_new != _acc_email_cur:
                    _other = [u for u in _acc_users if u["id"] != _acc_uid]
                    if any(u.get("email","").lower() == _acc_email_new for u in _other):
                        st.error(L["email_in_use"])
                    else:
                        for u in _acc_users:
                            if u["id"] == _acc_uid:
                                u["email"] = _acc_email_new
                        save_users(_acc_users)
                        st.success(L["email_saved"])
                        st.rerun()

            st.markdown("**" + L["change_pw"] + "**")
            _acc_pw_cur = st.text_input(L["current_pw"], type="password", key="acc_pw_cur")
            _acc_pw1    = st.text_input(L["new_pw"], type="password", key="acc_pw1")
            _acc_pw2    = st.text_input(L["confirm_pw"], type="password", key="acc_pw2")
            if st.button(L["change_pw"], key="acc_pw_save"):
                if not check_password(_acc_user, _acc_pw_cur):
                    st.error(L["wrong_current_pw"])
                elif not _acc_pw1:
                    st.error(L["new_pw_required"])
                elif _acc_pw1 != _acc_pw2:
                    st.error(L["pw_mismatch"])
                else:
                    for u in _acc_users:
                        if u["id"] == _acc_uid:
                            u["password_hash"] = hash_password(_acc_pw1)
                    save_users(_acc_users)
                    st.success(L["pw_changed"])

tab_eval, tab_overview, tab_list, tab_saved, tab_discover, tab_insights = st.tabs([
    L["tab_eval"],
    L["tab_overview"],
    L["tab_list"],
    L["tab_saved"],
    L["tab_discover"],
    L["tab_insights"],
])

# ════════════════════════════════════════
# TAB 1: OVERVIEW
# ════════════════════════════════════════
with tab_overview:
    pipeline = data.get("pipeline", [])

    def update_status(idx, new_status):
        d = load_data()
        d["pipeline"][idx]["status"] = new_status
        d["pipeline"][idx]["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        save_data(d)
        st.rerun()

    def delete_entry(idx):
        d = load_data()
        d["pipeline"].pop(idx)
        save_data(d)
        st.rerun()

    def save_note(idx, note):
        d = load_data()
        d["pipeline"][idx]["notes"] = note
        save_data(d)
        st.rerun()

    if not pipeline:
        st.info(L["no_pipeline"])
    else:
        # ── Filters: collapse by default so mobile keeps room for the list ──
        st.markdown('<span class="overview-filter-marker"></span>', unsafe_allow_html=True)
        with st.expander("Filters", expanded=False):
            ov_status = st.multiselect("Status", STATUS_OPTIONS, default=STATUS_OPTIONS, key="ov_status")
            ov_decision = st.multiselect("Decision", ["Go", "Stretch", "Explore", "Skip", "—"],
                                         default=["Go", "Stretch", "Explore", "Skip", "—"], key="ov_decision")

        def _entry_score(e):
            t = get_total(e)
            return round(t / 8 * 100) if t is not None else -1

        dec_raw = [e.get("eval_decision","—") or "—" for e in pipeline]
        pipeline_sorted = sorted(enumerate(pipeline), key=lambda x: _entry_score(x[1]), reverse=True)
        pipeline_filtered = [
            (idx, e) for idx, e in pipeline_sorted
            if e.get("status","Not Applied") in ov_status
            and dec_raw[idx] in ov_decision
        ]
        st.caption(L["entries_count"](len(pipeline_filtered), len(pipeline)))

        # ── PC split view ────────────────────────────────────────
        _sel_idx = st.session_state.get("pipeline_selected_idx")
        if _sel_idx is not None and _sel_idx < len(pipeline):
            _list_col, _detail_col = st.columns([1, 3])
        else:
            _list_col = st.container()
            _detail_col = None

        # ── Card list ────────────────────────────────────────────
        with _list_col:
            for orig_idx, entry in pipeline_filtered:
                i = orig_idx
                _parts = build_entry_scan_parts(entry)
                _is_sel = (st.session_state.get("pipeline_selected_idx") == i)
                with st.container(border=True):
                    _card_top = st.container()
                    with _card_top:
                        st.markdown('<span class="overview-card-open-marker"></span>', unsafe_allow_html=True)
                        if st.button("Open details", key=f"card_{i}", use_container_width=True,
                                     type="secondary"):
                            if _is_sel:
                                st.session_state.pop("pipeline_selected_idx", None)
                            else:
                                st.session_state["pipeline_selected_idx"] = i
                            st.rerun()
                        render_scan_card_row(_parts)
                    _oc1, _oc2 = st.columns([5, 1])
                    if _parts["url"]:
                        _oc1.markdown(f"[→ Apply on Company / Job Site]({_parts['url']})")
                    _is_saved = entry.get("bookmarked", False)
                    if _oc2.button("⭐" if _is_saved else "☆", key=f"overview_star_{i}",
                                   help=L["unbookmark"] if _is_saved else L["bookmark"],
                                   use_container_width=True,
                                   type="primary" if _is_saved else "secondary",
                                   disabled=demo_mode):
                        _d = load_data()
                        _d["pipeline"][i]["bookmarked"] = not _is_saved
                        save_data(_d)
                        st.rerun()

        # ── Detail panel (PC split view) ─────────────────────────
        if _detail_col is not None:
            with _detail_col:
                st.markdown('<span class="detail-panel-marker"></span>', unsafe_allow_html=True)
                _sel_e = pipeline[_sel_idx]
                _sel_decision = _sel_e.get("eval_decision","")
                _sel_status   = _sel_e.get("status","")
                _sel_url      = _sel_e.get("url","")
                _sel_reason   = _sel_e.get("eval_reason","")
                _sel_bullets  = _sel_e.get("fit_bullets",[])
                _sel_loc_parts = [_sel_e.get("country",""), _sel_e.get("city","")]

                _sel_has_jd = bool(_sel_e.get("jd_text"))
                _sel_is_bm  = _sel_e.get("bookmarked", False)
                _dp_statuses = ["Not Applied","Applied","Interview","Offer","Rejected","Skip"]

                # ── Split into sticky header + scrollable body ────
                _dp_hdr = st.container()
                _dp_bdy = st.container()

                with _dp_hdr:
                    st.markdown('<span class="dp-header-marker"></span>', unsafe_allow_html=True)

                    _total_dp    = get_total(_sel_e)
                    _score100_dp = round(_total_dp / 8 * 100) if _total_dp is not None else None
                    if _score100_dp is not None:
                        if _score100_dp >= 75:
                            _sbg, _sfg = "#16a34a", "#dcfce7"
                        elif _score100_dp >= 50:
                            _sbg, _sfg = "#d97706", "#fef3c7"
                        else:
                            _sbg, _sfg = "#dc2626", "#fee2e2"

                    # ── Top row: title + close button ──
                    st.markdown('<span class="dp-title-close-row-marker"></span>', unsafe_allow_html=True)
                    _dph_t, _dph_x = st.columns([8.8, 0.8])
                    with _dph_t:
                        _dp_role = _sel_e.get("role","") or _sel_e.get("company","")
                        _dp_company = _sel_e.get("company","")
                        st.markdown(
                            f'<div style="font-size:20px;font-weight:700;color:#111;line-height:1.3;'
                            f'word-break:break-word;white-space:normal;margin-bottom:4px">'
                            f'{_dp_role}</div>'
                            f'<div style="font-size:16px;font-weight:600;color:#374151;margin-bottom:4px">'
                            f'{_dp_company}</div>',
                            unsafe_allow_html=True
                        )
                    with _dph_x:
                        if st.button("✕", key="close_detail", type="secondary"):
                            st.session_state.pop("pipeline_selected_idx", None)
                            st.rerun()
                    if _score100_dp is not None:
                        _ds_sp, _ds_score = st.columns([1, 1.2])
                        with _ds_sp:
                            st.markdown('<span class="detail-score-row-marker"></span>', unsafe_allow_html=True)
                        with _ds_score:
                            st.markdown(
                                score_square_html(_score100_dp, size=58, font_size=22),
                                unsafe_allow_html=True
                            )
                    if _sel_url:
                        st.markdown(f"[→ Apply on Company / Job Site]({_sel_url})")

                    _ca1, _ca2, _ca3, _ca4, _ca5 = st.columns(5)
                    if _ca1.button("💾 Save", key=f"dp_upd_h_{_sel_idx}", help=L["save_status_help"],
                                   use_container_width=True, disabled=True):
                        pass  # real save is below after selectbox
                    if _ca2.button("🔄 Re-eval", key=f"dp_reeval_h_{_sel_idx}",
                                   disabled=demo_mode or not (_sel_has_jd and api_key),
                                   help=L["re_eval_help"] if _sel_has_jd else L["no_jd"],
                                   use_container_width=True):
                        with st.spinner(L["re_evaluating"]):
                            try:
                                r = evaluate(_sel_e["jd_text"], api_key, lang=st.session_state.get("lang","EN"))
                                d = load_data()
                                d["pipeline"][_sel_idx].update({
                                    "eval_decision":           r.get("decision"),
                                    "eval_reason":             r.get("reason",""),
                                    "trajectory_fit":          r.get("trajectory_fit"),
                                    "core_strength_match":     r.get("core_strength_match"),
                                    "attraction":              r.get("attraction"),
                                    "competitiveness":         r.get("competitiveness"),
                                    "secondary_strength_used": r.get("secondary_strength_used",""),
                                    "practical_constraint":    r.get("practical_constraint",""),
                                    "risk":                    r.get("risk",""),
                                    "fit_bullets":             r.get("fit_bullets",[]),
                                    "eval_risk":               r.get("main_risk",""),
                                    "gap_note":                r.get("gap_note",""),
                                    "direction_warning":       r.get("direction_warning",""),
                                    "updated":                 datetime.now().strftime("%Y-%m-%d %H:%M"),
                                    "eval_profile_id":         get_active_profile_id(d),
                                })
                                save_data(d)
                                st.rerun()
                            except Exception as e:
                                st.error(f'{L["error_prefix"]}: {e}')
                    if _ca3.button("✏️ Edit", key=f"dp_edit_h_{_sel_idx}",
                                   disabled=demo_mode,
                                   help=L["edit_help"], use_container_width=True):
                        _ekey = f"editing_{_sel_idx}"
                        st.session_state[_ekey] = not st.session_state.get(_ekey, False)
                        st.rerun()
                    if st.session_state.get(f"confirm_del_{_sel_idx}"):
                        st.warning(L["confirm_del"])
                        _dy, _dn = st.columns(2)
                        if _dy.button(L["confirm_del_yes"], key=f"dp_yes_{_sel_idx}"):
                            del st.session_state[f"confirm_del_{_sel_idx}"]
                            delete_entry(_sel_idx)
                        if _dn.button(L["confirm_del_no"], key=f"dp_no_{_sel_idx}"):
                            del st.session_state[f"confirm_del_{_sel_idx}"]
                            st.rerun()
                    elif _ca4.button("🗑 Delete", key=f"dp_del_h_{_sel_idx}",
                                     disabled=demo_mode,
                                     help="Delete", use_container_width=True):
                        st.session_state[f"confirm_del_{_sel_idx}"] = True
                        st.rerun()
                    _bm_icon_dp = "☆ Save" if not _sel_is_bm else "⭐ Saved"
                    if _ca5.button(_bm_icon_dp, key=f"dp_bm_h_{_sel_idx}",
                                   help=L["unbookmark"] if _sel_is_bm else L["bookmark"],
                                   use_container_width=True,
                                   disabled=demo_mode,
                                   type="primary" if _sel_is_bm else "secondary"):
                        d = load_data()
                        d["pipeline"][_sel_idx]["bookmarked"] = not _sel_is_bm
                        save_data(d)
                        st.rerun()
                # ── end of sticky header ──────────────────────────

                with _dp_bdy:
                    st.markdown('<span class="dp-body-marker"></span>', unsafe_allow_html=True)

                    # ── Score + dimension grading ─────────────────
                    if _sel_decision:
                        st.markdown(decision_chips(_sel_decision), unsafe_allow_html=True)
                    if get_total(_sel_e) is not None:
                        render_dimensions(_sel_e)
                    if _sel_e.get("risk"):
                        st.caption(L["risk_label"] + f": {RISK_BADGE.get(_sel_e['risk'], _sel_e['risk'])}")

                    st.markdown("---")

                    # ── Status + meta ─────────────────────────────
                    _dp_new_status = st.selectbox("", _dp_statuses,
                        index=_dp_statuses.index(_sel_status) if _sel_status in _dp_statuses else 0,
                        key=f"dp_sel_{_sel_idx}", label_visibility="collapsed")
                    if _dp_new_status != _sel_status:
                        if st.button("💾 " + L["save_status_help"], key=f"dp_upd_{_sel_idx}", disabled=demo_mode):
                            update_status(_sel_idx, _dp_new_status)
                    _sel_meta = "  ·  ".join(p for p in [
                        f"📍 {' / '.join(p for p in _sel_loc_parts if p)}" if any(_sel_loc_parts) else "",
                        f"💰 {_sel_e['salary']}" if _sel_e.get("salary") else "",
                        _sel_e.get("source",""), _sel_e.get("updated","")
                    ] if p)
                    if _sel_meta: st.caption(_sel_meta)

                    st.markdown("---")

                    # ── Reason + bullets ──────────────────────────
                    if _sel_reason:
                        st.markdown(f"**Reason:** {t_text(_sel_reason)}")
                    for b in t_list(_sel_bullets):
                        st.write(f"✓ {b}")
                    if _sel_e.get("eval_risk"):
                        st.write(f"⚠ {t_text(_sel_e['eval_risk'])}")
                    if _sel_e.get("gap_note"):
                        st.caption(f"💬 {t_text(_sel_e['gap_note'])}")

                    st.markdown("---")

                    # ── Edit form ─────────────────────────────────
                    if st.session_state.get(f"editing_{_sel_idx}"):
                        with st.form(key=f"dp_edit_form_{_sel_idx}"):
                            ef1, ef2 = st.columns(2)
                            e_company = ef1.text_input("Company", value=_sel_e.get("company",""), key=f"dp_ec_{_sel_idx}")
                            e_role    = ef2.text_input("Position", value=_sel_e.get("role",""), key=f"dp_er_{_sel_idx}")
                            ef3, ef4, ef5, ef6, ef7 = st.columns(5)
                            _src_opts_dp = get_source_options()
                            _src_def_dp  = _sel_e.get("source","")
                            _src_idx_dp  = _src_opts_dp.index(_src_def_dp) if _src_def_dp in _src_opts_dp else 0
                            e_source  = ef3.selectbox("Source", _src_opts_dp, index=_src_idx_dp, key=f"dp_es_{_sel_idx}")
                            e_country = ef4.text_input("Country", value=_sel_e.get("country",""), key=f"dp_eco_{_sel_idx}", placeholder="e.g. Singapore")
                            e_city    = ef5.text_input("City", value=_sel_e.get("city",""), key=f"dp_eci_{_sel_idx}", placeholder="e.g. CBD")
                            e_job_id  = ef6.text_input("Job ID", value=_sel_e.get("job_id",""), key=f"dp_ej_{_sel_idx}")
                            e_salary  = ef7.text_input("Salary", value=_sel_e.get("salary",""), key=f"dp_esa_{_sel_idx}")
                            e_url     = st.text_input("URL", value=_sel_e.get("url",""), key=f"dp_eu_{_sel_idx}")
                            if st.form_submit_button("💾 Save changes"):
                                d = load_data()
                                d["pipeline"][_sel_idx].update({
                                    "company": e_company, "role": e_role,
                                    "source": e_source, "country": e_country, "city": e_city,
                                    "job_id": e_job_id, "salary": e_salary, "url": e_url,
                                    "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                })
                                save_data(d)
                                del st.session_state[f"editing_{_sel_idx}"]
                                st.rerun()

                    # ── Notes (bottom) ────────────────────────────
                    _dp_cur_note = _sel_e.get("notes","")
                    _dp_new_note = st.text_area("Notes", value=_dp_cur_note, height=80,
                                                key=f"dp_note_{_sel_idx}", label_visibility="collapsed",
                                                placeholder=L["notes_placeholder"])
                    if _dp_new_note != _dp_cur_note:
                        if st.button(L["save_note"], key=f"dp_savenote_{_sel_idx}", disabled=demo_mode):
                            save_note(_sel_idx, _dp_new_note)

        # ── CSV export (bottom) ───────────────────────────────────
        st.markdown("---")
        export_rows = []
        for e in pipeline:
            total_e = get_total(e)
            export_rows.append({
                "Company":    e.get("company",""), "Role": e.get("role",""),
                "Score (pts)": round(total_e/8*100) if total_e is not None else "",
                "Decision":   e.get("eval_decision",""), "Risk": e.get("risk",""),
                "Status":     e.get("status",""), "Source": e.get("source",""),
                "Country":    e.get("country",""), "City": e.get("city",""),
                "Salary":     e.get("salary",""), "Job ID": e.get("job_id",""),
                "Notes":      e.get("notes",""), "URL": e.get("url",""),
                "Updated":    e.get("updated",""),
            })
        df_export = pd.DataFrame(export_rows)
        csv_data = df_export.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(L["export"], data=csv_data,
                           file_name=f"job_pipeline_{date.today().strftime('%Y%m%d')}.csv",
                           mime="text/csv")

    st.markdown("---")
    with st.expander("+ Add role manually"):
        a1, a2, a3 = st.columns([2, 3, 2])
        new_company    = a1.text_input("Company", key="new_company")
        new_role       = a2.text_input("Position", key="new_role")
        new_status_add = a3.selectbox("Status", STATUS_OPTIONS, key="new_status")
        a4, a5, a6 = st.columns([2, 2, 3])
        _src_opts_m    = get_source_options()
        new_source_sel = a4.selectbox("Source", _src_opts_m, key="new_source")
        new_job_id     = a5.text_input("Job ID", key="new_job_id")
        new_url        = a6.text_input("URL", key="new_url")
        if new_source_sel == "Other":
            new_source_custom = st.text_input("媒体名を入力（追加されます）", key="new_source_custom")
            new_source = new_source_custom.strip() if new_source_custom.strip() else "Other"
        else:
            new_source = new_source_sel
        al1, al2, al3, al4 = st.columns(4)
        new_country = al1.text_input("Country", key="new_country", placeholder="e.g. Singapore")
        new_city    = al2.text_input("City", key="new_city", placeholder="e.g. CBD")
        new_salary  = al3.text_input("Salary", key="new_salary", placeholder="e.g. SGD 8,000–10,000")
        new_action  = al4.text_input("Next Action", key="new_action")
        if st.button(L["add_to_pipeline"], disabled=demo_mode):
            if new_company and new_role:
                data["pipeline"].append({
                    "company": new_company, "role": new_role,
                    "status": new_status_add, "next_action": new_action,
                    "url": new_url, "source": new_source, "job_id": new_job_id,
                    "country": new_country, "city": new_city,
                    "salary": new_salary, "notes": "",
                    "updated": datetime.now().strftime("%Y-%m-%d %H:%M")
                })
                save_data(data)
                add_custom_source(new_source)
                st.success(L["added"])
                st.rerun()

# ════════════════════════════════════════
# TAB: LIST
# ════════════════════════════════════════
with tab_list:
    _list_pipeline = list(enumerate(data.get("pipeline", [])))
    _list_pipeline = sorted(_list_pipeline, key=lambda x: entry_score_100(x[1]), reverse=True)
    render_pipeline_dense_list(
        _list_pipeline,
        empty_label=L["no_pipeline"],
        count_label=f"{len(_list_pipeline)} {L['tab_list']}"
    )

# ════════════════════════════════════════
# TAB: SAVED
# ════════════════════════════════════════
with tab_saved:
    _saved_pipeline = [(i, e) for i, e in enumerate(data.get("pipeline", [])) if e.get("bookmarked")]
    render_pipeline_scan_list(
        _saved_pipeline,
        empty_label=L["no_saved"],
        count_label=f"{len(_saved_pipeline)} {L['tab_saved'].replace('🔖','').strip()}" if _saved_pipeline else None,
        key_prefix="saved"
    )


# ════════════════════════════════════════
# TAB: INSIGHTS (goal + patterns + search brief)
# ════════════════════════════════════════
with tab_insights:
    pipeline_ins = data.get("pipeline", [])

    # ── Goal tracker ─────────────────────────────────────────────
    with st.expander("Goal & Funnel", expanded=True):
        target_offers = st.number_input("目標内定数（意思決定に必要）", min_value=1, max_value=10, value=2, step=1)

        n_applied    = sum(1 for p in pipeline if p.get("status") in ["Applied", "Interview", "Offer", "Rejected"])
        n_interview  = sum(1 for p in pipeline if p.get("status") in ["Interview", "Offer"])
        n_offer      = sum(1 for p in pipeline if p.get("status") == "Offer")

        # Funnel assumptions for senior APAC roles
        APPLY_TO_INTERVIEW = 0.25
        INTERVIEW_TO_OFFER = 0.30
        APPLY_TO_OFFER     = APPLY_TO_INTERVIEW * INTERVIEW_TO_OFFER  # ~7.5%

        offers_needed   = max(0, target_offers - n_offer)
        apps_needed     = round(offers_needed / APPLY_TO_OFFER) if offers_needed > 0 else 0
        apps_gap        = max(0, apps_needed - n_applied)

        today = date.today()
        HARD_DEADLINE = date(2026, 4, 30)
        APR_START     = date(2026, 4, 1)
        days_left  = max(0, (HARD_DEADLINE - today).days)
        weeks_left = max(1, days_left // 7)
        weekly_pace = round(apps_gap / weeks_left, 1) if apps_gap > 0 else 0

        # ── 4月カレンダー（週別） ─────────────────────────────
        if days_left <= 7:
            urgency_label = L["urgency_1week"]
        elif days_left <= 14:
            urgency_label = L["urgency_2week"]
        else:
            urgency_label = L["urgency_days"](days_left)

        # 4月を5週に分割: W1=Apr1-7, W2=8-14, W3=15-21, W4=22-28, W5=29-30
        weeks_apr = [
            ("W1", date(2026,4,1),  date(2026,4,7)),
            ("W2", date(2026,4,8),  date(2026,4,14)),
            ("W3", date(2026,4,15), date(2026,4,21)),
            ("W4", date(2026,4,22), date(2026,4,28)),
            ("W5", date(2026,4,29), date(2026,4,30)),
        ]

        # 各週に応募された件数
        def apps_in_week(wstart, wend):
            return sum(
                1 for p in pipeline
                if p.get("status") in ["Applied","Interview","Offer","Rejected"]
                and p.get("updated","")[:10] >= str(wstart)
                and p.get("updated","")[:10] <= str(wend)
            )

        st.markdown(f'<div style="margin-bottom:8px"><strong>{L["deadline_calendar_title"]}</strong> &nbsp; <span style="font-size:13px;color:#888">{urgency_label}</span></div>', unsafe_allow_html=True)

        week_cols = st.columns(5)
        for col, (wlabel, wstart, wend) in zip(week_cols, weeks_apr):
            wapps = apps_in_week(wstart, wend)
            is_current = wstart <= today <= wend
            is_past    = today > wend
            is_future  = today < wstart

            if is_past:
                bg = "#f3f4f6"; border = "1px solid #e5e7eb"; label_color = "#9ca3af"
                status_icon = "✓" if wapps > 0 else "—"
            elif is_current:
                bg = "#eff6ff"; border = "2px solid #2563eb"; label_color = "#2563eb"
                status_icon = "▶ NOW"
            else:
                bg = "#fafafa"; border = "1px dashed #d1d5db"; label_color = "#6b7280"
                status_icon = ""

            days_str = f"{wstart.strftime('%m/%d')}–{wend.strftime('%m/%d')}"
            apps_str = f"{wapps} apps" if wapps > 0 else ""

            col.markdown(
                f'<div style="background:{bg};border:{border};border-radius:6px;padding:8px 6px;text-align:center">'
                f'<div style="font-size:11px;color:{label_color};font-weight:bold">{wlabel}</div>'
                f'<div style="font-size:10px;color:#9ca3af;margin:2px 0">{days_str}</div>'
                f'<div style="font-size:12px;color:#374151;font-weight:bold;margin-top:4px">{apps_str}</div>'
                f'<div style="font-size:10px;color:{label_color}">{status_icon}</div>'
                f'</div>',
                unsafe_allow_html=True
            )
        st.markdown("")

        g1, g2, g3, g4 = st.columns(4)
        g1.metric("Applied", n_applied)
        g2.metric("Interview", n_interview)
        g3.metric("Offer", f"{n_offer} / {target_offers}")
        g4.metric("Offers needed", offers_needed, delta=f"-{offers_needed}" if offers_needed > 0 else "達成")

        if offers_needed > 0:
            st.info(L["goal_msg"](offers_needed, apps_gap, weeks_left, weekly_pace))
        else:
            st.success(L["goal_done"](target_offers))

        st.caption(L["conversion_note"])

    st.markdown("---")

    # ── Pattern Insights ─────────────────────────────────────────
    evaluated = [e for e in pipeline if e.get("eval_decision")]
    MIN_EVAL = 5
    if len(evaluated) >= MIN_EVAL:
        with st.expander("Pattern Insights", expanded=True):
            n_eval = len(evaluated)
            go_c      = sum(1 for e in evaluated if e.get("eval_decision") == "Go")
            stretch_c = sum(1 for e in evaluated if e.get("eval_decision") == "Stretch")
            explore_c = sum(1 for e in evaluated if e.get("eval_decision") == "Explore")
            skip_c    = sum(1 for e in evaluated if e.get("eval_decision") == "Skip")

            # Distribution bar
            ic1, ic2, ic3, ic4, ic5 = st.columns(5)
            ic1.metric("Evaluated", n_eval)
            ic2.metric("Go", go_c)
            ic3.metric("Stretch", stretch_c)
            ic4.metric("Explore", explore_c)
            ic5.metric("Skip", skip_c)

            skip_pct = round(skip_c / n_eval * 100)
            go_pct   = round(go_c / n_eval * 100)

            # Dimension averages — V3軸で統一表示（V2/V1は軸をマッピング）
            # V3: trajectory_fit, core_strength_match, attraction, competitiveness
            # V2: trajectory_fit, strength_utilization, personal_attraction, practical_fit → map to V3 names
            # V1: role_fit → trajectory_fit相当, capability_fit → core_strength_match相当 etc.
            def to_v3(e):
                v = _detect_version(e)
                if v == "v3":
                    return {k: e.get(k) for k in DIMS}
                if v == "v2":
                    return {
                        "trajectory_fit":    e.get("trajectory_fit"),
                        "core_strength_match": e.get("strength_utilization"),
                        "attraction":         e.get("personal_attraction"),
                        "competitiveness":    e.get("practical_fit"),
                    }
                return {
                    "trajectory_fit":    e.get("role_fit"),
                    "core_strength_match": e.get("capability_fit"),
                    "attraction":         e.get("market_value"),
                    "competitiveness":    e.get("practical_fit"),
                }

            scored_v3 = [to_v3(e) for e in evaluated if get_total(e) is not None]
            if scored_v3:
                avg = {d: sum(s[d] for s in scored_v3 if s[d] is not None) /
                          max(1, sum(1 for s in scored_v3 if s[d] is not None))
                       for d in DIMS}
                weakest_dim  = min(avg, key=lambda d: avg[d])
                strongest_dim = max(avg, key=lambda d: avg[d])

                st.markdown("**Average score by dimension** (across evaluated roles):")
                dcols = st.columns(4)
                for _ii, _dd in enumerate(DIMS):
                    dcols[_ii].markdown(
                        f"**{DIM_LABEL[_dd]}**  \n{score_bar_html(avg[_dd])}  \n{avg[_dd]:.1f}/2",
                        unsafe_allow_html=True
                    )
                st.markdown("---")
            else:
                scored_v3 = []
                avg = {d: 0 for d in DIMS}
                weakest_dim = DIMS[0]
                strongest_dim = DIMS[0]

            # Applied vs evaluated ratio
            n_applied_from_eval = sum(1 for e in evaluated
                                       if e.get("status") in ["Applied", "Interview", "Offer"])
            apply_rate = round(n_applied_from_eval / n_eval * 100) if n_eval else 0

            # Strategic suggestions (rule-based)
            suggestions = []
            if skip_pct >= 60:
                suggestions.append(L["insight_skip_high"](skip_pct))
            if scored_v3 and avg[weakest_dim] < 1.0:
                suggestions.append(L["insight_weakest_dim"](DIM_LABEL[weakest_dim], avg[weakest_dim]))
            if apply_rate < 30 and go_c >= 2:
                suggestions.append(L["insight_apply_gap"](go_c, n_applied_from_eval, apply_rate))
            if go_pct < 15 and n_eval >= 10:
                suggestions.append(L["insight_go_low"](go_pct))
            if scored_v3 and avg.get("competitiveness", 0) >= 1.5 and avg.get("trajectory_fit", 0) < 1.0:
                suggestions.append(L["insight_trajectory_gap"])

            if suggestions:
                st.markdown(f"**{L['strategic_signals']}**")
                for s in suggestions:
                    st.markdown(s)
            else:
                st.success(L["balanced_eval"](go_pct, skip_pct))

            st.caption(L["pattern_stats_caption"](n_eval))

    # ── Search Brief ─────────────────────────────────────────────
    st.markdown("---")
    with st.expander(L["search_brief_expander"], expanded=False):
        brief = data.get("search_brief")
        if brief:
            st.caption(L["generated_at"] + f": {data.get('search_brief_generated', '—')}")
            st.markdown(f"**Insight:** {brief.get('insight', '')}")
            st.markdown("---")
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"**{L['brief_target_titles']}**")
                for t in brief.get("target_titles", []):
                    st.write(f"✓ {t}")
                st.markdown(f"**{L['brief_company_types']}**")
                for c in brief.get("company_types", []):
                    st.write(f"🏢 {c}")
                st.markdown(f"**{L['brief_target_companies']}**")
                for c in brief.get("target_companies", []):
                    st.write(f"⭐ {c}")
            with col_b:
                st.markdown(f"**{L['brief_avoid_titles']}**")
                for t in brief.get("avoid_titles", []):
                    st.write(f"✗ {t}")
                st.markdown(f"**{L['brief_must_keywords']}**")
                for k in brief.get("must_keywords", []):
                    st.write(f"🔍 {k}")
                st.markdown(f"**{L['brief_avoid_keywords']}**")
                for k in brief.get("avoid_keywords", []):
                    st.write(f"✗ {k}")

            st.markdown("---")
            st.markdown(f"**{L['brief_platform_queries']}**")
            platform_searches = brief.get("platform_searches", {})
            for platform, queries in platform_searches.items():
                if queries:
                    st.markdown(f"**{platform}**")
                    for q in queries:
                        st.code(q, language=None)

        n_eval_brief = len([e for e in pipeline if e.get("eval_decision")])
        can_generate = api_key and n_eval_brief >= 3
        btn_label = L["brief_regenerate_btn"] if brief else L["brief_generate_btn"]
        if st.button(btn_label, disabled=not can_generate,
                     help=L["brief_help"]):
            with st.spinner(L["brief_spinner"]):
                try:
                    result = generate_search_brief(api_key, data)
                    d = load_data()
                    d["search_brief"] = result
                    d["search_brief_generated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    save_data(d)
                    st.rerun()
                except Exception as e:
                    st.error(f'{L["error_prefix"]}: {e}')
        if not can_generate:
            st.caption(L["eval_count_brief"](n_eval_brief))


# ════════════════════════════════════════
# TAB 3: DISCOVER
# ════════════════════════════════════════
with tab_discover:
    st.subheader(L["discover_subheader"])
    st.info(L["discover_beta_info"], icon=None)

    brief = data.get("search_brief", {})
    has_brief = bool(brief)
    discovered = data.get("discovered", [])
    active_jobs = [j for j in discovered if j.get("status") not in ("pipeline", "dismissed")]

    # ── コントロール行 ────────────────────────────────────────
    hb1, hb2, hb3 = st.columns([3, 2, 1])
    run_btn    = hb1.button("🔍 Run Discovery", type="primary", disabled=demo_mode or not api_key,
                             help=L["run_discovery_help"])
    screen_btn = hb2.button(L["screen_unscreened_btn"], disabled=demo_mode or not api_key,
                             help=L["screen_unscreened_help"])
    clear_btn  = hb3.button("🗑", help=L["clear_list_help"], disabled=demo_mode)

    n_unscreened = sum(1 for j in discovered if not j.get("screen_verdict") and j.get("status") not in ("pipeline", "dismissed"))
    if n_unscreened > 0:
        st.caption(L["unscreened_count"](n_unscreened))
    elif not active_jobs:
        st.caption(L["run_discovery_hint"])

    if not has_brief:
        st.warning(L["no_search_brief_warn"])

    # Run Discovery（取得 + スクリーニングを一括）
    if run_btn:
        with st.spinner(L["spinner_angles"]):
            d = load_data()
            angles = get_discovery_angles(d)
            angle_labels = [f"{ANGLE_LABEL[a[0]][1]}: {a[1]}" for a in angles]
            st.caption(L["angles_caption"](angle_labels))

        with st.spinner(L["spinner_fetching_jobs"]):
            new_jobs = run_discovery(d)
            d["discovered"] = new_jobs + d.get("discovered", [])
            save_data(d)

        if new_jobs:
            with st.spinner(L["spinner_screening"](len(new_jobs))):
                screened = screen_jobs(new_jobs, api_key, d)
                id_map = {j["id"]: j for j in screened}
                d = load_data()
                for j in d["discovered"]:
                    if j["id"] in id_map:
                        j["screen_verdict"] = id_map[j["id"]].get("screen_verdict", "")
                        j["screen_reason"]  = id_map[j["id"]].get("screen_reason", "")
                save_data(d)
            st.success(L["fetch_done_n"](len(new_jobs)))
            st.rerun()
        else:
            st.info(L["no_new_jobs"])
            st.rerun()

    # Screen unscreened
    if screen_btn:
        d = load_data()
        unscreened = [j for j in d.get("discovered", [])
                      if not j.get("screen_verdict") and j.get("status") not in ("pipeline","dismissed")]
        if unscreened:
            with st.spinner(L["spinner_screening"](len(unscreened))):
                screened = screen_jobs(unscreened, api_key, d)
                id_map = {j["id"]: j for j in screened}
                for j in d["discovered"]:
                    if j["id"] in id_map:
                        j["screen_verdict"] = id_map[j["id"]].get("screen_verdict", "")
                        j["screen_reason"]  = id_map[j["id"]].get("screen_reason", "")
                save_data(d)
                st.rerun()

    if clear_btn:
        d = load_data()
        d["discovered"] = []
        save_data(d)
        st.rerun()

    st.markdown("---")

    # ── 結果表示 ──────────────────────────────────────────────
    worth_jobs = [j for j in active_jobs if j.get("screen_verdict") == "Worth"]
    skip_jobs  = [j for j in active_jobs if j.get("screen_verdict") == "Skip"]
    unscr_jobs = [j for j in active_jobs if not j.get("screen_verdict")]

    def render_job_card(j, idx_key):
        icon, angle_name, angle_desc = ANGLE_LABEL.get(j.get("search_angle","core"), ("⚪","",""))
        reason = j.get("screen_reason", "")
        jc1, jc2 = st.columns([5, 1])
        with jc1:
            st.markdown(
                f"{icon} **{j['title']}**  \n"
                f"<span style='color:#888;font-size:13px'>{j.get('company','')}  ·  "
                f"{j.get('source','')}  ·  {j.get('date','')}</span>  \n"
                f"<span style='font-size:12px;color:#aaa'>*{angle_name}* — {angle_desc}"
                + (f"  ·  {reason}" if reason else "") + "</span>",
                unsafe_allow_html=True
            )
        with jc2:
            if st.button(L["disc_eval_btn"], key=f"disc_eval_{idx_key}", disabled=demo_mode):
                _fg = st.session_state.get("eval_form_gen", 0)
                st.session_state[f"eval_company_{_fg}"] = j.get("company", "")
                st.session_state[f"eval_role_{_fg}"]    = j.get("title", "")
                st.session_state[f"eval_url_{_fg}"]     = j.get("url", "")
                st.session_state[f"eval_source_{_fg}"]  = j.get("source", "")
                d = load_data()
                for dj in d["discovered"]:
                    if dj["id"] == j["id"]:
                        dj["status"] = "pipeline"
                save_data(d)
                st.rerun()

    if not active_jobs:
        st.info(L["no_jobs_discovery"])
    else:
        # Strong matches
        core_worth = [j for j in worth_jobs if j.get("search_angle") in ("core",)]
        if core_worth:
            st.markdown(L["disc_strong_matches"](len(core_worth)))
            for i, j in enumerate(core_worth):
                render_job_card(j, f"core_{i}")
            st.markdown("---")

        # Adjacent / worth a look
        adj_worth = [j for j in worth_jobs if j.get("search_angle") in ("adjacent","company_first")]
        if adj_worth:
            st.markdown(L["disc_worth_look"](len(adj_worth)))
            for i, j in enumerate(adj_worth):
                render_job_card(j, f"adj_{i}")
            st.markdown("---")

        # Blind spots
        blind_worth = [j for j in worth_jobs if j.get("search_angle") == "blind_spot"]
        if blind_worth:
            st.markdown(L["disc_blind_spot"](len(blind_worth)))
            st.caption(L["maybe_fit_caption"])
            for i, j in enumerate(blind_worth):
                render_job_card(j, f"blind_{i}")
            st.markdown("---")

        # What you may be missing
        target_companies = brief.get("target_companies", [])
        pipeline_companies = {e.get("company","").lower() for e in data.get("pipeline",[])}
        missing = [c for c in target_companies
                   if not any(c.lower() in pc or pc in c.lower() for pc in pipeline_companies)]
        if missing:
            st.markdown(f"### {L['missing_companies_header']}")
            st.caption(L["missing_companies_caption"])
            for c in missing[:6]:
                st.write(f"→ {c}")
            st.markdown("---")

        # Skipped (collapsed)
        if skip_jobs:
            with st.expander(f"Skipped by AI ({len(skip_jobs)})", expanded=False):
                for i, j in enumerate(skip_jobs):
                    render_job_card(j, f"skip_{i}")

        if unscr_jobs:
            with st.expander(f"Unscreened ({len(unscr_jobs)})", expanded=False):
                for i, j in enumerate(unscr_jobs):
                    render_job_card(j, f"unscr_{i}")

# ════════════════════════════════════════
# TAB 4: EVALUATE & APPLY
# ════════════════════════════════════════
with tab_eval:
    st.subheader(L["eval_subheader"])
    if demo_mode:
        demo_notice()

    _eval_data = load_data()
    _active_pid = get_active_profile_id(_eval_data)
    _active_pname = get_profile_name(_active_pid, _eval_data)
    st.caption(L["profile_in_use"](_active_pname))

    if "eval_form_gen" not in st.session_state:
        st.session_state["eval_form_gen"] = 0
    _fgen = st.session_state["eval_form_gen"]

    # Inject all staged values before widgets are instantiated
    _staged_key = f"_pdf_staged_{_fgen}"
    if _staged_key in st.session_state:
        st.session_state[f"eval_jd_{_fgen}"] = st.session_state.pop(_staged_key)
    if f"_staged_company_{_fgen}" in st.session_state:
        st.session_state[f"eval_company_{_fgen}"] = st.session_state.pop(f"_staged_company_{_fgen}")
    if f"_staged_role_{_fgen}" in st.session_state:
        st.session_state[f"eval_role_{_fgen}"] = st.session_state.pop(f"_staged_role_{_fgen}")

    # ── URL fetch row ──────────────────────────────────────────────
    eval_url = st.text_input("🔗 LinkedIn URL (optional)", placeholder="https://www.linkedin.com/jobs/view/...",
                              key=f"eval_url_{_fgen}", label_visibility="visible")
    _fetch_btn = st.button("Fetch", key=f"fetch_btn_{_fgen}", use_container_width=True, type="primary")
    if _fetch_btn:
        _raw_url = eval_url.strip()
        if not _raw_url:
            st.warning(L["fetch_url_required"])
        else:
            with st.spinner(L["fetching"]):
                _fetched = fetch_linkedin_jd(_raw_url)
            if _fetched.get("error"):
                st.error(_fetched["error"])
            else:
                if _fetched.get("company") and not st.session_state.get(f"eval_company_{_fgen}"):
                    st.session_state[f"eval_company_{_fgen}"] = _fetched["company"]
                if _fetched.get("title") and not st.session_state.get(f"eval_role_{_fgen}"):
                    st.session_state[f"eval_role_{_fgen}"] = _fetched["title"]
                if _fetched.get("jd"):
                    st.session_state[f"eval_jd_{_fgen}"] = _fetched["jd"]
                if _fetched.get("logo_url"):
                    st.session_state[f"eval_logo_url_{_fgen}"] = _fetched["logo_url"]
                st.success(L['fetch_done'] + f": {_fetched.get('company','')} / {_fetched.get('title','')}")
                st.rerun()

    eval_company = st.text_input(L["company_hint"], key=f"eval_company_{_fgen}")
    eval_role    = st.text_input(L["role_hint"], key=f"eval_role_{_fgen}")
    _src_opts = get_source_options()
    eval_source_sel = st.selectbox(L["source_hint"] + " (optional)", _src_opts, key=f"eval_source_{_fgen}")
    eval_country = st.text_input("Country (optional)", key=f"eval_country_{_fgen}", placeholder="e.g. Singapore")
    eval_city    = st.text_input("City (optional)", key=f"eval_city_{_fgen}", placeholder="e.g. CBD")
    eval_job_id  = st.text_input(L["job_id_hint"], key=f"eval_job_id_{_fgen}")
    eval_salary  = st.text_input(L["salary_hint"], key=f"eval_salary_{_fgen}", placeholder="e.g. SGD 8,000–10,000")
    if eval_source_sel == "Other":
        eval_source_custom = st.text_input("媒体名を入力（追加されます）", key=f"eval_source_custom_{_fgen}")
        eval_source = eval_source_custom.strip() if eval_source_custom.strip() else "Other"
    else:
        eval_source = eval_source_sel

    st.markdown("**Job Description**")
    jd_text = st.text_area("JD", height=260, placeholder=L["jd_hint"],
                             label_visibility="collapsed", key=f"eval_jd_{_fgen}")

    with st.expander("📎 Upload PDF (optional)", expanded=True):
        uploaded_pdf = st.file_uploader("PDF from JD", type="pdf", key=f"eval_pdf_{_fgen}", label_visibility="collapsed")
        if uploaded_pdf is not None:
            file_hash = hashlib.md5(uploaded_pdf.getvalue()).hexdigest()
            processed_key = f"_pdf_hash_{_fgen}"
            if st.session_state.get(processed_key) != file_hash:
                try:
                    with pdfplumber.open(uploaded_pdf) as pdf:
                        extracted = "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
                    if extracted:
                        pdf_dir = os.path.join(os.path.dirname(__file__), "pdfs")
                        os.makedirs(pdf_dir, exist_ok=True)
                        safe_name = re.sub(r"[^\w\-]", "_", f"{eval_company}_{eval_role}")[:60]
                        pdf_path = os.path.join(pdf_dir, f"{safe_name}.pdf")
                        with open(pdf_path, "wb") as f:
                            f.write(uploaded_pdf.getvalue())
                        st.session_state[f"_pdf_path_{_fgen}"] = pdf_path
                        st.session_state[processed_key] = file_hash
                        st.session_state[f"_pdf_staged_{_fgen}"] = extracted
                        st.rerun()
                except Exception as ex:
                    st.error(L["pdf_error"].format(e=ex))
            if st.session_state.get(processed_key) == file_hash:
                _pdf_safe = re.sub(r'[^\w\-]', '_', f'{eval_company}_{eval_role}')[:60]
                st.success(L["pdf_loaded"](_pdf_safe + ".pdf"))

    eb1, eb2 = st.columns([3, 1])
    eb1.markdown('<span class="eval-action-row-marker"></span>', unsafe_allow_html=True)
    eval_btn = eb1.button(L["evaluate_btn"], type="primary",
                          disabled=demo_mode or not (jd_text and api_key), use_container_width=True)
    if eb2.button("🗑 Clear", key="eval_clear", use_container_width=True):
        st.session_state["eval_form_gen"] = _fgen + 1
        st.rerun()

    if eval_btn:
        with st.spinner(L["eval_spinner"]):
            try:
                result = evaluate(jd_text, api_key, lang=st.session_state.get("lang", "EN"))
                decision = result.get("decision", "")

                # Auto-extract company/role from JD if fields were empty
                final_company = eval_company.strip() or result.get("company_name", "").strip()
                final_role    = eval_role.strip()    or result.get("job_title", "").strip()
                if not eval_company.strip() and final_company:
                    st.session_state[f"_staged_company_{_fgen}"] = final_company
                if not eval_role.strip() and final_role:
                    st.session_state[f"_staged_role_{_fgen}"] = final_role

                st.markdown(decision_chips(decision), unsafe_allow_html=True)
                if final_company or final_role:
                    st.caption(f"**{final_company}** {'— ' + final_role if final_role else ''}")
                st.markdown(f"**Risk:** {RISK_BADGE.get(result.get('risk',''), result.get('risk',''))}")
                st.markdown(f"**{t_text(result.get('reason', ''))}**")
                st.markdown("---")

                render_dimensions(result)
                st.markdown("---")

                col_l, col_r = st.columns(2)
                with col_l:
                    st.markdown(f"**{L['why_fit']}**")
                    for b in t_list(result.get("fit_bullets", [])):
                        st.write(f"✓ {b}")
                with col_r:
                    st.markdown(f"**{L['main_risk']}**")
                    st.write(f"⚠ {t_text(result.get('main_risk', ''))}")
                    if result.get("gap_note"):
                        st.caption(f"💬 {t_text(result.get('gap_note', ''))}")

                # Auto-save to pipeline
                data = load_data()
                active_pid = get_active_profile_id(data)
                data["pipeline"].append({
                    "company": final_company,
                    "role": final_role,
                    "status": "Not Applied" if decision in ["Go", "Stretch", "Explore"] else "Skip",
                    "next_action": "Apply" if decision == "Go" else ("検討" if decision == "Stretch" else ("情報収集" if decision == "Explore" else "")),
                    "url": eval_url,
                    "logo_url": st.session_state.get(f"eval_logo_url_{_fgen}", ""),
                    "source": eval_source,
                    "country": eval_country,
                    "city": eval_city,
                    "job_id": eval_job_id,
                    "salary": eval_salary,
                    "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "eval_decision":           decision,
                    "eval_reason":             result.get("reason", ""),
                    "trajectory_fit":          result.get("trajectory_fit"),
                    "core_strength_match":     result.get("core_strength_match"),
                    "attraction":              result.get("attraction"),
                    "competitiveness":         result.get("competitiveness"),
                    "secondary_strength_used": result.get("secondary_strength_used", ""),
                    "practical_constraint":    result.get("practical_constraint", ""),
                    "risk":                    result.get("risk", ""),
                    "fit_bullets":             result.get("fit_bullets", []),
                    "eval_risk":               result.get("main_risk", ""),
                    "gap_note":                result.get("gap_note", ""),
                    "direction_warning":       result.get("direction_warning", ""),
                    "jd_text":                 jd_text,
                    "pdf_path":                st.session_state.get(f"_pdf_path_{_fgen}", ""),
                    "eval_profile_id":         active_pid,
                })
                save_data(data)
                add_custom_source(eval_source)
                st.success(L["saved_with_decision"](decision))

            except Exception as e:
                st.error(f'{L["error_prefix"]}: {e}')
