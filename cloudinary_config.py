"""
Cloudinary Configuration & Upload Helper
Handles all image uploads to Cloudinary
"""

import os
import logging
import cloudinary
import cloudinary.uploader

logger = logging.getLogger(__name__)

# ================== Cloudinary Config ==================
# Get credentials from environment variables (set by .env or Railway)
cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
api_key = os.getenv("CLOUDINARY_API_KEY")
api_secret = os.getenv("CLOUDINARY_API_SECRET")

# Configure Cloudinary
cloudinary.config(
    cloud_name=cloud_name,
    api_key=api_key,
    api_secret=api_secret
)

CLOUDINARY_CONFIGURED = bool(cloud_name and api_key and api_secret)

# Avoid noisy "critical" logs during local development when filesystem storage is desired.
storage_backend = (os.getenv("STORAGE_BACKEND") or "auto").strip().lower()
if not CLOUDINARY_CONFIGURED:
    if storage_backend in {"cloudinary", "cloud"}:
        logger.error("🚨 Cloudinary credentials are missing but STORAGE_BACKEND=cloudinary was requested.")
        logger.error(f"  CLOUDINARY_CLOUD_NAME: {'✓' if cloud_name else '❌ MISSING'}")
        logger.error(f"  CLOUDINARY_API_KEY: {'✓' if api_key else '❌ MISSING'}")
        logger.error(f"  CLOUDINARY_API_SECRET: {'✓' if api_secret else '❌ MISSING'}")
    else:
        logger.info("Cloudinary credentials not found; app will use local filesystem storage unless configured otherwise.")


def upload_image(file_bytes, folder='generated', resource_type='image', format=None):
    """
    Upload image bytes to Cloudinary.
    
    Args:
        file_bytes (bytes): Image data as bytes
        folder (str): Cloudinary folder name (e.g., 'generated', 'photos', 'previews')
        resource_type (str): 'image', 'raw' (for PDFs), 'video', etc.
        format (str): Optional format override (e.g., 'jpg', 'png', 'pdf')
    
    Returns:
        str: Cloudinary URL of uploaded file
        dict: Full response with 'url' key
    
    Raises:
        Exception: If upload fails
    """
    try:
        if not CLOUDINARY_CONFIGURED:
            raise RuntimeError(
                "Cloudinary is not configured. Set CLOUDINARY_CLOUD_NAME/CLOUDINARY_API_KEY/"
                "CLOUDINARY_API_SECRET or set STORAGE_BACKEND=local."
            )
        # Generate unique public ID (without folder - folder is set separately)
        import uuid
        public_id = uuid.uuid4().hex
        
        # Prepare upload options.
        # NOTE: `quality` / `fetch_format` are image/video delivery-oriented options and
        # should not be sent for `raw` uploads (PDF/doc files), which can produce
        # unpredictable results on some accounts.
        upload_options = {
            'folder': folder,
            'resource_type': resource_type,
            'type': 'upload',
            'overwrite': False,
        }
        if resource_type == 'raw':
            # Keep raw/PDF delivery public where account settings allow it.
            upload_options['access_mode'] = 'public'
        if resource_type != 'raw':
            upload_options['quality'] = 'auto'
            upload_options['fetch_format'] = 'auto'
        
        # Preserve output format whenever explicitly provided.
        if format:
            upload_options['format'] = str(format).strip().lower()
        
        # Upload to Cloudinary
        # Wrap bytes in BytesIO for cloudinary.uploader.upload()
        import io
        file_obj = io.BytesIO(file_bytes)
        if format:
            file_obj.name = f"upload.{str(format).strip().lower()}"
        elif resource_type == 'raw':
            # Help Cloudinary keep proper raw metadata for PDFs.
            if isinstance(file_bytes, (bytes, bytearray)) and b"%PDF" in file_bytes[:1024]:
                file_obj.name = "upload.pdf"
            else:
                file_obj.name = "upload.bin"
        
        result = cloudinary.uploader.upload(
            file_obj,
            public_id=public_id,
            **upload_options
        )
        
        upload_url = result.get("secure_url") or result.get("url")
        if upload_url and upload_url.startswith("http://"):
            upload_url = "https://" + upload_url[len("http://"):]

        if not upload_url:
            raise RuntimeError("Cloudinary upload succeeded but no URL was returned.")

        logger.info(f"✅ Uploaded to Cloudinary: {upload_url}")
        
        # Return URL (string) for convenience
        return upload_url
    
    except Exception as e:
        logger.error(f"❌ Cloudinary upload failed: {e}")
        raise


def delete_image(public_id):
    """
    Delete image from Cloudinary (optional cleanup).
    
    Args:
        public_id (str): Cloudinary public ID of file to delete
    
    Returns:
        dict: Cloudinary response
    """
    try:
        result = cloudinary.uploader.destroy(public_id)
        logger.info(f"Deleted from Cloudinary: {public_id}")
        return result
    except Exception as e:
        logger.error(f"Failed to delete from Cloudinary: {e}")
        raise


def get_upload_signature():
    """
    Generate signature for unsigned uploads (optional, for client-side uploads).
    Not used in this app but available for future use.
    """
    try:
        import hashlib
        import time
        timestamp = int(time.time())
        to_sign = f"timestamp={timestamp}{os.getenv('CLOUDINARY_API_SECRET')}"
        signature = hashlib.sha1(to_sign.encode()).hexdigest()
        return {
            'timestamp': timestamp,
            'signature': signature,
            'api_key': os.getenv('CLOUDINARY_API_KEY')
        }
    except Exception as e:
        logger.error(f"Failed to generate upload signature: {e}")
        raise
