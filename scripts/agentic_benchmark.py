#!/usr/bin/env python3
"""
Agentic Benchmark Harness

Compares multiple agentic strategies across models:
- Vanilla (single-pass)
- Chain-of-Thought (CoT)
- ReAct (Reason + Act interleaved)
- Reflexion (self-reflection loop)
- Empirica (PREFLIGHT → CHECK → POSTFLIGHT with grounded verification)

Supports:
- Claude (Anthropic API)
- Local models via Ollama

Benchmarks:
- GPQA Diamond (graduate-level reasoning)
- GSM8K (math)
- ARC Challenge (science)
- GAIA-style tasks (multi-step)

Metrics:
- Accuracy
- Expected Calibration Error (ECE)
- Tokens per correct answer
- Time to solution

Usage:
    # Claude with Empirica on GPQA
    python agentic_benchmark.py --model claude-sonnet --strategy empirica --benchmark gpqa

    # Compare all strategies on local model
    python agentic_benchmark.py --model qwen3:8b --strategy all --benchmark gsm8k

    # Full comparison matrix
    python agentic_benchmark.py --compare-all --benchmark gpqa --output results.json
"""

import argparse
import json
import os
import time
import math
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional, Any, Callable
from pathlib import Path
from abc import ABC, abstractmethod
import subprocess
import sys

# Ensure dependencies
for pkg in ["requests", "anthropic"]:
    try:
        __import__(pkg)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", pkg, "-q"])

import requests

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False
    print("Warning: anthropic package not installed. Claude models unavailable.")


# =============================================================================
# Data Structures
# =============================================================================

@dataclass
class Question:
    id: str
    question: str
    choices: List[str]
    correct_answer: str
    subject: str = ""
    difficulty: str = ""


@dataclass
class StrategyResult:
    question_id: str
    model: str
    strategy: str
    answer: str
    correct: bool
    confidence: float
    reasoning: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    total_tokens: int
    iterations: int
    api_calls: int


@dataclass
class BenchmarkSummary:
    benchmark: str
    model: str
    strategy: str
    total_questions: int
    correct: int
    accuracy: float
    avg_confidence: float
    ece: float  # Expected Calibration Error
    tokens_per_correct: float
    avg_latency_ms: float
    total_tokens: int
    total_api_calls: int
    timestamp: str
    results: List[StrategyResult] = field(default_factory=list)


# =============================================================================
# Model Backends
# =============================================================================

class ModelBackend(ABC):
    @abstractmethod
    def generate(self, prompt: str, system: str = "", temperature: float = 0.1) -> Dict[str, Any]:
        """Generate response. Returns dict with 'response', 'input_tokens', 'output_tokens', 'latency_ms'."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass


class OllamaBackend(ModelBackend):
    def __init__(self, model: str, host: str = "http://localhost:11434"):
        self._model = model
        self.host = host

    @property
    def name(self) -> str:
        return self._model

    def generate(self, prompt: str, system: str = "", temperature: float = 0.1) -> Dict[str, Any]:
        url = f"{self.host}/api/generate"

        full_prompt = f"{system}\n\n{prompt}" if system else prompt

        start = time.perf_counter()
        response = requests.post(url, json={
            "model": self._model,
            "prompt": full_prompt,
            "stream": False,
            "options": {"temperature": temperature}
        }, timeout=180)
        latency = (time.perf_counter() - start) * 1000

        data = response.json()
        return {
            "response": data.get("response", ""),
            "input_tokens": data.get("prompt_eval_count", 0),
            "output_tokens": data.get("eval_count", 0),
            "latency_ms": latency
        }


class ClaudeCLIBackend(ModelBackend):
    """
    Claude backend using the `claude` CLI command (OAuth/MAX plan).
    No API key needed - uses existing Claude Code authentication.
    """

    def __init__(self, model: str = "claude"):
        self._model = model

    @property
    def name(self) -> str:
        return self._model

    def generate(self, prompt: str, system: str = "", temperature: float = 0.1) -> Dict[str, Any]:
        import subprocess

        full_prompt = f"{system}\n\n{prompt}" if system else prompt

        start = time.perf_counter()

        # Use claude CLI with --print flag for non-interactive output
        result = subprocess.run(
            ["claude", "--print", "--dangerously-skip-permissions"],
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=180
        )

        latency = (time.perf_counter() - start) * 1000

        response_text = result.stdout.strip()

        # Estimate tokens (rough: 4 chars per token)
        input_tokens = len(full_prompt) // 4
        output_tokens = len(response_text) // 4

        return {
            "response": response_text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency
        }


class ClaudeAPIBackend(ModelBackend):
    """Claude backend using Anthropic API (requires API key)."""

    MODEL_MAP = {
        "claude-sonnet": "claude-sonnet-4-20250514",
        "claude-opus": "claude-opus-4-20250514",
        "claude-haiku": "claude-3-5-haiku-20241022",
    }

    def __init__(self, model: str, api_key: Optional[str] = None):
        if not HAS_ANTHROPIC:
            raise ImportError("anthropic package required for Claude API models")

        self._model_alias = model
        self._model = self.MODEL_MAP.get(model, model)
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    @property
    def name(self) -> str:
        return self._model_alias

    def generate(self, prompt: str, system: str = "", temperature: float = 0.1) -> Dict[str, Any]:
        start = time.perf_counter()

        message = self.client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system if system else "You are a helpful assistant.",
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature
        )

        latency = (time.perf_counter() - start) * 1000

        response_text = ""
        for block in message.content:
            if hasattr(block, 'text'):
                response_text = block.text
                break

        return {
            "response": response_text,
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
            "latency_ms": latency
        }


def get_backend(model: str, ollama_host: str = "http://localhost:11434", use_cli: bool = False) -> ModelBackend:
    """Factory for model backends."""
    if model.startswith("claude"):
        if use_cli:
            return ClaudeCLIBackend(model)
        else:
            # Try CLI first if no API key
            if not os.environ.get("ANTHROPIC_API_KEY"):
                print(f"No ANTHROPIC_API_KEY found, using Claude CLI for {model}")
                return ClaudeCLIBackend(model)
            return ClaudeAPIBackend(model)
    else:
        return OllamaBackend(model, ollama_host)


# =============================================================================
# Agentic Strategies
# =============================================================================

def extract_answer_confidence(response: str) -> tuple[str, float]:
    """Extract answer letter and confidence from response."""
    answer = ""
    for letter in ["A", "B", "C", "D"]:
        patterns = [f"Answer: {letter}", f"answer is {letter}", f"**{letter}**", f"({letter})"]
        if any(p in response for p in patterns):
            answer = letter
            break

    confidence = 0.7
    response_lower = response.lower()
    if "confidence:" in response_lower:
        try:
            conf_str = response_lower.split("confidence:")[1].split()[0]
            confidence = float(conf_str.strip("%").strip())
            if confidence > 1:
                confidence /= 100
        except:
            pass
    elif "certain" in response_lower or "definitely" in response_lower:
        confidence = 0.95
    elif "likely" in response_lower or "probably" in response_lower:
        confidence = 0.8
    elif "unsure" in response_lower or "uncertain" in response_lower:
        confidence = 0.5
    elif "guess" in response_lower:
        confidence = 0.3

    return answer, min(1.0, max(0.0, confidence))


class Strategy(ABC):
    """Base class for agentic strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def solve(self, backend: ModelBackend, question: Question) -> StrategyResult:
        pass


class VanillaStrategy(Strategy):
    """Single-pass, no scaffolding."""

    @property
    def name(self) -> str:
        return "vanilla"

    def solve(self, backend: ModelBackend, question: Question) -> StrategyResult:
        prompt = f"""Answer this multiple choice question.

Question: {question.question}

{chr(10).join(question.choices)}

Provide your answer as "Answer: X" where X is A, B, C, or D."""

        result = backend.generate(prompt)
        answer, confidence = extract_answer_confidence(result["response"])

        return StrategyResult(
            question_id=question.id,
            model=backend.name,
            strategy=self.name,
            answer=answer,
            correct=answer == question.correct_answer,
            confidence=confidence,
            reasoning=result["response"][:500],
            latency_ms=result["latency_ms"],
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
            total_tokens=result["input_tokens"] + result["output_tokens"],
            iterations=1,
            api_calls=1
        )


class ChainOfThoughtStrategy(Strategy):
    """Chain-of-thought prompting."""

    @property
    def name(self) -> str:
        return "cot"

    def solve(self, backend: ModelBackend, question: Question) -> StrategyResult:
        prompt = f"""Answer this multiple choice question. Think step by step.

Question: {question.question}

{chr(10).join(question.choices)}

Let's think through this step by step:
1. First, identify what the question is asking.
2. Consider each option.
3. Reason through the problem.
4. State your final answer as "Answer: X" and "Confidence: Y" (0.0-1.0)."""

        result = backend.generate(prompt)
        answer, confidence = extract_answer_confidence(result["response"])

        return StrategyResult(
            question_id=question.id,
            model=backend.name,
            strategy=self.name,
            answer=answer,
            correct=answer == question.correct_answer,
            confidence=confidence,
            reasoning=result["response"][:500],
            latency_ms=result["latency_ms"],
            input_tokens=result["input_tokens"],
            output_tokens=result["output_tokens"],
            total_tokens=result["input_tokens"] + result["output_tokens"],
            iterations=1,
            api_calls=1
        )


class ReActStrategy(Strategy):
    """ReAct: Interleaved reasoning and acting."""

    @property
    def name(self) -> str:
        return "react"

    def solve(self, backend: ModelBackend, question: Question, max_steps: int = 3) -> StrategyResult:
        total_input = 0
        total_output = 0
        total_latency = 0
        api_calls = 0

        context = f"""Question: {question.question}

{chr(10).join(question.choices)}"""

        thoughts = []

        for step in range(max_steps):
            if step == 0:
                prompt = f"""{context}

Use the ReAct framework. For each step:
Thought: [Your reasoning about what to do]
Action: [THINK to reason more, or ANSWER to give final answer]
Observation: [Result of your action]

Begin:
Thought:"""
            else:
                prompt = f"""{context}

Previous reasoning:
{chr(10).join(thoughts)}

Continue reasoning or provide final answer:
Thought:"""

            result = backend.generate(prompt)
            total_input += result["input_tokens"]
            total_output += result["output_tokens"]
            total_latency += result["latency_ms"]
            api_calls += 1

            thoughts.append(result["response"][:300])

            # Check if we have an answer
            if "Answer:" in result["response"] or "ANSWER" in result["response"]:
                answer, confidence = extract_answer_confidence(result["response"])
                if answer:
                    return StrategyResult(
                        question_id=question.id,
                        model=backend.name,
                        strategy=self.name,
                        answer=answer,
                        correct=answer == question.correct_answer,
                        confidence=confidence,
                        reasoning="\n".join(thoughts)[:500],
                        latency_ms=total_latency,
                        input_tokens=total_input,
                        output_tokens=total_output,
                        total_tokens=total_input + total_output,
                        iterations=step + 1,
                        api_calls=api_calls
                    )

        # Final answer extraction
        final_prompt = f"""{context}

Your reasoning:
{chr(10).join(thoughts)}

Based on your analysis, provide your final answer as "Answer: X" and "Confidence: Y"."""

        result = backend.generate(final_prompt)
        total_input += result["input_tokens"]
        total_output += result["output_tokens"]
        total_latency += result["latency_ms"]
        api_calls += 1

        answer, confidence = extract_answer_confidence(result["response"])

        return StrategyResult(
            question_id=question.id,
            model=backend.name,
            strategy=self.name,
            answer=answer,
            correct=answer == question.correct_answer,
            confidence=confidence,
            reasoning="\n".join(thoughts)[:500],
            latency_ms=total_latency,
            input_tokens=total_input,
            output_tokens=total_output,
            total_tokens=total_input + total_output,
            iterations=max_steps,
            api_calls=api_calls
        )


class ReflexionStrategy(Strategy):
    """Reflexion: Self-reflection and retry on low confidence."""

    @property
    def name(self) -> str:
        return "reflexion"

    def solve(self, backend: ModelBackend, question: Question, max_iterations: int = 3) -> StrategyResult:
        total_input = 0
        total_output = 0
        total_latency = 0
        api_calls = 0

        context = f"""Question: {question.question}

{chr(10).join(question.choices)}"""

        previous_attempts = []

        for iteration in range(max_iterations):
            if iteration == 0:
                prompt = f"""{context}

Solve this step by step. At the end, provide:
Answer: [A, B, C, or D]
Confidence: [0.0-1.0]"""
            else:
                prompt = f"""{context}

Previous attempts:
{chr(10).join(previous_attempts)}

Reflect on your previous reasoning. What might be wrong? Try a different approach.
Provide:
Answer: [A, B, C, or D]
Confidence: [0.0-1.0]"""

            result = backend.generate(prompt)
            total_input += result["input_tokens"]
            total_output += result["output_tokens"]
            total_latency += result["latency_ms"]
            api_calls += 1

            answer, confidence = extract_answer_confidence(result["response"])
            previous_attempts.append(f"Attempt {iteration+1}: {result['response'][:200]}")

            # If confidence is high enough, stop
            if confidence >= 0.8 or iteration == max_iterations - 1:
                return StrategyResult(
                    question_id=question.id,
                    model=backend.name,
                    strategy=self.name,
                    answer=answer,
                    correct=answer == question.correct_answer,
                    confidence=confidence,
                    reasoning=result["response"][:500],
                    latency_ms=total_latency,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    total_tokens=total_input + total_output,
                    iterations=iteration + 1,
                    api_calls=api_calls
                )

        # Shouldn't reach here, but just in case
        return StrategyResult(
            question_id=question.id,
            model=backend.name,
            strategy=self.name,
            answer=answer,
            correct=answer == question.correct_answer,
            confidence=confidence,
            reasoning="Max iterations reached",
            latency_ms=total_latency,
            input_tokens=total_input,
            output_tokens=total_output,
            total_tokens=total_input + total_output,
            iterations=max_iterations,
            api_calls=api_calls
        )


class EmpiricaStrategy(Strategy):
    """
    Empirica: Full epistemic loop with PREFLIGHT → CHECK → POSTFLIGHT.

    Implements the CASCADE workflow:
    1. PREFLIGHT: Assess initial knowledge state (know, uncertainty, context)
    2. Investigation: Noetic phase - gather information, reason through problem
    3. CHECK: Gate decision - proceed if confident, investigate more if not
    4. Action: Praxic phase - commit to answer
    5. POSTFLIGHT: Assess final state, compare to baseline
    """

    @property
    def name(self) -> str:
        return "empirica"

    def solve(self, backend: ModelBackend, question: Question, max_iterations: int = 3) -> StrategyResult:
        total_input = 0
        total_output = 0
        total_latency = 0
        api_calls = 0

        context = f"""Question: {question.question}

{chr(10).join(question.choices)}"""

        # Phase 1: PREFLIGHT - Assess initial epistemic state
        preflight_prompt = f"""You are about to answer a question. First, assess your epistemic state.

{context}

Assess your current knowledge state:
- KNOW (0.0-1.0): How much domain knowledge do you have for this question?
- UNCERTAINTY (0.0-1.0): How uncertain are you about your ability to answer correctly?
- CONTEXT (0.0-1.0): How well do you understand what's being asked?

Respond in this format:
KNOW: [value]
UNCERTAINTY: [value]
CONTEXT: [value]
APPROACH: [how you will approach this problem]"""

        preflight = backend.generate(preflight_prompt)
        total_input += preflight["input_tokens"]
        total_output += preflight["output_tokens"]
        total_latency += preflight["latency_ms"]
        api_calls += 1

        # Extract PREFLIGHT vectors
        preflight_know = 0.5
        preflight_uncertainty = 0.5
        for line in preflight["response"].split("\n"):
            if "KNOW:" in line:
                try:
                    preflight_know = float(line.split(":")[1].strip())
                except:
                    pass
            if "UNCERTAINTY:" in line:
                try:
                    preflight_uncertainty = float(line.split(":")[1].strip())
                except:
                    pass

        # Phase 2: Investigation loop with CHECK gates
        investigation_history = []
        current_answer = ""
        current_confidence = 0.0

        for iteration in range(max_iterations):
            # Investigation (Noetic phase)
            if iteration == 0:
                investigate_prompt = f"""{context}

Based on your self-assessment, now investigate and solve this problem.

Think carefully:
1. What concepts are relevant?
2. What are the key constraints?
3. Analyze each option systematically.

Provide your reasoning."""
            else:
                investigate_prompt = f"""{context}

Previous reasoning:
{investigation_history[-1][:400]}

Your confidence was below threshold. Investigate further:
- What assumptions did you make?
- What alternative interpretations exist?
- Re-examine the options with fresh perspective.

Provide updated reasoning."""

            investigate = backend.generate(investigate_prompt)
            total_input += investigate["input_tokens"]
            total_output += investigate["output_tokens"]
            total_latency += investigate["latency_ms"]
            api_calls += 1
            investigation_history.append(investigate["response"])

            # CHECK gate - assess readiness to proceed
            check_prompt = f"""Based on your investigation, assess your readiness to answer.

Your reasoning:
{investigate["response"][:600]}

Provide:
KNOW: [0.0-1.0] - How confident are you in your domain knowledge now?
UNCERTAINTY: [0.0-1.0] - How uncertain are you about the answer?
READY: [yes/no] - Are you ready to commit to an answer?
Answer: [A, B, C, or D]
Confidence: [0.0-1.0]"""

            check = backend.generate(check_prompt)
            total_input += check["input_tokens"]
            total_output += check["output_tokens"]
            total_latency += check["latency_ms"]
            api_calls += 1

            current_answer, current_confidence = extract_answer_confidence(check["response"])

            # Check if ready to proceed
            ready = "READY: yes" in check["response"] or "READY:yes" in check["response"]
            if ready or current_confidence >= 0.75 or iteration == max_iterations - 1:
                break

        # Phase 3: POSTFLIGHT - Final verification and state assessment
        postflight_prompt = f"""{context}

Your final reasoning:
{investigation_history[-1][:400]}

Your answer: {current_answer}
Your confidence: {current_confidence}

POSTFLIGHT verification:
1. Does your answer logically follow from your reasoning?
2. Did you consider all options?
3. What's your final epistemic state?

Respond:
VERIFICATION: [passed/failed]
KNOW_FINAL: [0.0-1.0]
UNCERTAINTY_FINAL: [0.0-1.0]
FINAL_ANSWER: [A, B, C, or D]
FINAL_CONFIDENCE: [0.0-1.0]
LEARNING_DELTA: [what did you learn during investigation?]"""

        postflight = backend.generate(postflight_prompt)
        total_input += postflight["input_tokens"]
        total_output += postflight["output_tokens"]
        total_latency += postflight["latency_ms"]
        api_calls += 1

        # Extract final answer from POSTFLIGHT
        final_answer, final_confidence = extract_answer_confidence(postflight["response"])
        if final_answer:
            current_answer = final_answer
            current_confidence = final_confidence

        return StrategyResult(
            question_id=question.id,
            model=backend.name,
            strategy=self.name,
            answer=current_answer,
            correct=current_answer == question.correct_answer,
            confidence=current_confidence,
            reasoning=f"PREFLIGHT know={preflight_know:.2f}, uncertainty={preflight_uncertainty:.2f}\n{investigation_history[-1][:300]}",
            latency_ms=total_latency,
            input_tokens=total_input,
            output_tokens=total_output,
            total_tokens=total_input + total_output,
            iterations=len(investigation_history),
            api_calls=api_calls
        )


# =============================================================================
# Benchmarks
# =============================================================================

GPQA_SAMPLE = [
    Question("gpqa_001", "A quantum system is prepared in a superposition state |ψ⟩ = (|0⟩ + |1⟩)/√2. After a Hadamard gate is applied, what is the resulting state?",
             ["A) |0⟩", "B) |1⟩", "C) (|0⟩ + |1⟩)/√2", "D) (|0⟩ - |1⟩)/√2"], "A", "physics", "graduate"),
    Question("gpqa_002", "In organic chemistry, what is the major product when 2-butene reacts with HBr in the presence of peroxides?",
             ["A) 2-bromobutane", "B) 1-bromobutane", "C) 2,3-dibromobutane", "D) 1,2-dibromobutane"], "B", "chemistry", "graduate"),
    Question("gpqa_003", "What is the time complexity of the Bellman-Ford algorithm for finding shortest paths in a weighted directed graph with negative edge weights?",
             ["A) O(V log V)", "B) O(V²)", "C) O(VE)", "D) O(E log V)"], "C", "cs", "graduate"),
    Question("gpqa_004", "In statistical mechanics, what is the relationship between entropy S and the number of microstates Ω?",
             ["A) S = Ω", "B) S = ln(Ω)", "C) S = kB ln(Ω)", "D) S = kB Ω"], "C", "physics", "graduate"),
    Question("gpqa_005", "Which enzyme catalyzes the rate-limiting step of glycolysis?",
             ["A) Hexokinase", "B) Phosphofructokinase-1", "C) Pyruvate kinase", "D) Aldolase"], "B", "biology", "graduate"),
]

GSM8K_SAMPLE = [
    Question("gsm8k_001", "Janet's ducks lay 16 eggs per day. She eats three for breakfast every morning and bakes muffins for her friends every day with four. She sells the remainder at the farmers' market daily for $2 per fresh duck egg. How much in dollars does she make every day at the farmers' market?",
             ["A) $14", "B) $16", "C) $18", "D) $20"], "C", "math", "grade"),
    Question("gsm8k_002", "A robe takes 2 bolts of blue fiber and half that much white fiber. How many bolts in total does it take?",
             ["A) 2", "B) 2.5", "C) 3", "D) 3.5"], "C", "math", "grade"),
    Question("gsm8k_003", "Josh decides to try flipping a house. He buys a house for $80,000 and then puts in $50,000 in repairs. This increased the value of the house by 150%. How much profit did he make?",
             ["A) $70,000", "B) $80,000", "C) $90,000", "D) $100,000"], "A", "math", "grade"),
    Question("gsm8k_004", "James writes a 3-page letter to 2 different friends twice a week. How many pages does he write a year?",
             ["A) 312", "B) 520", "C) 624", "D) 728"], "C", "math", "grade"),
    Question("gsm8k_005", "Every day, Wendi feeds each of her chickens three cups of mixed chicken feed, containing seeds, mealworms and vegetables to help keep them healthy. She gives the chickens their feed in three separate meals. In the morning, she gives her flock of chickens 15 cups of feed. In the afternoon, she gives her chickens another 25 cups of feed. If Wendi counts 20 more cups in the evening, how many chickens does Wendi have?",
             ["A) 15", "B) 18", "C) 20", "D) 22"], "C", "math", "grade"),
]

ARC_SAMPLE = [
    Question("arc_001", "Which of the following is an example of a chemical change?",
             ["A) Ice melting", "B) Paper burning", "C) Sugar dissolving in water", "D) Cutting wood"], "B", "science", "challenge"),
    Question("arc_002", "What causes the seasons on Earth?",
             ["A) Distance from the Sun", "B) Earth's axial tilt", "C) Solar flares", "D) Moon's gravity"], "B", "science", "challenge"),
    Question("arc_003", "Which property of water allows insects to walk on its surface?",
             ["A) Density", "B) Viscosity", "C) Surface tension", "D) Temperature"], "C", "science", "challenge"),
    Question("arc_004", "What is the main function of the mitochondria in a cell?",
             ["A) Protein synthesis", "B) Energy production", "C) Cell division", "D) Waste removal"], "B", "biology", "challenge"),
    Question("arc_005", "When a red object is illuminated with red light, what color does it appear?",
             ["A) Black", "B) White", "C) Red", "D) Blue"], "C", "physics", "challenge"),
]

BENCHMARKS = {
    "gpqa": GPQA_SAMPLE,
    "gsm8k": GSM8K_SAMPLE,
    "arc": ARC_SAMPLE,
}

STRATEGIES = {
    "vanilla": VanillaStrategy(),
    "cot": ChainOfThoughtStrategy(),
    "react": ReActStrategy(),
    "reflexion": ReflexionStrategy(),
    "empirica": EmpiricaStrategy(),
}


# =============================================================================
# Metrics
# =============================================================================

def compute_ece(results: List[StrategyResult], num_bins: int = 10) -> float:
    """Compute Expected Calibration Error."""
    if not results:
        return 0.0

    bins = [[] for _ in range(num_bins)]

    for r in results:
        bin_idx = min(int(r.confidence * num_bins), num_bins - 1)
        bins[bin_idx].append(r)

    ece = 0.0
    total = len(results)

    for bin_results in bins:
        if bin_results:
            bin_confidence = sum(r.confidence for r in bin_results) / len(bin_results)
            bin_accuracy = sum(1 for r in bin_results if r.correct) / len(bin_results)
            ece += len(bin_results) / total * abs(bin_accuracy - bin_confidence)

    return ece


# =============================================================================
# Main
# =============================================================================

def run_benchmark(
    backend: ModelBackend,
    benchmark: str,
    strategy: Strategy,
    num_questions: Optional[int] = None
) -> BenchmarkSummary:
    """Run a single benchmark with one strategy."""
    questions = BENCHMARKS.get(benchmark, GPQA_SAMPLE)
    if num_questions:
        questions = questions[:num_questions]

    results = []
    print(f"\n{'='*60}")
    print(f"Model: {backend.name} | Strategy: {strategy.name} | Benchmark: {benchmark}")
    print(f"{'='*60}")

    for i, q in enumerate(questions):
        print(f"  [{i+1}/{len(questions)}] {q.id}...", end=" ", flush=True)
        try:
            result = strategy.solve(backend, q)
            results.append(result)
            status = "✓" if result.correct else "✗"
            print(f"{status} (conf={result.confidence:.2f}, calls={result.api_calls})")
        except Exception as e:
            print(f"ERROR: {e}")

    # Compute metrics
    correct = sum(1 for r in results if r.correct)
    accuracy = correct / len(results) if results else 0
    avg_conf = sum(r.confidence for r in results) / len(results) if results else 0
    ece = compute_ece(results)
    total_tokens = sum(r.total_tokens for r in results)
    tokens_per_correct = total_tokens / correct if correct > 0 else float('inf')
    avg_latency = sum(r.latency_ms for r in results) / len(results) if results else 0
    total_calls = sum(r.api_calls for r in results)

    summary = BenchmarkSummary(
        benchmark=benchmark,
        model=backend.name,
        strategy=strategy.name,
        total_questions=len(results),
        correct=correct,
        accuracy=accuracy,
        avg_confidence=avg_conf,
        ece=ece,
        tokens_per_correct=tokens_per_correct,
        avg_latency_ms=avg_latency,
        total_tokens=total_tokens,
        total_api_calls=total_calls,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        results=results
    )

    print(f"\nResults:")
    print(f"  Accuracy: {accuracy*100:.1f}% ({correct}/{len(results)})")
    print(f"  Confidence: {avg_conf:.3f}")
    print(f"  ECE: {ece:.3f}")
    print(f"  Tokens/correct: {tokens_per_correct:.0f}")
    print(f"  API calls: {total_calls}")

    return summary


def compare_strategies(
    model: str,
    benchmark: str,
    strategies: List[str],
    num_questions: Optional[int] = None,
    ollama_host: str = "http://localhost:11434",
    use_cli: bool = False
) -> List[BenchmarkSummary]:
    """Compare multiple strategies on same model and benchmark."""
    backend = get_backend(model, ollama_host, use_cli=use_cli)
    summaries = []

    for strategy_name in strategies:
        strategy = STRATEGIES.get(strategy_name)
        if strategy:
            summary = run_benchmark(backend, benchmark, strategy, num_questions)
            summaries.append(summary)

    # Print comparison table
    print(f"\n{'='*80}")
    print(f"COMPARISON: {model} on {benchmark}")
    print(f"{'='*80}")
    print(f"{'Strategy':<12} {'Accuracy':>10} {'ECE':>8} {'Tok/Correct':>12} {'API Calls':>10}")
    print("-" * 80)
    for s in summaries:
        print(f"{s.strategy:<12} {s.accuracy*100:>9.1f}% {s.ece:>8.3f} {s.tokens_per_correct:>12.0f} {s.total_api_calls:>10}")

    return summaries


def main():
    parser = argparse.ArgumentParser(description="Agentic Benchmark Harness")
    parser.add_argument("--model", "-m", default="qwen3:8b",
                        help="Model to use (e.g., claude-sonnet, qwen3:8b)")
    parser.add_argument("--strategy", "-s", default="all",
                        choices=list(STRATEGIES.keys()) + ["all"],
                        help="Strategy to use")
    parser.add_argument("--benchmark", "-b", default="gpqa",
                        choices=list(BENCHMARKS.keys()),
                        help="Benchmark to run")
    parser.add_argument("--questions", "-n", type=int, help="Number of questions")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama host")
    parser.add_argument("--output", "-o", help="Save results to JSON")
    parser.add_argument("--compare-all", action="store_true",
                        help="Compare all strategies")
    parser.add_argument("--cli", action="store_true",
                        help="Use Claude CLI instead of API (for MAX plan OAuth)")

    args = parser.parse_args()

    if args.strategy == "all" or args.compare_all:
        strategies = list(STRATEGIES.keys())
    else:
        strategies = [args.strategy]

    summaries = compare_strategies(
        args.model,
        args.benchmark,
        strategies,
        args.questions,
        args.host,
        use_cli=args.cli
    )

    if args.output:
        with open(args.output, "w") as f:
            json.dump([asdict(s) for s in summaries], f, indent=2)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
