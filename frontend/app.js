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

async function askAgent(text) {
  const clean = text.trim();
  if (!clean || chatBusy) return;
  const messages = document.querySelector('#messages');
  const submitButton = document.querySelector('#chat-form button');
  chatBusy = true;
  submitButton.disabled = true;
  appendMessage(messages, clean, 'user');
  const thinking = appendMessage(messages, '正在思考...', 'bot thinking');
  messages.scrollTop = messages.scrollHeight;
  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: clean, history: conversationHistory.slice(-10) }),
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
    thinking.textContent = error.message;
    thinking.classList.remove('thinking');
    thinking.classList.add('error');
  } finally {
    chatBusy = false;
    submitButton.disabled = false;
    messages.scrollTop = messages.scrollHeight;
  }
}

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

setInterval(() => {
  const temp = 4.2 + (Math.random() - .5) * .12;
  const humidity = 62 + Math.round((Math.random() - .5) * 2);
  document.querySelector('#temperature').textContent = temp.toFixed(1);
  document.querySelector('#humidity').textContent = humidity;
}, 5000);

initInventory(showToast);
updateModelStatus();
