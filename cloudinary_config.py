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

# Verify configuration is loaded
if not cloud_name or not api_key or not api_secret:
    logger.error("🚨 CRITICAL: Cloudinary credentials not found in environment!")
    logger.error(f"  CLOUDINARY_CLOUD_NAME: {'✓' if cloud_name else '❌ MISSING'}")
    logger.error(f"  CLOUDINARY_API_KEY: {'✓' if api_key else '❌ MISSING'}")
    logger.error(f"  CLOUDINARY_API_SECRET: {'✓' if api_secret else '❌ MISSING'}")
    logger.error("Set these in .env or Railway environment variables!")


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
        # Generate unique public ID (without folder - folder is set separately)
        import uuid
        public_id = uuid.uuid4().hex
        
        # Prepare upload options
        upload_options = {
            'folder': folder,
            'resource_type': resource_type,
            'overwrite': False,
            'quality': 'auto',
            'fetch_format': 'auto',
        }
        
        # If format specified, add it
        if format:
            upload_options['format'] = format
        
        # Upload to Cloudinary
        result = cloudinary.uploader.upload(
            file_bytes,
            public_id=public_id,
            **upload_options
        )
        
        logger.info(f"✅ Uploaded to Cloudinary: {result['url']}")
        
        # Return URL (string) for convenience
        return result['url']
    
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
