/**
 * static/js/main.js
 * GovCon Scout — client-side logic
 *
 * Sections:
 *   1. Loading overlay (shown on search form submit when AI analysis is checked)
 *   2. Deadline urgency highlighting
 *   3. Filter bar (fit label / recommended action)
 *   4. Copy-UEI-to-clipboard button
 */

document.addEventListener('DOMContentLoaded', () => {
  initLoadingOverlay();
  highlightDeadlines();
  initFilterBar();
  initCopyUEI();
});

/* ═══════════════════════════════════════════════════════════════
   1. LOADING OVERLAY
   Shows a full-screen spinner when a search form is submitted.
   If "Run AI Analysis" is checked, cycles through progress steps
   because Ollama can take 30-90 s per opportunity.
   ═══════════════════════════════════════════════════════════════ */

function initLoadingOverlay() {
  // Inject the overlay HTML once into the page
  const overlay = document.createElement('div');
  overlay.className = 'loading-overlay';
  overlay.id = 'loading-overlay';
  overlay.innerHTML = `
    <div class="spinner"></div>
    <div>
      <div class="loading-title" id="loading-title">Searching SAM.gov...</div>
      <div class="loading-msg" id="loading-msg">
        Querying contract opportunities matched to your NAICS codes.
      </div>
    </div>
    <ul class="loading-steps" id="loading-steps">
      <li id="step-entity">Fetching company profile</li>
      <li id="step-search">Searching SAM.gov opportunities</li>
      <li id="step-ai">Running AI fit analysis</li>
      <li id="step-done">Preparing results</li>
    </ul>
  `;
  document.body.appendChild(overlay);

  // Watch every form that targets /search
  document.querySelectorAll('form[action$="/search"]').forEach(form => {
    form.addEventListener('submit', () => {
      const hasAnalysis = form.querySelector('[name="include_analysis"]')?.checked;
      showLoadingOverlay(hasAnalysis);
    });
  });

  // Watch the /lookup form too (faster — no spinner steps needed)
  document.querySelectorAll('form[action$="/lookup"]').forEach(form => {
    form.addEventListener('submit', () => showLoadingOverlay(false));
  });
}

function showLoadingOverlay(withAnalysis) {
  const overlay = document.getElementById('loading-overlay');
  if (!overlay) return;
  overlay.classList.add('visible');

  if (!withAnalysis) {
    document.getElementById('loading-title').textContent = 'Searching SAM.gov...';
    document.getElementById('loading-msg').textContent =
      'Querying contract opportunities matched to your NAICS codes.';
    document.getElementById('loading-steps').style.display = 'none';
    return;
  }

  // AI analysis path — cycle through steps with timers
  document.getElementById('loading-steps').style.display = 'flex';
  document.getElementById('loading-title').textContent = 'Analyzing Opportunities...';
  document.getElementById('loading-msg').textContent =
    'The AI is scoring each contract for fit. This can take a minute or two.';

  const steps = [
    { id: 'step-entity', delay: 0,    label: 'Fetching company profile' },
    { id: 'step-search', delay: 2000, label: 'Searching SAM.gov opportunities' },
    { id: 'step-ai',     delay: 5000, label: 'Running AI fit analysis (may take a while...)' },
    { id: 'step-done',   delay: -1 }, // activated only when the page is actually done
  ];

  steps.forEach(({ id, delay, label }) => {
    if (delay < 0) return; // skip the final step — it fires naturally on navigation
    setTimeout(() => {
      // Mark previous step done
      const allSteps = document.querySelectorAll('.loading-steps li');
      allSteps.forEach(li => { if (li.classList.contains('active')) li.classList.replace('active', 'done'); });

      const el = document.getElementById(id);
      if (el) {
        el.classList.add('active');
        if (label) el.textContent = '▶ ' + label;
      }
    }, delay);
  });
}

/* ═══════════════════════════════════════════════════════════════
   2. DEADLINE URGENCY HIGHLIGHTING
   Parses deadline strings on .deadline-flag elements.
   Marks them red and adds ⚠ if the deadline is within 7 days.
   Also adds .deadline-urgent-card to the parent card.
   ═══════════════════════════════════════════════════════════════ */

function highlightDeadlines() {
  const now = Date.now();
  const sevenDays = 7 * 24 * 60 * 60 * 1000;

  document.querySelectorAll('.deadline-flag').forEach(el => {
    const raw = el.dataset.deadline;
    if (!raw) return;

    // SAM.gov often returns formats like "2025-06-01T00:00:00-05:00" or "06/01/2025"
    const deadline = new Date(raw);
    if (isNaN(deadline)) return;

    const diff = deadline - now;

    if (diff < 0) {
      // Past deadline
      el.classList.add('deadline-urgent');
      el.textContent = `Due: ${formatDate(deadline)} — CLOSED`;
      el.closest('.opp-card')?.classList.add('deadline-urgent-card');
    } else if (diff <= sevenDays) {
      el.classList.add('deadline-urgent');
      const daysLeft = Math.ceil(diff / (24 * 60 * 60 * 1000));
      el.textContent = `Due: ${formatDate(deadline)} ⚠ ${daysLeft}d left`;
      el.closest('.opp-card')?.classList.add('deadline-urgent-card');
    } else {
      el.textContent = `Due: ${formatDate(deadline)}`;
    }
  });
}

function formatDate(date) {
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

/* ═══════════════════════════════════════════════════════════════
   3. FILTER BAR
   Filters .opp-card elements by data-fit-label or data-action
   without a page reload. Active filter button is highlighted.
   Supports both fit-label filters and action filters.
   ═══════════════════════════════════════════════════════════════ */

function initFilterBar() {
  const filterBtns = document.querySelectorAll('.filter-btn');
  if (!filterBtns.length) return;

  const cards = document.querySelectorAll('.opp-card');

  filterBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      // Toggle active state — clicking the already-active filter resets to All
      const wasActive = btn.classList.contains('active');

      filterBtns.forEach(b => b.classList.remove('active'));

      if (wasActive && btn.dataset.filter !== 'all') {
        // Reset to show all
        cards.forEach(c => c.classList.remove('hidden'));
        document.querySelector('[data-filter="all"]')?.classList.add('active');
        return;
      }

      btn.classList.add('active');

      const fitFilter    = btn.dataset.filter;    // e.g. "Strong Fit", "all"
      const actionFilter = btn.dataset.filterAction; // e.g. "Apply"

      cards.forEach(card => {
        if (!fitFilter && !actionFilter || fitFilter === 'all') {
          card.classList.remove('hidden');
          return;
        }

        const label  = card.dataset.fitLabel || '';
        const action = card.dataset.action   || '';

        let show = true;
        if (fitFilter && fitFilter !== 'all') show = (label === fitFilter);
        if (actionFilter)                     show = (action === actionFilter);

        card.classList.toggle('hidden', !show);
      });

      updateVisibleCount();
    });
  });
}

function updateVisibleCount() {
  const total   = document.querySelectorAll('.opp-card').length;
  const visible = document.querySelectorAll('.opp-card:not(.hidden)').length;
  const statEl  = document.querySelector('.stat-value'); // first stat = count
  if (statEl && visible !== total) {
    statEl.textContent = `${visible} / ${total}`;
  } else if (statEl) {
    statEl.textContent = total;
  }
}

/* ═══════════════════════════════════════════════════════════════
   4. COPY CAGE CODE TO CLIPBOARD
   Looks for elements with data-copy-cage attribute and adds a
   small inline button that copies the CAGE code on click.
   Usage in template: <span data-copy-cage="{{ profile.cage_code }}">{{ profile.cage_code }}</span>
   ═══════════════════════════════════════════════════════════════ */

function initCopyUEI() {
  document.querySelectorAll('[data-copy-cage]').forEach(el => {
    const uei = el.dataset.copyCage;
    if (!uei) return;

    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.title = 'Copy CAGE code';
    btn.textContent = '⧉ Copy';
    btn.type = 'button';

    btn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(uei);
        btn.textContent = '✓ Copied';
        btn.classList.add('copied');
        setTimeout(() => {
          btn.textContent = '⧉ Copy';
          btn.classList.remove('copied');
        }, 2000);
      } catch {
        // Fallback for non-secure contexts — let the user copy manually
        window.prompt('Copy this UEI:', uei);
      }
    });

    el.insertAdjacentElement('afterend', btn);
  });
}
