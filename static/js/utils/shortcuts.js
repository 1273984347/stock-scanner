import { helpCenter } from '/js/components/HelpCenter.js';

const PANELS = ['portfolio', 'scanner', 'risk', 'positions', 'factors', 'backtest', 'audit', 'reports', 'settings'];

const shortcuts = {
  'j': () => navigatePanel(1),
  'k': () => navigatePanel(-1),
  '/': (e) => { e.preventDefault(); document.querySelector('#global-search')?.focus(); },
  'Escape': () => closeModal(),
  'r': () => document.getElementById('refresh-btn')?.click(),
  '?': () => showShortcutsHelp(),
  'F1': (e) => { e.preventDefault(); helpCenter.show(); },
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
  document.querySelectorAll('.modal.active').forEach(m => {
    m.classList.remove('active');
    m.setAttribute('aria-hidden', 'true');
  });
  document.querySelectorAll('.modal-overlay.active').forEach(m => {
    m.classList.remove('active');
    m.setAttribute('aria-hidden', 'true');
  });
}

function showShortcutsHelp() {
  const help = document.getElementById('shortcuts-help');
  if (help) {
    help.style.display = help.style.display === 'none' ? 'block' : 'none';
  }
}

// 处理 Enter/Space 激活按钮和链接
function handleActivation(e) {
  const target = e.target;
  if (target.matches('.nav-item, .tf-btn, .ind-btn, .theme-option, button')) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      target.click();
    }
  }
}

// 处理 Arrow keys 在表格中导航
function handleTableNavigation(e) {
  const target = e.target;
  if (target.tagName === 'TD' || target.tagName === 'TH') {
    const tr = target.closest('tr');
    const table = target.closest('table');
    if (!tr || !table) return;

    const rows = Array.from(table.querySelectorAll('tr'));
    const cells = Array.from(tr.cells);
    const rowIdx = rows.indexOf(tr);
    const cellIdx = cells.indexOf(target);

    let nextCell = null;

    switch (e.key) {
      case 'ArrowUp':
        if (rowIdx > 0) {
          const prevRow = rows[rowIdx - 1];
          nextCell = prevRow.cells[Math.min(cellIdx, prevRow.cells.length - 1)];
        }
        break;
      case 'ArrowDown':
        if (rowIdx < rows.length - 1) {
          const nextRow = rows[rowIdx + 1];
          nextCell = nextRow.cells[Math.min(cellIdx, nextRow.cells.length - 1)];
        }
        break;
      case 'ArrowLeft':
        if (cellIdx > 0) nextCell = cells[cellIdx - 1];
        break;
      case 'ArrowRight':
        if (cellIdx < cells.length - 1) nextCell = cells[cellIdx + 1];
        break;
    }

    if (nextCell) {
      e.preventDefault();
      nextCell.focus();
    }
  }
}

export function setActivePanel(panel) {
  activePanel = panel;
}

export function initShortcuts() {
  // 添加 Skip link 功能
  const skipLink = document.querySelector('.skip-link');
  if (skipLink) {
    skipLink.addEventListener('click', (e) => {
      e.preventDefault();
      const mainContent = document.getElementById('main-content');
      if (mainContent) {
        mainContent.focus();
        mainContent.scrollIntoView();
      }
    });
  }

  // 为所有可交互元素添加 tabindex
  document.querySelectorAll('.nav-item, .tf-btn, .ind-btn, .theme-option').forEach(el => {
    if (!el.hasAttribute('tabindex')) {
      el.tabIndex = 0;
    }
  });

  // 为表格单元格添加 tabindex 以支持键盘导航
  document.querySelectorAll('td, th').forEach(cell => {
    cell.tabIndex = -1;
  });

  document.addEventListener('keydown', (e) => {
    // Ignore keypresses in input fields
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) {
      if (e.key === 'Escape') e.target.blur();
      return;
    }

    // 处理 Enter/Space 激活
    handleActivation(e);

    // 处理表格导航
    handleTableNavigation(e);

    // 处理快捷键
    const handler = shortcuts[e.key];
    if (handler) {
      e.preventDefault();
      handler(e);
    }
  });

  // 焦点可见样式管理
  document.addEventListener('mousedown', () => {
    document.body.classList.add('using-mouse');
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') {
      document.body.classList.remove('using-mouse');
    }
  });
}
