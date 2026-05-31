const PANELS = ['portfolio', 'scanner', 'risk', 'positions', 'factors', 'backtest', 'audit', 'reports', 'settings'];

const shortcuts = {
  'j': () => navigatePanel(1),
  'k': () => navigatePanel(-1),
  '/': (e) => { e.preventDefault(); document.querySelector('#global-search')?.focus(); },
  'Escape': () => closeModal(),
  'r': () => document.getElementById('refresh-btn')?.click(),
  '?': () => showShortcutsHelp(),
};

let activePanel = 'portfolio';

function navigatePanel(direction) {
  const idx = PANELS.indexOf(activePanel);
  const next = Math.max(0, Math.min(PANELS.length - 1, idx + direction));
  activePanel = PANELS[next];

  // Trigger navigation event
  document.dispatchEvent(new CustomEvent('navigate', { detail: { panel: activePanel } }));
}

function closeModal() {
  document.querySelectorAll('.modal.active').forEach(m => m.classList.remove('active'));
}

function showShortcutsHelp() {
  const help = document.getElementById('shortcuts-help');
  if (help) {
    help.style.display = help.style.display === 'none' ? 'block' : 'none';
  }
}

export function setActivePanel(panel) {
  activePanel = panel;
}

export function initShortcuts() {
  document.addEventListener('keydown', (e) => {
    // Ignore keypresses in input fields
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) {
      if (e.key === 'Escape') e.target.blur();
      return;
    }

    const handler = shortcuts[e.key];
    if (handler) {
      e.preventDefault();
      handler(e);
    }
  });
}
