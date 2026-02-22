#!/usr/bin/env python3
"""
Benchmark TUI - Interactive benchmark runner for Empirica vs other agentic strategies.

A terminal UI for running benchmarks collaboratively.
"""

import subprocess
import sys
import json
import os
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

# Ensure dependencies
for pkg in ["rich", "questionary"]:
    try:
        __import__(pkg)
    except ImportError:
        print(f"Installing {pkg}...")
        subprocess.run([sys.executable, "-m", "pip", "install", pkg, "-q"])

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.layout import Layout
from rich.live import Live
from rich.markdown import Markdown
from rich import box
import questionary
from questionary import Style

# Import benchmark module
sys.path.insert(0, str(Path(__file__).parent))
try:
    from agentic_benchmark import (
        BENCHMARKS, STRATEGIES, get_backend,
        run_benchmark, compute_ece, BenchmarkSummary
    )
    HAS_BENCHMARK = True
except ImportError:
    HAS_BENCHMARK = False
    print("Warning: agentic_benchmark.py not found in same directory")

console = Console()

# Custom style for questionary
custom_style = Style([
    ('qmark', 'fg:cyan bold'),
    ('question', 'fg:white bold'),
    ('answer', 'fg:green bold'),
    ('pointer', 'fg:cyan bold'),
    ('highlighted', 'fg:cyan bold'),
    ('selected', 'fg:green'),
])


@dataclass
class BenchmarkConfig:
    """Configuration for a benchmark run."""
    models: List[str]
    strategies: List[str]
    benchmark: str
    num_questions: Optional[int]
    use_cli: bool
    ollama_host: str


def get_ollama_models(host: str = "http://localhost:11434") -> List[str]:
    """Get list of available Ollama models."""
    try:
        import requests
        response = requests.get(f"{host}/api/tags", timeout=5)
        if response.ok:
            data = response.json()
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        pass
    return []


def get_available_models(ollama_host: str = "http://localhost:11434") -> Dict[str, List[str]]:
    """Get all available models grouped by type."""
    models = {
        "claude": ["claude (CLI/OAuth)", "claude-sonnet (API)", "claude-opus (API)", "claude-haiku (API)"],
        "ollama": get_ollama_models(ollama_host)
    }
    return models


def show_header():
    """Display the TUI header."""
    console.clear()
    header = """
╔═══════════════════════════════════════════════════════════════════════════════╗
║                     🧠 EMPIRICA BENCHMARK TUI                                  ║
║         Compare Agentic Strategies: Vanilla | CoT | ReAct | Reflexion | Empirica  ║
╚═══════════════════════════════════════════════════════════════════════════════╝
"""
    console.print(Panel(header.strip(), style="cyan", box=box.DOUBLE))


def show_model_status(ollama_host: str = "http://localhost:11434"):
    """Show available models."""
    models = get_available_models(ollama_host)

    table = Table(title="Available Models", box=box.ROUNDED)
    table.add_column("Type", style="cyan")
    table.add_column("Models", style="green")
    table.add_column("Count", style="yellow")

    table.add_row("Claude", ", ".join(models["claude"][:2]) + "...", str(len(models["claude"])))
    ollama_display = ", ".join(models["ollama"][:3]) + ("..." if len(models["ollama"]) > 3 else "")
    table.add_row("Ollama", ollama_display or "(none found)", str(len(models["ollama"])))

    console.print(table)
    return models


def select_models(available: Dict[str, List[str]]) -> List[tuple[str, bool]]:
    """Interactive model selection. Returns list of (model_name, use_cli)."""
    all_models = []

    # Add Claude models
    for m in available["claude"]:
        all_models.append(m)

    # Add Ollama models
    for m in available["ollama"]:
        all_models.append(f"ollama: {m}")

    if not all_models:
        console.print("[red]No models available![/red]")
        return []

    selected = questionary.checkbox(
        "Select models to benchmark:",
        choices=all_models,
        style=custom_style
    ).ask()

    if not selected:
        return []

    # Parse selections
    results = []
    for s in selected:
        if s.startswith("claude"):
            use_cli = "(CLI" in s
            model_name = s.split(" ")[0]
            results.append((model_name, use_cli))
        elif s.startswith("ollama: "):
            model_name = s.replace("ollama: ", "")
            results.append((model_name, False))

    return results


def select_strategies() -> List[str]:
    """Interactive strategy selection."""
    strategy_info = {
        "vanilla": "Single-pass, no scaffolding (1 API call)",
        "cot": "Chain-of-thought prompting (1 API call)",
        "react": "ReAct: Reason + Act interleaved (3-4 calls)",
        "reflexion": "Self-reflection loop on low confidence (1-3 calls)",
        "empirica": "PREFLIGHT → CHECK → POSTFLIGHT (4-6 calls)",
    }

    choices = [
        questionary.Choice(f"{name}: {desc}", value=name)
        for name, desc in strategy_info.items()
    ]

    selected = questionary.checkbox(
        "Select strategies to compare:",
        choices=choices,
        style=custom_style
    ).ask()

    return selected or []


def select_benchmark() -> tuple[str, Optional[int]]:
    """Interactive benchmark selection."""
    benchmark_info = {
        "gpqa": f"GPQA Diamond - Graduate-level science ({len(BENCHMARKS.get('gpqa', []))} questions)",
        "gsm8k": f"GSM8K - Math word problems ({len(BENCHMARKS.get('gsm8k', []))} questions)",
        "arc": f"ARC Challenge - Science reasoning ({len(BENCHMARKS.get('arc', []))} questions)",
    }

    choices = [
        questionary.Choice(desc, value=name)
        for name, desc in benchmark_info.items()
    ]

    benchmark = questionary.select(
        "Select benchmark:",
        choices=choices,
        style=custom_style
    ).ask()

    if not benchmark:
        return "gpqa", None

    # Ask for number of questions
    max_q = len(BENCHMARKS.get(benchmark, []))
    num_q = questionary.text(
        f"Number of questions (max {max_q}, enter for all):",
        default=str(max_q),
        style=custom_style
    ).ask()

    try:
        num_questions = int(num_q) if num_q else None
    except ValueError:
        num_questions = None

    return benchmark, num_questions


def run_single_benchmark(
    model: str,
    strategy_name: str,
    benchmark: str,
    num_questions: Optional[int],
    use_cli: bool,
    ollama_host: str
) -> Optional[BenchmarkSummary]:
    """Run a single benchmark configuration."""
    if not HAS_BENCHMARK:
        console.print("[red]Benchmark module not available[/red]")
        return None

    try:
        backend = get_backend(model, ollama_host, use_cli=use_cli)
        strategy = STRATEGIES.get(strategy_name)
        if not strategy:
            return None

        return run_benchmark(backend, benchmark, strategy, num_questions)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        return None


def display_results(summaries: List[BenchmarkSummary]):
    """Display benchmark results in a table."""
    if not summaries:
        console.print("[yellow]No results to display[/yellow]")
        return

    # Group by model
    by_model: Dict[str, List[BenchmarkSummary]] = {}
    for s in summaries:
        if s.model not in by_model:
            by_model[s.model] = []
        by_model[s.model].append(s)

    for model, model_summaries in by_model.items():
        table = Table(
            title=f"Results: {model} on {model_summaries[0].benchmark}",
            box=box.ROUNDED
        )
        table.add_column("Strategy", style="cyan")
        table.add_column("Accuracy", justify="right", style="green")
        table.add_column("ECE", justify="right", style="yellow")
        table.add_column("Tok/Correct", justify="right")
        table.add_column("API Calls", justify="right")
        table.add_column("Latency", justify="right")

        # Sort by accuracy descending
        model_summaries.sort(key=lambda x: x.accuracy, reverse=True)

        for s in model_summaries:
            accuracy_str = f"{s.accuracy*100:.1f}%"
            ece_str = f"{s.ece:.3f}"
            tpc_str = f"{s.tokens_per_correct:.0f}" if s.tokens_per_correct != float('inf') else "N/A"
            latency_str = f"{s.avg_latency_ms:.0f}ms"

            # Highlight best
            if s == model_summaries[0]:
                accuracy_str = f"[bold green]{accuracy_str}[/bold green]"

            table.add_row(
                s.strategy,
                accuracy_str,
                ece_str,
                tpc_str,
                str(s.total_api_calls),
                latency_str
            )

        console.print(table)
        console.print()

    # Summary comparison if multiple models
    if len(by_model) > 1:
        console.print(Panel("Cross-Model Comparison", style="cyan"))

        # Find best strategy per model
        for model, model_summaries in by_model.items():
            best = max(model_summaries, key=lambda x: x.accuracy)
            console.print(f"  {model}: Best strategy = [green]{best.strategy}[/green] ({best.accuracy*100:.1f}%)")


def save_results(summaries: List[BenchmarkSummary], filename: str):
    """Save results to JSON file."""
    from dataclasses import asdict

    data = [asdict(s) for s in summaries]

    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

    console.print(f"[green]Results saved to {filename}[/green]")


def main_menu():
    """Main TUI menu."""
    show_header()

    while True:
        action = questionary.select(
            "What would you like to do?",
            choices=[
                questionary.Choice("🚀 Run Benchmark Comparison", value="run"),
                questionary.Choice("📊 View Available Models", value="models"),
                questionary.Choice("📈 Load Previous Results", value="load"),
                questionary.Choice("⚙️  Settings", value="settings"),
                questionary.Choice("❌ Exit", value="exit"),
            ],
            style=custom_style
        ).ask()

        if action == "exit" or action is None:
            console.print("[cyan]Goodbye![/cyan]")
            break

        elif action == "models":
            show_header()
            show_model_status()
            questionary.press_any_key_to_continue().ask()

        elif action == "load":
            show_header()
            files = list(Path(".").glob("*.json"))
            if not files:
                console.print("[yellow]No JSON result files found[/yellow]")
            else:
                file_choices = [str(f) for f in files]
                selected = questionary.select(
                    "Select results file:",
                    choices=file_choices + ["Cancel"],
                    style=custom_style
                ).ask()

                if selected and selected != "Cancel":
                    with open(selected) as f:
                        data = json.load(f)
                    console.print(Panel(json.dumps(data, indent=2)[:2000], title=selected))

            questionary.press_any_key_to_continue().ask()

        elif action == "settings":
            show_header()
            console.print("[yellow]Settings not yet implemented[/yellow]")
            questionary.press_any_key_to_continue().ask()

        elif action == "run":
            show_header()

            # Get available models
            available = get_available_models()

            # Select models
            models = select_models(available)
            if not models:
                console.print("[yellow]No models selected, returning to menu[/yellow]")
                continue

            # Select strategies
            strategies = select_strategies()
            if not strategies:
                console.print("[yellow]No strategies selected, returning to menu[/yellow]")
                continue

            # Select benchmark
            benchmark, num_questions = select_benchmark()

            # Confirm
            console.print()
            console.print(Panel(f"""
[bold]Benchmark Configuration[/bold]

Models: {', '.join([m[0] for m in models])}
Strategies: {', '.join(strategies)}
Benchmark: {benchmark}
Questions: {num_questions or 'all'}
            """.strip(), title="Confirm", style="cyan"))

            if not questionary.confirm("Run this benchmark?", style=custom_style).ask():
                continue

            # Run benchmarks
            all_summaries = []

            total_runs = len(models) * len(strategies)
            current_run = 0

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                console=console
            ) as progress:
                task = progress.add_task("Running benchmarks...", total=total_runs)

                for model_name, use_cli in models:
                    for strategy_name in strategies:
                        current_run += 1
                        progress.update(task, description=f"[{current_run}/{total_runs}] {model_name} + {strategy_name}")

                        summary = run_single_benchmark(
                            model_name,
                            strategy_name,
                            benchmark,
                            num_questions,
                            use_cli,
                            "http://localhost:11434"
                        )

                        if summary:
                            all_summaries.append(summary)

                        progress.advance(task)

            # Display results
            console.print()
            display_results(all_summaries)

            # Offer to save
            if all_summaries and questionary.confirm("Save results?", style=custom_style).ask():
                filename = questionary.text(
                    "Filename:",
                    default=f"benchmark_{benchmark}_{len(all_summaries)}.json",
                    style=custom_style
                ).ask()
                if filename:
                    save_results(all_summaries, filename)

            questionary.press_any_key_to_continue().ask()

        show_header()


def quick_run():
    """Quick run mode for non-interactive use."""
    import argparse

    parser = argparse.ArgumentParser(description="Benchmark TUI - Quick Run Mode")
    parser.add_argument("--model", "-m", required=True, help="Model to benchmark")
    parser.add_argument("--strategies", "-s", default="all", help="Comma-separated strategies or 'all'")
    parser.add_argument("--benchmark", "-b", default="gpqa", help="Benchmark to run")
    parser.add_argument("--questions", "-n", type=int, help="Number of questions")
    parser.add_argument("--cli", action="store_true", help="Use Claude CLI")
    parser.add_argument("--output", "-o", help="Output file")

    args = parser.parse_args()

    strategies = list(STRATEGIES.keys()) if args.strategies == "all" else args.strategies.split(",")

    summaries = []
    for strategy in strategies:
        console.print(f"Running {args.model} + {strategy}...")
        summary = run_single_benchmark(
            args.model, strategy, args.benchmark, args.questions, args.cli, "http://localhost:11434"
        )
        if summary:
            summaries.append(summary)

    display_results(summaries)

    if args.output and summaries:
        save_results(summaries, args.output)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] != "--help":
        quick_run()
    else:
        if not HAS_BENCHMARK:
            console.print("[red]Error: agentic_benchmark.py required in same directory[/red]")
            sys.exit(1)
        main_menu()
