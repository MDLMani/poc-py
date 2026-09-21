// --- Warehouse Slots Desktop App Logic ---

let BASE_URL = 'http://127.0.0.1:8765';
let state = {
  config: null,
  skus: [],
  stock: [],
  events: [],
  fixtures: [],
  currentScan: null,
  selectedSlot: null,
  activeFilter: 'all',
  activeTab: 'tab-layout'
};

// --- Toast Notifications ---
function showToast(message, type = 'success') {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    toast.style.transition = 'all 0.2s';
    setTimeout(() => toast.remove(), 200);
  }, 3500);
}

// --- API Helper ---
async function api(endpoint, options = {}) {
  try {
    const res = await fetch(`${BASE_URL}${endpoint}`, {
      headers: { 'Content-Type': 'application/json' },
      ...options
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: res.statusText }));
      throw new Error(err.error || `HTTP ${res.status}`);
    }
    return await res.json();
  } catch (err) {
    console.error(`API Error on ${endpoint}:`, err);
    throw err;
  }
}

// --- Backend Health Check & Polling ---
async function checkBackend() {
  const statusEl = document.getElementById('backendStatus');
  try {
    const data = await api('/health');
    if (data.ok) {
      statusEl.className = 'status-badge connected';
      statusEl.querySelector('.status-text').textContent = 'Backend Offline Ready';
      return true;
    }
  } catch (e) {
    statusEl.className = 'status-badge';
    statusEl.querySelector('.status-text').textContent = 'Connecting...';
  }
  return false;
}

// --- Navigation Tabs ---
function initTabs() {
  const tabs = document.querySelectorAll('.nav-tab');
  tabs.forEach(tab => {
    tab.addEventListener('click', () => {
      const targetId = tab.dataset.tab;
      tabs.forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById(targetId).classList.add('active');
      state.activeTab = targetId;

      if (targetId === 'tab-layout') loadConfig();
      if (targetId === 'tab-inventory') loadEvents();
      if (targetId === 'tab-skus') { loadSkus(); loadStock(); }
    });
  });
}

// --- TAB 1: COLUMNS & SLOTS FLOW ---
async function loadConfig() {
  try {
    const data = await api('/api/config');
    state.config = data;
    await loadSkus();
    renderColumnsBoard();
  } catch (err) {
    showToast(`Failed to load configuration: ${err.message}`, 'error');
  }
}

async function loadSkus() {
  try {
    const data = await api('/api/skus');
    state.skus = data.skus || [];
    updateSkuDropdowns();
  } catch (err) {
    console.error('Failed to load SKUs:', err);
  }
}

function updateSkuDropdowns() {
  const defaultSelect = document.getElementById('colDefaultSkuInput');
  if (defaultSelect) {
    defaultSelect.innerHTML = '<option value="">(Leave slots empty)</option>';
    state.skus.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.sku_id;
      opt.textContent = `${s.sku_id} — ${s.name}`;
      defaultSelect.appendChild(opt);
    });
  }
}

function renderColumnsBoard() {
  const board = document.getElementById('columnsBoard');
  board.innerHTML = '';

  if (!state.config || !state.config.slots || state.config.slots.length === 0) {
    board.innerHTML = '<div class="empty-placeholder">No columns configured yet. Click "+ Add Column" to create one.</div>';
    return;
  }

  state.config.slots.forEach(col => {
    const card = document.createElement('div');
    card.className = 'column-card';

    // Header
    const header = document.createElement('div');
    header.className = 'column-header';
    header.innerHTML = `
      <div class="column-header-title">
        <span class="column-id-badge">${col.id}</span>
        <span class="badge badge-info">${col.capacity} Slots</span>
      </div>
      <button class="btn-icon btn-del-col" title="Delete Column" data-col-id="${col.id}">&times;</button>
    `;

    // Slots List
    const slotsList = document.createElement('div');
    slotsList.className = 'column-slots-list';

    const expected = col.expected_products || [];
    for (let i = 0; i < col.capacity; i++) {
      const slotItem = document.createElement('div');
      slotItem.className = 'slot-item';

      const currentProduct = expected[i] || '';

      const slotHdr = document.createElement('div');
      slotHdr.className = 'slot-item-header';
      slotHdr.innerHTML = `
        <span class="slot-index">Slot #${i}</span>
        ${currentProduct ? `<span class="badge badge-success">${currentProduct}</span>` : `<span class="badge badge-warning">Empty</span>`}
      `;

      const select = document.createElement('select');
      select.className = 'slot-product-select';
      select.innerHTML = '<option value="">(Empty / No Product)</option>';
      state.skus.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.sku_id;
        opt.textContent = s.sku_id;
        if (s.sku_id === currentProduct) opt.selected = true;
        select.appendChild(opt);
      });

      select.addEventListener('change', async (e) => {
        const newProduct = e.target.value || null;
        try {
          await api('/api/slots/product', {
            method: 'POST',
            body: JSON.stringify({
              column_id: col.id,
              slot_index: i,
              product_id: newProduct
            })
          });
          showToast(`Assigned ${newProduct || 'Empty'} to ${col.id} [Slot #${i}]`);
          loadConfig();
        } catch (err) {
          showToast(`Failed to update slot: ${err.message}`, 'error');
        }
      });

      slotItem.appendChild(slotHdr);
      slotItem.appendChild(select);
      slotsList.appendChild(slotItem);
    }

    // Footer
    const footer = document.createElement('div');
    footer.className = 'column-footer';
    const [x, y, w, h] = col.roi;
    footer.innerHTML = `<span class="column-roi-meta">ROI: [${x}, ${y}, ${w}×${h}]</span>`;

    card.appendChild(header);
    card.appendChild(slotsList);
    card.appendChild(footer);
    board.appendChild(card);
  });

  // Attach delete handlers
  document.querySelectorAll('.btn-del-col').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      const colId = e.target.dataset.colId;
      if (confirm(`Are you sure you want to remove Column ${colId}?`)) {
        try {
          await api(`/api/columns/${encodeURIComponent(colId)}`, { method: 'DELETE' });
          showToast(`Removed Column ${colId}`);
          loadConfig();
        } catch (err) {
          showToast(`Failed to remove column: ${err.message}`, 'error');
        }
      }
    });
  });
}

// Modal handlers for Add Column
function initColumnModal() {
  const modal = document.getElementById('modalAddColumn');
  const btnOpen = document.getElementById('btnOpenAddColumnModal');
  const btnClose = document.getElementById('btnCloseAddColumnModal');
  const btnCancel = document.getElementById('btnCancelAddColumn');
  const form = document.getElementById('formAddColumn');

  btnOpen.addEventListener('click', () => {
    // Intelligent position calculation based on last column
    if (state.config && state.config.slots && state.config.slots.length > 0) {
      const last = state.config.slots[state.config.slots.length - 1];
      const nextX = last.roi[0] + last.roi[2] + 40;
      document.getElementById('colXInput').value = nextX;
      document.getElementById('colYInput').value = last.roi[1];
      document.getElementById('colWInput').value = last.roi[2];
      document.getElementById('colHInput').value = last.roi[3];
      document.getElementById('colCapacityInput').value = last.capacity;
      document.getElementById('colIdInput').value = `COL-${state.config.slots.length + 1}`;
    }
    modal.style.display = 'flex';
  });

  const closeModal = () => { modal.style.display = 'none'; };
  btnClose.addEventListener('click', closeModal);
  btnCancel.addEventListener('click', closeModal);

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const colId = document.getElementById('colIdInput').value.trim();
    const capacity = parseInt(document.getElementById('colCapacityInput').value, 10);
    const x = parseInt(document.getElementById('colXInput').value, 10);
    const y = parseInt(document.getElementById('colYInput').value, 10);
    const w = parseInt(document.getElementById('colWInput').value, 10);
    const h = parseInt(document.getElementById('colHInput').value, 10);
    const defSku = document.getElementById('colDefaultSkuInput').value || null;

    const expected = Array(capacity).fill(defSku);

    try {
      await api('/api/columns/add', {
        method: 'POST',
        body: JSON.stringify({
          id: colId,
          roi: [x, y, w, h],
          capacity: capacity,
          expected_products: expected
        })
      });
      closeModal();
      showToast(`Column ${colId} created with ${capacity} slots!`);
      loadConfig();
    } catch (err) {
      showToast(`Failed to add column: ${err.message}`, 'error');
    }
  });

  // Reset 5-column board preset
  document.getElementById('btnResetBoardPreset').addEventListener('click', async () => {
    if (confirm('Reset layout to standard 5-column warehouse board preset?')) {
      const presetSlots = [
        { id: "F1", roi: [40, 40, 240, 2400], capacity: 10, expected_products: Array(10).fill("SKU-ALPHA") },
        { id: "F2", roi: [320, 40, 240, 1440], capacity: 6, expected_products: ["SKU-A", "SKU-A", "SKU-B", "SKU-B", "SKU-C", "SKU-C"] },
        { id: "F3", roi: [600, 40, 240, 1920], capacity: 8, expected_products: ["SKU-X", "SKU-X", "SKU-Y", null, null, null, null, null] },
        { id: "F4", roi: [880, 40, 240, 1440], capacity: 6, expected_products: Array(6).fill("SKU-COMP") },
        { id: "F5", roi: [1160, 40, 240, 1440], capacity: 6, expected_products: Array(6).fill(null) }
      ];
      try {
        await api('/api/config', {
          method: 'POST',
          body: JSON.stringify({ slots: presetSlots })
        });
        showToast('Reset to 5-column board preset!');
        loadConfig();
      } catch (err) {
        showToast(`Failed to reset preset: ${err.message}`, 'error');
      }
    }
  });

  // Save layout button
  document.getElementById('btnSaveConfig').addEventListener('click', async () => {
    try {
      await api('/api/config', {
        method: 'POST',
        body: JSON.stringify(state.config)
      });
      showToast('Layout configuration saved successfully!');
    } catch (err) {
      showToast(`Failed to save config: ${err.message}`, 'error');
    }
  });
}

// --- TAB 2: SCAN & FLOW ANALYSIS ---
async function loadFixtures() {
  try {
    const data = await api('/api/fixtures');
    state.fixtures = data.fixtures || [];
    const select = document.getElementById('selectFixture');
    select.innerHTML = '';
    state.fixtures.forEach(f => {
      const opt = document.createElement('option');
      opt.value = f.path;
      opt.textContent = `${f.name} (${f.path})`;
      if (f.name === 'board.png') opt.selected = true;
      select.appendChild(opt);
    });
  } catch (err) {
    console.error('Failed to load fixtures:', err);
  }
}

function initScanHandlers() {
  document.getElementById('btnBrowseImage').addEventListener('click', async () => {
    if (window.electronAPI && window.electronAPI.selectImage) {
      const path = await window.electronAPI.selectImage();
      if (path) {
        const select = document.getElementById('selectFixture');
        const opt = document.createElement('option');
        opt.value = path;
        opt.textContent = `[Custom File] ${path}`;
        opt.selected = true;
        select.prepend(opt);
        showToast(`Selected: ${path}`);
      }
    } else {
      showToast('Native file picker not available in browser mode.', 'warning');
    }
  });

  document.getElementById('btnRunAnalysis').addEventListener('click', async () => {
    const select = document.getElementById('selectFixture');
    const imagePath = select.value;
    if (!imagePath) {
      showToast('Please select a fixture or image file to analyze.', 'warning');
      return;
    }

    const btn = document.getElementById('btnRunAnalysis');
    const originalText = btn.innerHTML;
    btn.innerHTML = 'Analyzing...';
    btn.disabled = true;

    try {
      const result = await api('/api/analyze', {
        method: 'POST',
        body: JSON.stringify({ image_path: imagePath })
      });
      state.currentScan = result;
      renderScanResults(result);
      showToast(`Analyzed ${result.summary.total_cells} slots: ${result.summary.matches} matches, ${result.summary.mismatches} mismatches`);
    } catch (err) {
      showToast(`Analysis error: ${err.message}`, 'error');
    } finally {
      btn.innerHTML = originalText;
      btn.disabled = false;
    }
  });

  // Table filters
  document.querySelectorAll('.filter-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      state.activeFilter = btn.dataset.filter;
      renderSlotsTable();
    });
  });
}

function renderScanResults(report) {
  // 1. Overlay image preview
  const imgEl = document.getElementById('overlayImagePreview');
  const placeholder = document.getElementById('canvasPlaceholder');
  const badge = document.getElementById('imageNameBadge');

  if (report.overlay_base64) {
    imgEl.src = report.overlay_base64;
    imgEl.style.display = 'block';
    placeholder.style.display = 'none';
    badge.textContent = `${report.image_name} (${report.image_width}×${report.image_height})`;
  }

  // 2. Metrics ribbon
  document.getElementById('scanMetricsRibbon').style.display = 'flex';
  document.getElementById('metricTotal').textContent = report.summary.total_cells;
  document.getElementById('metricMatches').textContent = report.summary.matches;
  document.getElementById('metricEmpty').textContent = report.summary.empty;
  document.getElementById('metricMismatches').textContent = report.summary.mismatches;
  document.getElementById('metricUnreadable').textContent = report.summary.unreadable;

  // 3. Filter counts
  let countMatch = 0, countMismatch = 0, countEmpty = 0, countUnreadable = 0;
  report.slots.forEach(s => {
    s.cells.forEach(c => {
      if (c.verdict === 'MATCH') countMatch++;
      else if (c.verdict === 'MISMATCH' || c.verdict === 'MISSING') countMismatch++;
      else if (c.verdict === 'EMPTY') countEmpty++;
      else if (c.verdict === 'UNREADABLE') countUnreadable++;
    });
  });
  document.getElementById('countFilterAll').textContent = report.summary.total_cells;
  document.getElementById('countFilterMatch').textContent = countMatch;
  document.getElementById('countFilterMismatch').textContent = countMismatch;
  document.getElementById('countFilterEmpty').textContent = countEmpty;
  document.getElementById('countFilterUnreadable').textContent = countUnreadable;

  // 4. Render Table
  renderSlotsTable();

  // 5. Default select the first slot
  if (report.slots.length > 0 && report.slots[0].cells.length > 0) {
    inspectSlot(report.slots[0].slot_id, report.slots[0].cells[0]);
  }
}

function renderSlotsTable() {
  const tbody = document.getElementById('slotsTableBody');
  tbody.innerHTML = '';

  if (!state.currentScan) return;

  const filter = state.activeFilter;
  let rowsRendered = 0;

  state.currentScan.slots.forEach(slot => {
    slot.cells.forEach(cell => {
      let matchesFilter = false;
      if (filter === 'all') matchesFilter = true;
      else if (filter === 'match' && cell.verdict === 'MATCH') matchesFilter = true;
      else if (filter === 'mismatch' && (cell.verdict === 'MISMATCH' || cell.verdict === 'MISSING')) matchesFilter = true;
      else if (filter === 'empty' && cell.verdict === 'EMPTY') matchesFilter = true;
      else if (filter === 'unreadable' && cell.verdict === 'UNREADABLE') matchesFilter = true;

      if (!matchesFilter) return;

      rowsRendered++;
      const tr = document.createElement('tr');
      if (state.selectedSlot && state.selectedSlot.colId === slot.slot_id && state.selectedSlot.cell.index === cell.index) {
        tr.className = 'selected';
      }

      let verdictBadgeClass = 'badge-info';
      if (cell.verdict === 'MATCH') verdictBadgeClass = 'badge-success';
      else if (cell.verdict === 'MISMATCH' || cell.verdict === 'MISSING') verdictBadgeClass = 'badge-danger';
      else if (cell.verdict === 'EMPTY') verdictBadgeClass = 'badge-warning';
      else if (cell.verdict === 'UNREADABLE') verdictBadgeClass = 'badge-danger';

      tr.innerHTML = `
        <td><strong class="column-id-badge">${slot.slot_id}</strong></td>
        <td>Slot #${cell.index}</td>
        <td>${cell.expected_product ? `<code>${cell.expected_product}</code>` : '<span class="text-muted">(Empty)</span>'}</td>
        <td>${cell.payload ? `<strong style="color:#60a5fa">${cell.payload}</strong>` : '<span class="text-muted">—</span>'}</td>
        <td><span class="badge badge-info">${cell.match_source || (cell.status === 'EMPTY' ? 'none' : 'unreadable')}</span></td>
        <td><span class="badge ${verdictBadgeClass}">${cell.verdict_label}</span></td>
      `;

      tr.addEventListener('click', () => {
        document.querySelectorAll('#slotsTableBody tr').forEach(r => r.classList.remove('selected'));
        tr.classList.add('selected');
        inspectSlot(slot.slot_id, cell);
      });

      tbody.appendChild(tr);
    });
  });

  if (rowsRendered === 0) {
    tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">No slots match the active filter.</td></tr>';
  }
}

function inspectSlot(colId, cell) {
  state.selectedSlot = { colId, cell };
  document.getElementById('inspectorHeaderBadge').textContent = `${colId} — Slot #${cell.index}`;

  const container = document.getElementById('slotFlowCard');
  let badgeClass = 'badge-info';
  if (cell.verdict === 'MATCH') badgeClass = 'badge-success';
  else if (cell.verdict === 'MISMATCH' || cell.verdict === 'MISSING') badgeClass = 'badge-danger';
  else if (cell.verdict === 'EMPTY') badgeClass = 'badge-warning';
  else if (cell.verdict === 'UNREADABLE') badgeClass = 'badge-danger';

  container.innerHTML = `
    <div class="inspector-grid">
      <div class="inspector-crop-box" title="Cell crop snapshot">
        ${cell.crop_thumbnail ? `<img src="${cell.crop_thumbnail}" alt="Slot #${cell.index}">` : '<span>No crop</span>'}
      </div>
      <div class="flow-details-list">
        <div class="flow-row">
          <span class="label">Column &amp; Slot:</span>
          <span class="value">${colId} &rsaquo; Slot #${cell.index}</span>
        </div>
        <div class="flow-row">
          <span class="label">Expected Product:</span>
          <span class="value">${cell.expected_product ? cell.expected_product : '<em class="text-muted">None (Empty)</em>'}</span>
        </div>
        <div class="flow-row">
          <span class="label">Detected Barcode:</span>
          <span class="value">${cell.payload ? cell.payload : '<em class="text-muted">None detected</em>'}</span>
        </div>
        <div class="flow-row">
          <span class="label">Detection Method:</span>
          <span class="value">${cell.match_source ? cell.match_source.toUpperCase() : (cell.status === 'EMPTY' ? 'Empty cell' : 'Unreadable')}</span>
        </div>
        <div class="flow-row" style="margin-top: 4px;">
          <span class="label">Flow Verdict:</span>
          <span class="badge ${badgeClass}">${cell.verdict_label}</span>
        </div>
      </div>
    </div>
  `;
}

// --- TAB 3: INBOUND / OUTBOUND INVENTORY ---
async function loadEvents() {
  try {
    const data = await api('/api/events?limit=50');
    state.events = data.events || [];
    renderEventsTable();
  } catch (err) {
    console.error('Failed to load events:', err);
  }
}

function renderEventsTable() {
  const tbody = document.getElementById('eventsTableBody');
  tbody.innerHTML = '';

  if (state.events.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">No inventory events recorded yet.</td></tr>';
    return;
  }

  state.events.forEach(ev => {
    const tr = document.createElement('tr');
    const badgeClass = ev.direction === 'IN' ? 'badge-success' : 'badge-danger';
    const skuSummary = (ev.sku_lines || []).map(l => `${l.sku_id} (×${l.qty})`).join(', ') || '(no skus)';

    tr.innerHTML = `
      <td>#${ev.id}</td>
      <td><span class="badge ${badgeClass}">${ev.direction}</span></td>
      <td><code>${ev.created_at}</code></td>
      <td>${ev.note || '<span class="text-muted">—</span>'}</td>
      <td><strong>${skuSummary}</strong></td>
    `;
    tbody.appendChild(tr);
  });
}

function initInventoryHandlers() {
  const handleConfirm = async (direction) => {
    const note = document.getElementById('eventNoteInput').value.trim();
    const imagePath = state.currentScan ? state.currentScan.image_path : 'manual';

    try {
      const res = await api(`/api/confirm/${direction.toLowerCase()}`, {
        method: 'POST',
        body: JSON.stringify({
          direction: direction,
          image_path: imagePath,
          scan: state.currentScan ? { slots: state.currentScan.slots } : {},
          note: note
        })
      });
      showToast(`Confirmed ${direction} event #${res.id}!`);
      document.getElementById('eventNoteInput').value = '';
      loadEvents();
      loadStock();
    } catch (err) {
      showToast(`Confirm failed: ${err.message}`, 'error');
    }
  };

  document.getElementById('btnConfirmIn').addEventListener('click', () => handleConfirm('IN'));
  document.getElementById('btnConfirmOut').addEventListener('click', () => handleConfirm('OUT'));
  document.getElementById('btnRefreshEvents').addEventListener('click', loadEvents);
}

// --- TAB 4: PRODUCT CATALOG & STOCK ---
async function loadStock() {
  try {
    const data = await api('/api/stock');
    state.stock = data.stock || [];
    renderStockTable();
  } catch (err) {
    console.error('Failed to load stock:', err);
  }
}

function renderStockTable() {
  const tbody = document.getElementById('stockTableBody');
  tbody.innerHTML = '';

  if (state.stock.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">No products found. Register one or click "Seed Default Fixture SKUs".</td></tr>';
    return;
  }

  state.stock.forEach(item => {
    const tr = document.createElement('tr');
    const badgeClass = item.on_hand > 0 ? 'badge-success' : 'badge-danger';
    tr.innerHTML = `
      <td><code>${item.sku_id}</code></td>
      <td><strong>${item.name}</strong></td>
      <td class="text-muted">${item.description || '—'}</td>
      <td><span style="font-family: monospace; font-weight: 700; font-size: 1rem;">${item.on_hand}</span></td>
      <td><span class="badge ${badgeClass}">${item.status}</span></td>
    `;
    tbody.appendChild(tr);
  });
}

function initSkuHandlers() {
  document.getElementById('btnRefreshStock').addEventListener('click', loadStock);

  document.getElementById('formAddSku').addEventListener('submit', async (e) => {
    e.preventDefault();
    const skuId = document.getElementById('skuIdInput').value.trim();
    const name = document.getElementById('skuNameInput').value.trim();
    const desc = document.getElementById('skuDescInput').value.trim();

    try {
      await api('/api/skus', {
        method: 'POST',
        body: JSON.stringify({ sku_id: skuId, name: name, description: desc })
      });
      showToast(`Registered SKU: ${skuId}`);
      document.getElementById('formAddSku').reset();
      loadSkus();
      loadStock();
    } catch (err) {
      showToast(`Failed to add product: ${err.message}`, 'error');
    }
  });

  document.getElementById('btnSeedSkus').addEventListener('click', async () => {
    try {
      const res = await api('/api/skus/seed', { method: 'POST' });
      showToast(`Seeded ${res.added} default fixture SKUs!`);
      loadSkus();
      loadStock();
    } catch (err) {
      showToast(`Failed to seed SKUs: ${err.message}`, 'error');
    }
  });
}

// --- App Initialization ---
window.addEventListener('DOMContentLoaded', async () => {
  initTabs();
  initColumnModal();
  initScanHandlers();
  initInventoryHandlers();
  initSkuHandlers();

  // Try retrieving backend URL from Electron context bridge if available
  if (window.electronAPI && window.electronAPI.getBackendUrl) {
    try {
      BASE_URL = await window.electronAPI.getBackendUrl();
    } catch (e) {
      console.warn('Using default BASE_URL');
    }
  }

  // Poll until backend is ready, then load initial data
  let ready = false;
  for (let i = 0; i < 10; i++) {
    ready = await checkBackend();
    if (ready) break;
    await new Promise(r => setTimeout(r, 600));
  }

  if (ready) {
    await loadConfig();
    await loadFixtures();
    await loadSkus();
    await loadStock();
    await loadEvents();
  } else {
    showToast('Backend did not respond immediately. Retrying...', 'warning');
  }

  setInterval(checkBackend, 5000);
});
