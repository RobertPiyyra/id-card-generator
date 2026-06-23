"""Benchmark parallel vs sequential rendering."""
import time, os, sys, gc
sys.path.insert(0, os.getcwd())

from app import create_app
from app.services.parallel_render import bulk_render_students, get_optimal_workers
from types import SimpleNamespace

app = create_app()

with app.app_context():
    from models import Template
    template = Template.query.first()
    if not template:
        print("No template found"); sys.exit(1)

    print(f"Template: {template.filename} (ID: {template.id})")
    print(f"CPU cores: {os.cpu_count()}")
    print()

    # Create test student data
    def make_student_data(i):
        return {
            'name': f'Student {i}',
            'father_name': f'Father {i}',
            'class_name': '5A',
            'dob': '2000-01-01',
            'phone': f'+123456789{i}',
            'address': f'Address {i}',
            'photo_url': None,
            'photo_filename': None,
            'custom_data': {},
            'school_name': 'Test School',
            '_template_fields': [],
            '_prepared_photo_cache': {},
        }

    # Test different worker counts
    for num_cards in [10, 25, 50]:
        student_data = [make_student_data(i) for i in range(num_cards)]

        # Sequential (1 worker)
        gc.collect()
        start = time.time()
        seq_results = bulk_render_students(
            app, template, student_data, max_workers=1
        )
        seq_time = (time.time() - start) * 1000
        seq_success = sum(1 for r in seq_results if r['success'])

        # Parallel (optimal workers)
        optimal = get_optimal_workers(num_cards)
        gc.collect()
        start = time.time()
        par_results = bulk_render_students(
            app, template, student_data, max_workers=optimal
        )
        par_time = (time.time() - start) * 1000
        par_success = sum(1 for r in par_results if r['success'])

        speedup = seq_time / par_time if par_time > 0 else 0

        print(f"--- {num_cards} cards ---")
        print(f"  Sequential (1 worker):  {seq_time:>8.1f}ms total, {seq_time/num_cards:>8.1f}ms/card, {seq_success} ok")
        print(f"  Parallel ({optimal} workers): {par_time:>8.1f}ms total, {par_time/num_cards:>8.1f}ms/card, {par_success} ok")
        print(f"  Speedup: {speedup:.2f}x")
        print(f"  Throughput: {num_cards/(par_time/1000):.1f} cards/sec = {num_cards*60/(par_time/1000):.0f} cards/min")
        print()
