document.addEventListener('DOMContentLoaded', () => {
    // Tab switching elements
    const tabExcelBtn = document.getElementById('tab-excel-btn');
    const tabPdfBtn = document.getElementById('tab-pdf-btn');
    const excelGeneratorTab = document.getElementById('excel-generator-tab');
    const pdfParserTab = document.getElementById('pdf-parser-tab');

    // Tab event listeners
    tabExcelBtn.addEventListener('click', () => {
        tabExcelBtn.classList.add('active');
        tabPdfBtn.classList.remove('active');
        excelGeneratorTab.classList.remove('hidden');
        pdfParserTab.classList.add('hidden');
    });

    tabPdfBtn.addEventListener('click', () => {
        tabPdfBtn.classList.add('active');
        tabExcelBtn.classList.remove('active');
        pdfParserTab.classList.remove('hidden');
        excelGeneratorTab.classList.add('hidden');
    });

    // ==========================================
    // WORKFLOW 1: Excel Generator (Dual upload)
    // ==========================================
    const specDropZone = document.getElementById('spec-drop-zone');
    const specFileInput = document.getElementById('spec-file-input');
    const specSelectBtn = document.getElementById('spec-select-btn');
    const specFileStatus = document.getElementById('spec-file-status');

    const priceDropZone = document.getElementById('price-drop-zone');
    const priceFileInput = document.getElementById('price-file-input');
    const priceSelectBtn = document.getElementById('price-select-btn');
    const priceFileStatus = document.getElementById('price-file-status');

    const generateKpBtn = document.getElementById('generate-kp-btn');
    const kpResultsContainer = document.getElementById('kp-results-container');
    const kpTablesWrapper = document.getElementById('kp-tables-wrapper');
    const kpGrandTotalValue = document.getElementById('kp-grand-total-value');
    const downloadKpExcelBtn = document.getElementById('download-kp-excel-btn');
    const kpFilesInfo = document.getElementById('kp-files-info');

    const generalLoaderCard = document.getElementById('general-loader-card');

    let specFile = null;
    let priceFile = null;
    let activeKpData = null; // Store active calculated KP JSON payload

    // Helper to check if both files are selected and enable button
    function updateGenerateBtnState() {
        if (specFile && priceFile) {
            generateKpBtn.disabled = false;
        } else {
            generateKpBtn.disabled = true;
        }
    }

    // Set up file selection events for Specification
    specSelectBtn.addEventListener('click', (e) => { e.stopPropagation(); specFileInput.click(); });
    specDropZone.addEventListener('click', () => specFileInput.click());
    specFileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            setSpecFile(e.target.files[0]);
        }
    });
    setupDragDrop(specDropZone, setSpecFile);

    function setSpecFile(file) {
        if (!file.name.toLowerCase().endsWith('.xlsx') && !file.name.toLowerCase().endsWith('.xls')) {
            alert('Спецификация должна быть файлом Excel (.xlsx)');
            return;
        }
        specFile = file;
        specFileStatus.textContent = `Выбран файл: ${file.name}`;
        specDropZone.classList.add('active-file');
        updateGenerateBtnState();
    }

    // Set up file selection events for Price list
    priceSelectBtn.addEventListener('click', (e) => { e.stopPropagation(); priceFileInput.click(); });
    priceDropZone.addEventListener('click', () => priceFileInput.click());
    priceFileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            setPriceFile(e.target.files[0]);
        }
    });
    setupDragDrop(priceDropZone, setPriceFile);

    function setPriceFile(file) {
        if (!file.name.toLowerCase().endsWith('.xlsx') && !file.name.toLowerCase().endsWith('.xls')) {
            alert('Прайс-лист должен быть файлом Excel (.xlsx)');
            return;
        }
        priceFile = file;
        priceFileStatus.textContent = `Выбран файл: ${file.name}`;
        priceDropZone.classList.add('active-file');
        updateGenerateBtnState();
    }

    // Generic drag and drop setup
    function setupDragDrop(zone, fileCallback) {
        zone.addEventListener('dragover', (e) => {
            e.preventDefault();
            zone.classList.add('dragover');
        });
        zone.addEventListener('dragleave', () => {
            zone.classList.remove('dragover');
        });
        zone.addEventListener('drop', (e) => {
            e.preventDefault();
            zone.classList.remove('dragover');
            if (e.dataTransfer.files.length > 0) {
                fileCallback(e.dataTransfer.files[0]);
            }
        });
    }

    // Generate KP Click Event
    generateKpBtn.addEventListener('click', () => {
        if (!specFile || !priceFile) return;

        // Show general loader
        generalLoaderCard.classList.remove('hidden');
        kpResultsContainer.classList.add('hidden');

        const formData = new FormData();
        formData.append('specification', specFile);
        formData.append('pricelist', priceFile);

        fetch('/api/generate-kp', {
            method: 'POST',
            body: formData
        })
        .then(res => {
            if (!res.ok) throw new Error(`Ошибка сервера при расчете КП (${res.status})`);
            return res.json();
        })
        .then(data => {
            generalLoaderCard.classList.add('hidden');
            if (data.status === 'success' && data.kp) {
                activeKpData = data.kp;
                kpFilesInfo.textContent = `Рассчитано на основе файлов: Спецификация ("${data.specification_file}") + Прайс-лист CHINT ("${data.pricelist_file}")`;
                renderKpTables(data.kp);
                kpResultsContainer.classList.remove('hidden');
            } else {
                alert('Не удалось рассчитать КП: некорректный ответ сервера.');
            }
        })
        .catch(err => {
            generalLoaderCard.classList.add('hidden');
            console.error(err);
            alert(`Ошибка: ${err.message}`);
        });
    });

    // Render KP Tables
    function renderKpTables(kp) {
        kpTablesWrapper.innerHTML = '';

        if (!kp.boards || kp.boards.length === 0) {
            kpTablesWrapper.innerHTML = '<p class="text-center" style="padding: 20px;">Нет позиций для отображения.</p>';
            kpGrandTotalValue.textContent = '0.00 руб.';
            return;
        }

        kp.boards.forEach((board) => {
            const section = document.createElement('div');
            section.className = 'kp-board-section';

            // Board Header
            const header = document.createElement('div');
            header.className = 'board-title-row';
            header.textContent = `Щит / Раздел: ${board.board_name}`;
            section.appendChild(header);

            // Table
            const table = document.createElement('table');
            table.className = 'items-table';

            // Table Header
            table.innerHTML = `
                <thead>
                    <tr>
                        <th style="width: 5%;">№</th>
                        <th style="width: 15%;">Артикул</th>
                        <th style="width: 45%;">Наименование позиции</th>
                        <th style="width: 10%; text-align: right;">Кол-во</th>
                        <th style="width: 10%; text-align: center;">Ед. изм.</th>
                        <th style="width: 15%; text-align: right;">Цена с НДС</th>
                        <th style="width: 15%; text-align: right;">Сумма с НДС</th>
                    </tr>
                </thead>
                <tbody></tbody>
            `;

            const tbody = table.querySelector('tbody');

            // Insert rows
            board.items.forEach((item, idx) => {
                const tr = document.createElement('tr');
                const priceFormatted = item.price_found
                    ? `${formatMoney(item.price)} руб.`
                    : '<span class="price-not-found">не найден в прайсе</span>';

                tr.innerHTML = `
                    <td style="text-align: center; color: var(--text-muted);">${idx + 1}</td>
                    <td><strong>${escapeHtml(item.article || '-')}</strong></td>
                    <td>${escapeHtml(item.name)}</td>
                    <td style="text-align: right; font-weight: 600;">${item.qty}</td>
                    <td style="text-align: center;">${escapeHtml(item.unit || 'шт')}</td>
                    <td style="text-align: right;">${priceFormatted}</td>
                    <td style="text-align: right; font-weight: 700; color: var(--primary-color);">${formatMoney(item.total)} руб.</td>
                `;
                tbody.appendChild(tr);
            });

            // Section Subtotal row
            const subtotalTr = document.createElement('tr');
            subtotalTr.className = 'subtotal-row';
            subtotalTr.innerHTML = `
                <td colspan="6" style="text-align: right;">Итого по разделу "${escapeHtml(board.board_name)}":</td>
                <td style="text-align: right; color: var(--primary-color); font-size: 1rem;">${formatMoney(board.subtotal)} руб.</td>
            `;
            tbody.appendChild(subtotalTr);

            section.appendChild(table);
            kpTablesWrapper.appendChild(section);
        });

        // Set Grand total
        kpGrandTotalValue.textContent = `${formatMoney(kp.grand_total)} руб.`;
    }

    // Export to Excel Button
    downloadKpExcelBtn.addEventListener('click', () => {
        if (!activeKpData) return;

        fetch('/api/export-kp', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(activeKpData)
        })
        .then(res => {
            if (!res.ok) throw new Error('Ошибка при генерации файла коммерческого предложения.');
            return res.blob();
        })
        .then(blob => {
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'Коммерческое_предложение_CHINT.xlsx';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
        })
        .catch(err => {
            console.error(err);
            alert(`Ошибка при экспорте КП: ${err.message}`);
        });
    });

    // Helper money formatter
    function formatMoney(val) {
        return Number(val).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }


    // ==========================================
    // WORKFLOW 2: PDF Parser (OCR / Backwards compatible)
    // ==========================================
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

    selectBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        fileInput.click();
    });

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

    dropZone.addEventListener('click', () => {
        fileInput.click();
    });

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFileUpload(e.target.files[0]);
        }
    });

    function handleFileUpload(file) {
        if (!file.name.toLowerCase().endsWith('.pdf')) {
            showStatus('Ошибка', 'Пожалуйста, выберите корректный PDF-файл.', false);
            return;
        }

        showStatus('Обработка файла...', `Загружаем и анализируем "${file.name}"`, true);
        resultsCard.classList.add('hidden');

        const formData = new FormData();
        formData.append('file', file);

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
        extractedTextViewer.textContent = data.extracted_text || 'Текст пуст.';

        itemsTableBody.innerHTML = '';
        if (data.items && data.items.length > 0) {
            data.items.forEach((item, index) => {
                const tr = document.createElement('tr');
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

        jsonViewer.textContent = JSON.stringify(data, null, 2);
    }

    toggleJsonBtn.addEventListener('click', () => {
        if (jsonViewer.classList.contains('hidden')) {
            jsonViewer.classList.remove('hidden');
            toggleJsonBtn.textContent = 'Скрыть исходный JSON';
        } else {
            jsonViewer.classList.add('hidden');
            toggleJsonBtn.textContent = 'Показать исходный JSON';
        }
    });

    // Escaping helper
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
