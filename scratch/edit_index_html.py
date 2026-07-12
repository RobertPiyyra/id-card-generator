import os

index_path = 'templates/index.html'

with open(index_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update elements dictionary
target_elements = """            // Photo Upload
            uploadOptions: document.querySelectorAll('.upload-option'),
            fileUploadSection: document.getElementById('fileUploadSection'),
            cameraSection: document.getElementById('cameraSection'),
            photoInput: document.getElementById('photo'),
            photoDataInput: document.getElementById('photoData'),
            fileInfo: document.getElementById('fileInfo'),"""

replacement_elements = """            // Photo Upload
            uploadOptions: document.querySelectorAll('.upload-option'),
            fileUploadSection: document.getElementById('fileUploadSection'),
            cameraSection: document.getElementById('cameraSection'),
            photoInput: document.getElementById('photo'),
            photoDataInput: document.getElementById('photoData'),
            fileInfo: document.getElementById('fileInfo'),
            
            // Photo Preview
            photoPreviewContainer: document.getElementById('photoPreviewContainer'),
            formPhotoPreview: document.getElementById('formPhotoPreview'),
            photoPreviewInfo: document.getElementById('photoPreviewInfo'),
            photoPreviewSource: document.getElementById('photoPreviewSource'),
            clearPhotoPreviewBtn: document.getElementById('clearPhotoPreviewBtn'),"""

if target_elements in content:
    content = content.replace(target_elements, replacement_elements)
    print("1. Elements updated successfully")
else:
    print("1. Target elements not found")

# 2. Update photo input change listener
target_change = """                // File validation
                elements.photoInput.addEventListener('change', (e) => {
                    elements.photoDataInput.value = '';
                    Utils.validateFile(e.target.files[0]);
                    FormManager.updateFormState();
                });"""

replacement_change = """                // File validation
                elements.photoInput.addEventListener('change', (e) => {
                    elements.photoDataInput.value = '';
                    const file = e.target.files[0];
                    if (Utils.validateFile(file)) {
                        const reader = new FileReader();
                        reader.onload = (event) => {
                            if (elements.formPhotoPreview && elements.photoPreviewContainer) {
                                elements.formPhotoPreview.src = event.target.result;
                                elements.photoPreviewContainer.style.display = 'flex';
                                if (elements.photoPreviewInfo) elements.photoPreviewInfo.textContent = 'Selected Photo';
                                if (elements.photoPreviewSource) elements.photoPreviewSource.textContent = file.name;
                            }
                        };
                        reader.readAsDataURL(file);
                    } else {
                        if (elements.photoPreviewContainer) {
                            elements.photoPreviewContainer.style.display = 'none';
                        }
                    }
                    FormManager.updateFormState();
                });

                // Clear photo preview button click listener
                if (elements.clearPhotoPreviewBtn) {
                    elements.clearPhotoPreviewBtn.addEventListener('click', () => {
                        elements.photoInput.value = '';
                        elements.photoDataInput.value = '';
                        if (elements.photoPreviewContainer) elements.photoPreviewContainer.style.display = 'none';
                        if (elements.formPhotoPreview) elements.formPhotoPreview.src = '';
                        elements.fileInfo.textContent = I18n.t('supported_formats');
                        elements.fileInfo.classList.remove('error');
                        FormManager.updateFormState();
                    });
                }"""

if target_change in content:
    content = content.replace(target_change, replacement_change)
    print("2. Photo change listener updated successfully")
else:
    # Try normalized spacing
    target_change_norm = target_change.replace('\r\n', '\n').replace('\n', '\r\n')
    if target_change_norm in content:
        content = content.replace(target_change_norm, replacement_change.replace('\n', '\r\n'))
        print("2. Photo change listener (CRLF normalized) updated successfully")
    else:
        print("2. Photo change listener not found")

# 3. Update CameraManager.capture()
target_capture = """                const photoData = canvas.toDataURL('image/jpeg', 0.8);
                elements.photoDataInput.value = photoData;
                elements.photoInput.required = false;
                
                video.style.display = 'none';"""

replacement_capture = """                const photoData = canvas.toDataURL('image/jpeg', 0.8);
                elements.photoDataInput.value = photoData;
                elements.photoInput.required = false;
                
                // Show in the photo preview container too!
                if (elements.formPhotoPreview && elements.photoPreviewContainer) {
                    elements.formPhotoPreview.src = photoData;
                    elements.photoPreviewContainer.style.display = 'flex';
                    if (elements.photoPreviewInfo) elements.photoPreviewInfo.textContent = 'Captured Photo';
                    if (elements.photoPreviewSource) elements.photoPreviewSource.textContent = 'Camera Capture';
                }
                
                video.style.display = 'none';"""

if target_capture in content:
    content = content.replace(target_capture, replacement_capture)
    print("3. CameraManager.capture updated successfully")
else:
    target_capture_norm = target_capture.replace('\r\n', '\n').replace('\n', '\r\n')
    if target_capture_norm in content:
        content = content.replace(target_capture_norm, replacement_capture.replace('\n', '\r\n'))
        print("3. CameraManager.capture (CRLF normalized) updated successfully")
    else:
        print("3. CameraManager.capture not found")

# 4. Update FormManager.clearForm()
target_clear = """            clearForm() {
                elements.idCardForm.reset();
                elements.studentId.value = '';
                elements.customClassInput.style.display = 'none';
                elements.dynamicFieldsContainer.innerHTML = '';
                
                if (AppState.currentUploadOption === 'camera') {
                    CameraManager.stop();
                }"""

replacement_clear = """            clearForm() {
                elements.idCardForm.reset();
                elements.studentId.value = '';
                elements.customClassInput.style.display = 'none';
                elements.dynamicFieldsContainer.innerHTML = '';
                
                // Clear serial fields
                if (document.getElementById('serial_card_id')) document.getElementById('serial_card_id').value = '';
                if (document.getElementById('serial_batch_id')) document.getElementById('serial_batch_id').value = '';
                if (document.getElementById('serial_search_input')) document.getElementById('serial_search_input').value = '';
                if (document.getElementById('serial_search_result')) document.getElementById('serial_search_result').innerHTML = '';
                
                // Clear photo preview
                if (elements.photoPreviewContainer) elements.photoPreviewContainer.style.display = 'none';
                if (elements.formPhotoPreview) elements.formPhotoPreview.src = '';
                
                if (AppState.currentUploadOption === 'camera') {
                    CameraManager.stop();
                }"""

if target_clear in content:
    content = content.replace(target_clear, replacement_clear)
    print("4. FormManager.clearForm updated successfully")
else:
    target_clear_norm = target_clear.replace('\r\n', '\n').replace('\n', '\r\n')
    if target_clear_norm in content:
        content = content.replace(target_clear_norm, replacement_clear.replace('\n', '\r\n'))
        print("4. FormManager.clearForm (CRLF normalized) updated successfully")
    else:
        print("4. FormManager.clearForm not found")

# 5. Update lookupSerial response handlers
target_lookup = """                    // Show photo and info
                    let photoHtml = '';
                    if (data.photo_thumbnail) {
                        photoHtml = '<img src="/' + data.photo_thumbnail + '" style="width:60px;height:75px;object-fit:cover;border-radius:4px;margin-right:12px;"/>';
                    }
                    document.getElementById('serial_search_result').innerHTML =
                        '<div style="display:flex;align-items:center;">' + photoHtml +
                        '<div><strong>' + data.serial_no + '</strong> — ' + (data.status || 'photo_only') +
                        '<br><small style="color:#27ae60;">Form filled. Click Generate to create card.</small></div></div>';
                } else {
                    document.getElementById('serial_search_result').innerHTML = '<span style="color:#e74c3c;">' + (data.error || 'Not found') + '</span>';
                    // Clear hidden fields on not found
                    document.getElementById('serial_card_id').value = '';
                    document.getElementById('serial_batch_id').value = '';
                }"""

replacement_lookup = """                    // Show photo and info
                    let photoHtml = '';
                    if (data.photo_thumbnail) {
                        photoHtml = '<img src="/' + data.photo_thumbnail + '" style="width:60px;height:75px;object-fit:cover;border-radius:4px;margin-right:12px;"/>';
                    }
                    document.getElementById('serial_search_result').innerHTML =
                        '<div style="display:flex;align-items:center;">' + photoHtml +
                        '<div><strong>' + data.serial_no + '</strong> — ' + (data.status || 'photo_only') +
                        '<br><small style="color:#27ae60;">Form filled. Click Generate to create card.</small></div></div>';
                    
                    // Show photo preview inside upload card
                    if (data.photo_path) {
                        const previewImg = document.getElementById('formPhotoPreview');
                        const container = document.getElementById('photoPreviewContainer');
                        const label = document.getElementById('photoPreviewInfo');
                        const source = document.getElementById('photoPreviewSource');
                        if (previewImg && container) {
                            previewImg.src = '/' + data.photo_path;
                            container.style.display = 'flex';
                            if (label) label.textContent = 'Batch Photo';
                            if (source) source.textContent = 'Serial: ' + data.serial_no;
                        }
                    }
                } else {
                    document.getElementById('serial_search_result').innerHTML = '<span style="color:#e74c3c;">' + (data.error || 'Not found') + '</span>';
                    // Clear hidden fields on not found
                    document.getElementById('serial_card_id').value = '';
                    document.getElementById('serial_batch_id').value = '';
                    
                    // Hide preview on error
                    const container = document.getElementById('photoPreviewContainer');
                    if (container) container.style.display = 'none';
                }"""

if target_lookup in content:
    content = content.replace(target_lookup, replacement_lookup)
    print("5. lookupSerial updated successfully")
else:
    target_lookup_norm = target_lookup.replace('\r\n', '\n').replace('\n', '\r\n')
    if target_lookup_norm in content:
        content = content.replace(target_lookup_norm, replacement_lookup.replace('\n', '\r\n'))
        print("5. lookupSerial (CRLF normalized) updated successfully")
    else:
        print("5. lookupSerial not found")

with open(index_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Modification complete!")
