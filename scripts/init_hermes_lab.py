#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LAB_ROOT = REPO_ROOT / "outputs" / "visualizations" / "hermes_lab_workspace"

DIRECTORY_CLONES = [
    ("src", "src"),
    ("config", "config"),
    ("outputs/model", "outputs/model"),
    ("outputs/prototype", "outputs/prototype"),
]

LAB_PROCESSED_FILES = [
    "collector_runs.jsonl",
    "sejong_pm_inferred_rides.csv",
    "sejong_pm_latest_snapshot.csv",
    "sejong_pm_od_flows.csv",
    "sejong_pm_operator_move_candidates.csv",
    "sejong_pm_operator_snapshot_counts.csv",
    "sejong_pm_preprocess_summary.json",
]

LARGE_REFERENCE_PROCESSED_FILES = [
    "sejong_pm_activity_by_zone.csv",
    "sejong_pm_device_intervals.csv",
    "sejong_pm_snapshots_accumulated.csv",
    "sejong_pm_zone_snapshot_counts.csv",
]

ROOT_FILES = [
    "Data_Model_Sheet.md",
    "README.md",
    "Spec.md",
    "requirements.txt",
]

VISUALIZATION_FILES = [
    ("outputs/visualizations/optimization_model.html", "optimization_model.html"),
    ("outputs/visualizations/optimization_model_map.html", "optimization_model_map.html"),
    ("outputs/visualizations/optimization_model_data.json", "optimization_model_data.json"),
    ("outputs/visualizations/temporal_inventory_map.html", "temporal_inventory_map.html"),
    ("outputs/visualizations/temporal_inventory_shortages.csv", "temporal_inventory_shortages.csv"),
    ("outputs/visualizations/temporal_inventory_hourly_summary.csv", "temporal_inventory_hourly_summary.csv"),
    ("outputs/visualizations/temporal_inventory_od_movements.csv", "temporal_inventory_od_movements.csv"),
    ("outputs/visualizations/parameter_search_results.json", "parameter_search_results.json"),
    ("outputs/visualizations/hermes_widget.css", "hermes_widget.css"),
    ("outputs/visualizations/hermes_widget.js", "hermes_widget.js"),
]

LAB_GITIGNORE = [
    "# Lab-only guardrails",
    "data/raw/",
    *[f"data/processed/sejong_tago/{name}" for name in LARGE_REFERENCE_PROCESSED_FILES],
    "__pycache__/",
    "*.pyc",
    ".run/",
]


def run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def copy_tree(src: Path, dst: Path, overwrite: bool = False) -> None:
    if dst.exists():
        if overwrite:
            shutil.copytree(src, dst, dirs_exist_ok=True)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    result = run(["cp", "-a", "--reflink=auto", str(src), str(dst)])
    if result.returncode != 0:
        shutil.copytree(src, dst)


def copy_file(src: Path, dst: Path, overwrite: bool = False) -> None:
    if dst.exists() and not overwrite:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    result = run(["cp", "--reflink=auto", "-p", str(src), str(dst)])
    if result.returncode != 0:
        shutil.copy2(src, dst)


def strip_embedded_agent_widget(path: Path) -> None:
    if not path.exists() or path.suffix.lower() != ".html":
        return
    text = path.read_text(encoding="utf-8")
    cleaned = re.sub(r'  <script defer src="\./hermes_widget\.js(?:\?[^"]*)?"></script>\n', "", text)
    cleaned = re.sub(r'<script defer src="\./hermes_widget\.js(?:\?[^"]*)?"></script>\n', "", cleaned)
    cleaned = cleaned.replace('href="./index.html"', 'href="../index.html" target="_top"')
    if cleaned != text:
        path.write_text(cleaned, encoding="utf-8")


def write_lab_readme() -> None:
    readme = LAB_ROOT / "LAB.md"
    if readme.exists():
        return
    readme.write_text(
        "\n".join(
            [
                "# GCOO Agent Lab",
                "",
                "This workspace is an isolated copy for natural-language experiments.",
                "Use the lab page buttons to save or revert states.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def write_lab_gitignore() -> None:
    path = LAB_ROOT / ".gitignore"
    text = "\n".join(LAB_GITIGNORE) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


def remove_forbidden_lab_clones() -> None:
    raw_data = LAB_ROOT / "data" / "raw"
    if raw_data.is_symlink() or raw_data.is_file():
        raw_data.unlink()
    elif raw_data.exists():
        shutil.rmtree(raw_data)


def init_git() -> None:
    if not (LAB_ROOT / ".git").exists():
        run(["git", "init"], cwd=LAB_ROOT)
    run(["git", "config", "user.name", "Agent Lab"], cwd=LAB_ROOT)
    run(["git", "config", "user.email", "agent-lab@example.local"], cwd=LAB_ROOT)
    if run(["git", "rev-parse", "--verify", "HEAD"], cwd=LAB_ROOT).returncode != 0:
        run(["git", "add", "."], cwd=LAB_ROOT)
        run(["git", "commit", "-m", "Initialize agent lab workspace"], cwd=LAB_ROOT)


def init_lab(force: bool = False, quick: bool = False) -> None:
    if force and LAB_ROOT.exists():
        shutil.rmtree(LAB_ROOT)
    LAB_ROOT.mkdir(parents=True, exist_ok=True)
    refresh_existing = not quick

    for src_rel, dst_rel in DIRECTORY_CLONES:
        copy_tree(REPO_ROOT / src_rel, LAB_ROOT / dst_rel, overwrite=refresh_existing)

    processed_dst = LAB_ROOT / "data" / "processed" / "sejong_tago"
    processed_dst.mkdir(parents=True, exist_ok=True)
    for filename in LAB_PROCESSED_FILES:
        src = REPO_ROOT / "data" / "processed" / "sejong_tago" / filename
        if src.exists():
            copy_file(src, processed_dst / filename, overwrite=refresh_existing)

    for filename in ROOT_FILES:
        src = REPO_ROOT / filename
        if src.exists():
            copy_file(src, LAB_ROOT / filename, overwrite=refresh_existing)

    for src_rel, dst_rel in VISUALIZATION_FILES:
        src = REPO_ROOT / src_rel
        if src.exists():
            copy_file(src, LAB_ROOT / dst_rel, overwrite=refresh_existing)
            strip_embedded_agent_widget(LAB_ROOT / dst_rel)

    write_lab_readme()
    write_lab_gitignore()
    remove_forbidden_lab_clones()
    if not quick:
        init_git()
    elif not (LAB_ROOT / ".git").exists():
        init_git()


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the Hermes experiment lab workspace.")
    parser.add_argument("--force", action="store_true", help="Recreate the lab workspace.")
    parser.add_argument("--quick", action="store_true", help="Skip expensive work when the lab already exists.")
    args = parser.parse_args()
    init_lab(force=args.force, quick=args.quick)
    print(LAB_ROOT)


if __name__ == "__main__":
    main()
