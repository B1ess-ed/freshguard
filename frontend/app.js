import { initInventory } from './inventory.js';

const titles = {
  dashboard: '早上好，冰箱一切正常', inventory: '食材库存', insights: '环境与使用趋势',
  assistant: '智能体对话', settings: '系统设置',
};

function switchView(name) {
  document.querySelectorAll('.view').forEach(view => view.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === name));
  document.querySelector(`#${name}-view`)?.classList.add('active');
  document.querySelector('#page-title').textContent = titles[name];
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

document.querySelectorAll('.nav-item').forEach(button => button.addEventListener('click', () => switchView(button.dataset.view)));
document.querySelectorAll('[data-jump]').forEach(button => button.addEventListener('click', () => switchView(button.dataset.jump)));

let doorOpen = false;
const doorToggle = document.querySelector('#door-toggle');
doorToggle.addEventListener('click', () => {
  doorOpen = !doorOpen;
  doorToggle.classList.toggle('open', doorOpen);
  document.querySelector('#door-tag').textContent = doorOpen ? '已打开' : '已关闭';
  document.querySelector('#door-tag').classList.toggle('open', doorOpen);
  document.querySelector('#door-title').textContent = doorOpen ? '冰箱门已打开' : '安全关闭';
  document.querySelector('#door-detail').textContent = doorOpen ? '正在计时，超过 60 秒将提醒' : '刚刚关闭 · 持续 6 秒';
  showToast(doorOpen ? '模拟状态：冰箱门已打开' : '模拟状态：冰箱门已关闭');
});

document.querySelectorAll('.switch').forEach(button => button.addEventListener('click', () => {
  button.classList.toggle('on');
  showToast(button.classList.contains('on') ? '功能已开启' : '功能已关闭');
}));

function appendMessage(messages, text, type) {
  const message = document.createElement('div');
  message.className = `message ${type}`;
  message.textContent = text;
  messages.append(message);
  return message;
}

const conversationHistory = [];
let chatBusy = false;
let activeRequestId = null;
let activeChatController = null;
let stopRequested = false;

function createRequestId() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') {
    return window.crypto.randomUUID();
  }
  const randomPart = Math.random().toString(36).slice(2);
  return `${Date.now()}-${randomPart}`;
}

async function askAgent(text) {
  const clean = text.trim();
  if (!clean || chatBusy) return;
  const messages = document.querySelector('#messages');
  const submitButton = document.querySelector('#chat-submit');
  const stopButton = document.querySelector('#chat-stop');
  const requestId = createRequestId();
  const controller = new AbortController();
  chatBusy = true;
  activeRequestId = requestId;
  activeChatController = controller;
  stopRequested = false;
  submitButton.disabled = true;
  stopButton.hidden = false;
  stopButton.disabled = false;
  stopButton.textContent = '停止';
  appendMessage(messages, clean, 'user');
  const thinking = appendMessage(messages, '正在思考...', 'bot thinking');
  messages.scrollTop = messages.scrollHeight;
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: clean,
        history: conversationHistory.slice(-10),
        request_id: requestId,
      }),
      signal: controller.signal,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || '本地模型暂时无法回答');
    thinking.remove();
    appendMessage(messages, result.answer, 'bot');
    conversationHistory.push(
      { role: 'user', content: clean },
      { role: 'assistant', content: result.answer },
    );
    if (conversationHistory.length > 10) conversationHistory.splice(0, conversationHistory.length - 10);
  } catch (error) {
    const wasStopped = stopRequested || error.name === 'AbortError';
    thinking.textContent = error.message;
    if (wasStopped) thinking.textContent = '回答已停止';
    thinking.classList.remove('thinking');
    if (error.message !== '回答已停止') thinking.classList.add('error');
    if (wasStopped) thinking.classList.remove('error');
  } finally {
    chatBusy = false;
    activeRequestId = null;
    activeChatController = null;
    stopRequested = false;
    submitButton.disabled = false;
    stopButton.hidden = true;
    messages.scrollTop = messages.scrollHeight;
  }
}

document.querySelector('#chat-stop').addEventListener('click', async event => {
  if (!activeRequestId) return;
  const button = event.currentTarget;
  const requestId = activeRequestId;
  const controller = activeChatController;
  stopRequested = true;
  button.disabled = true;
  controller?.abort();
  button.textContent = '停止中';
  try {
    await fetch(`/api/chat/${encodeURIComponent(requestId)}/cancel`, { method: 'POST' });
  } catch {
    button.disabled = false;
    button.textContent = '重试停止';
  }
});

document.querySelector('#chat-form').addEventListener('submit', event => {
  event.preventDefault();
  const input = document.querySelector('#chat-input');
  askAgent(input.value);
  input.value = '';
});
document.querySelectorAll('.suggestion').forEach(button => button.addEventListener('click', () => askAgent(button.textContent)));

async function updateModelStatus() {
  const label = document.querySelector('#assistant-status');
  const dot = document.querySelector('#assistant-status-dot');
  try {
    const response = await fetch('/api/chat/status');
    const status = await response.json();
    const ready = status.online && status.installed;
    label.textContent = ready ? `${status.model} 本地模型在线` : `${status.model} 模型未就绪`;
    dot.classList.toggle('offline', !ready);
  } catch {
    label.textContent = '本地模型服务不可用';
    dot.classList.add('offline');
  }
}

let toastTimer;
export function showToast(message) {
  const toast = document.querySelector('#toast');
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 2200);
}

document.querySelector('#notification-button').addEventListener('click', () => showToast('库存提醒会随食材状态自动更新'));

function pathFromPoints(points, width, height) {
  if (!points.length) return '';
  if (points.length === 1) {
    const [x, y] = points[0];
    return `M${x},${y} L${Math.min(width, x + 1)},${y}`;
  }
  return points.map(([x, y], index) => `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
}

function seriesPoints(history, key, width, height, padding = 10) {
  const values = history.map(item => Number(item[key])).filter(Number.isFinite);
  if (!values.length) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(1, max - min);
  const denom = Math.max(1, history.length - 1);
  return history.map((item, index) => {
    const value = Number(item[key]);
    const x = history.length === 1 ? width : (index / denom) * width;
    const y = height - padding - ((value - min) / span) * (height - padding * 2);
    return [x, y];
  });
}

function updateMiniChart(history) {
  const recent = history.slice(-24);
  const points = seriesPoints(recent, 'temperature_c', 600, 120, 14);
  const line = document.querySelector('#mini-temp-line');
  const area = document.querySelector('#mini-temp-area');
  const point = document.querySelector('#mini-temp-point');
  const d = pathFromPoints(points, 600, 120);
  if (!d) return;
  line.setAttribute('d', d);
  area.setAttribute('d', `${d} L600,120 L0,120 Z`);
  const last = points[points.length - 1];
  point.setAttribute('cx', last[0].toFixed(1));
  point.setAttribute('cy', last[1].toFixed(1));
}

function updateHistoryChart(history) {
  const empty = document.querySelector('#environment-empty');
  empty.hidden = history.length > 1;
  document.querySelector('#history-temp-line').setAttribute(
    'd',
    pathFromPoints(seriesPoints(history, 'temperature_c', 600, 260, 18), 600, 260),
  );
  document.querySelector('#history-humidity-line').setAttribute(
    'd',
    pathFromPoints(seriesPoints(history, 'humidity', 600, 260, 18), 600, 260),
  );
}

function updateEnvironmentMetrics(history) {
  if (!history.length) return;
  const avg = (key) => history.reduce((sum, item) => sum + Number(item[key] || 0), 0) / history.length;
  document.querySelector('#avg-temperature').textContent = `${avg('temperature_c').toFixed(1)}°C`;
  document.querySelector('#avg-humidity').textContent = `${Math.round(avg('humidity'))}%`;
  document.querySelector('#humidity-status').textContent = `已缓存 ${history.length} 条数据`;
}

function clearEnvironmentReadings() {
  document.querySelector('#temperature').textContent = '--';
  document.querySelector('#humidity').textContent = '--';
  document.querySelector('#avg-temperature').textContent = '--°C';
  document.querySelector('#avg-humidity').textContent = '--%';
  document.querySelector('#humidity-status').textContent = '等待传感器数据';
}

async function updateEnvironment() {
  try {
    const response = await fetch('/api/environment');
    const data = await response.json();
    const history = Array.isArray(data.history) ? data.history : [];
    if (!response.ok) throw new Error(data.detail || 'environment request failed');
    if (data.last_error) console.warn('SHT environment read failed:', data.last_error);
    if (data.latest) {
      document.querySelector('#temperature').textContent = Number(data.latest.temperature_c).toFixed(1);
      document.querySelector('#humidity').textContent = Math.round(Number(data.latest.humidity));
    } else {
      clearEnvironmentReadings();
    }
    updateMiniChart(history);
    updateHistoryChart(history);
    updateEnvironmentMetrics(history);
    return Math.max(3000, Number(data.sensor?.interval_seconds || 10) * 1000);
  } catch {
    clearEnvironmentReadings();
    document.querySelector('#environment-empty').hidden = false;
    return 10000;
  }
}

async function scheduleEnvironmentUpdate() {
  const delay = await updateEnvironment();
  window.setTimeout(scheduleEnvironmentUpdate, delay);
}

initInventory(showToast);
updateModelStatus();
scheduleEnvironmentUpdate();
