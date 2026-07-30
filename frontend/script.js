document.addEventListener('DOMContentLoaded', () => {
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const selectBtn = document.getElementById('select-btn');

    const statusCard = document.getElementById('status-card');
    const statusTitle = document.getElementById('status-title');
    const statusSubtitle = document.getElementById('status-subtitle');
    const loader = document.getElementById('loader');

    const resultsCard = document.getElementById('results-card');
    const extractedTextViewer = document.getElementById('extracted-text-viewer');
    const itemsTableBody = document.querySelector('#items-table tbody');
    const toggleJsonBtn = document.getElementById('toggle-json-btn');
    const jsonViewer = document.getElementById('json-viewer');

    let rawJsonResponse = null;

    // Trigger file input click when selecting button
    selectBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        fileInput.click();
    });

    // Handle drag and drop events
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('dragover');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleFileUpload(files[0]);
        }
    });

    // Handle click on drag and drop card directly
    dropZone.addEventListener('click', () => {
        fileInput.click();
    });

    // Handle standard file selection
    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFileUpload(e.target.files[0]);
        }
    });

    // Upload PDF helper function
    function handleFileUpload(file) {
        if (!file.name.toLowerCase().endsWith('.pdf')) {
            showStatus('Ошибка', 'Пожалуйста, выберите корректный PDF-файл.', false);
            return;
        }

        showStatus('Обработка файла...', `Загружаем и анализируем "${file.name}"`, true);
        resultsCard.classList.add('hidden');

        const formData = new FormData();
        formData.append('file', file);

        // Fetch to local FastAPI /api/upload-pdf endpoint
        fetch('/api/upload-pdf', {
            method: 'POST',
            body: formData
        })
        .then(response => {
            if (!response.ok) {
                throw new Error(`Ошибка сервера (${response.status})`);
            }
            return response.json();
        })
        .then(data => {
            rawJsonResponse = data;
            hideStatus();
            displayResults(data);
        })
        .catch(err => {
            console.error(err);
            showStatus('Произошла ошибка', err.message, false);
        });
    }

    function showStatus(title, subtitle, isSpinning = false) {
        statusCard.classList.remove('hidden');
        statusTitle.textContent = title;
        statusSubtitle.textContent = subtitle;
        if (isSpinning) {
            loader.classList.remove('hidden');
        } else {
            loader.classList.add('hidden');
        }
    }

    function hideStatus() {
        statusCard.classList.add('hidden');
    }

    function displayResults(data) {
        resultsCard.classList.remove('hidden');

        // Show actual or fallback extracted text
        extractedTextViewer.textContent = data.extracted_text || 'Текст пуст.';

        // Populate items table
        itemsTableBody.innerHTML = '';
        if (data.items && data.items.length > 0) {
            data.items.forEach((item, index) => {
                const tr = document.createElement('tr');
                // Support both .qty and .quantity from backend formats
                const quantity = item.qty !== undefined ? item.qty : (item.quantity !== undefined ? item.quantity : 1);
                tr.innerHTML = `
                    <td>${item.id || (index + 1)}</td>
                    <td><strong>${escapeHtml(item.name)}</strong></td>
                    <td class="text-right">${quantity}</td>
                    <td>${escapeHtml(item.unit || 'шт')}</td>
                `;
                itemsTableBody.appendChild(tr);
            });
        } else {
            itemsTableBody.innerHTML = '<tr><td colspan="4" class="text-center">Оборудование не распознано.</td></tr>';
        }

        // Format and render raw JSON payload
        jsonViewer.textContent = JSON.stringify(data, null, 2);
    }

    // Toggle raw JSON display
    toggleJsonBtn.addEventListener('click', () => {
        if (jsonViewer.classList.contains('hidden')) {
            jsonViewer.classList.remove('hidden');
            toggleJsonBtn.textContent = 'Скрыть исходный JSON';
        } else {
            jsonViewer.classList.add('hidden');
            toggleJsonBtn.textContent = 'Показать исходный JSON';
        }
    });

    // Helper function to prevent basic XSS
    function escapeHtml(str) {
        if (!str) return '';
        return str
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }
});
