"""
FS Profiler
-----------
A Streamlit GUI that runs the 15-step feedstock analysis workflow.
Supports two AI backends:
  - Anthropic (claude-opus-4-6) — requires ANTHROPIC_API_KEY in .env
  - Ollama (local LLM)          — free, no API key, requires Ollama installed
"""

import os
import io
from pathlib import Path
from PIL import Image
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import claude_client as cc

load_dotenv()

# ---------------------------------------------------------------------------
# Asset paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
_PROFILER_LOGO = _HERE / "Profiler.png"
_BURNHAM_LOGO  = _HERE / "burnham_logo.png"

def _load_image(path: Path):
    """Return PIL Image if file exists, else None."""
    return Image.open(path) if path.exists() else None

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
_page_icon = _load_image(_PROFILER_LOGO) or "⚗️"
st.set_page_config(
    page_title="FS Profiler",
    page_icon=_page_icon,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Password gate
# ---------------------------------------------------------------------------
# Reads the expected password from Streamlit Cloud Secrets (set under
# Manage app → Settings → Secrets as `profiler_password = "..."`). If no
# secret is configured the app is effectively locked — set the secret
# before deploying.
def _check_password() -> bool:
    if st.session_state.get("_password_correct"):
        return True
    try:
        expected = st.secrets["profiler_password"]
    except (KeyError, FileNotFoundError, Exception):
        expected = ""

    def _on_submit():
        if st.session_state.get("_password_input") == expected and expected:
            st.session_state["_password_correct"] = True
            st.session_state.pop("_password_input", None)
        else:
            st.session_state["_password_correct"] = False

    st.text_input(
        "Password",
        type="password",
        key="_password_input",
        on_change=_on_submit,
    )
    if st.session_state.get("_password_correct") is False:
        st.error("Incorrect password")
    return False

if not _check_password():
    st.stop()

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------
STEPS = [
    "Welcome",
    "Enter Feedstock",
    "Review Sub-Types",
    "Table 1 – High-Level Summary",
    "Table 2 – Citation Table",
    "Table 3 – Detailed Summary",
    "Table 4 – Extraction Sheet",
    "Table 5 – Min/Max Analysis",
    "Table 6 – Theoretical BMP",
    "Export CSVs",
]

def _init_state():
    defaults = {
        "step": 0,
        "feedstock_name": "",
        "subtypes": [],
        "corrections": {},   # keyed by step index
        "table1": None,
        "table2": None,
        "table3": None,
        "table4": None,
        "table5": None,
        "table6": None,
        "generating": False,
        "error": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    [data-testid="stSidebar"] {
        background-color: #858181;
    }
    [data-testid="stSidebar"] * {
        color: #ffffff !important;
    }
    .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"],
    [data-testid="stHeader"], header[data-testid="stHeader"] {
        background-color: #598cb3;
    }
    [data-testid="stMain"] * {
        color: #ffffff !important;
    }
    /* Keep input/textarea text dark so it's readable against the white input background */
    [data-testid="stMain"] input,
    [data-testid="stMain"] textarea,
    [data-testid="stMain"] select,
    [data-testid="stMain"] [data-baseweb="input"] input,
    [data-testid="stMain"] [data-baseweb="textarea"] textarea {
        color: #1a1a1a !important;
        background-color: #ffffff !important;
    }
    /* Start Analysis button */
    [data-testid="stMain"] button[kind="secondary"],
    [data-testid="stMain"] .stButton > button {
        background-color: #325730 !important;
        color: #ffffff !important;
        border: none !important;
    }
    [data-testid="stMain"] .stButton > button:hover {
        background-color: #254520 !important;
        color: #ffffff !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar – progress tracker
# ---------------------------------------------------------------------------
with st.sidebar:
    # Burnham logo at top
    _bl = _load_image(_BURNHAM_LOGO)
    if _bl:
        st.image(_bl, use_container_width=True)
    # Profiler logo + app name
    _pl = _load_image(_PROFILER_LOGO)
    if _pl:
        st.image(_pl, use_container_width=True)
    else:
        st.title("FS Profiler")
    st.markdown("---")
    st.subheader("Progress")
    for i, name in enumerate(STEPS):
        if i < st.session_state.step:
            st.markdown(f"✅ **{name}**")
        elif i == st.session_state.step:
            st.markdown(f"▶️ **{name}**")
        else:
            st.markdown(f"⬜ {name}")
    st.markdown("---")
    if st.button("🔄 Start Over", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def go_to(step: int):
    st.session_state.step = step
    st.session_state.error = ""


def show_error(msg: str):
    st.session_state.error = msg


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def all_tables_to_excel_bytes() -> bytes:
    tables = {
        "Table 1 - High Level Summary": st.session_state.table1,
        "Table 2 - Citations": st.session_state.table2,
        "Table 3 - Detailed Summary": st.session_state.table3,
        "Table 4 - Extraction Sheet": st.session_state.table4,
        "Table 5 - Min Max Analysis": st.session_state.table5,
        "Table 6 - Theoretical BMP": st.session_state.table6,
    }
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for sheet_name, df in tables.items():
            if df is not None:
                df.to_excel(writer, sheet_name=sheet_name, index=False)
    return buf.getvalue()


def approval_block(step_index: int, label: str):
    """Render Approve / Request Corrections UI. Returns ('approved', 'corrections', or None)."""
    st.markdown("---")
    col1, col2 = st.columns([1, 3])
    with col1:
        approved = st.button(f"✅ Approve {label}", key=f"approve_{step_index}", use_container_width=True)
    with col2:
        with st.expander("✏️ Request Corrections"):
            correction_text = st.text_area(
                "Describe what needs to change:",
                key=f"corrections_text_{step_index}",
                height=80,
            )
            request_btn = st.button("Submit Corrections", key=f"submit_corrections_{step_index}")
    if approved:
        return "approved"
    if request_btn and correction_text.strip():
        st.session_state.corrections[step_index] = correction_text.strip()
        return "corrections"
    return None


def generating_spinner(msg: str = "Consulting Claude..."):
    return st.spinner(f"⏳ {msg} This may take 30–120 seconds.")


# ---------------------------------------------------------------------------
# Step renderers
# ---------------------------------------------------------------------------

def step_welcome():
    # Header: title left, Burnham logo right
    _bl = _load_image(_BURNHAM_LOGO)
    col_title, col_brand = st.columns([4, 1])
    with col_title:
        st.title("Burnham's Feedstock Profiler")
    with col_brand:
        if _bl:
            st.image(_bl, width=160)

    # --- Two-column layout: table index LEFT, button + backend RIGHT ---
    col_table, col_action = st.columns([3, 1])

    with col_table:
        st.markdown(
            """
Comprehensive **15-step scientific characterization** of any organic feedstock
for anaerobic digestion (AD), powered by AI.

| Table | Description |
|---|---|
| Table 1 | High-Level Summary of feedstock sub-types |
| Table 2 | 20+ peer-reviewed citations |
| Table 3 | Detailed parameter values (TS, VS, BMP, elemental, proximate, nutrients) |
| Table 4 | Data Extraction Sheet |
| Table 5 | Min/Max Range Analysis per sub-type (TS, VS, BMP, TCOD, SCOD) |
| Table 6 | Theoretical BMP via Buswell Equation vs. measured BMP |

All tables can be downloaded as Excel or CSV at the end.
            """
        )

    with col_action:
        anthropic_ok = cc.check_anthropic()
        ollama_ok, ollama_msg = cc.check_ollama()
        active_backend = cc.get_backend()

        # Backend status badge
        if active_backend == "anthropic" and anthropic_ok:
            st.success("Claude (Anthropic)", icon="✅")
            ready = True
        elif active_backend == "ollama" and ollama_ok and "NOT found" not in ollama_msg:
            st.success(f"Ollama ({cc.OLLAMA_MODEL})", icon="✅")
            ready = True
        elif active_backend == "ollama" and ollama_ok and "NOT found" in ollama_msg:
            st.error(f"Model `{cc.OLLAMA_MODEL}` not installed.\nRun: `ollama pull {cc.OLLAMA_MODEL}`")
            ready = False
        else:
            st.error("No AI backend available.")
            ready = False

        st.markdown("&nbsp;", unsafe_allow_html=True)

        if ready:
            if st.button("▶️ Start Analysis", use_container_width=True):
                go_to(1)
                st.rerun()

    # --- Backend setup details (collapsed expanders below) ---
    st.markdown("---")
    st.subheader("AI Backend Setup")
    col1, col2 = st.columns(2)

    with col1:
        if anthropic_ok:
            st.success("**Anthropic (Claude)** — API key configured", icon="✅")
        else:
            st.warning("**Anthropic (Claude)** — API key not set", icon="🔑")
            with st.expander("How to set up Anthropic API"):
                st.markdown(
                    """
1. Go to **console.anthropic.com** and sign in (or create a free account).
2. Click **API Keys** → **Create Key**.
3. Copy the key (starts with `sk-ant-...`).
4. Open `.env` in the `Feedstock Characterizer` folder.
5. Set: `ANTHROPIC_API_KEY=sk-ant-...`
6. Restart the app.
                    """
                )

    with col2:
        if ollama_ok and "NOT found" not in ollama_msg:
            st.success(f"**Ollama (local AI)** — {ollama_msg}", icon="✅")
        elif ollama_ok:
            st.warning(f"**Ollama (local AI)** — {ollama_msg}", icon="⚠️")
            st.markdown(f"Run: `ollama pull {cc.OLLAMA_MODEL}`")
        else:
            st.info("**Ollama (local AI)** — not running", icon="💡")
            with st.expander("How to set up Ollama (free, no API key)"):
                st.markdown(
                    f"""
Ollama runs AI models locally — completely free, no account needed.

1. Download from **ollama.com** and install it.
2. Run: `ollama pull {cc.OLLAMA_MODEL}` *(~2 GB, one-time)*
3. Restart the app — Ollama will be detected automatically.

**Optional:** Change model via `OLLAMA_MODEL=llama3.1` in `.env`.
*(Recommended: `llama3.2` (fast), `llama3.1` (quality), `mistral`)*
                    """
                )


def step_enter_feedstock():
    st.header("Step 1 – Enter Feedstock Name")
    st.markdown("Type the name of the feedstock you want to characterize (e.g., *Primary Sludge*, *Apple Pomace*, *Onion Waste*).")

    feedstock = st.text_input(
        "Feedstock name:",
        value=st.session_state.feedstock_name,
        placeholder="e.g., Primary Sludge",
    )

    if st.button("Continue →"):
        if not feedstock.strip():
            st.warning("Please enter a feedstock name.")
        else:
            st.session_state.feedstock_name = feedstock.strip()
            # Reset downstream results
            for k in ["subtypes", "table1", "table2", "table3", "table4", "table5", "table6"]:
                st.session_state[k] = [] if k == "subtypes" else None
            go_to(2)
            st.rerun()


def step_subtypes():
    st.header("Step 2 – Identify Feedstock Sub-Types")
    name = st.session_state.feedstock_name
    st.markdown(f"Claude will identify the relevant sub-types of **{name}**.")

    corrections = st.session_state.corrections.get(2, "")

    if not st.session_state.subtypes:
        if st.button("Generate Sub-Types", use_container_width=False):
            with generating_spinner("Identifying sub-types..."):
                try:
                    subtypes = cc.generate_feedstock_types(name, corrections)
                    st.session_state.subtypes = subtypes
                    st.session_state.error = ""
                except Exception as e:
                    show_error(str(e))
            st.rerun()

    if st.session_state.error:
        st.error(st.session_state.error)

    if st.session_state.subtypes:
        st.subheader("Identified Sub-Types")
        for i, t in enumerate(st.session_state.subtypes, 1):
            st.markdown(f"{i}. {t}")

        action = approval_block(2, "Sub-Types")
        if action == "approved":
            go_to(3)
            st.rerun()
        elif action == "corrections":
            st.session_state.subtypes = []
            st.rerun()


def _table_step(
    step_index: int,
    title: str,
    table_key: str,
    generate_fn,
    generate_kwargs: dict,
    csv_filename: str,
    description: str = "",
):
    """Generic renderer for a table generation step."""
    st.header(title)
    if description:
        st.markdown(description)

    corrections = st.session_state.corrections.get(step_index, "")

    if st.session_state[table_key] is None:
        btn_label = "Regenerate" if corrections else "Generate"
        if st.button(f"{btn_label} →", use_container_width=False):
            with generating_spinner(f"Generating {title}..."):
                try:
                    df = generate_fn(**generate_kwargs, corrections=corrections)
                    st.session_state[table_key] = df
                    st.session_state.error = ""
                except Exception as e:
                    show_error(str(e))
            st.rerun()

    if st.session_state.error:
        st.error(st.session_state.error)

    df = st.session_state[table_key]
    if df is not None:
        st.dataframe(df, use_container_width=True, hide_index=True)

        st.download_button(
            label=f"⬇️ Download {csv_filename}",
            data=dataframe_to_csv_bytes(df),
            file_name=csv_filename,
            mime="text/csv",
            key=f"dl_{table_key}",
        )

        action = approval_block(step_index, title.split("–")[0].strip())
        if action == "approved":
            go_to(step_index + 1)
            st.rerun()
        elif action == "corrections":
            st.session_state[table_key] = None
            st.rerun()


def step_table1():
    _table_step(
        step_index=3,
        title="Table 1 – High-Level Summary",
        table_key="table1",
        generate_fn=cc.generate_table1,
        generate_kwargs={
            "feedstock_name": st.session_state.feedstock_name,
            "subtypes": st.session_state.subtypes,
        },
        csv_filename="Table1_High_Level_Summary.csv",
        description="A high-level overview of each sub-type including physical description, sources, moisture content, typical BMP (scf CH4/lb VS), and AD challenges.",
    )


def step_table2():
    _table_step(
        step_index=4,
        title="Table 2 – Citation Table",
        table_key="table2",
        generate_fn=cc.generate_table2,
        generate_kwargs={
            "feedstock_name": st.session_state.feedstock_name,
            "subtypes": st.session_state.subtypes,
        },
        csv_filename="Table2_Citation_Table.csv",
        description="At least 20 peer-reviewed references used to characterize this feedstock.",
    )


def step_table3():
    _table_step(
        step_index=5,
        title="Table 3 – Detailed Parameter Summary",
        table_key="table3",
        generate_fn=cc.generate_table3,
        generate_kwargs={
            "feedstock_name": st.session_state.feedstock_name,
            "subtypes": st.session_state.subtypes,
            "table2_df": st.session_state.table2,
        },
        csv_filename="Table3_Detailed_Summary.csv",
        description="Full parameter table: TS, VS, BMP (scf CH4/lb VS), TCOD, SCOD, kinetics, elemental analysis, proximate analysis, and macro/micronutrients. **Single values only — no ranges.**",
    )


def step_table4():
    _table_step(
        step_index=6,
        title="Table 4 – Data Extraction Sheet",
        table_key="table4",
        generate_fn=cc.generate_table4,
        generate_kwargs={
            "feedstock_name": st.session_state.feedstock_name,
            "subtypes": st.session_state.subtypes,
            "table2_df": st.session_state.table2,
        },
        csv_filename="Table4_Extraction_Sheet.csv",
        description="Documents how each parameter value was obtained from the literature (extraction method, data source, assumptions).",
    )


def step_table5():
    _table_step(
        step_index=7,
        title="Table 5 – Min/Max Range Analysis",
        table_key="table5",
        generate_fn=cc.generate_table5,
        generate_kwargs={
            "feedstock_name": st.session_state.feedstock_name,
            "subtypes": st.session_state.subtypes,
            "table2_df": st.session_state.table2,
        },
        csv_filename="Table5_MinMax_Analysis.csv",
        description="Literature-sourced min/max ranges per feedstock sub-type (rows) for TS, VS, BMP (scf CH4/lb VS), TCOD, and SCOD (columns).",
    )


def step_table6():
    _table_step(
        step_index=8,
        title="Table 6 – Theoretical BMP Analysis (Buswell Equation)",
        table_key="table6",
        generate_fn=cc.generate_table6,
        generate_kwargs={
            "feedstock_name": st.session_state.feedstock_name,
            "subtypes": st.session_state.subtypes,
            "table3_df": st.session_state.table3,
        },
        csv_filename="Table6_Theoretical_BMP_Analysis.csv",
        description="Buswell equation B₀ (theoretical BMP) vs. tested BMP — all values in scf CH4/lb VS — with biodegradability interpretation.",
    )


def step_export():
    st.header("Export Tables")
    name = st.session_state.feedstock_name
    st.markdown(f"All 6 tables for **{name}** are ready.")

    # --- Combined Excel download ---
    safe_name = name.replace(" ", "_")
    st.download_button(
        label="⬇️ Download All Tables as Excel (.xlsx) — one tab per table",
        data=all_tables_to_excel_bytes(),
        file_name=f"{safe_name}_Feedstock_Analysis.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="export_xlsx_all",
        use_container_width=True,
        type="primary",
    )

    st.markdown("---")
    st.markdown("**Or download individual CSV files:**")

    tables = {
        "Table1_High_Level_Summary.csv": st.session_state.table1,
        "Table2_Citation_Table.csv": st.session_state.table2,
        "Table3_Detailed_Summary.csv": st.session_state.table3,
        "Table4_Extraction_Sheet.csv": st.session_state.table4,
        "Table5_MinMax_Analysis.csv": st.session_state.table5,
        "Table6_Theoretical_BMP_Analysis.csv": st.session_state.table6,
    }

    for filename, df in tables.items():
        col1, col2 = st.columns([3, 1])
        with col1:
            label = filename.replace(".csv", "").replace("_", " ")
            st.markdown(f"**{label}**")
        with col2:
            if df is not None:
                st.download_button(
                    label="⬇️ Download",
                    data=dataframe_to_csv_bytes(df),
                    file_name=filename,
                    mime="text/csv",
                    key=f"export_{filename}",
                )
            else:
                st.markdown("*(not generated)*")

    st.markdown("---")
    st.success("Analysis complete! Use 'Start Over' in the sidebar to analyze another feedstock.")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

STEP_FNS = [
    step_welcome,
    step_enter_feedstock,
    step_subtypes,
    step_table1,
    step_table2,
    step_table3,
    step_table4,
    step_table5,
    step_table6,
    step_export,
]

current = st.session_state.step
if 0 <= current < len(STEP_FNS):
    STEP_FNS[current]()
else:
    st.error("Invalid step. Please use 'Start Over'.")
