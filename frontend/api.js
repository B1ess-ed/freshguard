const API_BASE = '/api/ingredients';

async function request(path = '', options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 204) return null;
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const validation = Array.isArray(body?.detail)
      ? body.detail.map(item => item.msg).join('；')
      : body?.detail;
    throw new Error(validation || '请求失败，请稍后重试');
  }
  return body;
}

export const ingredientApi = {
  list: ({ status = '', category = '' } = {}) => {
    const params = new URLSearchParams();
    if (status && status !== 'all') params.set('status', status);
    if (category) params.set('category', category);
    const query = params.toString();
    return request(query ? `?${query}` : '');
  },
  summary: () => request('/summary'),
  create: payload => request('', { method: 'POST', body: JSON.stringify(payload) }),
  update: (id, payload) => request(`/${id}`, { method: 'PUT', body: JSON.stringify(payload) }),
  remove: id => request(`/${id}`, { method: 'DELETE' }),
};
