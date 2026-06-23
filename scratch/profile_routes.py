"""
Route Performance Profiler.

Measures response time, database query count, and memory usage for all registered routes.
Run: python scratch/profile_routes.py
"""
import time
import sys
import os
import gc
import tracemalloc
import json
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app


def profile_route(app, route, method='GET', data=None, headers=None):
    """Profile a single route's performance."""
    gc.collect()
    tracemalloc.start()

    with app.test_client() as client:
        # Enable query counting
        from sqlalchemy import event
        from sqlalchemy.engine import Engine
        query_count = [0]
        query_times = []

        @event.listens_for(Engine, "before_cursor_execute")
        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            query_count[0] += 1
            conn.info.setdefault('query_start', []).append(time.time())

        @event.listens_for(Engine, "after_cursor_execute")
        def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            total = time.time() - conn.info['query_start'].pop()
            query_times.append(total)

        # Warm up
        try:
            if method == 'GET':
                client.get(route, headers=headers or {})
            elif method == 'POST':
                client.post(route, data=data, headers=headers or {})
        except Exception:
            pass

        # Actual measurement
        gc.collect()
        snapshot_before = tracemalloc.take_snapshot()
        start_time = time.time()

        try:
            if method == 'GET':
                response = client.get(route, headers=headers or {})
            elif method == 'POST':
                response = client.post(route, data=data, headers=headers or {})
            else:
                response = client.get(route, headers=headers or {})
        except Exception as e:
            tracemalloc.stop()
            return {
                'route': route,
                'method': method,
                'status': 'ERROR',
                'error': str(e),
                'response_time_ms': 0,
                'query_count': 0,
                'total_query_time_ms': 0,
                'memory_delta_kb': 0,
            }

        elapsed = (time.time() - start_time) * 1000  # ms
        snapshot_after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        # Memory delta
        stats = snapshot_after.compare_to(snapshot_before, 'lineno')
        memory_delta = sum(s.size_diff for s in stats if s.size_diff > 0) / 1024  # KB

        total_query_time = sum(query_times) * 1000  # ms

        return {
            'route': route,
            'method': method,
            'status': response.status_code,
            'response_time_ms': round(elapsed, 2),
            'query_count': query_count[0],
            'total_query_time_ms': round(total_query_time, 2),
            'memory_delta_kb': round(memory_delta, 2),
        }


def main():
    print("=" * 100)
    print("ROUTE PERFORMANCE PROFILER")
    print("=" * 100)

    app = create_app()
    rules = list(app.url_map.iter_rules())

    # Filter to meaningful routes (exclude static, HEAD, OPTIONS)
    routes = []
    seen = set()
    for rule in rules:
        if rule.rule in seen:
            continue
        if rule.rule.startswith('/static'):
            continue
        methods = [m for m in rule.methods if m in ('GET', 'POST')]
        if not methods:
            continue
        # Skip routes that need complex auth for now
        routes.append((rule.rule, methods[0]))
        seen.add(rule.rule)

    print(f"\nProfiling {len(routes)} routes...\n")

    results = []
    errors = []

    for i, (route, method) in enumerate(routes):
        result = profile_route(app, route, method=method)
        results.append(result)

        status_icon = '✅' if result.get('status') == 200 else '⚠️' if isinstance(result.get('status'), int) else '❌'
        print(f"[{i+1:3d}/{len(routes)}] {status_icon} {method:4s} {route:60s} "
              f"Time: {result['response_time_ms']:8.2f}ms | "
              f"Queries: {result['query_count']:3d} | "
              f"Mem: {result['memory_delta_kb']:8.2f}KB")

        if result.get('status') == 'ERROR':
            errors.append(result)

    # Sort by response time
    results.sort(key=lambda r: r.get('response_time_ms', 0), reverse=True)

    print("\n" + "=" * 100)
    print("TOP 20 SLOWEST ROUTES (by response time)")
    print("=" * 100)
    print(f"{'#':>3} {'Method':<6} {'Route':<50} {'Time(ms)':>10} {'Queries':>8} {'QTime(ms)':>10} {'Mem(KB)':>10} {'Status':>6}")
    print("-" * 100)
    for i, r in enumerate(results[:20], 1):
        print(f"{i:3d} {r['method']:<6} {r['route'][:50]:<50} {r['response_time_ms']:>10.2f} {r['query_count']:>8} {r['total_query_time_ms']:>10.2f} {r['memory_delta_kb']:>10.2f} {str(r.get('status','')):>6}")

    # Top query-heavy routes
    results_by_queries = sorted(results, key=lambda r: r.get('query_count', 0), reverse=True)
    print("\n" + "=" * 100)
    print("TOP 15 QUERY-HEAVY ROUTES")
    print("=" * 100)
    print(f"{'#':>3} {'Method':<6} {'Route':<50} {'Queries':>8} {'QTime(ms)':>10} {'Resp(ms)':>10}")
    print("-" * 100)
    for i, r in enumerate(results_by_queries[:15], 1):
        print(f"{i:3d} {r['method']:<6} {r['route'][:50]:<50} {r['query_count']:>8} {r['total_query_time_ms']:>10.2f} {r['response_time_ms']:>10.2f}")

    # Top memory routes
    results_by_memory = sorted(results, key=lambda r: r.get('memory_delta_kb', 0), reverse=True)
    print("\n" + "=" * 100)
    print("TOP 15 MEMORY-HEAVY ROUTES")
    print("=" * 100)
    print(f"{'#':>3} {'Method':<6} {'Route':<50} {'Mem(KB)':>10} {'Queries':>8} {'Resp(ms)':>10}")
    print("-" * 100)
    for i, r in enumerate(results_by_memory[:15], 1):
        print(f"{i:3d} {r['method']:<6} {r['route'][:50]:<50} {r['memory_delta_kb']:>10.2f} {r['query_count']:>8} {r['response_time_ms']:>10.2f}")

    # Summary statistics
    valid_results = [r for r in results if r.get('status') != 'ERROR']
    if valid_results:
        avg_time = sum(r['response_time_ms'] for r in valid_results) / len(valid_results)
        avg_queries = sum(r['query_count'] for r in valid_results) / len(valid_results)
        avg_memory = sum(r['memory_delta_kb'] for r in valid_results) / len(valid_results)
        total_queries = sum(r['query_count'] for r in valid_results)

        print("\n" + "=" * 100)
        print("SUMMARY")
        print("=" * 100)
        print(f"  Total routes profiled:    {len(results)}")
        print(f"  Successful:               {len(valid_results)}")
        print(f"  Errors:                   {len(errors)}")
        print(f"  Avg response time:        {avg_time:.2f}ms")
        print(f"  Avg queries per route:    {avg_queries:.1f}")
        print(f"  Avg memory delta:         {avg_memory:.2f}KB")
        print(f"  Total queries:            {total_queries}")

    # Save detailed results
    output_path = os.path.join(os.path.dirname(__file__), 'profile_results.json')
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nDetailed results saved to: {output_path}")

    if errors:
        print(f"\n⚠️  {len(errors)} routes had errors:")
        for e in errors[:10]:
            print(f"  - {e['method']} {e['route']}: {e.get('error', '')[:80]}")


if __name__ == '__main__':
    main()
