# Memory

- GCOO optimization visualization/read-mode agent should prioritize core implementation and generated model-summary files instead of scanning large raw data dumps.
- For model, variable, filter, and visualization questions, start with `src/visualize_optimization_model.py`, `Data_Model_Sheet.md`, `outputs/visualizations/optimization_model_data.json`, and `outputs/visualizations/sejong_visualization_manifest.json`.
- Avoid searching `data/raw/**`, large processed CSV/JSON snapshots, and generated map HTML files unless the user explicitly asks for row-level raw-data evidence.
- Keep answers grounded in implementation file names, variables, equations, and generated summary metadata.
