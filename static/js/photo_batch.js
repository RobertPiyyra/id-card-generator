/**
 * Photo Batch — Frontend JavaScript
 * Manages batch creation, photo upload, serial search, and card generation.
 */
const PhotoBatch = {
    currentBatchId: null,

    csrfToken() {
        // Get CSRF token from meta tag or hidden input
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) return meta.getAttribute('content');
        const input = document.querySelector('input[name="csrf_token"]');
        if (input) return input.value;
        return '';
    },

    init() {
        // Auto-load batches when Photo Batch tab is clicked
        document.querySelector('[onclick*="photoBatch"]').addEventListener('click', () => {
            this.loadBatches();
        });

        // Bind upload zone events
        this.bindUploadZone();
    },

    onClassSelectChange() {
        const select = document.getElementById('pb_class_name');
        const customInput = document.getElementById('pb_class_name_custom');
        if (select && customInput) {
            if (select.value === '__custom__') {
                customInput.style.display = 'inline-block';
            } else {
                customInput.style.display = 'none';
                customInput.value = '';
            }
        }
    },

    // ================== Batch Operations ==================

    async loadBatches() {
        try {
            const resp = await fetch('/admin/serial_batches/');
            if (!resp.ok) throw new Error('Failed to load batches');
            const data = await resp.json();
            this.renderBatchList(data.batches);
        } catch (err) {
            document.getElementById('pb_batch_list').innerHTML =
                '<p style="color:#e74c3c;">Error loading batches: ' + err.message + '</p>';
        }
    },

    renderBatchList(batches) {
        const container = document.getElementById('pb_batch_list');
        if (!batches || batches.length === 0) {
            container.innerHTML = '<p style="color:#888;">No batches yet. Create one above to get started.</p>';
            return;
        }
        let html = '';
        batches.forEach(b => {
            const statusClass = 'status-' + (b.status || 'uploading');
            const classText = b.class_name ? ` — Class: ${b.class_name}` : '';
            html += `
                <div class="batch-row" onclick="PhotoBatch.viewBatch(${b.id})">
                    <div>
                        <strong>Batch #${b.id}</strong> — ${b.school_name}${classText}
                        <span class="batch-status ${statusClass}" style="margin-left:8px;">${b.status}</span>
                    </div>
                    <div style="display:flex; gap:12px; align-items:center;">
                        <span style="color:#888; font-size:13px;">${b.card_count} cards</span>
                        <span style="color:#888; font-size:13px;">${b.prefix}</span>
                        <button onclick="event.stopPropagation(); PhotoBatch.confirmDeleteBatch(${b.id}, '${b.school_name}')" class="btn btn-sm btn-danger" style="font-size:11px; padding:2px 8px;">
                            <i class="fas fa-trash-alt"></i>
                        </button>
                        <i class="fas fa-chevron-right" style="color:#ccc;"></i>
                    </div>
                </div>
            `;
        });
        container.innerHTML = html;
    },

    async createBatch() {
        const templateId = document.getElementById('pb_template_id').value;
        const prefix = document.getElementById('pb_prefix').value || 'SCH-';
        
        let className = document.getElementById('pb_class_name').value;
        if (className === '__custom__') {
            className = document.getElementById('pb_class_name_custom').value.trim();
        }

        if (!templateId) {
            alert('Please select a template.');
            return;
        }

        try {
            const resp = await fetch('/admin/serial_batches/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.csrfToken()
                },
                body: JSON.stringify({
                    template_id: parseInt(templateId),
                    prefix: prefix,
                    class_name: className
                })
            });
            const data = await resp.json();
            if (data.success) {
                // Clear fields
                document.getElementById('pb_class_name').value = '';
                document.getElementById('pb_class_name_custom').value = '';
                document.getElementById('pb_class_name_custom').style.display = 'none';
                this.loadBatches();
                this.viewBatch(data.batch_id);
            } else {
                alert('Error: ' + (data.error || 'Failed to create batch'));
            }
        } catch (err) {
            alert('Error creating batch: ' + err.message);
        }
    },

    async viewBatch(batchId) {
        this.currentBatchId = batchId;
        try {
            const resp = await fetch(`/admin/serial_batches/${batchId}`);
            if (!resp.ok) throw new Error('Failed to load batch');
            const data = await resp.json();

            document.getElementById('pb_batch_detail').style.display = 'block';
            const classText = data.batch.class_name ? ` (Class: ${data.batch.class_name})` : '';
            document.getElementById('pb_detail_title').textContent =
                `Batch #${data.batch.id} — ${data.batch.school_name}${classText} (${data.batch.prefix})`;
            this.renderCardGrid(data.cards);
        } catch (err) {
            alert('Error loading batch: ' + err.message);
        }
    },


    closeBatch() {
        document.getElementById('pb_batch_detail').style.display = 'none';
        this.currentBatchId = null;
    },

    renderCardGrid(cards) {
        const container = document.getElementById('pb_card_grid');
        if (!cards || cards.length === 0) {
            container.innerHTML = '<p style="color:#888;">No cards yet. Upload photos to generate serial numbers.</p>';
            return;
        }
        let html = '';
        cards.forEach(card => {
            const thumbUrl = card.photo_thumbnail ? '/' + card.photo_thumbnail : '';
            const statusColor = card.status === 'photo_only' ? '#888' : '#27ae60';
            html += `
                <div class="card-thumb">
                    <div style="position:relative;">
                        ${thumbUrl ? `<img src="${thumbUrl}" alt="${card.serial_no}"/>` : '<div style="height:150px;background:#f0f0f0;display:flex;align-items:center;justify-content:center;color:#888;">No Photo</div>'}
                    </div>
                    <div class="serial-badge">${card.serial_no}</div>
                    <div class="card-actions">
                        <span style="font-size:11px;color:${statusColor};font-weight:600;">${card.status}</span>
                        ${card.name ? '<i class="fas fa-check-circle" style="color:#27ae60;" title="Details filled"></i>' : ''}
                    </div>
                    <div style="padding:4px 8px;">
                        <button onclick="PhotoBatch.generateCard(${card.id})" class="btn btn-sm btn-primary" style="width:100%;font-size:11px;" ${!card.name ? 'disabled' : ''}>
                            <i class="fas fa-file-pdf"></i> Generate
                        </button>
                    </div>
                </div>
            `;
        });
        container.innerHTML = html;
    },

    // ================== Photo Upload ==================

    bindUploadZone() {
        const zone = document.getElementById('pb_upload_zone');
        const input = document.getElementById('pb_photo_input');
        if (!zone || !input) return;

        zone.addEventListener('click', () => input.click());
        zone.addEventListener('dragover', (e) => {
            e.preventDefault();
            zone.classList.add('drag-over');
        });
        zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
        zone.addEventListener('drop', (e) => {
            e.preventDefault();
            zone.classList.remove('drag-over');
            if (e.dataTransfer.files.length > 0) this.uploadFiles(e.dataTransfer.files);
        });
        input.addEventListener('change', (e) => {
            if (e.target.files.length > 0) this.uploadFiles(e.target.files);
        });
    },

    async uploadFiles(files) {
        if (!this.currentBatchId) {
            alert('Please select a batch first.');
            return;
        }
        const formData = new FormData();
        for (let f of files) formData.append('photos', f);
        // Include CSRF token for upload
        const csrf = this.csrfToken();
        if (csrf) formData.append('csrf_token', csrf);

        const statusEl = document.getElementById('pb_upload_status');
        statusEl.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Uploading...';

        try {
            const resp = await fetch(`/admin/serial_batches/${this.currentBatchId}/upload`, {
                method: 'POST', body: formData
            });
            const data = await resp.json();
            if (data.success) {
                statusEl.innerHTML = `<i class="fas fa-check" style="color:#27ae60;"></i> Uploaded ${data.uploaded} photo(s)`;
                // Refresh batch view
                this.viewBatch(this.currentBatchId);
                this.loadBatches();
            } else {
                statusEl.innerHTML = '<span style="color:#e74c3c;">Error: ' + (data.error || 'Upload failed') + '</span>';
            }
        } catch (err) {
            statusEl.innerHTML = '<span style="color:#e74c3c;">Upload error: ' + err.message + '</span>';
        }
    },

    // ================== Serial Search ==================

    async searchSerial() {
        const serial = document.getElementById('pb_serial_search').value.trim();
        if (!serial) return;

        try {
            const resp = await fetch(`/admin/serial_batches/api/serial_lookup/${encodeURIComponent(serial)}`);
            const data = await resp.json();
            if (resp.ok) {
                this.showCardDetail(data);
            } else {
                alert('Serial number not found: ' + serial);
            }
        } catch (err) {
            alert('Search error: ' + err.message);
        }
    },

    showCardDetail(card) {
        // Show card info in a popup/modal for editing
        const html = `
            <div id="pb_card_modal" style="position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:1000;display:flex;align-items:center;justify-content:center;">
                <div style="background:white;border-radius:12px;padding:24px;max-width:500px;width:90%;max-height:80vh;overflow-y:auto;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
                        <h3 style="margin:0;">${card.serial_no}</h3>
                        <button onclick="document.getElementById('pb_card_modal').remove()" style="background:none;border:none;font-size:20px;cursor:pointer;">&times;</button>
                    </div>
                    ${card.photo_thumbnail ? `<img src="/${card.photo_thumbnail}" style="width:120px;height:150px;object-fit:cover;border-radius:8px;margin-bottom:16px;"/>` : ''}
                    <div style="margin-bottom:12px;">
                        <label style="font-weight:600;display:block;margin-bottom:4px;">Name</label>
                        <input type="text" id="pb_edit_name" value="${card.name || ''}" class="form-control" style="width:100%;">
                    </div>
                    <div style="margin-bottom:12px;">
                        <label style="font-weight:600;display:block;margin-bottom:4px;">Father's Name</label>
                        <input type="text" id="pb_edit_father" value="${card.father_name || ''}" class="form-control" style="width:100%;">
                    </div>
                    <div style="margin-bottom:12px;">
                        <label style="font-weight:600;display:block;margin-bottom:4px;">Class</label>
                        <input type="text" id="pb_edit_class" value="${card.class_name || ''}" class="form-control" style="width:100%;">
                    </div>
                    <div style="margin-bottom:12px;">
                        <label style="font-weight:600;display:block;margin-bottom:4px;">Date of Birth</label>
                        <input type="text" id="pb_edit_dob" value="${card.dob || ''}" class="form-control" style="width:100%;">
                    </div>
                    <div style="margin-bottom:12px;">
                        <label style="font-weight:600;display:block;margin-bottom:4px;">Address</label>
                        <textarea id="pb_edit_address" class="form-control" style="width:100%;">${card.address || ''}</textarea>
                    </div>
                    <div style="margin-bottom:16px;">
                        <label style="font-weight:600;display:block;margin-bottom:4px;">Phone</label>
                        <input type="text" id="pb_edit_phone" value="${card.phone || ''}" class="form-control" style="width:100%;">
                    </div>
                    <div style="display:flex;gap:8px;">
                        <button onclick="PhotoBatch.saveCard(${card.id})" class="btn btn-primary" style="flex:1;">
                            <i class="fas fa-save"></i> Save Details
                        </button>
                        <button onclick="PhotoBatch.generateCard(${card.id})" class="btn btn-success" style="flex:1;">
                            <i class="fas fa-file-pdf"></i> Generate Card
                        </button>
                    </div>
                </div>
            </div>
        `;
        document.body.insertAdjacentHTML('beforeend', html);
    },

    async saveCard(cardId) {
        const data = {
            name: document.getElementById('pb_edit_name').value,
            father_name: document.getElementById('pb_edit_father').value,
            class_name: document.getElementById('pb_edit_class').value,
            dob: document.getElementById('pb_edit_dob').value,
            address: document.getElementById('pb_edit_address').value,
            phone: document.getElementById('pb_edit_phone').value,
        };

        try {
            const resp = await fetch(`/admin/serial_batches/${this.currentBatchId}/cards/${cardId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': this.csrfToken()
                },
                body: JSON.stringify(data)
            });
            const result = await resp.json();
            if (result.success) {
                document.getElementById('pb_card_modal').remove();
                this.viewBatch(this.currentBatchId);
                this.loadBatches();
            } else {
                alert('Save failed: ' + (result.error || 'Unknown error'));
            }
        } catch (err) {
            alert('Save error: ' + err.message);
        }
    },

    async generateCard(cardId) {
        if (!this.currentBatchId) return;
        try {
            const resp = await fetch(`/admin/serial_batches/${this.currentBatchId}/generate/${cardId}`, {
                method: 'POST'
            });
            if (resp.ok) {
                const blob = await resp.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `card_${cardId}.pdf`;
                a.click();
                URL.revokeObjectURL(url);
            } else {
                const data = await resp.json();
                alert('Generation failed: ' + (data.error || 'Unknown error'));
            }
        } catch (err) {
            alert('Generation error: ' + err.message);
        }
    },

    async deleteBatch() {
        if (!this.currentBatchId) return;
        if (!confirm('Are you sure you want to delete this batch and all its photos? This cannot be undone.')) return;
        try {
            const resp = await fetch(`/admin/serial_batches/${this.currentBatchId}`, {
                method: 'DELETE',
                headers: {'X-CSRFToken': this.csrfToken()}
            });
            const data = await resp.json();
            if (data.success) {
                this.closeBatch();
                this.loadBatches();
                alert('Batch deleted successfully.');
            } else {
                alert('Delete failed: ' + (data.error || 'Unknown error'));
            }
        } catch (err) {
            alert('Delete error: ' + err.message);
        }
    },

    confirmDeleteBatch(batchId, schoolName) {
        if (!confirm(`Delete batch #${batchId} (${schoolName})?\n\nThis will permanently remove all photos and cards in this batch. This cannot be undone.`)) return;
        this._doDeleteBatch(batchId);
    },

    deleteBatch() {
        if (!this.currentBatchId) return;
        if (!confirm('Are you sure you want to delete this batch and all its photos? This cannot be undone.')) return;
        this._doDeleteBatch(this.currentBatchId);
    },

    async _doDeleteBatch(batchId) {
        try {
            const resp = await fetch(`/admin/serial_batches/${batchId}`, {
                method: 'DELETE',
                headers: {'X-CSRFToken': this.csrfToken()}
            });
            const data = await resp.json();
            if (data.success) {
                alert('Batch deleted successfully.');
                if (this.currentBatchId === batchId) this.closeBatch();
                this.loadBatches();
            } else {
                alert('Delete failed: ' + (data.error || 'Unknown error'));
            }
        } catch (err) {
            alert('Delete error: ' + err.message);
        }
    },

    async downloadAll() {
        if (!this.currentBatchId) return;
        try {
            const resp = await fetch(`/admin/serial_batches/${this.currentBatchId}/download_all`);
            if (resp.ok) {
                const blob = await resp.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `batch_${this.currentBatchId}_cards.pdf`;
                a.click();
                URL.revokeObjectURL(url);
            } else {
                const data = await resp.json();
                alert('Download failed: ' + (data.error || 'No cards ready'));
            }
        } catch (err) {
            alert('Download error: ' + err.message);
        }
    }
};

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => PhotoBatch.init());
