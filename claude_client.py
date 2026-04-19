"""
AI client for the Feedstock Characterizer app.
Supports two backends:
  - Anthropic (claude-opus-4-6)  — requires ANTHROPIC_API_KEY in .env
  - Ollama (local LLM)           — free, no API key, requires Ollama installed
Backend is auto-detected, or set explicitly via BACKEND=anthropic|ollama in .env.
"""

import os
import re
import json
import socket
import requests
import pandas as pd
from dotenv import load_dotenv

# Force IPv4 to avoid broken IPv6 connectivity issues
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _ipv4_getaddrinfo

load_dotenv()

# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")


def get_backend() -> str:
    """Return 'anthropic' or 'ollama' based on env config / auto-detection."""
    explicit = os.environ.get("BACKEND", "").strip().lower()
    if explicit in ("anthropic", "ollama"):
        return explicit
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key and key != "your_api_key_here":
        return "anthropic"
    return "ollama"


def check_anthropic() -> bool:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return bool(key) and key != "your_api_key_here"


def check_ollama() -> tuple[bool, str]:
    """Returns (is_running, model_status_message)."""
    try:
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            model_short = OLLAMA_MODEL.split(":")[0]
            available = any(model_short in m for m in models)
            if available:
                return True, f"Running · model `{OLLAMA_MODEL}` available"
            else:
                names = ", ".join(models) if models else "none"
                return True, f"Running · configured model `{OLLAMA_MODEL}` NOT found (installed: {names})"
        return False, "Ollama not responding"
    except Exception:
        return False, "Ollama not running (start with: ollama serve)"


# ---------------------------------------------------------------------------
# Core call functions
# ---------------------------------------------------------------------------

def _call_anthropic(prompt: str, max_tokens: int) -> str:
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=key)
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        block.text for block in response.content if hasattr(block, "text")
    )


def _call_ollama(prompt: str, max_tokens: int) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": 0.2,
        },
    }
    r = requests.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json=payload,
        timeout=300,
    )
    r.raise_for_status()
    return r.json().get("response", "")


def call_ai(prompt: str, max_tokens: int = 8000) -> str:
    """Dispatch to the active backend."""
    backend = get_backend()
    if backend == "anthropic":
        return _call_anthropic(prompt, max_tokens)
    else:
        return _call_ollama(prompt, max_tokens)


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

def parse_json_from_response(text: str) -> any:
    """Extract a JSON object or array from a response that may contain markdown fences."""
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence_match:
        candidate = fence_match.group(1).strip()
    else:
        start = min(
            (text.find(c) for c in ["{", "["] if text.find(c) != -1),
            default=-1,
        )
        if start == -1:
            raise ValueError("No JSON found in response.\n\nRaw response:\n" + text[:500])
        candidate = text[start:]
    return json.loads(candidate)


# ---------------------------------------------------------------------------
# Table generation functions
# ---------------------------------------------------------------------------

def generate_feedstock_types(feedstock_name: str, corrections: str = "") -> list[str]:
    correction_block = f"\nUser corrections from previous attempt:\n{corrections}" if corrections else ""
    prompt = f"""You are an expert in anaerobic digestion and organic waste characterization.

The user wants to analyze the feedstock: "{feedstock_name}"

Task: Identify all relevant sub-types or variants of this feedstock that would be found at a wastewater treatment plant or biogas facility.
Return a JSON array of strings, each being a distinct sub-type name. Aim for 5-10 sub-types.
Include only the JSON array — no explanation, no markdown, no preamble.{correction_block}

Example format: ["Sub-type 1", "Sub-type 2", "Sub-type 3"]"""
    response = call_ai(prompt, max_tokens=2000)
    return parse_json_from_response(response)


def generate_table1(feedstock_name: str, subtypes: list[str], corrections: str = "") -> pd.DataFrame:
    correction_block = f"\nUser corrections from previous attempt:\n{corrections}" if corrections else ""
    prompt = f"""You are an expert in anaerobic digestion and organic waste characterization.

Feedstock: "{feedstock_name}"
Sub-types: {json.dumps(subtypes)}

Task: Create a high-level summary table (Table 1) with one row per sub-type.

Columns (exact keys):
"Sub-Type", "Physical Description", "Common Sources", "Moisture Content (%)", "Typical BMP (scf CH4/lb VS)", "Key Challenges for AD"

Rules:
- BMP unit is scf CH4/lb VS. Typical values for organic wastes are roughly 3–12 scf CH4/lb VS.
  (Conversion: 1 mL CH4/g VS = 0.016018 scf CH4/lb VS)
- Each cell must contain a single concise value — no bullet points, no line breaks within cells.
- Base values on published literature and established knowledge.
- Return ONLY a JSON array of objects — no explanation, no markdown, no preamble.
{correction_block}"""
    response = call_ai(prompt, max_tokens=4000)
    data = parse_json_from_response(response)
    return pd.DataFrame(data)


def generate_table2(feedstock_name: str, subtypes: list[str], corrections: str = "") -> pd.DataFrame:
    correction_block = f"\nUser corrections from previous attempt:\n{corrections}" if corrections else ""
    prompt = f"""You are an expert in anaerobic digestion and organic waste characterization.

Feedstock: "{feedstock_name}"
Sub-types: {json.dumps(subtypes)}

Task: Compile at least 20 peer-reviewed references relevant to the anaerobic digestion characterization of these feedstock sub-types.
Focus on papers that report TS, VS, BMP, TCOD, SCOD, elemental composition, or proximate analysis.

Return ONLY a JSON array of objects — no explanation, no markdown, no preamble.
Keys: "Ref ID" (R1, R2, ...), "Authors", "Year", "Title", "Journal", "DOI or Source", "Sub-Types Covered", "Key Parameters Reported"

Rules:
- Each cell must contain a single value — no nested lists. For "Sub-Types Covered" and "Key Parameters Reported", use a comma-separated string.
- Include at least 20 references.
- Use only real, verifiable publications.
{correction_block}"""
    response = call_ai(prompt, max_tokens=16000)
    data = parse_json_from_response(response)
    return pd.DataFrame(data)


def generate_table3(
    feedstock_name: str,
    subtypes: list[str],
    table2_df: pd.DataFrame,
    corrections: str = "",
) -> pd.DataFrame:
    ref_list = table2_df[["Ref ID", "Authors", "Year", "Title"]].to_dict("records")
    correction_block = f"\nUser corrections from previous attempt:\n{corrections}" if corrections else ""
    prompt = f"""You are an expert in anaerobic digestion and organic waste characterization.

Feedstock: "{feedstock_name}"
Sub-types: {json.dumps(subtypes)}

Available references (use Ref IDs when citing): {json.dumps(ref_list)}

Task: Create a detailed parameter table (Table 3) with one row per sub-type.

Columns (in this exact order):
Sub-Type, TS (%), VS/TS (%), BMP (scf CH4/lb VS), Kin1 (days to 75%), Kin2 (days to 100%), TCOD (mg/L), SCOD (mg/L), C (%TS), H (%TS), O (%TS), N (%TS), S (%TS), C:N Ratio, Cab (%VS), Prot (%VS), Fat (%VS), Fiber (%VS), Na (mg/kg TS), K (mg/kg TS), Ca (mg/kg TS), P (mg/kg TS), Mg (mg/kg TS), Fe (mg/kg TS), Zn (mg/kg TS), Cu (mg/kg TS), Mn (mg/kg TS), Primary References

UNIT NOTES:
- BMP must be in scf CH4/lb VS. Convert from mL CH4/g VS by multiplying by 0.016018.
  Typical range for organic wastes: 3–12 scf CH4/lb VS.
- SCOD = Soluble Chemical Oxygen Demand, in mg/L.
- TCOD = Total Chemical Oxygen Demand, in mg/L.

CRITICAL RULES:
1. Every cell must contain a SINGLE numeric value — absolutely no ranges (do NOT write "2-6", write "4.0" instead).
2. If the literature reports a range, calculate and report the mean of the range endpoints.
3. If data is unavailable, write "N/A".
4. "Primary References": comma-separated Ref IDs.
5. Return ONLY a JSON array of objects with the exact column names — no explanation, no markdown.
{correction_block}"""
    response = call_ai(prompt, max_tokens=10000)
    data = parse_json_from_response(response)
    return pd.DataFrame(data)


def generate_table4(
    feedstock_name: str,
    subtypes: list[str],
    table2_df: pd.DataFrame,
    corrections: str = "",
) -> pd.DataFrame:
    ref_list = table2_df[["Ref ID", "Authors", "Year"]].to_dict("records")
    correction_block = f"\nUser corrections from previous attempt:\n{corrections}" if corrections else ""
    prompt = f"""You are an expert in anaerobic digestion and organic waste characterization.

Feedstock: "{feedstock_name}"
Sub-types: {json.dumps(subtypes)}
References: {json.dumps(ref_list)}

Task: Create a data extraction sheet (Table 4) showing how each parameter was obtained.

Columns: Parameter Group, Parameter, Unit, Extraction Method, Data Source (Ref IDs), Notes/Assumptions

Parameter groups:
- Physical Properties: TS, VS
- Biogas Performance: BMP (scf CH4/lb VS), Kin1, Kin2
- Organic Composition: Carbohydrates, Protein, Fat, Fiber
- Elemental Analysis: C, H, O, N, S
- Macronutrients: Na, K, Ca, P, Mg
- Micronutrients: Fe, Zn, Cu, Mn
- Oxygen Demand: TCOD, SCOD

Rules:
- Each cell: single value, no line breaks.
- "Extraction Method": e.g., "Direct measurement", "Calculated from VS fraction", "Literature mean"
- For BMP, note that values were converted from mL CH4/g VS to scf CH4/lb VS (×0.016018) where needed.
- Return ONLY a JSON array of objects with the exact column names — no explanation, no markdown.
{correction_block}"""
    response = call_ai(prompt, max_tokens=6000)
    data = parse_json_from_response(response)
    return pd.DataFrame(data)


def generate_table5(
    feedstock_name: str,
    subtypes: list[str],
    table2_df: pd.DataFrame,
    corrections: str = "",
) -> pd.DataFrame:
    ref_list = table2_df[["Ref ID", "Authors", "Year", "Title"]].to_dict("records")
    correction_block = f"\nUser corrections from previous attempt:\n{corrections}" if corrections else ""
    prompt = f"""You are an expert in anaerobic digestion and organic waste characterization.

Feedstock: "{feedstock_name}"
Sub-types: {json.dumps(subtypes)}

Available references: {json.dumps(ref_list)}

Task: Create a Min/Max Range table (Table 5) based on a thorough review of the peer-reviewed literature.

Structure:
- One ROW per feedstock sub-type.
- Columns show the minimum and maximum values reported in the literature for 5 key parameters.

Exact column names:
"Sub-Type",
"TS Min (%)", "TS Max (%)",
"VS Min (%TS)", "VS Max (%TS)",
"BMP Min (scf CH4/lb VS)", "BMP Max (scf CH4/lb VS)",
"TCOD Min (mg/L)", "TCOD Max (mg/L)",
"SCOD Min (mg/L)", "SCOD Max (mg/L)",
"Key References"

UNIT NOTES:
- BMP must be in scf CH4/lb VS. Convert from mL CH4/g VS by multiplying by 0.016018.
- SCOD = Soluble Chemical Oxygen Demand.
- Report the true literature range (not just the mean) — this table is specifically about variability.

CRITICAL RULES:
1. Values must come from real peer-reviewed literature for each specific sub-type.
2. Each numeric cell: single value rounded to 2 decimal places, or "N/A" if not found.
3. "Key References": comma-separated Ref IDs from the reference list above.
4. Return ONLY a JSON array of objects with the exact column names — no explanation, no markdown.
{correction_block}"""
    response = call_ai(prompt, max_tokens=8000)
    data = parse_json_from_response(response)
    return pd.DataFrame(data)


def generate_table6(
    feedstock_name: str,
    subtypes: list[str],
    table3_df: pd.DataFrame,
    corrections: str = "",
) -> pd.DataFrame:
    cols = ["Sub-Type", "C (%TS)", "H (%TS)", "O (%TS)", "N (%TS)", "S (%TS)", "VS/TS (%)", "BMP (scf CH4/lb VS)"]
    t3_json = table3_df[[c for c in cols if c in table3_df.columns]].to_dict("records")
    correction_block = f"\nUser corrections from previous attempt:\n{corrections}" if corrections else ""
    prompt = f"""You are an expert in anaerobic digestion and organic waste characterization.

Feedstock: "{feedstock_name}"
Sub-types: {json.dumps(subtypes)}

Table 3 elemental data (BMP already in scf CH4/lb VS):
{json.dumps(t3_json)}

Task: Calculate theoretical BMP via Buswell Equation and compare to tested BMP.

Buswell Equation:
  B0 (mL CH4/g VS) = 22400 × (n/2 + a/8 - b/4 - 3c/8 - d/4) / M
  Where: n=C%VS/12, a=H%VS/1, b=O%VS/16, c=N%VS/14, d=S%VS/32; M = 12n + a + 16b + 14c + 32d
  Convert to scf CH4/lb VS: multiply mL CH4/g VS × 0.016018

Steps per sub-type:
1. Convert elemental % from %TS to %VS using VS/TS.
2. Derive molar ratios.
3. Apply Buswell equation to get B0 in mL CH4/g VS.
4. Convert B0 to scf CH4/lb VS (× 0.016018).
5. Compare to tested BMP (which is already in scf CH4/lb VS from Table 3).

Exact column names:
"Sub-Type", "C (%VS)", "H (%VS)", "O (%VS)", "N (%VS)", "S (%VS)",
"Formula (CnHaObNcSd)",
"B0 Theoretical (scf CH4/lb VS)",
"Tested BMP (scf CH4/lb VS)",
"Difference (%)",
"Interpretation"

Rules:
- All numeric values: 4 decimal places for BMP (e.g. "5.2341"), 2 decimal places for elemental %.
- "Formula": e.g. "C1.00H1.67O0.62N0.05S0.01"
- "Difference (%)": (Theoretical - Tested) / Theoretical × 100, rounded to 1 decimal place.
- "Interpretation": e.g. "66% biodegradable" based on Tested/Theoretical ratio.
- Return ONLY a JSON array of objects with the exact column names — no explanation, no markdown.
{correction_block}"""
    response = call_ai(prompt, max_tokens=6000)
    data = parse_json_from_response(response)
    return pd.DataFrame(data)
