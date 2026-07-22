/* ═══ EGX Lite Market Radar — Dashboard JS ═══ */

let allItems = [];
let currentFilter = 'all';
let currentSort = { col: 'activity_score', dir: 'desc' };
let refreshTimer = null;

// ─── Init ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    loadRadarData();
    loadHistory();
    refreshTimer = setInterval(loadRadarData, 60000);
});

// ─── API Calls ──────────────────────────────────────────────────────
async function loadRadarData() {
    try {
        const resp = await fetch('/api/radar');
        const data = await resp.json();
        allItems = data.items || [];
        updateSummary(data);
        updateFreshness(data);
        renderTable();
        document.getElementById('last-scan').textContent = `Last scan: ${data.timestamp || '--'}`;
    } catch (e) {
        console.error('Failed to load radar data:', e);
    }
}

async function refreshScan() {
    const bar = document.getElementById('status-bar');
    const btn = document.getElementById('btn-refresh');
    bar.classList.remove('hidden');
    btn.disabled = true;
    btn.textContent = 'Scanning...';

    try {
        const resp = await fetch('/api/radar/refresh', { method: 'POST' });
        const data = await resp.json();
        allItems = data.items || [];
        updateSummary(data);
        updateFreshness(data);
        renderTable();
        document.getElementById('last-scan').textContent = `Last scan: ${data.timestamp || '--'}`;
        await loadHistory();
    } catch (e) {
        console.error('Refresh failed:', e);
    } finally {
        bar.classList.add('hidden');
        btn.disabled = false;
        btn.textContent = 'Refresh Scan';
    }
}

async function loadHistory() {
    try {
        const resp = await fetch('/api/history');
        const history = await resp.json();
        renderHistory(history);
    } catch (e) {
        console.error('Failed to load history:', e);
    }
}

// ─── Update UI ──────────────────────────────────────────────────────
function updateSummary(data) {
    const stats = data.stats || {};
    document.getElementById('stat-scanned').textContent = stats.symbols_scanned || 0;
    document.getElementById('stat-buying').textContent = stats.buying_count || 0;
    document.getElementById('stat-selling').textContent = stats.selling_count || 0;
    document.getElementById('stat-unusual').textContent = stats.unusual_count || 0;
    document.getElementById('stat-delay').textContent = data.freshness_delay_days || 0;
}

function updateFreshness(data) {
    const badge = document.getElementById('freshness-badge');
    const status = data.freshness_status || '';
    badge.textContent = status;
    badge.className = 'badge';
    if (status === 'CURRENT') badge.classList.add('badge-ok');
    else if (status === 'PROVIDER_DELAYED') badge.classList.add('badge-warn');
    else if (status === 'MARKET_OPEN') badge.classList.add('badge-info');
    else badge.classList.add('badge-error');

    document.getElementById('freshness-provider-date').textContent = data.provider_latest_date || '--';
    document.getElementById('freshness-expected').textContent = data.expected_latest_session || '--';
    document.getElementById('freshness-status').textContent = status || '--';
}

// ─── Table Rendering ────────────────────────────────────────────────
function renderTable() {
    const tbody = document.getElementById('stocks-tbody');
    let items = [...allItems];

    const query = document.getElementById('search-box').value.toLowerCase();
    if (query) {
        items = items.filter(i =>
            i.symbol.toLowerCase().includes(query) ||
            (i.company_name || '').toLowerCase().includes(query)
        );
    }

    if (currentFilter !== 'all') {
        items = items.filter(i => i.activity_category === currentFilter);
    }

    items.sort((a, b) => {
        let va = a[currentSort.col];
        let vb = b[currentSort.col];
        if (typeof va === 'string') { va = va.toLowerCase(); vb = (vb || '').toLowerCase(); }
        if (va < vb) return currentSort.dir === 'asc' ? -1 : 1;
        if (va > vb) return currentSort.dir === 'asc' ? 1 : -1;
        return 0;
    });

    if (items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" class="loading">No stocks match filters / لا توجد نتائج</td></tr>';
        return;
    }

    tbody.innerHTML = items.map(item => {
        const chgClass = item.price_change_percent > 0 ? 'cat-buying' : item.price_change_percent < 0 ? 'cat-selling' : '';
        const scoreClass = item.activity_score >= 70 ? 'score-high' : item.activity_score >= 40 ? 'score-mid' : 'score-low';
        const levelClass = `level-${(item.activity_level || '').toLowerCase()}`;
        const catClass = item.activity_category === 'BUYING_ACTIVITY' ? 'cat-buying' :
                         item.activity_category === 'SELLING_ACTIVITY' ? 'cat-selling' : 'cat-unusual';
        const catLabel = item.activity_category === 'BUYING_ACTIVITY' ? 'Buying' :
                         item.activity_category === 'SELLING_ACTIVITY' ? 'Selling' : 'Unusual';
        const rsiArrow = item.rsi_change > 0 ? '\u2191' : item.rsi_change < 0 ? '\u2193' : '\u2192';
        const macdArrow = item.macd_histogram_change > 0 ? '\u2191' : '\u2193';

        return `<tr class="clickable" onclick="showDetail('${item.symbol}')">
            <td><strong>${item.symbol}</strong></td>
            <td>${item.company_name || ''}</td>
            <td class="num">${(item.latest_close || 0).toFixed(2)}</td>
            <td class="num ${chgClass}">${item.price_change_percent > 0 ? '+' : ''}${(item.price_change_percent || 0).toFixed(1)}%</td>
            <td class="num ${scoreClass}">${item.activity_score || 0}</td>
            <td class="${levelClass}">${item.activity_level || ''}</td>
            <td class="${catClass}">${catLabel}</td>
            <td class="num">${(item.rvol_20 || 0).toFixed(2)}x</td>
            <td class="num">${(item.rsi_14 || 0).toFixed(0)} ${rsiArrow}</td>
            <td class="num">${macdArrow} ${(item.macd_histogram || 0).toFixed(3)}</td>
        </tr>`;
    }).join('');
}

// ─── Sort ───────────────────────────────────────────────────────────
function sortTable(col) {
    if (currentSort.col === col) {
        currentSort.dir = currentSort.dir === 'asc' ? 'desc' : 'asc';
    } else {
        currentSort = { col, dir: 'desc' };
    }

    document.querySelectorAll('th.sortable').forEach(th => {
        const arrow = th.querySelector('.sort-arrow');
        if (th.dataset.col === col) {
            arrow.textContent = currentSort.dir === 'asc' ? ' \u25B2' : ' \u25BC';
        } else {
            arrow.textContent = '';
        }
    });

    renderTable();
}

// ─── Filter ─────────────────────────────────────────────────────────
function setFilter(filter, btn) {
    currentFilter = filter;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderTable();
}

function filterTable() { renderTable(); }

// ─── Stock Detail ───────────────────────────────────────────────────
function showDetail(symbol) {
    const item = allItems.find(i => i.symbol === symbol);
    if (!item) return;

    document.getElementById('detail-title').textContent = `${item.symbol} — ${item.company_name || ''}`;

    const catClass = item.activity_category === 'BUYING_ACTIVITY' ? 'cat-buying' :
                     item.activity_category === 'SELLING_ACTIVITY' ? 'cat-selling' : 'cat-unusual';
    const rsiArrow = item.rsi_change > 0 ? '\u2191' : item.rsi_change < 0 ? '\u2193' : '\u2192';
    const macdArrow = item.macd_histogram_change > 0 ? '\u2191' : '\u2193';
    const histArrow = item.macd_histogram_change > 0 ? 'Improving' : 'Weakening';

    const rows = [
        ['Activity Score', `${item.activity_score}/100`],
        ['Activity Level', item.activity_level],
        ['Activity Type', `<span class="${catClass}">${item.activity_category}</span>`],
        ['Label', item.activity_label],
        ['', ''],
        ['Last Completed Close', `${(item.latest_close || 0).toFixed(2)} (${item.price_change_percent > 0 ? '+' : ''}${(item.price_change_percent || 0).toFixed(1)}%)`],
        ['Session Open', (item.session_open || 0).toFixed(2)],
        ['Session High', (item.session_high || 0).toFixed(2)],
        ['Session Low', (item.session_low || 0).toFixed(2)],
        ['Previous Close', (item.previous_close || 0).toFixed(2)],
        ['Session Date', item.price_date],
        ['Data Mode', item.data_mode],
        ['', ''],
        ['Provider Latest', item.provider_latest_date],
        ['Expected Session', item.expected_latest_session],
        ['Freshness', item.freshness_status],
        ['Freshness Note', item.freshness_note],
        ['', ''],
        ['Volume (RVOL)', `${(item.rvol_20 || 0).toFixed(2)}x average`],
        ['Traded Value', `${(item.traded_value || 0).toLocaleString()} EGP`],
        ['Avg Traded Value 20d', `${(item.average_traded_value_20 || 0).toLocaleString()} EGP`],
        ['', ''],
        ['RSI (14)', `${(item.rsi_14 || 0).toFixed(0)} ${rsiArrow} (${item.rsi_change > 0 ? '+' : ''}${(item.rsi_change || 0).toFixed(1)})`],
        ['MACD Histogram', `${(item.macd_histogram || 0).toFixed(4)} (${macdArrow} ${(item.macd_histogram_change || 0).toFixed(4)})`],
        ['MACD Trend', histArrow],
        ['Close Location', (item.close_location_value || 0).toFixed(3)],
        ['Candle Body %', `${(item.candle_body_percent || 0).toFixed(1)}%`],
        ['Volume Percentile', `${(item.volume_percentile_60 || 0).toFixed(0)}th`],
    ];

    if (item.adx_14 != null) rows.push(['ADX (14)', item.adx_14.toFixed(1)]);
    if (item.price_return_5d != null) rows.push(['5-Day Return', `${item.price_return_5d > 0 ? '+' : ''}${item.price_return_5d.toFixed(1)}%`]);
    if (item.price_return_20d != null) rows.push(['20-Day Return', `${item.price_return_20d > 0 ? '+' : ''}${item.price_return_20d.toFixed(1)}%`]);

    const grid = rows.map(([label, value]) => {
        if (!label && !value) return '<div style="height:1px;background:var(--border);margin:4px 0"></div>';
        return `<div class="detail-row"><span class="label">${label}</span><span class="value">${value}</span></div>`;
    }).join('');

    let reasonsHtml = '';
    if (item.reasons && item.reasons.length > 0) {
        reasonsHtml = `<div class="detail-reasons"><h3>Reasons / الأسباب</h3><ul>${
            item.reasons.map(r => `<li>${r}</li>`).join('')
        }</ul></div>`;
    }

    document.getElementById('detail-grid').innerHTML = grid + reasonsHtml;
    document.getElementById('stock-detail').classList.remove('hidden');
}

function closeDetail() {
    document.getElementById('stock-detail').classList.add('hidden');
}

// ─── History ────────────────────────────────────────────────────────
function renderHistory(history) {
    const tbody = document.getElementById('history-tbody');
    if (!history || history.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="loading">No history yet</td></tr>';
        return;
    }

    tbody.innerHTML = history.slice(0, 50).map(h => {
        const freshClass = h.freshness_status === 'CURRENT' ? 'cat-buying' :
                           h.freshness_status === 'PROVIDER_DELAYED' ? 'cat-selling' : '';
        return `<tr>
            <td>${h.timestamp || '--'}</td>
            <td>${h.scanned || 0}</td>
            <td>${h.activity || 0}</td>
            <td class="cat-buying">${h.buying || 0}</td>
            <td class="cat-selling">${h.selling || 0}</td>
            <td class="cat-unusual">${h.unusual || 0}</td>
            <td>${h.duration || '--'}s</td>
            <td class="${freshClass}">${h.freshness_status || '--'}</td>
        </tr>`;
    }).join('');
}

// ─── Export CSV ─────────────────────────────────────────────────────
function exportCSV() {
    if (allItems.length === 0) return;

    const headers = ['Symbol','Name','Close','Chg%','Score','Level','Category','RVOL','RSI','MACD','Provider Date','Expected','Freshness','Delay Days'];
    const rows = allItems.map(i => [
        i.symbol,
        `"${(i.company_name || '').replace(/"/g, '""')}"`,
        i.latest_close,
        i.price_change_percent,
        i.activity_score,
        i.activity_level,
        i.activity_category,
        i.rvol_20,
        i.rsi_14,
        i.macd_histogram,
        i.provider_latest_date,
        i.expected_latest_session,
        i.freshness_status,
        i.freshness_delay_days,
    ].join(','));

    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `egx_radar_${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
}

// ─── Keyboard ───────────────────────────────────────────────────────
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeDetail();
});
