# chem-agent: a chemistry research agent

A command-line research assistant for chemists, powered by Claude. Ask a question and the agent searches
the literature, looks compounds up in PubChem and ChEMBL, computes properties with RDKit, queries crystal
structure databases, and answers with cited sources. It can also save the result as a Markdown report.

It covers organic and synthetic chemistry, medicinal chemistry and drug discovery, materials science, and
general chemistry.

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...   # https://console.anthropic.com/
```

Optional keys (see `.env.example`):

| Variable | What it adds |
|---|---|
| `MP_API_KEY` | `materials_project_properties`: band gaps, stability, formation energies ([free key](https://next-gen.materialsproject.org/api)) |
| `NCBI_API_KEY` | Higher PubMed rate limit |
| `OPENALEX_API_KEY` | Higher OpenAlex limits (optional) |
| `CHEM_AGENT_MODEL` | Default model override (default `claude-opus-5`) |

## Usage

One question:

```bash
chem-agent "What are the main degradation pathways of perovskite solar cells since 2022?"
chem-agent --report "Compare the drug-likeness of imatinib, nilotinib and dasatinib, with their main targets"
chem-agent -e medium "Melting point and GHS hazards of benzoic acid"
```

Interactive session, where follow-up questions keep the context:

```bash
chem-agent
chem> Find recent literature on Cu-based catalysts for CO2 electroreduction to ethylene
chem> Which of those papers report Faradaic efficiencies above 60%?
chem> /report focus on catalyst design principles
```

Commands: `/report [focus]`, `/reset`, `/usage`, `/quit`.

Options:

| Flag | Meaning |
|---|---|
| `-r, --report` | Save a Markdown report to `reports/` when done |
| `-e, --effort` | `low`, `medium`, `high` (default), `xhigh`, `max`. Lower is faster and cheaper; `medium` is fine for lookups |
| `-m, --model` | Claude model ID |
| `-o, --output-dir` | Folder for reports and structure drawings (default `reports/`) |
| `--no-web` | Disable web search/fetch and use only the chemistry databases |
| `--thinking` | Show a summary of the model's reasoning |

From Python:

```python
from chem_agent import ResearchAgent

agent = ResearchAgent(effort="medium")
print(agent.ask("SMILES and logP of ibuprofen?"))
```

## Research-gap workflow

`chem-gaps` runs a fixed, reproducible pipeline: it reads the literature on a topic, finds research gaps,
checks that nobody has already filled them, and designs studies for the gaps that remain open.

```bash
chem-gaps "stability of tin halide perovskite solar cells" --year-from 2019 \
  --context "we have XRD, XPS, a glovebox and access to DFT"
```

| Step | What happens | API |
|---|---|---|
| 1. Plan | Topic becomes a research question, inclusion/exclusion criteria, and 6-12 search queries | Claude |
| 2. Retrieve | Queries run across sources; results are merged and deduplicated by DOI/title | OpenAlex, Europe PMC, PubMed, arXiv, Crossref |
| 3. Screen | Each title/abstract is scored 0-10 against the criteria; the top N are kept | Claude |
| 4. Full text | Open-access papers are read in full (methods and limitations rarely fit in abstracts) | Europe PMC |
| 5. Extract | Per paper: study type, systems, methods, findings with units, stated limitations, future work, open questions | Claude |
| 6. Gaps | Compares all extractions: unexplored combinations, contradictions, recurring limitations, missing methods, undone future work, each citing papers | Claude |
| 7. Novelty check | Each gap is searched for again; gaps that newer work already fills are marked "addressed" | OpenAlex, Europe PMC, Claude |
| 8. Methods | For each open gap: hypothesis, steps, computational methods, characterization, controls, methods to reuse from cited papers, metrics, risks, safety | Claude |
| 9. Report | `report.md` with summary table, search strategy, evidence table, gaps, study designs, references | |

Every step saves its output (`plan.json`, `screening.json`, `extractions/`, `gaps.json`, `novelty.json`,
`methods/`) in `runs/<date>-<topic>/`. If a run stops, the same command resumes it. You can also edit a
file, for example removing a gap from `gaps.json`, and re-run from that point by deleting the later files.

Every Claude call uses structured outputs, so each step returns validated JSON. Routine steps run at
`medium` effort and the gap analysis and study design at `high`, both configurable. Useful flags:
`-n/--max-papers` (default 30), `-g/--max-gaps` (default 6), `--no-full-text`, `-e/--effort`,
`--extract-effort`, `-w/--workers`.

From Python:

```python
from pathlib import Path
from chem_agent.workflow import GapWorkflow, WorkflowConfig

report = GapWorkflow(WorkflowConfig(topic="MOF catalysts for CO2 hydrogenation", run_dir=Path("runs/mof"))).run()
```

## Tools

| Area | Tool | Source |
|---|---|---|
| Literature | `search_literature` | Europe PMC (incl. ChemRxiv preprints), PubMed, arXiv, Crossref, OpenAlex |
| | `web_search`, `web_fetch` | Web, including open-access full texts (run by Anthropic) |
| Compounds | `pubchem_compound` | Names to SMILES, identifiers, computed properties, synonyms |
| | `pubchem_safety` | GHS pictograms, H-statements, precautionary codes |
| | `pubchem_experimental_data` | Melting/boiling point, solubility, pKa, toxicity and more, with sources |
| Drug discovery | `chembl_search`, `chembl_activities` | ChEMBL molecules, targets, IC50/Ki/Kd potencies |
| Cheminformatics | `molecule_properties` | RDKit: MW, logP, TPSA, HBD/HBA, QED, Lipinski, InChIKey... |
| | `similarity_search`, `substructure_search` | Morgan-fingerprint Tanimoto similarity, SMARTS matching |
| | `run_reaction` | Apply reaction SMARTS templates to enumerate products |
| | `draw_molecule` | 2D structure depictions saved as SVG |
| Materials | `search_crystal_structures` | OPTIMADE: Materials Project, OQMD, COD (no key) |
| | `materials_project_properties` | Materials Project DFT properties (needs `MP_API_KEY`) |
| Reports | `save_report` | Markdown report with citations and embedded drawings |

## How it works

`src/chem_agent/agent.py` runs a tool-use loop on the Claude API. Each step is streamed. Claude chooses
tools, the agent runs them (in parallel when there are several), and the results go back to Claude until
it writes its answer. Details:

- **Adaptive thinking** with a configurable effort level.
- **Prompt caching** on the conversation, so long research sessions reuse the cached history.
- **Refusal fallback** (`fallbacks: "default"`). Chemistry questions sometimes trip safety classifiers
  even when they are benign. When that happens, the API retries the request on a fallback model.
- **Validation** of every tool input before it runs. A failed tool returns an error message that Claude
  can react to, for example by fixing a SMILES string or trying another database.
- **Safety policy** in the system prompt. The agent declines synthesis or weaponization help for chemical
  weapons, explosives, and illicit drugs, and it points out hazards.

To add a tool, write a function in `src/chem_agent/tools/` and decorate it with `@tool(...)`. It is
registered automatically.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests run offline. RDKit tools are tested for real; database parsers use canned API responses; the
agent loop uses a fake Claude client.

Data from the tools is only as good as its sources. Check critical values, especially safety data,
against the primary literature or an SDS.
