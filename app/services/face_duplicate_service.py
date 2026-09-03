"""
Face Duplicate Detection Service.

Provides indexing and similarity search across student photos within a school tenant
to detect reused or duplicate photos across admissions, classes, and batches.
"""

import logging
from typing import Optional, List, Dict, Any
from PIL import Image
import numpy as np

from models import db, FaceEmbedding, Student, SerialCard
from app.services.face_service import compute_face_embedding, compute_face_phash, calculate_face_similarity

logger = logging.getLogger(__name__)


def save_face_embedding_for_student(student: Student, pil_img: Optional[Image.Image] = None) -> Optional[FaceEmbedding]:
    """Computes and stores or updates face embedding for a Student record."""
    if not student or not student.school_name:
        return None

    try:
        vec, phash = None, None
        if pil_img is not None:
            vec, phash = compute_face_embedding(pil_img)

        if not vec:
            return None

        # Check existing
        rec = FaceEmbedding.query.filter_by(student_id=student.id).first()
        if not rec:
            rec = FaceEmbedding(
                student_id=student.id,
                school_name=student.school_name,
                embedding_vector=vec,
                face_phash=phash
            )
            db.session.add(rec)
        else:
            rec.embedding_vector = vec
            rec.face_phash = phash
            rec.school_name = student.school_name

        db.session.commit()
        return rec
    except Exception as e:
        db.session.rollback()
        logger.warning("save_face_embedding_for_student failed for student %s: %s", student.id, e)
        return None


def save_face_embedding_for_serial_card(card: SerialCard, pil_img: Optional[Image.Image] = None) -> Optional[FaceEmbedding]:
    """Computes and stores or updates face embedding for a SerialCard record."""
    if not card or not card.batch or not card.batch.school_name:
        return None

    try:
        vec, phash = None, None
        if pil_img is not None:
            vec, phash = compute_face_embedding(pil_img)

        if not vec:
            return None

        school_name = card.batch.school_name
        rec = FaceEmbedding.query.filter_by(serial_card_id=card.id).first()
        if not rec:
            rec = FaceEmbedding(
                serial_card_id=card.id,
                school_name=school_name,
                embedding_vector=vec,
                face_phash=phash
            )
            db.session.add(rec)
        else:
            rec.embedding_vector = vec
            rec.face_phash = phash
            rec.school_name = school_name

        db.session.commit()
        return rec
    except Exception as e:
        db.session.rollback()
        logger.warning("save_face_embedding_for_serial_card failed for card %s: %s", card.id, e)
        return None


def find_duplicate_faces(
    school_name: str,
    pil_img: Optional[Image.Image] = None,
    embedding_vec: Optional[list] = None,
    phash: Optional[str] = None,
    threshold: float = 0.85,
    exclude_student_id: Optional[int] = None,
    exclude_serial_card_id: Optional[int] = None,
    max_results: int = 5
) -> List[Dict[str, Any]]:
    """
    Searches for duplicate/similar faces in the specified school tenant.
    Returns matches sorted by similarity score descending.
    """
    if not school_name:
        return []

    try:
        if embedding_vec is None and pil_img is not None:
            embedding_vec, phash = compute_face_embedding(pil_img)

        if not embedding_vec:
            return []

        query = FaceEmbedding.query.filter_by(school_name=school_name)
        if exclude_student_id:
            query = query.filter((FaceEmbedding.student_id != exclude_student_id) | (FaceEmbedding.student_id.is_(None)))
        if exclude_serial_card_id:
            query = query.filter((FaceEmbedding.serial_card_id != exclude_serial_card_id) | (FaceEmbedding.serial_card_id.is_(None)))

        records = query.all()
        if not records:
            return []

        query_arr = np.array(embedding_vec, dtype=np.float32)
        norm_q = np.linalg.norm(query_arr)
        if norm_q < 1e-9:
            return []

        matches = []
        for r in records:
            if not r.embedding_vector:
                continue

            # Check fast pHash match
            exact_phash = bool(phash and r.face_phash and phash == r.face_phash)

            target_arr = np.array(r.embedding_vector, dtype=np.float32)
            norm_t = np.linalg.norm(target_arr)
            if norm_t < 1e-9:
                continue

            sim = float(np.dot(query_arr, target_arr) / (norm_q * norm_t))
            sim = max(0.0, min(1.0, sim))

            # If exact phash, ensure minimum 0.95 score
            if exact_phash:
                sim = max(sim, 0.98)

            if sim >= threshold:
                item_info = {
                    "embedding_id": r.id,
                    "similarity": round(sim, 4),
                    "is_exact_match": exact_phash or sim >= 0.95,
                    "matched_type": "student" if r.student_id else "serial_card",
                    "student_id": r.student_id,
                    "serial_card_id": r.serial_card_id,
                }

                if r.student:
                    item_info.update({
                        "name": r.student.name,
                        "father_name": r.student.father_name,
                        "class_name": r.student.class_name,
                        "photo_url": r.student.photo_url or r.student.photo_filename
                    })
                elif r.serial_card:
                    item_info.update({
                        "name": r.serial_card.name or r.serial_card.serial_no,
                        "serial_no": r.serial_card.serial_no,
                        "class_name": r.serial_card.class_name,
                        "photo_url": r.serial_card.photo_path or r.serial_card.photo_thumbnail
                    })

                matches.append(item_info)

        # Sort matches by similarity score descending
        matches.sort(key=lambda m: m["similarity"], reverse=True)
        return matches[:max_results]

    except Exception as e:
        logger.error("find_duplicate_faces failed for school %s: %s", school_name, e)
        return []
