#!/usr/bin/env python3
"""
LLM Benchmarking Script for Ollama models.

Measures:
- Tokens per second (generation speed)
- Time to first token (TTFT)
- Memory usage
- Prompt evaluation speed

Usage:
    python llm_benchmark.py [--model MODEL] [--all] [--prompt PROMPT] [--output json|table]
"""

import argparse
import json
import subprocess
import time
from dataclasses import dataclass, asdict
from typing import List, Optional
import sys

try:
    import requests
except ImportError:
    print("Installing requests...")
    subprocess.run([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests


@dataclass
class BenchmarkResult:
    """Results from a single benchmark run."""
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    prompt_eval_time_ms: float
    eval_time_ms: float
    total_time_ms: float
    tokens_per_second: float
    time_to_first_token_ms: float
    prompt_eval_rate: float  # tokens/s for prompt processing
    timestamp: str


@dataclass
class ModelInfo:
    """Information about a model."""
    name: str
    size_gb: float
    parameter_count: Optional[str] = None


BENCHMARK_PROMPTS = {
    "short": "What is 2+2?",
    "medium": "Explain the concept of machine learning in three sentences.",
    "long": """Analyze the following text and identify any personally identifiable information (PII):

"Dear Mr. John Smith,

Your order #12345 has been shipped to 123 Main Street, Springfield, IL 62701.
Your credit card ending in 4242 was charged $99.99.
For questions, contact us at john.smith@email.com or call 555-123-4567.

Best regards,
Customer Service"

List each PII item found with its type and location in the text.""",
    "pii_detection": """You are a PII detection system. Analyze this text and output JSON with detected PII:

Text: "Contact Sarah Johnson at sarah.j@company.com or 415-555-0123.
Her SSN is 123-45-6789 and she lives at 456 Oak Avenue, San Francisco, CA 94102."

Output format: {"entities": [{"text": "...", "type": "...", "confidence": 0.0-1.0}]}"""
}


def get_ollama_models() -> List[ModelInfo]:
    """Get list of available Ollama models."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=30
        )
        models = []
        for line in result.stdout.strip().split("\n")[1:]:  # Skip header
            if line.strip():
                parts = line.split()
                if len(parts) >= 3:
                    name = parts[0]
                    size_str = parts[2]
                    # Parse size (e.g., "19 GB" -> 19.0)
                    try:
                        size = float(size_str)
                    except ValueError:
                        size = 0.0
                    models.append(ModelInfo(name=name, size_gb=size))
        return models
    except Exception as e:
        print(f"Error getting models: {e}")
        return []


def benchmark_model(
    model: str,
    prompt: str,
    ollama_host: str = "http://localhost:11434"
) -> Optional[BenchmarkResult]:
    """Benchmark a single model with a prompt."""

    url = f"{ollama_host}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {
            "num_predict": 256  # Limit output for consistent benchmarks
        }
    }

    try:
        start_time = time.perf_counter()
        first_token_time = None
        full_response = ""

        response = requests.post(url, json=payload, stream=True, timeout=300)
        response.raise_for_status()

        final_stats = None
        for line in response.iter_lines():
            if line:
                data = json.loads(line)

                if first_token_time is None and data.get("response"):
                    first_token_time = time.perf_counter()

                if data.get("response"):
                    full_response += data["response"]

                if data.get("done"):
                    final_stats = data
                    break

        end_time = time.perf_counter()

        if not final_stats:
            return None

        # Extract timing info
        prompt_eval_count = final_stats.get("prompt_eval_count", 0)
        eval_count = final_stats.get("eval_count", 0)
        prompt_eval_duration = final_stats.get("prompt_eval_duration", 0) / 1e6  # ns to ms
        eval_duration = final_stats.get("eval_duration", 0) / 1e6  # ns to ms
        total_duration = final_stats.get("total_duration", 0) / 1e6  # ns to ms

        # Calculate rates
        tokens_per_second = (eval_count / (eval_duration / 1000)) if eval_duration > 0 else 0
        prompt_eval_rate = (prompt_eval_count / (prompt_eval_duration / 1000)) if prompt_eval_duration > 0 else 0

        ttft = ((first_token_time - start_time) * 1000) if first_token_time else prompt_eval_duration

        return BenchmarkResult(
            model=model,
            prompt_tokens=prompt_eval_count,
            completion_tokens=eval_count,
            total_tokens=prompt_eval_count + eval_count,
            prompt_eval_time_ms=prompt_eval_duration,
            eval_time_ms=eval_duration,
            total_time_ms=total_duration,
            tokens_per_second=round(tokens_per_second, 2),
            time_to_first_token_ms=round(ttft, 2),
            prompt_eval_rate=round(prompt_eval_rate, 2),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S")
        )

    except requests.exceptions.Timeout:
        print(f"  Timeout for {model}")
        return None
    except Exception as e:
        print(f"  Error benchmarking {model}: {e}")
        return None


def run_benchmarks(
    models: List[str],
    prompt_type: str = "medium",
    ollama_host: str = "http://localhost:11434",
    runs: int = 1
) -> List[BenchmarkResult]:
    """Run benchmarks on multiple models."""

    prompt = BENCHMARK_PROMPTS.get(prompt_type, prompt_type)
    results = []

    print(f"\n{'='*60}")
    print(f"LLM Benchmark - Prompt: {prompt_type}")
    print(f"{'='*60}\n")

    for model in models:
        print(f"Benchmarking: {model}")

        for run in range(runs):
            if runs > 1:
                print(f"  Run {run + 1}/{runs}...", end=" ", flush=True)

            result = benchmark_model(model, prompt, ollama_host)

            if result:
                results.append(result)
                if runs > 1:
                    print(f"{result.tokens_per_second} tok/s")
                else:
                    print(f"  → {result.tokens_per_second} tok/s, TTFT: {result.time_to_first_token_ms}ms")
            else:
                print("  → Failed")

    return results


def print_table(results: List[BenchmarkResult]):
    """Print results as a formatted table."""
    if not results:
        print("No results to display")
        return

    print(f"\n{'Model':<30} {'Tok/s':>8} {'TTFT(ms)':>10} {'Prompt tok/s':>12} {'Gen tokens':>10}")
    print("-" * 75)

    for r in results:
        print(f"{r.model:<30} {r.tokens_per_second:>8.2f} {r.time_to_first_token_ms:>10.2f} {r.prompt_eval_rate:>12.2f} {r.completion_tokens:>10}")


def main():
    parser = argparse.ArgumentParser(description="Benchmark Ollama models")
    parser.add_argument("--model", "-m", help="Specific model to benchmark")
    parser.add_argument("--all", "-a", action="store_true", help="Benchmark all available models")
    parser.add_argument("--prompt", "-p", default="medium",
                        choices=list(BENCHMARK_PROMPTS.keys()) + ["custom"],
                        help="Prompt type to use")
    parser.add_argument("--custom-prompt", help="Custom prompt text (use with --prompt custom)")
    parser.add_argument("--output", "-o", default="table", choices=["table", "json"],
                        help="Output format")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama host URL")
    parser.add_argument("--runs", "-r", type=int, default=1, help="Number of runs per model")
    parser.add_argument("--save", "-s", help="Save results to JSON file")

    args = parser.parse_args()

    # Get models to benchmark
    if args.all:
        available = get_ollama_models()
        models = [m.name for m in available]
        print(f"Found {len(models)} models: {', '.join(models)}")
    elif args.model:
        models = [args.model]
    else:
        # Default to a few common models
        available = get_ollama_models()
        models = [m.name for m in available[:3]] if available else []
        if not models:
            print("No models found. Use --model to specify one.")
            return

    # Get prompt
    if args.prompt == "custom":
        if not args.custom_prompt:
            print("--custom-prompt required when using --prompt custom")
            return
        prompt = args.custom_prompt
    else:
        prompt = args.prompt

    # Run benchmarks
    results = run_benchmarks(models, prompt, args.host, args.runs)

    # Output results
    if args.output == "json":
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print_table(results)

    # Save if requested
    if args.save and results:
        with open(args.save, "w") as f:
            json.dump([asdict(r) for r in results], f, indent=2)
        print(f"\nResults saved to {args.save}")

    # Summary
    if results:
        avg_tps = sum(r.tokens_per_second for r in results) / len(results)
        avg_ttft = sum(r.time_to_first_token_ms for r in results) / len(results)
        print(f"\nSummary: {len(results)} benchmarks, avg {avg_tps:.2f} tok/s, avg TTFT {avg_ttft:.2f}ms")


if __name__ == "__main__":
    main()
