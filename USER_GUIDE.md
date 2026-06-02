# ID Card Generator - User Guide

## 1. System Overview
The ID Card Generator is a robust, cloud-native Flask application designed to generate print-ready ID cards (both single and double-sided) with features like multi-language support (RTL), dynamic typography scaling, and high-quality CorelDRAW-compatible PDF exports.

## 2. User Roles & Access
- **Super Admin**: Has unrestricted access to all schools, templates, and bulk generation features. Can delete the entire database or execute disaster recovery snapshots. Log in via Environment Variable credentials.
- **School Admin**: Restricted to their assigned school. Can view students, manage templates, and export PDFs only for their school.
- **Student**: Limited to viewing and editing their own ID card details. (Maximum 3 generations per session to prevent abuse/spam).

## 3. Template Management
Navigate to the **Admin Dashboard** to upload templates.
- **Formats**: Upload PDFs, JPGs, or PNGs. (PDFs are strictly recommended for the highest quality exports).
- **Double-Sided Printing**: Check the "Double Sided" box and upload a back template if needed.
- **Form Fields**: Dynamic fields can be added via the visual editor settings and will automatically appear on the student registration form.
- **Typography & Layout**: 
  - Set custom TrueType (`.ttf`) fonts. 
  - The system supports auto-resizing text to fit bounded boxes. Address fields will automatically wrap up to 2 lines if they exceed width constraints.

## 4. Multi-Language & RTL Support
The platform fully supports Right-to-Left (RTL) languages like Arabic and Urdu, as well as Hindi.
- Text is automatically reshaped (glyph joining) and BiDi algorithms are applied to ensure perfect rendering in the generated PDF.
- **Fallback Safety**: If a selected font does not contain required Unicode Arabic/Urdu glyphs, the system falls back to a Presentation-Forms-safe font (like `arabtype.ttf`) to prevent tofu (□) characters in CorelDRAW.

## 5. Photo & QR Code Configurations
- **Photo Cropping**: Photos are automatically center-cropped or face-cropped using MediaPipe (if enabled). You can add rounded borders directly from the settings.
- **Cloudinary Storage**: Photos and generated assets are securely hosted on Cloudinary, guaranteeing zero data-loss during ephemeral server restarts.
- **QR Codes / Barcodes**: Embed dynamic student data, URLs, or custom text into QR codes or Code128 barcodes.

## 6. Bulk Generation
Upload an Excel (`.xlsx`) or CSV file to generate hundreds of cards at once.
- The process runs asynchronously via an RQ/Redis queue (with fallback to local threads).
- A live progress tracker will appear in the dashboard.
- **Import Mappings**: You can map Excel column headers to custom template fields dynamically if the source data formats vary.

## 7. CorelDRAW Export
When downloading compiled PDFs, you have two modes:
- **Print Mode (600 DPI)**: A high-resolution, fully flattened PDF ready for direct printing. Text and images are merged to guarantee absolute visual fidelity.
- **Editable Mode**: Retains vector text where possible, specifically sanitized to avoid common CorelDRAW import errors. (It strips nested transparency layers, OCGs, and ExtGState tags which Corel historically rejects).

## 8. Disaster Recovery
- **Snapshots**: The system automatically captures snapshots of your template configurations before major updates.
- **Restore**: You can roll back a template's design to any previous point in time via the Admin panel.