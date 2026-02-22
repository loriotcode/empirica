#!/usr/bin/env python3
"""
Empirica Benchmark Harness

Compares LLM performance on reasoning benchmarks:
1. Baseline (single-pass)
2. With Empirica epistemic loop (PREFLIGHT → investigate → CHECK → act → POSTFLIGHT)

Hypothesis: Structured epistemic loops with grounded verification outperform
single-pass inference on hard reasoning tasks.

Benchmarks supported:
- GPQA Diamond (graduate-level science)
- GSM8K (grade school math)
- ARC Challenge (science reasoning)
- MMLU-Pro (multitask)
- Custom reasoning tasks

Usage:
    python empirica_benchmark.py --model qwen3:8b --benchmark gsm8k --runs 10
    python empirica_benchmark.py --model qwen3:8b --benchmark gpqa --with-empirica
"""

import argparse
import json
import time
import subprocess
import random
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional, Any
from pathlib import Path
import sys

try:
    import requests
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests


@dataclass
class BenchmarkQuestion:
    """A single benchmark question."""
    id: str
    question: str
    choices: List[str]
    correct_answer: str
    subject: str = ""
    difficulty: str = ""


@dataclass
class BenchmarkResult:
    """Result from a single question."""
    question_id: str
    model: str
    mode: str  # "baseline" or "empirica"
    answer: str
    correct: bool
    confidence: float
    reasoning: str
    latency_ms: float
    tokens_used: int
    iterations: int = 1  # For Empirica mode


@dataclass
class BenchmarkSummary:
    """Summary of benchmark run."""
    benchmark: str
    model: str
    mode: str
    total_questions: int
    correct: int
    accuracy: float
    avg_confidence: float
    avg_latency_ms: float
    total_tokens: int
    calibration_error: float  # |accuracy - avg_confidence|
    timestamp: str
    results: List[BenchmarkResult] = field(default_factory=list)


# Sample benchmark questions (subset for demo)
SAMPLE_GPQA = [
    BenchmarkQuestion(
        id="gpqa_001",
        question="A quantum system is prepared in a superposition state |ψ⟩ = (|0⟩ + |1⟩)/√2. After a Hadamard gate is applied, what is the resulting state?",
        choices=["A) |0⟩", "B) |1⟩", "C) (|0⟩ + |1⟩)/√2", "D) (|0⟩ - |1⟩)/√2"],
        correct_answer="A",
        subject="physics",
        difficulty="graduate"
    ),
    BenchmarkQuestion(
        id="gpqa_002",
        question="In organic chemistry, what is the major product of the reaction between 2-butene and HBr in the presence of peroxides?",
        choices=["A) 2-bromobutane", "B) 1-bromobutane", "C) 2,3-dibromobutane", "D) 1,2-dibromobutane"],
        correct_answer="B",
        subject="chemistry",
        difficulty="graduate"
    ),
    BenchmarkQuestion(
        id="gpqa_003",
        question="What is the time complexity of finding the shortest path in a weighted directed graph with negative edge weights using the Bellman-Ford algorithm?",
        choices=["A) O(V log V)", "B) O(V²)", "C) O(VE)", "D) O(E log V)"],
        correct_answer="C",
        subject="computer_science",
        difficulty="graduate"
    ),
]

SAMPLE_GSM8K = [
    BenchmarkQuestion(
        id="gsm8k_001",
        question="Janet's ducks lay 16 eggs per day. She eats three for breakfast every morning and bakes muffins for her friends every day with four. She sells the remainder at the farmers' market daily for $2 per fresh duck egg. How much in dollars does she make every day at the farmers' market?",
        choices=["A) $14", "B) $16", "C) $18", "D) $20"],
        correct_answer="C",
        subject="math",
        difficulty="grade_school"
    ),
    BenchmarkQuestion(
        id="gsm8k_002",
        question="A robe takes 2 bolts of blue fiber and half that much white fiber. How many bolts in total does it take?",
        choices=["A) 2", "B) 2.5", "C) 3", "D) 3.5"],
        correct_answer="C",
        subject="math",
        difficulty="grade_school"
    ),
]

SAMPLE_ARC = [
    BenchmarkQuestion(
        id="arc_001",
        question="Which of the following is an example of a chemical change?",
        choices=["A) Ice melting", "B) Paper burning", "C) Sugar dissolving in water", "D) Cutting wood"],
        correct_answer="B",
        subject="science",
        difficulty="challenge"
    ),
    BenchmarkQuestion(
        id="arc_002",
        question="What causes the seasons on Earth?",
        choices=["A) Distance from the Sun", "B) Earth's axial tilt", "C) Solar flares", "D) Moon's gravity"],
        correct_answer="B",
        subject="science",
        difficulty="challenge"
    ),
]

BENCHMARKS = {
    "gpqa": SAMPLE_GPQA,
    "gsm8k": SAMPLE_GSM8K,
    "arc": SAMPLE_ARC,
}


def query_ollama(
    model: str,
    prompt: str,
    host: str = "http://localhost:11434",
    temperature: float = 0.1
) -> Dict[str, Any]:
    """Query Ollama and return response with metadata."""
    url = f"{host}/api/generate"

    start = time.perf_counter()
    response = requests.post(url, json={
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature}
    }, timeout=120)
    latency = (time.perf_counter() - start) * 1000

    data = response.json()
    return {
        "response": data.get("response", ""),
        "latency_ms": latency,
        "tokens": data.get("eval_count", 0) + data.get("prompt_eval_count", 0)
    }


def extract_answer_and_confidence(response: str) -> tuple[str, float, str]:
    """Extract answer letter, confidence, and reasoning from response."""
    # Look for answer pattern
    answer = ""
    for letter in ["A", "B", "C", "D"]:
        if f"Answer: {letter}" in response or f"answer is {letter}" in response.lower() or f"({letter})" in response:
            answer = letter
            break

    # Look for confidence
    confidence = 0.7  # Default
    if "confidence:" in response.lower():
        try:
            conf_str = response.lower().split("confidence:")[1].split()[0]
            confidence = float(conf_str.strip("%").strip()) / 100 if "%" in conf_str else float(conf_str)
        except:
            pass
    elif "certain" in response.lower():
        confidence = 0.9
    elif "likely" in response.lower():
        confidence = 0.75
    elif "unsure" in response.lower() or "uncertain" in response.lower():
        confidence = 0.5

    return answer, min(1.0, max(0.0, confidence)), response


def run_baseline(
    model: str,
    question: BenchmarkQuestion,
    host: str = "http://localhost:11434"
) -> BenchmarkResult:
    """Run single-pass baseline inference."""
    prompt = f"""Answer the following multiple choice question. Think step by step, then provide your answer and confidence level.

Question: {question.question}

{chr(10).join(question.choices)}

Respond with your reasoning, then state "Answer: X" where X is A, B, C, or D, and "Confidence: Y" where Y is 0.0-1.0."""

    result = query_ollama(model, prompt, host)
    answer, confidence, reasoning = extract_answer_and_confidence(result["response"])

    return BenchmarkResult(
        question_id=question.id,
        model=model,
        mode="baseline",
        answer=answer,
        correct=answer == question.correct_answer,
        confidence=confidence,
        reasoning=reasoning[:500],
        latency_ms=result["latency_ms"],
        tokens_used=result["tokens"],
        iterations=1
    )


def run_empirica_loop(
    model: str,
    question: BenchmarkQuestion,
    host: str = "http://localhost:11434",
    max_iterations: int = 3
) -> BenchmarkResult:
    """
    Run Empirica epistemic loop:
    1. PREFLIGHT: Assess initial knowledge/uncertainty
    2. Investigate: Reason through the problem
    3. CHECK: Assess confidence to proceed
    4. If low confidence, iterate with reflection
    5. POSTFLIGHT: Final answer with calibrated confidence
    """
    total_latency = 0
    total_tokens = 0
    iterations = 0

    # Phase 1: PREFLIGHT - Initial assessment
    preflight_prompt = f"""You are about to answer a question. First, assess your knowledge state.

Question: {question.question}
{chr(10).join(question.choices)}

Before answering, assess:
1. What domain knowledge do you need?
2. What is your initial uncertainty (0.0-1.0)?
3. What approach will you take?

Respond with:
Domain: <domain>
Initial_Uncertainty: <0.0-1.0>
Approach: <your approach>"""

    preflight = query_ollama(model, preflight_prompt, host)
    total_latency += preflight["latency_ms"]
    total_tokens += preflight["tokens"]

    # Phase 2: Investigation loop
    current_answer = ""
    current_confidence = 0.0
    reasoning_history = [preflight["response"]]

    for iteration in range(max_iterations):
        iterations += 1

        # Investigate
        if iteration == 0:
            investigate_prompt = f"""Based on your assessment, now solve this step by step:

Question: {question.question}
{chr(10).join(question.choices)}

Think carefully through each step. Consider edge cases and verify your reasoning."""
        else:
            investigate_prompt = f"""Your previous answer may be incorrect. Reflect and reconsider:

Question: {question.question}
{chr(10).join(question.choices)}

Previous reasoning:
{reasoning_history[-1][:500]}

What might be wrong? Reconsider alternative approaches."""

        investigate = query_ollama(model, investigate_prompt, host)
        total_latency += investigate["latency_ms"]
        total_tokens += investigate["tokens"]
        reasoning_history.append(investigate["response"])

        # CHECK gate - assess confidence
        check_prompt = f"""Based on your analysis, provide your answer and confidence.

Your reasoning:
{investigate["response"][:800]}

Now state:
Answer: <A, B, C, or D>
Confidence: <0.0-1.0>
Uncertainty_Reduced: <yes/no>

Be honest about uncertainty. If confidence < 0.7, we may iterate."""

        check = query_ollama(model, check_prompt, host)
        total_latency += check["latency_ms"]
        total_tokens += check["tokens"]

        current_answer, current_confidence, _ = extract_answer_and_confidence(check["response"])

        # If confidence is high enough, proceed
        if current_confidence >= 0.75 or iteration == max_iterations - 1:
            break

    # Phase 3: POSTFLIGHT - Final verification
    postflight_prompt = f"""Final verification before submitting answer.

Question: {question.question}
Your answer: {current_answer}
Your confidence: {current_confidence}

Perform a final check:
1. Does the answer make logical sense?
2. Did you consider all options?
3. Are there any errors in your reasoning?

Final_Answer: <A, B, C, or D>
Final_Confidence: <0.0-1.0>
Verification: <passed/failed>"""

    postflight = query_ollama(model, postflight_prompt, host)
    total_latency += postflight["latency_ms"]
    total_tokens += postflight["tokens"]

    final_answer, final_confidence, final_reasoning = extract_answer_and_confidence(postflight["response"])

    # Use final answer if different, otherwise keep current
    if final_answer:
        current_answer = final_answer
        current_confidence = final_confidence

    return BenchmarkResult(
        question_id=question.id,
        model=model,
        mode="empirica",
        answer=current_answer,
        correct=current_answer == question.correct_answer,
        confidence=current_confidence,
        reasoning=final_reasoning[:500],
        latency_ms=total_latency,
        tokens_used=total_tokens,
        iterations=iterations
    )


def run_benchmark(
    model: str,
    benchmark: str,
    mode: str = "both",
    num_questions: Optional[int] = None,
    host: str = "http://localhost:11434"
) -> List[BenchmarkSummary]:
    """Run a benchmark and return summaries."""
    questions = BENCHMARKS.get(benchmark, SAMPLE_GSM8K)
    if num_questions:
        questions = questions[:num_questions]

    summaries = []

    modes_to_run = ["baseline", "empirica"] if mode == "both" else [mode]

    for run_mode in modes_to_run:
        results = []
        print(f"\n{'='*60}")
        print(f"Running {benchmark.upper()} - {run_mode.upper()} mode")
        print(f"{'='*60}")

        for i, q in enumerate(questions):
            print(f"  [{i+1}/{len(questions)}] {q.id}...", end=" ", flush=True)

            if run_mode == "baseline":
                result = run_baseline(model, q, host)
            else:
                result = run_empirica_loop(model, q, host)

            results.append(result)
            status = "✓" if result.correct else "✗"
            print(f"{status} (conf: {result.confidence:.2f}, iter: {result.iterations})")

        # Compute summary
        correct = sum(1 for r in results if r.correct)
        accuracy = correct / len(results) if results else 0
        avg_conf = sum(r.confidence for r in results) / len(results) if results else 0
        avg_latency = sum(r.latency_ms for r in results) / len(results) if results else 0
        total_tokens = sum(r.tokens_used for r in results)
        cal_error = abs(accuracy - avg_conf)

        summary = BenchmarkSummary(
            benchmark=benchmark,
            model=model,
            mode=run_mode,
            total_questions=len(results),
            correct=correct,
            accuracy=accuracy,
            avg_confidence=avg_conf,
            avg_latency_ms=avg_latency,
            total_tokens=total_tokens,
            calibration_error=cal_error,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            results=results
        )
        summaries.append(summary)

        print(f"\n{run_mode.upper()} Results:")
        print(f"  Accuracy: {accuracy*100:.1f}% ({correct}/{len(results)})")
        print(f"  Avg Confidence: {avg_conf:.3f}")
        print(f"  Calibration Error: {cal_error:.3f}")
        print(f"  Avg Latency: {avg_latency:.0f}ms")
        print(f"  Total Tokens: {total_tokens}")

    return summaries


def compare_results(summaries: List[BenchmarkSummary]):
    """Compare baseline vs empirica results."""
    baseline = next((s for s in summaries if s.mode == "baseline"), None)
    empirica = next((s for s in summaries if s.mode == "empirica"), None)

    if baseline and empirica:
        print(f"\n{'='*60}")
        print("COMPARISON: Baseline vs Empirica")
        print(f"{'='*60}")
        print(f"{'Metric':<25} {'Baseline':>12} {'Empirica':>12} {'Delta':>12}")
        print("-" * 60)

        acc_delta = empirica.accuracy - baseline.accuracy
        print(f"{'Accuracy':<25} {baseline.accuracy*100:>11.1f}% {empirica.accuracy*100:>11.1f}% {acc_delta*100:>+11.1f}%")

        cal_delta = baseline.calibration_error - empirica.calibration_error  # Lower is better
        print(f"{'Calibration Error':<25} {baseline.calibration_error:>12.3f} {empirica.calibration_error:>12.3f} {cal_delta:>+12.3f}")

        conf_delta = empirica.avg_confidence - baseline.avg_confidence
        print(f"{'Avg Confidence':<25} {baseline.avg_confidence:>12.3f} {empirica.avg_confidence:>12.3f} {conf_delta:>+12.3f}")

        token_ratio = empirica.total_tokens / baseline.total_tokens if baseline.total_tokens else 0
        print(f"{'Total Tokens':<25} {baseline.total_tokens:>12} {empirica.total_tokens:>12} {token_ratio:>11.1f}x")

        lat_ratio = empirica.avg_latency_ms / baseline.avg_latency_ms if baseline.avg_latency_ms else 0
        print(f"{'Avg Latency (ms)':<25} {baseline.avg_latency_ms:>12.0f} {empirica.avg_latency_ms:>12.0f} {lat_ratio:>11.1f}x")

        print("-" * 60)
        if acc_delta > 0:
            print(f"✓ Empirica improved accuracy by {acc_delta*100:.1f}%")
        if cal_delta > 0:
            print(f"✓ Empirica improved calibration by {cal_delta:.3f}")


def main():
    parser = argparse.ArgumentParser(description="Empirica Benchmark Harness")
    parser.add_argument("--model", "-m", default="qwen3:8b", help="Model to benchmark")
    parser.add_argument("--benchmark", "-b", default="gsm8k",
                        choices=list(BENCHMARKS.keys()), help="Benchmark to run")
    parser.add_argument("--mode", default="both", choices=["baseline", "empirica", "both"],
                        help="Run mode")
    parser.add_argument("--questions", "-n", type=int, help="Number of questions (default: all)")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama host")
    parser.add_argument("--output", "-o", help="Save results to JSON file")

    args = parser.parse_args()

    print(f"Empirica Benchmark Harness")
    print(f"Model: {args.model}")
    print(f"Benchmark: {args.benchmark}")
    print(f"Mode: {args.mode}")

    summaries = run_benchmark(
        model=args.model,
        benchmark=args.benchmark,
        mode=args.mode,
        num_questions=args.questions,
        host=args.host
    )

    if args.mode == "both":
        compare_results(summaries)

    if args.output:
        output_data = [asdict(s) for s in summaries]
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
