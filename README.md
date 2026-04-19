# Feedstock Profiler

A Streamlit app that runs a 15-step feedstock characterization workflow, powered by Claude.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
cp .env.example .env            # then edit .env and set ANTHROPIC_API_KEY
streamlit run app.py
```

Open http://localhost:8501.

## Deploy

Hosted on Streamlit Community Cloud. The `ANTHROPIC_API_KEY` is provided via Streamlit Cloud's secrets UI, not committed to the repo.
