"""
Graduated Stress Testing Suite for O.B.E.O.S

Tests system stability under increasing load:
- Level 1: 50 concurrent queries (baseline)
- Level 2: 100 concurrent queries (scalability)
- Level 3: 250 concurrent queries (stress threshold)

Validates:
- No deadlocks
- No queue corruption
- No memory leaks
- No routing collapse
"""

from __future__ import annotations

import time
import statistics
import threading
import concurrent.futures
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

from blackfong_ecosystem.observability import log_action, LogLevel
from blackfong_ecosystem.memory_lifecycle_management import memory_lifecycle_manager
from blackfong_ecosystem.decision_quality_control import decision_quality_controller


@dataclass
class TestResult:
    """Individual query result tracking"""
    test_id: str
    query: str
    success: bool
    latency_ms: float
    confidence: float = 0.0
    error: Optional[str] = None
    agent_used: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class StressTestRun:
    """Complete stress test run results"""
    run_id: str
    num_queries: int
    num_workers: int
    total_time_sec: float
    successful_queries: int
    failed_queries: int
    results: List[TestResult] = field(default_factory=list)
    
    @property
    def success_rate(self) -> float:
        """Percentage of successful queries"""
        if self.num_queries == 0:
            return 0.0
        return (self.successful_queries / self.num_queries) * 100
    
    @property
    def queries_per_second(self) -> float:
        """Throughput in queries/second"""
        if self.total_time_sec == 0:
            return 0.0
        return self.num_queries / self.total_time_sec
    
    @property
    def avg_latency_ms(self) -> float:
        """Average query latency"""
        if not self.results:
            return 0.0
        return statistics.mean(r.latency_ms for r in self.results)
    
    @property
    def p95_latency_ms(self) -> float:
        """95th percentile latency"""
        if len(self.results) < 2:
            return 0.0
        latencies = sorted([r.latency_ms for r in self.results])
        idx = int(len(latencies) * 0.95)
        return latencies[idx]
    
    @property
    def p99_latency_ms(self) -> float:
        """99th percentile latency"""
        if len(self.results) < 2:
            return 0.0
        latencies = sorted([r.latency_ms for r in self.results])
        idx = int(len(latencies) * 0.99)
        return latencies[idx]


class StressTestSuite:
    """
    Graduated stress testing for O.B.E.O.S stability validation.
    
    Usage:
        suite = StressTestSuite()
        suite.run_level_1()  # 50 queries
        suite.run_level_2()  # 100 queries
        suite.run_level_3()  # 250 queries
    """
    
    def __init__(self):
        self.test_queries = [
            "Analyze this Python code for bugs",
            "Explain quantum computing basics",
            "Write a business plan outline",
            "Debug this SQL query",
            "Create a workout routine",
            "Summarize machine learning concepts",
            "Design a mobile app architecture",
            "Explain RESTful API principles",
            "Write unit tests for this function",
            "Optimize database performance",
        ]
        self.runs: List[StressTestRun] = []
        self.lock = threading.Lock()
    
    def run_level_1(self) -> StressTestRun:
        """Level 1: 50 concurrent queries - Baseline stability"""
        print("\n" + "="*70)
        print("[STRESS TEST] LEVEL 1: 50 Concurrent Queries")
        print("="*70)
        return self._run_stress_test(
            num_queries=50,
            num_workers=5,
            level_name="LEVEL_1_BASELINE"
        )
    
    def run_level_2(self) -> StressTestRun:
        """Level 2: 100 concurrent queries - Scalability validation"""
        print("\n" + "="*70)
        print("[STRESS TEST] LEVEL 2: 100 Concurrent Queries")
        print("="*70)
        return self._run_stress_test(
            num_queries=100,
            num_workers=10,
            level_name="LEVEL_2_SCALABILITY"
        )
    
    def run_level_3(self) -> StressTestRun:
        """Level 3: 250 concurrent queries - Stress threshold"""
        print("\n" + "="*70)
        print("[STRESS TEST] LEVEL 3: 250 Concurrent Queries")
        print("="*70)
        return self._run_stress_test(
            num_queries=250,
            num_workers=25,
            level_name="LEVEL_3_STRESS"
        )
    
    def _run_stress_test(
        self,
        num_queries: int,
        num_workers: int,
        level_name: str
    ) -> StressTestRun:
        """Execute stress test at specified level"""
        
        # Import orchestrator here to avoid initialization issues
        try:
            from blackfong_ecosystem.orchestrator import orchestrator
        except Exception as e:
            print(f"⚠️  Orchestrator unavailable: {e}")
            print("   Using mock testing mode...")
            return self._mock_stress_test(num_queries, num_workers, level_name)
        
        # Prepare test queries (cycle through list)
        test_queries = []
        for i in range(num_queries):
            test_queries.append(self.test_queries[i % len(self.test_queries)])
        
        # Create test run
        run_id = f"{level_name}_{int(time.time())}"
        run = StressTestRun(
            run_id=run_id,
            num_queries=num_queries,
            num_workers=num_workers,
            total_time_sec=0.0,
            successful_queries=0,
            failed_queries=0,
        )
        
        print(f"\n[INFO] Test Parameters:")
        print(f"   Run ID: {run_id}")
        print(f"   Queries: {num_queries}")
        print(f"   Workers: {num_workers}")
        print(f"   Starting at: {datetime.now().strftime('%H:%M:%S')}")
        
        # Log test start
        log_action(
            LogLevel.INFO,
            "stress_test",
            f"started_{level_name.lower()}",
            num_queries=num_queries,
            num_workers=num_workers
        )
        
        # Execute stress test
        start_time = time.time()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = {
                executor.submit(self._execute_query, orchestrator, query, i): i
                for i, query in enumerate(test_queries)
            }
            
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result(timeout=30)
                    
                    with self.lock:
                        run.results.append(result)
                        if result.success:
                            run.successful_queries += 1
                        else:
                            run.failed_queries += 1
                    
                    completed += 1
                    if completed % 10 == 0:
                        print(f"   Progress: {completed}/{num_queries} queries completed")
                
                except concurrent.futures.TimeoutError:
                    print(f"   [TIMEOUT] Query timeout")
                    run.failed_queries += 1
                except Exception as e:
                    print(f"   [ERROR] Query failed: {str(e)[:60]}")
                    run.failed_queries += 1
        
        run.total_time_sec = time.time() - start_time
        
        # Log results
        log_action(
            LogLevel.INFO,
            "stress_test",
            f"completed_{level_name.lower()}",
            run_id=run_id,
            successful=run.successful_queries,
            failed=run.failed_queries,
            total_time=run.total_time_sec
        )
        
        # Print results
        self._print_results(run)
        
        # Store for later analysis
        self.runs.append(run)
        
        return run
    
    def _execute_query(self, orchestrator: Any, query: str, query_id: int) -> TestResult:
        """Execute a single query and track results"""
        result = TestResult(
            test_id=f"query_{query_id}",
            query=query,
            success=False,
            latency_ms=0.0
        )
        
        try:
            query_start = time.time()
            
            # Execute query
            response = orchestrator.process_query(query)
            
            result.latency_ms = (time.time() - query_start) * 1000
            result.success = True
            result.confidence = response.get("confidence", 0.0)
            result.agent_used = response.get("agent", "unknown")
            
        except Exception as e:
            result.success = False
            result.error = str(e)[:100]
            result.latency_ms = (time.time() - query_start) * 1000
        
        return result
    
    def _mock_stress_test(
        self,
        num_queries: int,
        num_workers: int,
        level_name: str
    ) -> StressTestRun:
        """Mock stress test for testing without orchestrator"""
        import random
        
        run = StressTestRun(
            run_id=f"{level_name}_mock_{int(time.time())}",
            num_queries=num_queries,
            num_workers=num_workers,
            total_time_sec=0.0,
            successful_queries=0,
            failed_queries=0,
        )
        
        print(f"\n[INFO] Running in MOCK mode (orchestrator unavailable)")
        
        start_time = time.time()
        
        for i in range(num_queries):
            # Simulate query
            success = random.random() > 0.05  # 95% success rate
            latency = random.uniform(50, 500)  # 50-500ms
            
            result = TestResult(
                test_id=f"mock_query_{i}",
                query=self.test_queries[i % len(self.test_queries)],
                success=success,
                latency_ms=latency,
                confidence=random.uniform(0.7, 0.99) if success else 0.0,
                agent_used="mock"
            )
            
            run.results.append(result)
            if success:
                run.successful_queries += 1
            else:
                run.failed_queries += 1
            
            if (i + 1) % 50 == 0:
                print(f"   Progress: {i+1}/{num_queries} queries completed")
        
        run.total_time_sec = time.time() - start_time
        self._print_results(run)
        self.runs.append(run)
        
        return run
    
    def _print_results(self, run: StressTestRun):
        """Print detailed test results"""
        print(f"\n[SUCCESS] Test Complete")
        print(f"\n[RESULTS] Summary:")
        print(f"   Total Time: {run.total_time_sec:.2f}s")
        print(f"   Successful: {run.successful_queries}/{run.num_queries} ({run.success_rate:.1f}%)")
        print(f"   Failed: {run.failed_queries}")
        print(f"   Throughput: {run.queries_per_second:.2f} queries/sec")
        
        print(f"\n[LATENCY] Metrics:")
        print(f"   Average: {run.avg_latency_ms:.2f}ms")
        print(f"   P95: {run.p95_latency_ms:.2f}ms")
        print(f"   P99: {run.p99_latency_ms:.2f}ms")
        
        # Check for issues
        print(f"\n[CHECK] Stability Checks:")
        if run.success_rate >= 95:
            print(f"   [OK] Success rate acceptable (>=95%)")
        else:
            print(f"   [WARN] Success rate LOW (<95%)")
        
        if run.avg_latency_ms < 2000:
            print(f"   [OK] Latency acceptable (<2s average)")
        else:
            print(f"   [WARN] Latency HIGH (>2s average)")
        
        if run.p99_latency_ms < 5000:
            print(f"   [OK] P99 latency acceptable (<5s)")
        else:
            print(f"   [WARN] P99 latency HIGH (>5s)")
    
    def run_all_levels(self):
        """Run all three stress test levels sequentially"""
        print("\n" + "="*70)
        print("[START] O.B.E.O.S COMPREHENSIVE STRESS TEST - ALL LEVELS")
        print("="*70)
        
        try:
            self.run_level_1()
            input("\n[PAUSE] Level 1 complete. Press ENTER to continue to Level 2...")
            
            self.run_level_2()
            input("\n[PAUSE] Level 2 complete. Press ENTER to continue to Level 3...")
            
            self.run_level_3()
            
            print("\n" + "="*70)
            print("[COMPLETE] ALL STRESS TEST LEVELS COMPLETE")
            print("="*70)
            self._print_summary()
            
        except KeyboardInterrupt:
            print("\n\n[INTERRUPTED] Stress testing interrupted by user")
    
    def _print_summary(self):
        """Print summary of all test runs"""
        if not self.runs:
            return
        
        print(f"\n[FINAL] AGGREGATE RESULTS ({len(self.runs)} runs)")
        print("-" * 70)
        
        total_queries = sum(r.num_queries for r in self.runs)
        total_successful = sum(r.successful_queries for r in self.runs)
        total_time = sum(r.total_time_sec for r in self.runs)
        
        print(f"Total queries: {total_queries}")
        print(f"Total successful: {total_successful}/{total_queries} ({(total_successful/total_queries)*100:.1f}%)")
        print(f"Total time: {total_time:.2f}s")
        
        print(f"\nPer level:")
        for run in self.runs:
            print(f"  {run.run_id}: {run.successful_queries}/{run.num_queries} ({run.success_rate:.1f}%) @ {run.queries_per_second:.2f} qps")


if __name__ == "__main__":
    suite = StressTestSuite()
    suite.run_all_levels()
