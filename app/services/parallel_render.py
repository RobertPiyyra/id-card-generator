"""
Parallel Card Generation Service.

Uses ThreadPoolExecutor to render multiple cards concurrently.
This significantly speeds up bulk generation by utilizing multiple CPU cores.

Usage:
    from app.services.parallel_render import render_cards_parallel
    
    results = render_cards_parallel(
        template_obj=template,
        students=student_list,
        side='front',
        max_workers=4
    )
"""
import os
import io
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image
from app.services.render_service import render_student_card_side
from app.legacy_app import get_template_path, get_template_settings, get_card_size, load_font_dynamic
logger = logging.getLogger(__name__)

# Thread-local storage for template data (loaded once per thread)
_thread_local = threading.local()


def _get_thread_template_cache():
    """Get or create thread-local template cache."""
    if not hasattr(_thread_local, 'cache'):
        _thread_local.cache = {}
    return _thread_local.cache


def _render_single_card(args):
    """
    Render a single card. Designed to be called from a thread pool.

    Args:
        args: dict with keys:
            - template_obj: Template object
            - student_like: Student-like object with name, photo_url, etc.
            - side: 'front' or 'back'
            - render_scale: float
            - include_photo: bool
            - include_qr: bool
            - include_barcode: bool
            - include_text: bool
            - student_id: optional int
            - school_name: optional str

    Returns:
        dict with keys:
            - success: bool
            - image: PIL Image or None
            - error: str or None
            - render_time_ms: float
    """
    start = time.time()
    try:
        result = render_student_card_side(
            template_obj=args['template_obj'],
            student_like=args['student_like'],
            side=args.get('side', 'front'),
            student_id=args.get('student_id'),
            school_name=args.get('school_name'),
            render_scale=args.get('render_scale', 1.0),
            include_photo=args.get('include_photo', True),
            include_qr=args.get('include_qr', True),
            include_barcode=args.get('include_barcode', True),
            include_text=args.get('include_text', True),
        )
        elapsed = (time.time() - start) * 1000
        return {
            'success': result is not None,
            'image': result,
            'error': None,
            'render_time_ms': elapsed,
        }
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        logger.error(f"Parallel render error: {e}")
        return {
            'success': False,
            'image': None,
            'error': str(e),
            'render_time_ms': elapsed,
        }


def render_cards_parallel(template_obj, students, side='front', render_scale=1.0,
                          include_photo=True, include_qr=True, include_barcode=True,
                          include_text=True, max_workers=None, progress_callback=None):
    """
    Render multiple student cards in parallel using ThreadPoolExecutor.

    Args:
        template_obj: Template object (shared across all renders)
        students: List of student-like objects
        side: 'front' or 'back'
        render_scale: float
        include_photo: bool
        include_qr: bool
        include_barcode: bool
        include_text: bool
        max_workers: Number of parallel workers (default: CPU count)
        progress_callback: Optional callable(completed, total) for progress updates

    Returns:
        list of dicts with keys: success, image, error, render_time_ms
    """
    if max_workers is None:
        max_workers = min(os.cpu_count() or 4, 8)

    total = len(students)
    logger.info(f"Starting parallel render: {total} cards with {max_workers} workers")

    # Pre-load template data once (shared across threads)
    template_id = template_obj.id
    template_path = get_template_path(template_id, side=side)
    font_settings, photo_settings, qr_settings, _ = get_template_settings(template_id, side=side)
    card_width, card_height = get_card_size(template_id)

    # Pre-load fonts into cache (thread-safe after this)
    if font_settings:
        font_regular = font_settings.get('font_regular', 'arial.ttf')
        font_bold = font_settings.get('font_bold', 'arialbd.ttf')
        for size in [20, 24, 28, 32, 36, 40]:
            try:
                load_font_dynamic(font_regular, "X", None, size)
                load_font_dynamic(font_bold, "X", None, size)
            except Exception:
                pass

    # Build args for each student
    render_args = []
    for student in students:
        render_args.append({
            'template_obj': template_obj,
            'student_like': student,
            'side': side,
            'render_scale': render_scale,
            'include_photo': include_photo,
            'include_qr': include_qr,
            'include_barcode': include_barcode,
            'include_text': include_text,
            'student_id': getattr(student, 'id', None),
            'school_name': getattr(student, 'school_name', None),
        })

    # Execute in parallel
    results = []
    completed = 0
    total_start = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(_render_single_card, args): idx
            for idx, args in enumerate(render_args)
        }

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                result = future.result()
            except Exception as e:
                result = {
                    'success': False,
                    'image': None,
                    'error': str(e),
                    'render_time_ms': 0,
                }
            results.append((idx, result))
            completed += 1

            if progress_callback:
                try:
                    progress_callback(completed, total)
                except Exception:
                    pass

    # Sort by original order
    results.sort(key=lambda x: x[0])
    results = [r[1] for r in results]

    total_elapsed = (time.time() - total_start) * 1000
    success_count = sum(1 for r in results if r['success'])
    avg_time = sum(r['render_time_ms'] for r in results) / len(results) if results else 0

    logger.info(
        f"Parallel render complete: {success_count}/{total} successful, "
        f"total: {total_elapsed:.0f}ms, avg: {avg_time:.0f}ms/card, "
        f"throughput: {total/(total_elapsed/1000):.1f} cards/sec"
    )

    return results


def render_cards_parallel_to_bytes(template_obj, students, side='front',
                                    render_scale=1.0, output_format='JPEG',
                                    quality=95, max_workers=None):
    """
    Render cards in parallel and convert to bytes (for download/upload).

    Returns list of dicts with: success, bytes_data, error, render_time_ms
    """
    render_results = render_cards_parallel(
        template_obj=template_obj,
        students=students,
        side=side,
        render_scale=render_scale,
        max_workers=max_workers,
    )

    byte_results = []
    for result in render_results:
        if result['success'] and result['image']:
            try:
                buf = io.BytesIO()
                result['image'].save(buf, format=output_format, quality=quality)
                buf.seek(0)
                byte_results.append({
                    'success': True,
                    'bytes_data': buf.getvalue(),
                    'error': None,
                    'render_time_ms': result['render_time_ms'],
                })
            except Exception as e:
                byte_results.append({
                    'success': False,
                    'bytes_data': None,
                    'error': str(e),
                    'render_time_ms': result['render_time_ms'],
                })
        else:
            byte_results.append({
                'success': False,
                'bytes_data': None,
                'error': result.get('error', 'Render failed'),
                'render_time_ms': result['render_time_ms'],
            })

    return byte_results


def get_optimal_workers(card_count):
    """Determine optimal number of workers based on card count and CPU cores."""
    cpu_count = os.cpu_count() or 4
    # Don't use more workers than cards
    # Don't use more than CPU cores (PIL is CPU-bound)
    # Use at least 2 workers for bulk operations
    optimal = min(card_count, cpu_count, 8)
    return max(optimal, 2)
def bulk_render_students(app, template_obj, student_data_list, side='front',
                         render_scale=1.0, max_workers=None,
                         progress_callback=None):
    """
    High-level bulk rendering: prepares student objects, renders in parallel,
    and returns results ready for database saving.

    Args:
        app: Flask app instance (for app context in threads)
        template_obj: Template object
        student_data_list: List of dicts with student data keys:
            name, father_name, class_name, dob, address, phone,
            photo_url, photo_filename, custom_data, school_name,
            _template_fields, _prepared_photo_cache
        side: 'front' or 'back'
        render_scale: float
        max_workers: int (default: auto)
        progress_callback: callable(completed, total)

    Returns:
        list of dicts: {success, front_image, back_image, error, render_time_ms, student_data}
    """
    from types import SimpleNamespace

    if max_workers is None:
        max_workers = get_optimal_workers(len(student_data_list))

    card_width, card_height = get_card_size(template_obj.id)
    is_double_sided = getattr(template_obj, "is_double_sided", False)
    def _render_one(student_data):
        """Render a single student card (runs in thread pool)."""
        start = time.time()
        try:
            with app.app_context():
                # Build student-like object
                side_render_student = SimpleNamespace(
                    name=student_data.get('name', ''),
                    father_name=student_data.get('father_name', ''),
                    class_name=student_data.get('class_name', ''),
                    dob=student_data.get('dob', ''),
                    address=student_data.get('address', ''),
                    phone=student_data.get('phone', ''),
                    photo_url=student_data.get('photo_url'),
                    photo_filename=student_data.get('photo_filename'),
                    custom_data=student_data.get('custom_data', {}),
                    school_name=student_data.get('school_name', ''),
                    _template_fields=student_data.get('_template_fields', []),
                    _prepared_photo_cache=student_data.get('_prepared_photo_cache', {}),
                )

                front_image = render_student_card_side(
                    template_obj=template_obj,
                    student_like=side_render_student,
                    side='front',
                    student_id=None,
                    school_name=student_data.get('school_name', ''),
                    render_scale=render_scale,
                )

                back_image = None
                if is_double_sided:
                    back_image = render_student_card_side(
                        template_obj=template_obj,
                        student_like=side_render_student,
                        side='back',
                        student_id=None,
                        school_name=student_data.get('school_name', ''),
                        render_scale=render_scale,
                    )
                    if back_image is None:
                        from app.legacy_app import load_static_back_template_image
                        back_image = load_static_back_template_image(template_obj, card_width, card_height)

            elapsed = (time.time() - start) * 1000
            return {
                'success': front_image is not None,
                'front_image': front_image,
                'back_image': back_image,
                'error': None,
                'render_time_ms': elapsed,
                'student_data': student_data,
            }
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            return {
                'success': False,
                'front_image': None,
                'back_image': None,
                'error': str(e),
                'render_time_ms': elapsed,
                'student_data': student_data,
            }
    results = []
    total = len(student_data_list)
    completed = 0

    logger.info(f"Parallel bulk render: {total} cards, {max_workers} workers")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_render_one, data): idx
            for idx, data in enumerate(student_data_list)
        }

        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
            except Exception as e:
                result = {
                    'success': False, 'front_image': None, 'back_image': None,
                    'error': str(e), 'render_time_ms': 0,
                    'student_data': student_data_list[idx],
                }
            results.append((idx, result))
            completed += 1
            if progress_callback:
                try:
                    progress_callback(completed, total)
                except Exception:
                    pass

    # Sort by original order
    results.sort(key=lambda x: x[0])
    results = [r[1] for r in results]

    total_time = sum(r['render_time_ms'] for r in results)
    success_count = sum(1 for r in results if r['success'])
    logger.info(
        f"Parallel bulk render complete: {success_count}/{total} successful, "
        f"total render time: {total_time:.0f}ms, "
        f"avg: {total_time/total:.0f}ms/card" if total > 0 else "no cards"
    )

    return results
