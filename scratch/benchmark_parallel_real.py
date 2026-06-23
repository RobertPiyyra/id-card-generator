"""Realistic parallel rendering benchmark with proper student data."""
import time, os, sys, gc, tracemalloc
sys.path.insert(0, os.getcwd())

from app import create_app
from app.services.parallel_render import bulk_render_students, get_optimal_workers
from types import SimpleNamespace

app = create_app()

with app.app_context():
    from models import Template, Student, TemplateField
    from app.legacy_app import get_template_path, get_template_settings, get_card_size
    from app.services.render_service import render_student_card_side

    template = Template.query.first()
    if not template:
        print("No template found"); sys.exit(1)

    print(f"Template: {template.filename} (ID: {template.id})")
    print(f"Card size: {get_card_size(template.id)}")
    print(f"CPU cores: {os.cpu_count()}")
    print()

    # Pre-load template
    template_path = get_template_path(template.id)
    card_width, card_height = get_card_size(template.id)
    font_settings, photo_settings, qr_settings, _ = get_template_settings(template.id, side="front")
    from app.legacy_app import _load_template_image_for_render
    _load_template_image_for_render(template_path, card_width, card_height, render_scale=1.0)

    # Create realistic student-like objects
    def make_student(i):
        return SimpleNamespace(
            name=f'Student Number {i}',
            father_name=f'Father {i}',
            class_name='5A',
            dob='2000-01-01',
            phone=f'+123456789{i}',
            address=f'House {i} Street {i}',
            photo_url=None,
            photo_filename=None,
            custom_data={},
            school_name='Test School',
            _template_fields=TemplateField.query.filter_by(template_id=template.id).order_by(TemplateField.display_order.asc()).all(),
            _prepared_photo_cache={},
        )

    # First: benchmark single card render (the actual bottleneck)
    print("=== SINGLE CARD RENDER ===")
    student = make_student(0)
    gc.collect()
    start = time.time()
    result = render_student_card_side(
        template_obj=template, student_like=student, side='front', render_scale=1.0
    )
    single_time = (time.time() - start) * 1000
    print(f"  Time: {single_time:.1f}ms")
    print()

    # Benchmark parallel rendering with different worker counts
    for num_cards in [10, 25]:
        students = [make_student(i) for i in range(num_cards)]

        # Sequential
        gc.collect()
        start = time.time()
        seq_success = 0
        for s in students:
            try:
                r = render_student_card_side(template_obj=template, student_like=s, side='front', render_scale=1.0)
                if r: seq_success += 1
            except Exception: pass
        seq_time = (time.time() - start) * 1000

        # Parallel with different worker counts
        for workers in [2, 4, min(os.cpu_count() or 4, 8)]:
            gc.collect()
            start = time.time()
            par_results = bulk_render_students(
                app, template, [{'name': s.name, 'father_name': s.father_name,
                                 'class_name': s.class_name, 'dob': s.dob,
                                 'phone': s.phone, 'address': s.address,
                                 'photo_url': None, 'photo_filename': None,
                                 'custom_data': {}, 'school_name': 'Test School',
                                 '_template_fields': [], '_prepared_photo_cache': {}}
                                for s in students],
                max_workers=workers
            )
            par_time = (time.time() - start) * 1000
            par_success = sum(1 for r in par_results if r['success'])
            speedup = seq_time / par_time if par_time > 0 else 0

            print(f"--- {num_cards} cards, {workers} workers ---")
            print(f"  Sequential: {seq_time:>8.1f}ms ({seq_success} ok)")
            print(f"  Parallel:   {par_time:>8.1f}ms ({par_success} ok)")
            print(f"  Speedup:    {speedup:.2f}x")
            print()

    print("=== SUMMARY ===")
    print(f"Single card render: {single_time:.1f}ms")
    print(f"CPU cores: {os.cpu_count()}")
    print(f"Optimal workers for 10 cards:  {get_optimal_workers(10)}")
    print(f"Optimal workers for 25 cards:  {get_optimal_workers(25)}")
    print(f"Optimal workers for 50 cards:  {get_optimal_workers(50)}")
    print(f"Optimal workers for 100 cards: {get_optimal_workers(100)}")
