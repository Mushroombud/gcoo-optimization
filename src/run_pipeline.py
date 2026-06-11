from __future__ import annotations

import argparse

from data_input import run_data_input
from model import run_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Run data input collection, then model build.")
    parser.add_argument("--config", default="config/model_config.yaml")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--snapshot-label")
    parser.add_argument("--source", default="all", choices=["all", "seoul-bike", "tago-pm", "private-pm-summary"])
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--out", default="outputs/latest_run")
    parser.add_argument("--allow-fixtures", action="store_true")
    args = parser.parse_args()

    run_data_input(args)
    run_model(args)


if __name__ == "__main__":
    main()
