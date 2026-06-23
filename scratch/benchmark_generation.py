"""
Card Generation Performance Benchmark.
Run: python scratch/benchmark_generation.py
"""
import time, os, sys, gc, tracemalloc, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    print("=" * 80)
    print("CARD GENERATION PERFORMANCE BENCHMARK")
    print("=" * 80)

    from app import create_app
    print("\nCreating app...")
    app = create_app()
    print("App ready.\n")

    from app.services.render_service import render_student_card_side
    from app.legacy_app import get_template_path, load_template_smart, get_card_size, get_template_settings
    from models import Template, Student

    with app.app_context():
        template = Template.query.first()
        if not template:
            print("ERROR: No template found in database")
            return

        print(f"Template: {template.filename} (ID: {template.id})")
        print(f"School: {template.school_name}")
        print(f"Card size: {get_card_size(template.id)}")
        print()

        # 1. Template loading
        print("1. Template image loading...", end=' ', flush=True)
        gc.collect()
        start = time.time()
        path = get_template_path(template.id)
        if path:
            img = load_template_smart(path)
            t_load = (time.time() - start) * 1000
            print(f"{t_load:.1f}ms")
        else:
            t_load = 0
            print("SKIPPED (no template path)")

        # 2. Settings loading
        print("2. Template settings loading...", end=' ', flush=True)
        gc.collect()
        start = time.time()
        font_s, photo_s, qr_s, _ = get_template_settings(template.id, side='front')
        t_settings = (time.time() - start) * 1000
        print(f"{t_settings:.1f}ms")

        # 3. Single card render
        print("3. Single card render (front)...", end=' ', flush=True)
        student = Student(
            id=99999, name='Test Student', father_name='Test Father',
            class_name='5A', dob='2000-01-01', phone='+1234567890',
            school_name='Test School', template_id=template.id,
        )
        gc.collect()
        tracemalloc.start()
        start = time.time()
        try:
            result = render_student_card_side(
                template_obj=template,
                student_like=student,
                side='front',
                render_scale=1.0
            )
            t_render = (time.time() - start) * 1000
            snap = tracemalloc.take_snapshot()
            tracemalloc.stop()
            mem_stat = snap.statistics('lineno')
            peak_mem = sum(s.size for s in mem_stat[:10]) / 1024
            result_size = len(result.tobytes()) if result and hasattr(result, 'tobytes') else (result.size if result else 0)
            print(f"{t_render:.1f}ms | Memory: {peak_mem:.1f}KB | Output: {result_size}")
        except Exception as e:
            t_render = (time.time() - start) * 1000
            tracemalloc.stop()
            print(f"ERROR after {t_render:.1f}ms: {e}")
            result_size = 0

        # 4. Bulk render (10 cards)
        print(f"4. Bulk render 10 cards...", end=' ', flush=True)
        gc.collect()
        start = time.time()
        for i in range(10):
            s = Student(id=100000+i, name=f'Student {i}', school_name='Test School', template_id=template.id)
            try:
                render_student_card_side(template_obj=template, student_like=s, side='front', render_scale=1.0)
            except Exception:
                pass
        t_bulk10 = (time.time() - start) * 1000
        print(f"{t_bulk10:.1f}ms total, {t_bulk10/10:.1f}ms/card")

        # 5. Bulk render (50 cards)
        print(f"5. Bulk render 50 cards...", end=' ', flush=True)
        gc.collect()
        start = time.time()
        for i in range(50):
            s = Student(id=100000+i, name=f'Student {i}', school_name='Test School', template_id=template.id)
            try:
                render_student_card_side(template_obj=template, student_like=s, side='front', render_scale=1.0)
            except Exception:
                pass
        t_bulk50 = (time.time() - start) * 1000
        print(f"{t_bulk50:.1f}ms total, {t_bulk50/50:.1f}ms/card")

        # 6. PDF export mode
        print("6. PDF export mode parsing...", end=' ', flush=True)
        gc.collect()
        start = time.time()
        from app.services.corel_export_service import parse_pdf_export_mode, _render_profile
        mode = parse_pdf_export_mode('print')
        profile = _render_profile(mode)
        t_pdf = (time.time() - start) * 1000
        print(f"{t_pdf:.2f}ms (mode={mode}, dpi={profile.get('dpi', 'N/A')})")

    # Network analysis
    print("\n" + "=" * 80)
    print("NETWORK SPEED ANALYSIS")
    print("=" * 80)

    # Estimate card file sizes
    card_sizes = {
        'JPEG preview (300 DPI)': 50 * 1024,      # ~50KB
        'PNG high quality': 200 * 1024,            # ~200KB
        'PDF print (600 DPI)': 150 * 1024,         # ~150KB
        'PDF editable': 300 * 1024,                # ~300KB
    }

    per_card_ms = t_render if t_render > 0 else 100  # fallback

    print(f"\nServer-side generation time: {per_card_ms:.1f}ms per card")
    print(f"Throughput: {1000/per_card_ms:.1f} cards/sec = {60000/per_card_ms:.0f} cards/min\n")

    print(f"{'Network':<20} {'Speed':<12} {'Card Size':<18} {'Download':<12} {'Total/Card':<14} {'Cards/Min':<12}")
    print("-" * 90)

    networks = [
        ('56K Modem', 0.056),
        ('DSL', 5),
        ('4G Mobile', 20),
        ('WiFi', 50),
        ('WiFi AC', 100),
        ('LAN', 1000),
    ]

    for net_name, mbps in networks:
        for size_name, size_bytes in card_sizes.items():
            # Download time = file_size / bandwidth + overhead
            bw_bytes_per_sec = mbps * 125000  # Mbps to bytes/sec
            download_sec = (size_bytes / bw_bytes_per_sec) * 1.1  # 10% TCP overhead
            download_ms = download_sec * 1000
            total_ms = per_card_ms + download_ms
            cards_per_min = 60000 / total_ms if total_ms > 0 else 0
            print(f"{net_name:<20} {mbps:<12} {size_name:<18} {download_ms:>8.1f}ms{'':<4} {total_ms:>8.1f}ms{'':<4} {cards_per_min:>8.1f}")
        print()

    # Bottleneck analysis
    print("=" * 80)
    print("BOTTLENECK ANALYSIS")
    print("=" * 80)
    print(f"""
For a typical deployment:

SERVER SIDE:
  - Template loading:     {t_load:>8.1f}ms (one-time, cached after first load)
  - Settings loading:     {t_settings:>8.1f}ms (one-time, cached)
  - Card rendering:       {per_card_ms:>8.1f}ms per card (CPU-bound, PIL operations)
  - PDF export parsing:   {t_pdf:>8.2f}ms (negligible)

NETWORK SIDE (typical card = 150KB PDF):
  - 56K Modem:            ~24,000ms download (BOTTLENECK)
  - DSL 5Mbps:            ~260ms download
  - 4G Mobile 20Mbps:     ~65ms download
  - WiFi 50Mbps:          ~26ms download
  - LAN 1Gbps:            ~1.3ms download

KEY INSIGHT:
  - For slow networks (< 5Mbps), NETWORK is the bottleneck
  - For fast networks (> 20Mbps), SERVER RENDERING is the bottleneck
  - Template loading is a one-time cost (cached in memory)
  - Card rendering is CPU-bound and scales linearly
  - Bulk generation: {60000/per_card_ms:.0f} cards/minute server-side

RECOMMENDATIONS:
  1. Use Redis/template cache to avoid repeated disk I/O
  2. For bulk jobs, use async generation (Celery workers)
  3. Compress output images (WebP instead of JPEG)
  4. Use CDN for serving generated cards
  5. For >100 cards, use background jobs with progress tracking
""")


if __name__ == '__main__':
    main()
