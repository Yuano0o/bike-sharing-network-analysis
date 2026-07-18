import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
PP_ROOT = SCRIPT_DIR.parent
PROJECT_ROOT = PP_ROOT.parent.parent

TASK_SCRIPTS = {
    "cross": {
        "script": SCRIPT_DIR / "cross_excitation_check.py",
        "required_modules": ["numpy", "pandas", "matplotlib"],
    },
    "hawkes": {
        "script": SCRIPT_DIR / "hawkes_diagnostics.py",
        "required_modules": ["numpy", "pandas", "matplotlib", "scipy", "statsmodels"],
    },
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Unified launcher for point-process diagnostics in this project."
    )
    parser.add_argument(
        "--task",
        choices=["cross", "hawkes", "all"],
        default="cross",
        help="Which diagnostic to run.",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a cleaned trip CSV or CSV.GZ file.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PP_ROOT / "results" / "manual_run"),
        help="Directory where run outputs will be written.",
    )
    return parser.parse_args()


def missing_modules(module_names):
    return [name for name in module_names if importlib.util.find_spec(name) is None]


def build_command(script_path, input_path, output_dir):
    return [
        sys.executable,
        str(script_path),
        "--input",
        str(input_path),
        "--output-dir",
        str(output_dir),
    ]


def run_task(task_name, input_path, output_dir):
    task = TASK_SCRIPTS[task_name]
    missing = missing_modules(task["required_modules"])
    if missing:
        missing_list = ", ".join(missing)
        raise RuntimeError(
            f"Cannot run '{task_name}' because these Python modules are missing: {missing_list}"
        )

    env = os.environ.copy()
    env.setdefault("MPLBACKEND", "Agg")
    env.setdefault("MPLCONFIGDIR", str(PROJECT_ROOT / ".mplconfig"))
    output_dir.mkdir(parents=True, exist_ok=True)

    command = build_command(task["script"], input_path, output_dir)
    print(f"\nRunning {task_name}: {' '.join(command)}", flush=True)
    subprocess.run(command, check=True, env=env)


def main():
    args = parse_args()
    input_path = Path(args.input).resolve()
    output_dir = Path(args.output_dir).resolve()

    tasks = ["cross", "hawkes"] if args.task == "all" else [args.task]
    try:
        for task_name in tasks:
            task_output_dir = output_dir / task_name if len(tasks) > 1 else output_dir
            run_task(task_name, input_path, task_output_dir)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except subprocess.CalledProcessError as exc:
        print(f"Error: task failed with exit code {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode) from None

    print(f"\nFinished. Outputs written under: {output_dir}")


if __name__ == "__main__":
    main()
