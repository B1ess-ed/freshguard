import { ingredientApi } from './api.js';

const STATUS_META = {
  fresh: { label: '状态良好' },
  soon: { label: '即将过期' },
  expired: { label: '已过期' },
  low: { label: '余量不足' },
};

const CATEGORY_META = {
  蔬菜: ['🥬', '#eef6e8'], 水果: ['🍎', '#f8ebe8'], 肉类: ['🥩', '#fae9e5'],
  海鲜: ['🐟', '#e7f0f5'], 乳制品: ['🥛', '#edf5f8'], 蛋类: ['🥚', '#faf3df'],
  饮品: ['🧃', '#fff0dc'], 主食: ['🍞', '#fcf3d9'], 调味品: ['🧂', '#f0f1fa'],
  其他: ['🥣', '#f1f4ef'],
};

const state = { items: [], allItems: [], summary: null, status: 'all', category: '' };
let notify = () => {};

const elements = {};

export async function initInventory(showToast) {
  notify = showToast;
  Object.assign(elements, {
    strip: document.querySelector('#inventory-strip'),
    grid: document.querySelector('#inventory-grid'),
    badge: document.querySelector('#inventory-badge'),
    summary: document.querySelector('#inventory-summary'),
    categoryFilter: document.querySelector('#category-filter'),
    dialog: document.querySelector('#ingredient-dialog'),
    form: document.querySelector('#ingredient-form'),
    dialogTitle: document.querySelector('#dialog-title'),
    formMessage: document.querySelector('#form-message'),
    deleteButton: document.querySelector('#delete-item'),
    saveButton: document.querySelector('#save-item'),
    confirmModal: document.querySelector('#confirm-modal'),
  });

  bindEvents();
  await refreshInventory();
}

function bindEvents() {
  document.querySelector('#add-item').addEventListener('click', () => openForm());
  document.querySelectorAll('.dialog-close, .dialog-cancel').forEach(button =>
    button.addEventListener('click', closeForm));
  elements.dialog.addEventListener('click', event => {
    if (event.target === elements.dialog) closeForm();
  });
  elements.form.addEventListener('submit', submitForm);
  elements.deleteButton.addEventListener('click', openDeleteConfirmation);
  document.querySelector('#cancel-delete').addEventListener('click', closeDeleteConfirmation);
  document.querySelector('.confirm-backdrop').addEventListener('click', closeDeleteConfirmation);
  document.querySelector('#confirm-delete').addEventListener('click', deleteIngredient);

  document.querySelectorAll('.filter').forEach(button => button.addEventListener('click', async () => {
    document.querySelectorAll('.filter').forEach(item => item.classList.remove('active'));
    button.classList.add('active');
    state.status = button.dataset.filter;
    await loadFilteredItems();
  }));
  elements.categoryFilter.addEventListener('change', async event => {
    state.category = event.target.value;
    await loadFilteredItems();
  });
}

async function refreshInventory(message = '') {
  renderLoading();
  try {
    const [allItems, summary] = await Promise.all([ingredientApi.list(), ingredientApi.summary()]);
    state.allItems = allItems;
    state.summary = summary;
    syncCategories();
    syncSummary();
    await loadFilteredItems(false);
    renderStrip();
    if (message) notify(message);
  } catch (error) {
    renderError(error.message);
  }
}

async function loadFilteredItems(showLoading = true) {
  if (showLoading) renderLoading();
  try {
    state.items = await ingredientApi.list({ status: state.status, category: state.category });
    renderGrid();
  } catch (error) {
    renderError(error.message);
  }
}

function syncSummary() {
  const { total, soon, expired, low } = state.summary;
  const attention = soon + expired + low;
  elements.badge.textContent = total;
  elements.summary.textContent = `${total} 件食材，${attention} 件需要关注`;
}

function syncCategories() {
  const categories = [...new Set(state.allItems.map(item => item.category))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
  const selected = state.category;
  elements.categoryFilter.replaceChildren(new Option('全部分类', ''));
  categories.forEach(category => elements.categoryFilter.add(new Option(category, category)));
  elements.categoryFilter.value = categories.includes(selected) ? selected : '';
  state.category = elements.categoryFilter.value;
}

function renderGrid() {
  elements.grid.replaceChildren();
  if (!state.items.length) {
    elements.grid.append(createStateMessage('没有符合条件的食材', '换一个筛选条件，或添加新的食材。'));
    return;
  }
  state.items.forEach(item => elements.grid.append(createFoodCard(item, true)));
}

function renderStrip() {
  elements.strip.replaceChildren();
  if (!state.allItems.length) {
    elements.strip.append(createStateMessage('冰箱里还没有食材', '前往食材页面添加第一项库存。'));
    return;
  }
  state.allItems.slice(0, 4).forEach(item => elements.strip.append(createFoodCard(item, false)));
}

function createFoodCard(item, editable) {
  const card = document.createElement('article');
  card.className = `food-card${editable ? ' editable' : ''}`;
  card.dataset.status = item.status;
  const top = document.createElement('div');
  top.className = 'food-top';
  const icon = document.createElement('span');
  icon.className = 'food-icon';
  const [emoji, background] = CATEGORY_META[item.category] || CATEGORY_META.其他;
  icon.style.setProperty('--food-bg', background);
  icon.textContent = emoji;
  const status = document.createElement('span');
  status.className = `food-status ${item.status}`;
  status.textContent = STATUS_META[item.status]?.label || item.status;
  top.append(icon, status);

  const copy = document.createElement('div');
  const heading = document.createElement('h3');
  heading.textContent = item.name;
  const detail = document.createElement('p');
  detail.textContent = formatDetail(item);
  copy.append(heading, detail);
  card.append(top, copy);

  if (editable) {
    card.tabIndex = 0;
    card.setAttribute('role', 'button');
    card.setAttribute('aria-label', `编辑${item.name}`);
    card.addEventListener('click', () => openForm(item));
    card.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openForm(item);
      }
    });
    const editHint = document.createElement('span');
    editHint.className = 'edit-hint';
    editHint.textContent = '编辑';
    card.append(editHint);
  }
  return card;
}

function formatDetail(item) {
  const quantity = Number.isInteger(item.quantity)
    ? item.quantity
    : Number(item.quantity.toFixed(2));
  const parts = [`${quantity} ${item.unit}`];
  if (item.expiration_date) {
    const date = new Date(`${item.expiration_date}T00:00:00`);
    parts.push(`保质期 ${date.getMonth() + 1}月${date.getDate()}日`);
  }
  return parts.join(' · ');
}

function renderLoading() {
  elements.grid.replaceChildren(createStateMessage('正在读取库存...', ''));
}

function renderError(message) {
  elements.grid.replaceChildren(createStateMessage('库存加载失败', message, true));
}

function createStateMessage(title, detail, isError = false) {
  const stateMessage = document.createElement('div');
  stateMessage.className = `inventory-state${isError ? ' error' : ''}`;
  const heading = document.createElement('strong');
  heading.textContent = title;
  const copy = document.createElement('span');
  copy.textContent = detail;
  stateMessage.append(heading, copy);
  return stateMessage;
}

function openForm(item = null) {
  elements.form.reset();
  elements.formMessage.textContent = '';
  document.querySelector('#ingredient-id').value = item?.id || '';
  elements.dialogTitle.textContent = item ? '编辑食材' : '添加食材';
  elements.deleteButton.hidden = !item;
  if (item) {
    document.querySelector('#ingredient-name').value = item.name;
    document.querySelector('#ingredient-category').value = item.category;
    document.querySelector('#ingredient-quantity').value = item.quantity;
    document.querySelector('#ingredient-unit').value = item.unit;
    document.querySelector('#ingredient-expiration').value = item.expiration_date || '';
    document.querySelector('#ingredient-threshold').value = item.low_stock_threshold ?? '';
    document.querySelector('#ingredient-notes').value = item.notes || '';
  }
  elements.dialog.showModal();
  requestAnimationFrame(() => document.querySelector('#ingredient-name').focus());
}

function closeForm() {
  if (!elements.saveButton.disabled) elements.dialog.close();
}

function formPayload() {
  const threshold = document.querySelector('#ingredient-threshold').value;
  const expiry = document.querySelector('#ingredient-expiration').value;
  return {
    name: document.querySelector('#ingredient-name').value.trim(),
    category: document.querySelector('#ingredient-category').value.trim(),
    quantity: Number(document.querySelector('#ingredient-quantity').value),
    unit: document.querySelector('#ingredient-unit').value,
    expiration_date: expiry || null,
    notes: document.querySelector('#ingredient-notes').value.trim(),
    low_stock_threshold: threshold === '' ? null : Number(threshold),
  };
}

async function submitForm(event) {
  event.preventDefault();
  if (!elements.form.reportValidity()) return;
  const id = document.querySelector('#ingredient-id').value;
  setSubmitting(true);
  try {
    if (id) await ingredientApi.update(id, formPayload());
    else await ingredientApi.create(formPayload());
    elements.dialog.close();
    await refreshInventory(id ? '食材信息已更新' : '食材已添加');
  } catch (error) {
    elements.formMessage.textContent = error.message;
  } finally {
    setSubmitting(false);
  }
}

function setSubmitting(active) {
  elements.saveButton.disabled = active;
  elements.saveButton.textContent = active ? '保存中...' : '保存';
}

function openDeleteConfirmation() {
  const name = document.querySelector('#ingredient-name').value.trim();
  document.querySelector('#confirm-copy').textContent = `“${name}”删除后无法恢复。`;
  elements.confirmModal.hidden = false;
}

function closeDeleteConfirmation() {
  elements.confirmModal.hidden = true;
}

async function deleteIngredient() {
  const id = document.querySelector('#ingredient-id').value;
  const button = document.querySelector('#confirm-delete');
  button.disabled = true;
  button.textContent = '删除中...';
  try {
    await ingredientApi.remove(id);
    closeDeleteConfirmation();
    elements.dialog.close();
    await refreshInventory('食材已删除');
  } catch (error) {
    closeDeleteConfirmation();
    elements.formMessage.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = '确认删除';
  }
}
