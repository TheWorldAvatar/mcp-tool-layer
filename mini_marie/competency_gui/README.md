# Competency Test GUI

Streamlit app for running **MOF competency** and **TWA city** cached workflows with:

- **Table** — sortable results, CSV export  
- **Chart** — Plotly bars (counts, heights, metrics)  
- **Map** — Leaflet footprints when rows include `wkt` (city workflows)

## Install

```bash
pip install -r requirements-gui.txt
```

Requires network access to OntoMOFs / Bremen / Kaiserslautern SPARQL endpoints.

## Run

```bash
streamlit run mini_marie/competency_gui/app.py
```

Open http://localhost:8501

## Docker

```bash
docker compose --profile gui run --rm -p 8501:8501 competency-gui
```

## Usage

1. Choose **MOF** or **TWA City** in the sidebar.  
2. Pick a competency question / workflow.  
3. **Online** = LIMIT 10 probe; **Offline** = full cap (slow first time).  
4. Uncheck *Force refresh* on second run to measure cache speedup.  
5. Inspect **Table / Chart / Map** tabs.
