document.addEventListener('DOMContentLoaded', () => {
    // Select elements
    const pdfDropZone = document.getElementById('pdf-drop-zone');
    const pdfFileInput = document.getElementById('pdf-file-input');
    const pdfSelectBtn = document.getElementById('pdf-select-btn');
    const pdfFileStatus = document.getElementById('pdf-file-status');

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
    const extractedTextViewer = document.getElementById('extracted-text-viewer');

    const generalLoaderCard = document.getElementById('general-loader-card');

    let pdfFile = null;
    let priceFiles = []; // Supports multiple files selection
    let activePricelistName = null; // Store computed active pricelist name on backend
    let activeKpData = null; // Store computed commercial proposal payload

    // Query active pricelist on DOM load
    fetch('/api/active-pricelist')
        .then(res => res.json())
        .then(data => {
            if (data.status === 'success' && data.filename) {
                activePricelistName = data.filename;
                priceFileStatus.innerHTML = `<strong>Используется прайс:</strong> ${escapeHtml(activePricelistName)}<br><span style="font-size: 0.85em; color: var(--text-muted);">(Загрузите новый файл для замены)</span>`;
                priceDropZone.classList.add('active-file');
                updateActionState();
            }
        })
        .catch(err => console.error('Error fetching active pricelist:', err));

    // PDF selection events
    pdfSelectBtn.addEventListener('click', (e) => { e.stopPropagation(); pdfFileInput.click(); });
    pdfDropZone.addEventListener('click', () => pdfFileInput.click());
    pdfFileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            setPdfFile(e.target.files[0]);
        }
    });
    setupDragDrop(pdfDropZone, setPdfFile, '.pdf');

    function setPdfFile(file) {
        if (!file.name.toLowerCase().endsWith('.pdf')) {
            alert('Пожалуйста, выберите файл в формате PDF (.pdf)');
            return;
        }
        pdfFile = file;
        pdfFileStatus.innerHTML = `<strong>Выбран проект:</strong> ${escapeHtml(file.name)} (${formatSize(file.size)})`;
        pdfDropZone.classList.add('active-file');
        updateActionState();
    }

    // Price lists selection events
    priceSelectBtn.addEventListener('click', (e) => { e.stopPropagation(); priceFileInput.click(); });
    priceDropZone.addEventListener('click', () => priceFileInput.click());
    priceFileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            setPriceFiles(Array.from(e.target.files));
        }
    });
    setupDragDrop(priceDropZone, (file) => setPriceFiles([file]), '.xlsx');

    function setPriceFiles(files) {
        const xlsxFiles = files.filter(f => f.name.toLowerCase().endsWith('.xlsx') || f.name.toLowerCase().endsWith('.xls'));
        if (xlsxFiles.length === 0) {
            alert('Пожалуйста, выберите один или несколько файлов Excel (.xlsx)');
            return;
        }
        priceFiles = xlsxFiles;

        if (priceFiles.length === 1) {
            priceFileStatus.innerHTML = `<strong>Выбран прайс-лист:</strong> ${escapeHtml(priceFiles[0].name)}`;
        } else {
            priceFileStatus.innerHTML = `<strong>Выбрано прайсов (${priceFiles.length} шт.):</strong><br>` +
                priceFiles.map(f => `• ${escapeHtml(f.name)}`).join('<br>');
        }
        priceDropZone.classList.add('active-file');
        updateActionState();
    }

    // Update Action Button state
    function updateActionState() {
        if (pdfFile && (priceFiles.length > 0 || activePricelistName)) {
            generateKpBtn.disabled = false;
        } else {
            generateKpBtn.disabled = true;
        }
    }

    // Setup Drag-and-Drop files helper
    function setupDragDrop(zone, fileCallback, extension) {
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
                const dropped = Array.from(e.dataTransfer.files);
                if (extension === '.pdf') {
                    fileCallback(dropped[0]);
                } else {
                    fileCallback(dropped);
                }
            }
        });
    }

    // Call API generate KP
    generateKpBtn.addEventListener('click', () => {
        if (!pdfFile || (priceFiles.length === 0 && !activePricelistName)) return;

        generalLoaderCard.classList.remove('hidden');
        kpResultsContainer.classList.add('hidden');

        const formData = new FormData();
        formData.append('specification', pdfFile);

        // Append all selected price lists if uploaded
        if (priceFiles.length > 0) {
            priceFiles.forEach(file => {
                formData.append('pricelists', file);
            });
        }

        fetch('/api/generate-kp', {
            method: 'POST',
            body: formData
        })
        .then(res => {
            if (!res.ok) throw new Error(`Ошибка расчета коммерческого предложения на сервере (${res.status})`);
            return res.json();
        })
        .then(data => {
            generalLoaderCard.classList.add('hidden');
            if (data.status === 'success' && data.kp) {
                activeKpData = data.kp;

                // Show raw text block
                extractedTextViewer.textContent = data.extracted_text || 'Текст пуст или не был извлечён.';

                // Update file info subtext
                const prNames = priceFiles.length > 0
                    ? priceFiles.map(f => f.name).join(', ')
                    : activePricelistName;

                kpFilesInfo.innerHTML = `Сформировано на основе проекта <strong>"${escapeHtml(pdfFile.name)}"</strong> и прайс-листов: <strong>${escapeHtml(prNames)}</strong>`;

                // If a new price list was uploaded, update activePricelistName state
                if (priceFiles.length > 0) {
                    activePricelistName = priceFiles[0].name;
                }

                renderKpTables(data.kp);
                kpResultsContainer.classList.remove('hidden');
            } else {
                alert('Ошибка: сервер вернул некорректный ответ.');
            }
        })
        .catch(err => {
            generalLoaderCard.classList.add('hidden');
            console.error(err);
            alert(`Произошла ошибка при генерации КП: ${err.message}`);
        });
    });

    // Render resulting KP tables grouped by board
    function renderKpTables(kp) {
        kpTablesWrapper.innerHTML = '';

        if (!kp.boards || kp.boards.length === 0) {
            kpTablesWrapper.innerHTML = '<p class="text-center" style="padding: 20px;">Нет позиций оборудования для отображения.</p>';
            kpGrandTotalValue.textContent = '0.00 руб.';
            return;
        }

        kp.boards.forEach((board) => {
            if (!board.items || board.items.length === 0) return;

            const section = document.createElement('div');
            section.className = 'kp-board-section';

            // Group header
            const header = document.createElement('div');
            header.className = 'board-title-row';
            header.textContent = `Щит / Раздел: ${board.board_name}`;
            section.appendChild(header);

            // Table structure
            const table = document.createElement('table');
            table.className = 'items-table';
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

            board.items.forEach((item, index) => {
                const tr = document.createElement('tr');
                const priceFormatted = item.price_found
                    ? `${formatMoney(item.price)} руб.`
                    : '<span class="price-not-found">цена не найдена (0.00 руб.)</span>';

                tr.innerHTML = `
                    <td style="text-align: center; color: var(--text-muted);">${index + 1}</td>
                    <td><strong>${escapeHtml(item.article || '-')}</strong></td>
                    <td>${escapeHtml(item.name)}</td>
                    <td style="text-align: right; font-weight: 600;">${item.qty}</td>
                    <td style="text-align: center;">${escapeHtml(item.unit || 'шт')}</td>
                    <td style="text-align: right;">${priceFormatted}</td>
                    <td style="text-align: right; font-weight: 700; color: var(--primary-color);">${formatMoney(item.total)} руб.</td>
                `;
                tbody.appendChild(tr);
            });

            // Subtotal board section row
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

        kpGrandTotalValue.textContent = `${formatMoney(kp.grand_total)} руб.`;
    }

    // Export Excel action click
    downloadKpExcelBtn.addEventListener('click', () => {
        if (!activeKpData) return;

        fetch('/api/export-kp', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(activeKpData)
        })
        .then(res => {
            if (!res.ok) throw new Error('Ошибка генерации Excel файла предложения.');
            return res.blob();
        })
        .then(blob => {
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `Коммерческое_предложение_${pdfFile ? pdfFile.name.replace(/\.[^/.]+$/, "") : "БЭП"}.xlsx`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
        })
        .catch(err => {
            console.error(err);
            alert(`Ошибка скачивания Excel: ${err.message}`);
        });
    });

    // Helpers
    function formatMoney(val) {
        return Number(val).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function formatSize(bytes) {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }

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
