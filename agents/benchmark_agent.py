"""
RyzenSense v2 - Benchmark Agent
Runs CPU/GPU benchmarks, compares before/after tuning performance,
and broadcasts results to other agents.
"""

import time
import math
import threading
import os
from collections import deque
from agents.base_agent import BaseAgent


class BenchmarkAgent(BaseAgent):
    """
    Dedicated benchmarking agent.
    Runs stress tests, measures performance, and compares
    results across different tuning profiles.
    """

    def __init__(self):
        super().__init__()
        self._results_history = deque(maxlen=20)
        self._running_benchmark = False
        self._baseline = None

    def on_event(self, event: str, data: dict, sender: str):
        if event == "tune_applied" and data.get("auto_benchmark"):
            self.log("Auto-benchmark triggered after tune")
            threading.Thread(target=self.run_benchmark, daemon=True).start()

        elif event == "benchmark_request":
            threading.Thread(target=self.run_benchmark, daemon=True).start()

    def run_benchmark(self, label: str = None) -> dict:
        """Full benchmark: single-thread, multi-thread, memory."""
        if self._running_benchmark:
            self.log("Benchmark already running, skipping.")
            return {}

        self._running_benchmark = True
        label = label or time.strftime("%H:%M:%S")
        self.set_action(f"benchmarking ({label})")
        self.log(f"Starting benchmark: {label}")

        # Capture pre-benchmark state
        telemetry = self.get_agent("telemetry")
        pre_state = telemetry.get_current_state() if telemetry else {}

        results = {
            "label": label,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "cpu_temp_before": pre_state.get("cpu", {}).get("temp_c", 0),
            "gpu_temp_before": pre_state.get("gpu", {}).get("temp_c", 0),
        }

        # Single-thread score
        self.log("Running single-thread test...")
        st_score = self._single_thread_test()
        results["single_thread_score"] = st_score

        # Multi-thread score
        self.log("Running multi-thread test...")
        mt_score = self._multi_thread_test()
        results["multi_thread_score"] = mt_score
        results["mt_efficiency"] = round(mt_score / max(st_score, 1), 2)

        # Memory bandwidth estimate
        self.log("Running memory test...")
        mem_score = self._memory_test()
        results["memory_score"] = mem_score

        # Capture post-benchmark state
        post_state = telemetry.get_current_state() if telemetry else {}
        results["cpu_temp_after"] = post_state.get("cpu", {}).get("temp_c", 0)
        results["gpu_temp_after"] = post_state.get("gpu", {}).get("temp_c", 0)
        results["temp_delta"] = round(
            results["cpu_temp_after"] - results["cpu_temp_before"], 1
        )

        # Compare to baseline
        if self._baseline:
            results["vs_baseline"] = {
                "single_thread_pct": round(
                    (st_score / self._baseline["single_thread_score"] - 1) * 100, 1
                ),
                "multi_thread_pct": round(
                    (mt_score / self._baseline["multi_thread_score"] - 1) * 100, 1
                ),
            }
        else:
            self._baseline = results
            results["is_baseline"] = True

        self._results_history.append(results)
        self._running_benchmark = False

        # Print summary
        self._print_results(results)

        # Broadcast results
        self.broadcast("benchmark_complete", {"results": results})
        self.set_action(f"done | ST:{st_score} MT:{mt_score}")

        return results

    def set_baseline(self):
        """Run a benchmark and store as the comparison baseline."""
        self.log("Setting performance baseline...")
        self._baseline = None  # Reset so next run becomes baseline
        return self.run_benchmark(label="baseline")

    def get_history(self) -> list:
        return list(self._results_history)

    def compare_profiles(self, profile_a: str, profile_b: str) -> dict:
        """Compare two benchmark results by label."""
        results = {r["label"]: r for r in self._results_history}
        a = results.get(profile_a)
        b = results.get(profile_b)
        if not a or not b:
            return {"error": "One or both profiles not found in history"}
        return {
            "profile_a": profile_a,
            "profile_b": profile_b,
            "single_thread_diff_pct": round(
                (b["single_thread_score"] / a["single_thread_score"] - 1) * 100, 1
            ),
            "multi_thread_diff_pct": round(
                (b["multi_thread_score"] / a["multi_thread_score"] - 1) * 100, 1
            ),
            "temp_delta_diff": round(b["temp_delta"] - a["temp_delta"], 1),
        }

    # ── Internal benchmark workloads ──────────────────────────────────────────

    def _single_thread_test(self) -> int:
        """Math-heavy single-core test. Returns iterations/second."""
        iterations = 0
        end_time = time.perf_counter() + 2.0  # 2 second test
        while time.perf_counter() < end_time:
            for i in range(1, 1000):
                _ = math.sqrt(i) * math.log(i) * math.sin(i)
            iterations += 1000
        return int(iterations / 2)

    def _multi_thread_test(self) -> int:
        """Multi-core test using all available threads."""
        num_threads = os.cpu_count() or 4
        scores = []
        lock = threading.Lock()

        def worker():
            score = 0
            end_time = time.perf_counter() + 2.0
            while time.perf_counter() < end_time:
                for i in range(1, 500):
                    _ = math.sqrt(i) * math.log(i) * math.sin(i)
                score += 500
            with lock:
                scores.append(score)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        return int(sum(scores) / 2)

    def _memory_test(self) -> int:
        """Simple memory bandwidth test."""
        size = 1_000_000
        start = time.perf_counter()
        data = list(range(size))
        total = sum(data)
        elapsed = time.perf_counter() - start
        return int(size / max(elapsed, 0.001))

    def _print_results(self, r: dict):
        self.log("── Benchmark Results ─────────────────────")
        self.log(f"  Single-thread:  {r['single_thread_score']:,} ops/s")
        self.log(f"  Multi-thread:   {r['multi_thread_score']:,} ops/s")
        self.log(f"  MT efficiency:  {r['mt_efficiency']}x")
        self.log(f"  Memory:         {r['memory_score']:,} ops/s")
        self.log(f"  CPU temp delta: +{r['temp_delta']}°C")
        if "vs_baseline" in r:
            vs = r["vs_baseline"]
            sign = "+" if vs["single_thread_pct"] >= 0 else ""
            self.log(f"  vs baseline:    ST {sign}{vs['single_thread_pct']}%  MT {sign}{vs['multi_thread_pct']}%")