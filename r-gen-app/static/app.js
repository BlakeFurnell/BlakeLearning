/**
 * app.js — R Codegen frontend
 *
 * All API calls go through const API_URL so the app works on any host
 * without configuration.
 */

const API_URL = window.location.origin;

// ── State ─────────────────────────────────────────────────────────────────
let sessionId        = null;   // set after a successful /upload
let sessionCreatedAt = null;   // Date.now() timestamp of the last upload
let isStreaming      = false;  // guard against concurrent generations

// Show a session-expiry warning after 45 minutes of holding a session.
const SESSION_WARN_MS = 45 * 60 * 1000;

// ── DOM refs ──────────────────────────────────────────────────────────────
const uploadZone     = document.getElementById('upload-zone');
const fileInput      = document.getElementById('file-input');
const uploadIdle     = document.getElementById('upload-idle');
const uploadSpinner  = document.getElementById('upload-spinner');
const uploadError    = document.getElementById('upload-error');
const fileSummary    = document.getElementById('file-summary');
const fileNameLabel  = document.getElementById('file-name-label');
const fileShapeLabel = document.getElementById('file-shape-label');
const columnTbody    = document.getElementById('column-tbody');
const clearBtn       = document.getElementById('clear-btn');

const questionInput  = document.getElementById('question-input');
const generateBtn    = document.getElementById('generate-btn');
const noFileWarning  = document.getElementById('no-file-warning');
const codeOutput     = document.getElementById('code-output');
const genStatus      = document.getElementById('gen-status');
const copyBtn        = document.getElementById('copy-btn');
const clearCodeBtn   = document.getElementById('clear-code-btn');

const healthDot      = document.getElementById('health-dot');
const healthLabel    = document.getElementById('health-label');
const sessionBanner  = document.getElementById('session-banner');

// ── Health check ──────────────────────────────────────────────────────────

async function checkHealth() {
  try {
    const res = await fetch(`${API_URL}/health`);
    if (res.ok) {
      setHealth('ok');
    } else {
      setHealth('error');
    }
  } catch {
    setHealth('error');
  }
}

function setHealth(state) {
  healthDot.className = 'dot';
  if (state === 'ok') {
    healthDot.classList.add('dot--ok');
    healthLabel.textContent = 'Ready';
  } else {
    healthDot.classList.add('dot--error');
    healthLabel.textContent = 'Unreachable';
  }
}

// Poll once on load, then every 30 s.
checkHealth();
setInterval(checkHealth, 30_000);

// ── Upload zone interactions ───────────────────────────────────────────────

// Click anywhere in the zone triggers the hidden file input.
uploadZone.addEventListener('click', () => fileInput.click());

// Keyboard accessibility — Enter / Space activate the zone.
uploadZone.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    fileInput.click();
  }
});

// Drag-and-drop visual feedback.
uploadZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  uploadZone.classList.add('drag-over');
});
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-over'));
uploadZone.addEventListener('drop', (e) => {
  e.preventDefault();
  uploadZone.classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (file) handleFile(file);
});

fileInput.addEventListener('change', () => {
  if (fileInput.files[0]) handleFile(fileInput.files[0]);
  // Reset so re-selecting the same file triggers 'change' again.
  fileInput.value = '';
});

// ── File handling ─────────────────────────────────────────────────────────

const ACCEPTED = new Set(['.csv', '.xlsx', '.xls']);

function getExt(filename) {
  const m = filename.match(/\.[^.]+$/);
  return m ? m[0].toLowerCase() : '';
}

async function handleFile(file) {
  const ext = getExt(file.name);
  if (!ACCEPTED.has(ext)) {
    showUploadError(`Unsupported type "${ext}". Use .csv, .xlsx, or .xls.`);
    return;
  }

  // Clean up any existing session before uploading a new file.
  await clearSession();

  showUploadSpinner();

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch(`${API_URL}/upload`, { method: 'POST', body: formData });
    const data = await res.json();

    if (!res.ok) {
      showUploadError(data.detail || `Server error ${res.status}`);
      return;
    }

    sessionId = data.session_id;
    sessionCreatedAt = Date.now();
    scheduleSessionWarning();
    renderFileSummary(data.summary);
  } catch (err) {
    showUploadError(`Upload failed: ${err.message}`);
  }
}

// ── Session management ────────────────────────────────────────────────────

let _sessionWarnTimer = null;

async function clearSession() {
  if (!sessionId) return;
  try {
    await fetch(`${API_URL}/session/${sessionId}`, { method: 'DELETE' });
  } catch {
    // Best-effort cleanup — not fatal if it fails.
  }
  sessionId = null;
  sessionCreatedAt = null;
  clearTimeout(_sessionWarnTimer);
  _sessionWarnTimer = null;
  hideSessionBanner();
}

// Schedule the session-age warning banner to appear at SESSION_WARN_MS.
function scheduleSessionWarning() {
  clearTimeout(_sessionWarnTimer);
  const elapsed = Date.now() - sessionCreatedAt;
  const delay   = Math.max(0, SESSION_WARN_MS - elapsed);
  _sessionWarnTimer = setTimeout(showSessionBanner, delay);
}

function showSessionBanner() {
  if (!sessionId) return; // session already cleared
  sessionBanner.hidden = false;
}

function hideSessionBanner() {
  sessionBanner.hidden = true;
}

// ── Upload zone state helpers ─────────────────────────────────────────────

function showUploadSpinner() {
  uploadError.hidden = true;
  fileSummary.hidden = true;
  uploadIdle.hidden  = true;
  uploadSpinner.hidden = false;
}

function showUploadError(msg) {
  uploadSpinner.hidden = true;
  uploadIdle.hidden    = false;
  fileSummary.hidden   = true;
  uploadError.textContent = msg;
  uploadError.hidden = false;
}

function showUploadIdle() {
  uploadSpinner.hidden = true;
  fileSummary.hidden   = true;
  uploadError.hidden   = true;
  uploadIdle.hidden    = false;
}

// ── Render file summary ───────────────────────────────────────────────────

function renderFileSummary(summary) {
  uploadSpinner.hidden = true;
  uploadError.hidden   = true;
  uploadIdle.hidden    = true;

  fileNameLabel.textContent = summary.filename;
  const [rows, cols] = summary.shape;
  fileShapeLabel.textContent =
    `${rows.toLocaleString()} rows × ${cols.toLocaleString()} cols`;

  columnTbody.innerHTML = '';
  for (const col of summary.columns) {
    const nullPct = col.null_pct * 100;
    const nullClass =
      nullPct > 20 ? 'col-null col-null--high' :
      nullPct > 5  ? 'col-null col-null--mid'  :
                     'col-null col-null--low';

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="col-name">${escapeHtml(col.name)}</td>
      <td class="col-dtype">${escapeHtml(col.dtype)}</td>
      <td class="${nullClass}">${nullPct.toFixed(1)}%</td>
    `;
    columnTbody.appendChild(tr);
  }

  fileSummary.hidden = false;
}

// ── Clear button ──────────────────────────────────────────────────────────

clearBtn.addEventListener('click', async () => {
  await clearSession();
  showUploadIdle();
  resetCodeOutput();
});

// ── Code generation ───────────────────────────────────────────────────────

generateBtn.addEventListener('click', startGenerate);

// Cmd/Ctrl + Enter submits from anywhere on the page.
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
    e.preventDefault();
    startGenerate();
  }
});

async function startGenerate() {
  if (isStreaming) return;

  noFileWarning.hidden = true;

  console.log('[generate] clicked — sessionId:', sessionId);

  if (!sessionId) {
    noFileWarning.hidden = false;
    console.warn('[generate] aborted — no session');
    return;
  }

  const question = questionInput.value.trim();
  if (!question) {
    questionInput.focus();
    return;
  }

  isStreaming = true;
  generateBtn.disabled = true;
  copyBtn.hidden = true;
  setGenStatus('streaming', '● streaming…');
  codeOutput.classList.add('cursor-blink');

  // Clear previous output.
  codeOutput.textContent = '';

  try {
    console.log(`[generate] POST ${API_URL}/generate`, { session_id: sessionId, question });
    const res = await fetch(`${API_URL}/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, question }),
    });

    console.log('[generate] response status:', res.status);

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }

    // Read the streaming response chunk by chunk.
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let raw = '';
    let chunkCount = 0;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunkCount++;
      const text = decoder.decode(value, { stream: true });
      if (chunkCount === 1) console.log('[generate] first chunk:', JSON.stringify(text.slice(0, 80)));
      raw += text;
      // Render syntax-highlighted HTML into the pre element.
      codeOutput.innerHTML = highlightR(raw);
      // Auto-scroll to follow output as it streams.
      codeOutput.scrollTop = codeOutput.scrollHeight;
    }

    // Final flush.
    raw += decoder.decode();
    codeOutput.innerHTML = highlightR(raw);
    codeOutput.scrollTop = codeOutput.scrollHeight;
    console.log(`[generate] stream complete — ${chunkCount} chunks, ${raw.length} chars total`);

    // Detect the LLM's out-of-scope sentinel comment and swap the code output
    // for a plain-language notice so it doesn't look like broken codegen.
    if (raw.includes('# This request is outside the scope')) {
      codeOutput.textContent = '';
      codeOutput.classList.remove('cursor-blink');
      showOutOfScopeNotice();
      setGenStatus('', '');
    } else {
      setGenStatus('done', '✓ done');
      copyBtn.hidden = false;
    }

  } catch (err) {
    setGenStatus('error', '✗ error');
    codeOutput.innerHTML += `\n# ERROR: ${escapeHtml(err.message)}\n`;
  } finally {
    isStreaming = false;
    generateBtn.disabled = false;
    codeOutput.classList.remove('cursor-blink');
  }
}

// ── Gen status label ──────────────────────────────────────────────────────

function setGenStatus(state, text) {
  genStatus.textContent = text;
  genStatus.className = 'gen-status';
  if (state) genStatus.classList.add(`gen-status--${state}`);
}

// ── Copy button ───────────────────────────────────────────────────────────

copyBtn.addEventListener('click', async () => {
  const text = codeOutput.textContent;
  try {
    await navigator.clipboard.writeText(text);
    copyBtn.textContent = 'Copied!';
    copyBtn.classList.add('btn-copied');
    setTimeout(() => {
      copyBtn.textContent = 'Copy';
      copyBtn.classList.remove('btn-copied');
    }, 2000);
  } catch {
    copyBtn.textContent = 'Failed';
    setTimeout(() => { copyBtn.textContent = 'Copy'; }, 2000);
  }
});

// ── Clear code button ─────────────────────────────────────────────────────

clearCodeBtn.addEventListener('click', resetCodeOutput);

function resetCodeOutput() {
  codeOutput.textContent = '';
  codeOutput.classList.remove('cursor-blink');
  copyBtn.hidden = true;
  setGenStatus('', '');
  hideOutOfScopeNotice();
}

// ── Out-of-scope notice ───────────────────────────────────────────────────

const outOfScopeNotice = document.getElementById('out-of-scope-notice');

function showOutOfScopeNotice() {
  if (outOfScopeNotice) outOfScopeNotice.hidden = false;
}

function hideOutOfScopeNotice() {
  if (outOfScopeNotice) outOfScopeNotice.hidden = true;
}

// ── R syntax highlighter ──────────────────────────────────────────────────
//
// Lightweight regex-based tokeniser — good enough for the R idioms this tool
// generates (tidyverse, base R, comments).  Runs on the entire accumulated
// text on each chunk so highlighting is always consistent.
//
// Order matters: comments first (they swallow everything to EOL), then
// strings, then keywords/specials that could otherwise match inside words.

const R_RULES = [
  // Comments: # … to end of line
  { cls: 'hl-comment',  re: /#[^\n]*/g },
  // Strings: "…" or '…' (non-greedy, no multiline)
  { cls: 'hl-string',   re: /("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g },
  // Pipe operators: |> %>% %>>%
  { cls: 'hl-pipe',     re: /(\|>|%>%|%>>%)/g },
  // Assignment & other operators: <- -> = == != <= >= + - * / ^ : ~
  { cls: 'hl-operator', re: /(<-|->|==|!=|<=|>=|[=+\-*\/^:~])/g },
  // Special constants
  { cls: 'hl-special',  re: /\b(TRUE|FALSE|NULL|NA|NA_integer_|NA_real_|NA_complex_|NA_character_|Inf|NaN)\b/g },
  // Keywords
  { cls: 'hl-keyword',  re: /\b(if|else|for|while|repeat|break|next|return|function|in|library|require|source|stop|warning|message|invisible)\b/g },
  // Numbers: integers, floats, scientific, hex
  { cls: 'hl-number',   re: /\b(0x[0-9a-fA-F]+|\d+\.?\d*([eE][+-]?\d+)?L?)\b/g },
  // Function calls: word followed by (
  { cls: 'hl-function', re: /\b([A-Za-z_.][A-Za-z0-9_.]*)(?=\s*\()/g },
];

function highlightR(code) {
  // Strategy: scan the raw text and build an array of {start, end, cls}
  // spans, then render them in order without overlapping.

  const spans = [];

  for (const rule of R_RULES) {
    rule.re.lastIndex = 0;
    let m;
    while ((m = rule.re.exec(code)) !== null) {
      spans.push({ start: m.index, end: m.index + m[0].length, cls: rule.cls });
    }
  }

  // Sort by start position; on ties prefer the rule listed first (longer match wins via first rule).
  spans.sort((a, b) => a.start - b.start || a.end - b.end);

  // Walk through spans, skip any that overlap with an already-committed span.
  let out = '';
  let cursor = 0;
  let committed = -1; // end of last committed span

  for (const span of spans) {
    if (span.start < committed) continue; // overlap — skip
    // Text before this span.
    out += escapeHtml(code.slice(cursor, span.start));
    // The highlighted token.
    out += `<span class="${span.cls}">${escapeHtml(code.slice(span.start, span.end))}</span>`;
    cursor    = span.end;
    committed = span.end;
  }

  // Remaining unhighlighted tail.
  out += escapeHtml(code.slice(cursor));
  return out;
}

// ── Utilities ─────────────────────────────────────────────────────────────

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
