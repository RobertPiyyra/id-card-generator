import os

index_path = 'templates/index.html'

with open(index_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Let's search for "window.lookupSerial = async function()"
# and find the closing brace, then replace the whole block dynamically.

lookup_start_idx = content.find("window.lookupSerial = async function()")
if lookup_start_idx != -1:
    # Let's find the closing tag for window.lookupSerial block
    # We can search for the end of lookupSerial, which is right before "};" and "{% endif %}"
    lookup_end_search = "};\n       {% endif %}"
    lookup_end_idx = content.find(lookup_end_search, lookup_start_idx)
    if lookup_end_idx == -1:
        # try CRLF variant
        lookup_end_search = "};\r\n       {% endif %}"
        lookup_end_idx = content.find(lookup_end_search, lookup_start_idx)
        
    if lookup_end_idx != -1:
        # Let's extract the lookupSerial code
        # We can construct the new lookupSerial function
        new_lookup_serial = """window.lookupSerial = async function() {
           const serial = document.getElementById('serial_search_input').value.trim();
           if (!serial) {
               document.getElementById('serial_search_result').innerHTML = '<span style="color:#e74c3c;">Please enter a serial number</span>';
               return;
           }
           document.getElementById('serial_search_result').innerHTML = '<i class="fas fa-spinner fa-spin"></i> Searching...';
           try {
               const resp = await fetch('/admin/serial_batches/api/serial_lookup/' + encodeURIComponent(serial));
               const data = await resp.json();
               if (resp.ok) {
                   // Populate form fields
                   if (data.name) document.querySelector('#idCardForm input[name="name"]').value = data.name;
                   if (data.father_name) document.querySelector('#idCardForm input[name="father_name"]').value = data.father_name;
                   if (data.class_name) {
                       const classSelect = document.querySelector('#idCardForm select[name="class_name"]');
                       if (classSelect) classSelect.value = data.class_name;
                   }
                   if (data.dob) document.querySelector('#idCardForm input[name="dob"]').value = data.dob;
                   if (data.address) document.querySelector('#idCardForm textarea[name="address"]').value = data.address;
                   if (data.phone) document.querySelector('#idCardForm input[name="phone"]').value = data.phone;
                   // Store batch/card ID for generation
                   document.getElementById('serial_card_id').value = data.id;
                   document.getElementById('serial_batch_id').value = data.batch_id;
                   // Show photo and info
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
               }
           } catch (err) {
               document.getElementById('serial_search_result').innerHTML = '<span style="color:#e74c3c;">Search error: ' + err.message + '</span>';
           }
       }"""
        
        # Replace the function
        old_block = content[lookup_start_idx:lookup_end_idx]
        content = content.replace(old_block, new_lookup_serial)
        print("Success: lookupSerial updated successfully!")
        
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(content)
    else:
        print("Error: Could not find lookupSerial block end.")
else:
    print("Error: Could not find lookupSerial function start.")
