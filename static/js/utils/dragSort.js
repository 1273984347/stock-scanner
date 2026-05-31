export function initDragSort(container, itemSelector) {
  let draggedItem = null;

  container.addEventListener('dragstart', (e) => {
    if (!e.target.matches(itemSelector)) return;
    draggedItem = e.target;
    e.target.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
  });

  container.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';

    const afterElement = getDragAfterElement(container, e.clientY);
    if (afterElement) {
      container.insertBefore(draggedItem, afterElement);
    } else {
      container.appendChild(draggedItem);
    }
  });

  container.addEventListener('dragend', (e) => {
    e.target.classList.remove('dragging');
    draggedItem = null;

    container.dispatchEvent(new CustomEvent('sort-change', {
      detail: { order: getChildrenOrder(container, itemSelector) },
    }));
  });
}

function getDragAfterElement(container, y) {
  const elements = [...container.querySelectorAll('[draggable]:not(.dragging)')];

  return elements.reduce((closest, child) => {
    const box = child.getBoundingClientRect();
    const offset = y - box.top - box.height / 2;
    if (offset < 0 && offset > closest.offset) {
      return { offset, element: child };
    }
    return closest;
  }, { offset: Number.NEGATIVE_INFINITY }).element;
}

function getChildrenOrder(container, selector) {
  return [...container.querySelectorAll(selector)].map(el => el.dataset.id);
}
