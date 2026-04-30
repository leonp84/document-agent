'use strict';

// ── Translations ─────────────────────────────────────────────────────────────

const T = {
  de: {
    headerTagline:   'Österreichischer Rechnungsgenerator',
    subtitle:        'Angebot & Rechnung aus Freitext — §11 UStG-konform',
    inputLabel:      'Auftragsbeschreibung',
    inputPlaceholder:'z. B. Tischlerarbeit für Maria Huber: Eichentisch, 2 Tage à 8 h, EUR 75/h.',
    submitBtn:       'Angebot erstellen',
    profileBtn:      'Firmeninfo bearbeiten',
    freigebenBtn:    'Freigeben & Rechnung erstellen',
    quoteHeading:    'Angebot',
    colDescription:  'Beschreibung',
    colQty:          'Menge',
    colUnit:         'Einheit',
    colUnitPrice:    'Preis/Einheit',
    colAmount:       'Betrag',
    labelNet:        'Netto',
    labelGross:      'Brutto',
    statusCreating:  'Erstelle Angebot…',
    statusInvoice:   'Erstelle Rechnung…',
    statusDone:      'Rechnung heruntergeladen.',
    statusCompleted: 'Auftrag abgeschlossen.',
    errNoInput:      'Bitte eine Auftragsbeschreibung eingeben.',
    errNoKey:        'API-Schlüssel nicht geladen. Bitte Seite neu laden.',
    err401:          'Ungültiger API-Schlüssel (401).',
    err429:          'Tageslimit erreicht (429). Bitte morgen erneut versuchen.',
    errServer:       'Serverfehler',
    errNetwork:      'Netzwerkfehler',
    errFailed:       'Verarbeitung fehlgeschlagen.',
    errInvoice:      'Fehler beim Erstellen der Rechnung',
    filePrefix:      'rechnung',
    profileTitle:    'Firmeninformationen',
    pName:           'Firma',
    pAddr1:          'Adresse Zeile 1',
    pAddr2:          'Adresse Zeile 2 (PLZ/Ort)',
    pUid:            'UID-Nr.',
    pIban:           'IBAN',
    pBic:            'BIC',
    pColor:          'Markenfarbe',
    profileHint:     'Änderungen gelten nur für diese Sitzung — nichts wird gespeichert.',
    profileSave:     'Übernehmen',
    profileCancel:   'Abbrechen',
    clientsToggle:   'Bekannte Klienten anzeigen',
    clientsHide:     'Klienten ausblenden',
    clientsColName:  'Name',
    clientsColAlias: 'Erkannte Bezeichnungen',
  },
  en: {
    headerTagline:   'Austrian Invoice Generator',
    subtitle:        'Quote & invoice from plain text — §11 UStG compliant',
    inputLabel:      'Job Description',
    inputPlaceholder:'e.g. Carpentry for Maria Huber: oak table, 2 days at 8 h, EUR 75/h.',
    submitBtn:       'Create Quote',
    profileBtn:      'Edit Business Info',
    freigebenBtn:    'Approve & Generate Invoice',
    quoteHeading:    'Quote',
    colDescription:  'Description',
    colQty:          'Qty',
    colUnit:         'Unit',
    colUnitPrice:    'Unit Price',
    colAmount:       'Amount',
    labelNet:        'Net',
    labelGross:      'Total',
    statusCreating:  'Creating quote…',
    statusInvoice:   'Generating invoice…',
    statusDone:      'Invoice downloaded.',
    statusCompleted: 'Job completed.',
    errNoInput:      'Please enter a job description.',
    errNoKey:        'API key not loaded. Please reload the page.',
    err401:          'Invalid API key (401).',
    err429:          'Daily limit reached (429). Please try again tomorrow.',
    errServer:       'Server error',
    errNetwork:      'Network error',
    errFailed:       'Processing failed.',
    errInvoice:      'Error generating invoice',
    filePrefix:      'invoice',
    profileTitle:    'Business Information',
    pName:           'Company Name',
    pAddr1:          'Address Line 1',
    pAddr2:          'Address Line 2 (ZIP/City)',
    pUid:            'VAT No.',
    pIban:           'IBAN',
    pBic:            'BIC',
    pColor:          'Brand Colour',
    profileHint:     'Changes apply to this session only — nothing is saved permanently.',
    profileSave:     'Apply',
    profileCancel:   'Cancel',
    clientsToggle:   'Show known clients',
    clientsHide:     'Hide clients',
    clientsColName:  'Name',
    clientsColAlias: 'Recognised aliases',
  },
};

// ── State ────────────────────────────────────────────────────────────────────

let lang = 'de';
let embeddedKey = '';
let currentRequestId = null;
let pollTimer = null;
let currentVatRate = null;

// ── DOM refs ─────────────────────────────────────────────────────────────────

const rawInput      = document.getElementById('rawInput');
const submitBtn     = document.getElementById('submitBtn');
const profileBtn    = document.getElementById('profileBtn');
const statusSection = document.getElementById('statusSection');
const statusMsg     = document.getElementById('statusMsg');
const errorSection  = document.getElementById('errorSection');
const errorMsg      = document.getElementById('errorMsg');
const quoteSection  = document.getElementById('quoteSection');
const freigebenBtn  = document.getElementById('freigebenBtn');
const langDe        = document.getElementById('langDe');
const langEn        = document.getElementById('langEn');
const clientsToggleBtn = document.getElementById('clientsToggleBtn');
const clientsPanel     = document.getElementById('clientsPanel');
const clientsBody      = document.getElementById('clientsBody');
const profileModal  = document.getElementById('profileModal');
const profileForm   = document.getElementById('profileForm');
const profileCancel = document.getElementById('profileCancel');
const pColorInput   = document.getElementById('pColor');
const pColorHex     = document.getElementById('pColorHex');

const PROFILE_KEY = 'docassist_profile_override';

// ── Language switcher ─────────────────────────────────────────────────────────

function applyLang() {
  document.documentElement.lang = lang;
  const t = T[lang];

  // Update all data-i18n elements
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (t[key] !== undefined) el.textContent = t[key];
  });

  // Placeholder and header tagline aren't data-i18n elements
  rawInput.placeholder = t.inputPlaceholder;
  document.getElementById('headerTagline').textContent = t.headerTagline;

  // Active button state
  langDe.classList.toggle('active', lang === 'de');
  langEn.classList.toggle('active', lang === 'en');

  // Re-render VAT label if quote is showing
  if (!quoteSection.hidden && currentVatRate !== null) {
    document.getElementById('vatLabel').textContent =
      lang === 'de'
        ? `MwSt. ${currentVatRate} %`
        : `VAT ${currentVatRate}%`;
  }
}

langDe.addEventListener('click', () => { lang = 'de'; applyLang(); });
langEn.addEventListener('click', () => { lang = 'en'; applyLang(); });

// ── Clients panel ────────────────────────────────────────────────────────────

let clientsLoaded = false;

async function loadClients() {
  if (clientsLoaded) return;
  try {
    const res = await fetch('/clients');
    if (!res.ok) return;
    const clients = await res.json();
    clientsBody.innerHTML = '';
    for (const c of clients) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${escHtml(c.name)}</td><td class="aliases">${c.short_names.map(escHtml).join(', ')}</td>`;
      clientsBody.appendChild(tr);
    }
    clientsLoaded = true;
  } catch (_) {}
}

clientsToggleBtn.addEventListener('click', async () => {
  const open = !clientsPanel.hidden;
  if (!open) await loadClients();
  clientsPanel.hidden = open;
  clientsToggleBtn.setAttribute('data-i18n', open ? 'clientsToggle' : 'clientsHide');
  clientsToggleBtn.textContent = t(open ? 'clientsToggle' : 'clientsHide');
});

// ── Business profile modal ────────────────────────────────────────────────────

function profileOverride() {
  const raw = localStorage.getItem(PROFILE_KEY);
  return raw ? JSON.parse(raw) : null;
}

function populateProfileForm(data) {
  ['name', 'address_line1', 'address_line2', 'uid', 'bank_iban', 'bank_bic'].forEach(k => {
    const el = profileForm.elements[k];
    if (el && data[k] != null) el.value = data[k];
  });
  if (data.brand_color) {
    pColorInput.value = data.brand_color;
    pColorHex.value   = data.brand_color;
  }
}

profileBtn.addEventListener('click', async () => {
  const saved = profileOverride();
  if (saved) {
    populateProfileForm(saved);
  } else {
    try {
      const r = await fetch('/profile');
      if (r.ok) populateProfileForm(await r.json());
    } catch (_) {}
  }
  profileModal.showModal();
});

profileCancel.addEventListener('click', () => profileModal.close());

// Keep colour picker and hex input in sync
pColorInput.addEventListener('input', () => { pColorHex.value = pColorInput.value; });
pColorHex.addEventListener('input', () => {
  if (/^#[0-9a-fA-F]{6}$/.test(pColorHex.value)) pColorInput.value = pColorHex.value;
});

profileForm.addEventListener('submit', () => {
  const override = {};
  ['name', 'address_line1', 'address_line2', 'uid', 'bank_iban', 'bank_bic'].forEach(k => {
    const v = profileForm.elements[k]?.value.trim();
    if (v) override[k] = v;
  });
  if (pColorHex.value) override.brand_color = pColorHex.value;
  localStorage.setItem(PROFILE_KEY, JSON.stringify(override));
});

// ── Load API key from server ──────────────────────────────────────────────────

fetch('/config')
  .then(r => r.json())
  .then(d => { embeddedKey = d.api_key || ''; })
  .catch(() => { embeddedKey = ''; });

// ── Helpers ───────────────────────────────────────────────────────────────────

function t(key) { return T[lang][key] || key; }

function showError(msg) {
  errorMsg.textContent = msg;
  errorSection.hidden = false;
}
function clearError() { errorSection.hidden = true; }

function showStatus(msg, spinner = true) {
  statusMsg.textContent = msg;
  statusSection.querySelector('.spinner').hidden = !spinner;
  statusSection.hidden = false;
}
function hideStatus() { statusSection.hidden = true; }

function stopPolling() { clearInterval(pollTimer); pollTimer = null; }

function fmt(n) { return `€ ${n.toFixed(2)}`; }

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function reset() { stopPolling(); submitBtn.disabled = false; }

// ── Submit quote ──────────────────────────────────────────────────────────────

submitBtn.addEventListener('click', async () => {
  const raw = rawInput.value.trim();
  if (!raw) { showError(t('errNoInput')); return; }
  if (!embeddedKey) { showError(t('errNoKey')); return; }

  clearError();
  quoteSection.hidden = true;
  showStatus(t('statusCreating'));
  submitBtn.disabled = true;

  let data;
  try {
    const res = await fetch('/quote', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': embeddedKey },
      body: JSON.stringify({ raw_input: raw, language: lang }),
    });
    if (res.status === 401) { showError(t('err401')); reset(); return; }
    if (res.status === 429) { showError(t('err429')); reset(); return; }
    if (!res.ok) { showError(`${t('errServer')}: ${res.status}`); reset(); return; }
    data = await res.json();
  } catch (err) {
    showError(`${t('errNetwork')}: ${err.message}`);
    reset();
    return;
  }

  currentRequestId = data.request_id;
  pollTimer = setInterval(pollStatus, 2000);
});

// ── Poll status ───────────────────────────────────────────────────────────────

async function pollStatus() {
  let data;
  try {
    const res = await fetch(`/status/${currentRequestId}`, {
      headers: { 'X-API-Key': embeddedKey },
    });
    if (!res.ok) return;
    data = await res.json();
  } catch (_) { return; }

  if (data.status === 'queued' || data.status === 'running') {
    showStatus(t('statusCreating'));
  } else if (data.status === 'awaiting_approval') {
    stopPolling();
    hideStatus();
    renderQuote(data.quote);
    submitBtn.disabled = false;
  } else if (data.status === 'failed') {
    stopPolling();
    hideStatus();
    showError(data.error || t('errFailed'));
    reset();
  } else if (data.status === 'completed') {
    stopPolling();
    showStatus(t('statusCompleted'), false);
    submitBtn.disabled = false;
  }
}

// ── Render quote table ────────────────────────────────────────────────────────

function renderQuote(quote) {
  const tbody = document.getElementById('quoteBody');
  tbody.innerHTML = '';

  for (const item of quote.line_items) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${escHtml(item.description)}</td>
      <td class="num">${item.qty}</td>
      <td>${escHtml(item.unit || '—')}</td>
      <td class="num">${fmt(item.rate)}</td>
      <td class="num">${fmt(item.amount)}</td>
    `;
    tbody.appendChild(tr);
  }

  currentVatRate = Math.round(quote.vat_rate * 100);
  document.getElementById('netTotal').textContent   = fmt(quote.net_total);
  document.getElementById('vatLabel').textContent   =
    lang === 'de' ? `MwSt. ${currentVatRate} %` : `VAT ${currentVatRate}%`;
  document.getElementById('vatAmount').textContent  = fmt(quote.vat_amount);
  document.getElementById('grossTotal').textContent = fmt(quote.gross_total);

  const paymentEl = document.getElementById('paymentTerms');
  paymentEl.textContent = quote.payment_terms || '';
  paymentEl.hidden = !quote.payment_terms;

  const clientName = (quote.client && quote.client.name) ? quote.client.name : quote.client_ref;
  document.getElementById('clientName').textContent = clientName;

  freigebenBtn.disabled = false;
  quoteSection.hidden = false;
}

// ── Freigeben → PDF download ──────────────────────────────────────────────────

freigebenBtn.addEventListener('click', async () => {
  freigebenBtn.disabled = true;
  clearError();
  showStatus(t('statusInvoice'));

  let blob;
  try {
    const res = await fetch(`/invoice/${currentRequestId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': embeddedKey },
      body: JSON.stringify({ profile_override: profileOverride() }),
    });
    if (res.status === 401) { showError(t('err401')); hideStatus(); freigebenBtn.disabled = false; return; }
    if (!res.ok) { showError(`${t('errInvoice')}: ${res.status}`); hideStatus(); freigebenBtn.disabled = false; return; }
    blob = await res.blob();
  } catch (err) {
    showError(`${t('errNetwork')}: ${err.message}`);
    hideStatus();
    freigebenBtn.disabled = false;
    return;
  }

  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${t('filePrefix')}-${currentRequestId}.pdf`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  hideStatus();
  quoteSection.hidden = true;
  showStatus(t('statusDone'), false);

  currentRequestId = null;
  currentVatRate = null;
  submitBtn.disabled = false;
  freigebenBtn.disabled = false;
});

// ── Init ──────────────────────────────────────────────────────────────────────

applyLang();
