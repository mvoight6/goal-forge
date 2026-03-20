// ── Goal Forge PWA ──────────────────────────────────────────────────────────
'use strict';

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
let TOKEN = localStorage.getItem('gf_token') || '';

function saveToken(t) { TOKEN = t; localStorage.setItem('gf_token', t); }

async function api(method, path, body = null, isForm = false) {
  const opts = {
    method,
    headers: { 'Authorization': `Bearer ${TOKEN}` },
  };
  if (body && !isForm) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  } else if (body && isForm) {
    opts.body = body; // FormData — browser sets multipart boundary
  }
  const res = await fetch(path, opts);
  if (res.status === 401) { showLogin(); throw new Error('Unauthorized'); }
  if (!res.ok && res.status !== 207) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------
// Returns YYYY-MM-DD in LOCAL time (avoids UTC-offset date-flip bug)
function localDateStr(daysOffset = 0) {
  const d = new Date();
  if (daysOffset) d.setDate(d.getDate() + daysOffset);
  return d.toLocaleDateString('en-CA'); // en-CA always gives YYYY-MM-DD
}

let _toastTimer;
function toast(msg, duration = 2500) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), duration);
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------
const routes = {};
let currentView = null;
const _navStack = [];

function register(name, fn) { routes[name] = fn; }

function navigate(name, params = {}) {
  const fn = routes[name];
  if (!fn) return;
  // Clean up TV mode when leaving
  if (window._tvInterval) { clearInterval(window._tvInterval); window._tvInterval = null; }
  if (window._tvClockInterval) { clearInterval(window._tvClockInterval); window._tvClockInterval = null; }
  if (name !== 'tv') { const _navEl = document.getElementById('nav'); if (_navEl) _navEl.style.display = ''; }
  if (currentView) _navStack.push({ name: currentView, params: window._currentParams || {} });
  currentView = name;
  window._currentParams = params;
  const mc = document.getElementById('main-content');
  mc.removeAttribute('style');
  mc.className = '';
  mc.innerHTML = '';
  document.querySelectorAll('.nav-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.view === name);
  });
  fn(params);
}

function goBack(fallback = 'dashboard') {
  // Clean up TV mode when leaving
  if (window._tvInterval) { clearInterval(window._tvInterval); window._tvInterval = null; }
  if (window._tvClockInterval) { clearInterval(window._tvClockInterval); window._tvClockInterval = null; }
  const _navEl2 = document.getElementById('nav'); if (_navEl2) _navEl2.style.display = '';
  const prev = _navStack.pop();
  if (prev) {
    currentView = null; // allow navigate() to push if needed — but skip pushing again
    const fn = routes[prev.name];
    if (fn) {
      currentView = prev.name;
      window._currentParams = prev.params;
      const mc = document.getElementById('main-content');
      mc.removeAttribute('style');
      mc.className = '';
      mc.innerHTML = '';
      document.querySelectorAll('.nav-btn').forEach(b => {
        b.classList.toggle('active', b.dataset.view === prev.name);
      });
      fn(prev.params);
      return;
    }
  }
  navigate(fallback);
}

// ---------------------------------------------------------------------------
// Login screen
// ---------------------------------------------------------------------------
function showLogin() {
  document.getElementById('app').innerHTML = `
    <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;padding:32px;gap:16px;">
      <div style="font-size:48px;">⚒️</div>
      <h1 style="font-size:24px;font-weight:700;">Goal Forge</h1>
      <p style="color:var(--text-muted);text-align:center;">Enter your API token to connect to your server.</p>
      <input type="password" id="token-input" placeholder="Bearer token" style="max-width:320px;" />
      <button class="btn btn-primary" onclick="doLogin()" style="max-width:320px;width:100%;">Connect</button>
    </div>`;
}

window.doLogin = function() {
  const t = document.getElementById('token-input').value.trim();
  if (!t) return;
  saveToken(t);
  initApp();
};

// ---------------------------------------------------------------------------
// App shell
// ---------------------------------------------------------------------------
function buildShell() {
  document.getElementById('app').innerHTML = `
    <div id="main-content"></div>
    <nav id="nav">
      <button class="nav-btn" data-view="dashboard" onclick="navigate('dashboard')">
        <span class="icon">🏠</span>Dashboard
      </button>
      <button class="nav-btn" data-view="daily" onclick="navigate('daily')">
        <span class="icon">📋</span>Daily
      </button>
      <button class="nav-btn" data-view="goals" onclick="navigate('goals')">
        <span class="icon">🎯</span>Strategic
      </button>
      <button class="nav-btn" data-view="chat" onclick="navigate('chat')">
        <span class="icon">💬</span>Chat
      </button>
      <button class="nav-btn" data-view="more" onclick="navigate('more')">
        <span class="icon">⋯</span>More
      </button>
    </nav>
    <div id="toast"></div>`;
}

// ---------------------------------------------------------------------------
// Dashboard view
// ---------------------------------------------------------------------------
register('dashboard', async () => {
  const el = document.getElementById('main-content');
  el.innerHTML = `<div class="page-header"><span class="page-title">⚒️ Goal Forge</span><button class="btn btn-sm btn-ghost" onclick="navigate('dashboard')">↺</button></div><div class="spinner"></div>`;

  try {
    const [goals, inbox, dailyData] = await Promise.all([
      api('GET', '/goals'),
      api('GET', '/inbox'),
      api('GET', '/daily?days=1'),
    ]);

    const today = localDateStr();
    const in7 = localDateStr(7);
    const dueThisWeek = goals.filter(g => g.due_date && g.due_date >= today && g.due_date <= in7 && g.status !== 'Completed');
    const overdue = goals.filter(g => g.due_date && g.due_date < today && g.status !== 'Completed');
    const rootActive = goals.filter(g => g.depth === 0 && g.status === 'Active');
    const rootBacklog = goals.filter(g => g.depth === 0 && g.status === 'Backlog' && !g.is_milestone);

    const todayDaily = dailyData[0] || { date: today, items: [] };
    const dailyItems = todayDaily.items || [];
    const doneCount = dailyItems.filter(i => i.status === 'Completed').length;

    el.innerHTML = `
      <div class="page-header"><span class="page-title">⚒️ Goal Forge</span><button class="btn btn-sm btn-ghost" onclick="navigate('dashboard')">↺</button></div>

      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:20px;">
        ${summaryCard('🎯', 'Active', rootActive.length, 'primary')}
        ${summaryCard('🔥', 'Due Soon', dueThisWeek.length, 'warning')}
        ${summaryCard('🚨', 'Overdue', overdue.length, 'danger')}
      </div>

      <div class="section-header" style="display:flex;align-items:center;justify-content:space-between;">
        <span>📋 Today's Goals ${dailyItems.length ? `<span style="color:var(--text-muted);font-weight:400;font-size:13px;">(${doneCount}/${dailyItems.length})</span>` : ''}</span>
        <button class="btn btn-sm btn-ghost" onclick="navigate('daily')">View All</button>
      </div>
      ${renderDailyChecklist(dailyItems, today, true)}

      <div class="section-header">📅 Due This Week</div>
      ${dueThisWeek.length ? dueThisWeek.map(g => goalCard(g)).join('') : '<p style="color:var(--text-muted);font-size:14px;">Nothing due this week 🎉</p>'}

      ${overdue.length ? `<div class="section-header">🚨 Overdue</div>${overdue.map(g => goalCard(g)).join('')}` : ''}

      <div class="section-header">🌳 Active Strategic Goals</div>
      ${rootActive.length ? rootActive.map(g => goalCard(g)).join('') : '<p style="color:var(--text-muted);font-size:14px;">No active strategic goals.</p>'}

      ${rootBacklog.length ? `
        <div class="section-header">📋 Backlog</div>
        ${rootBacklog.map(g => goalCard(g)).join('')}
      ` : ''}

      ${inbox.length ? `
        <div class="section-header">📥 Inbox (${inbox.length})</div>
        <button class="btn btn-secondary btn-full" onclick="navigate('inbox')">Review Inbox</button>
      ` : ''}
    `;
    _restoreDailyFocus();
  } catch (e) {
    el.innerHTML = `<p style="color:var(--danger)">Error: ${e.message}</p>`;
  }
});

function summaryCard(icon, label, count, color) {
  const colors = { primary: '#2563eb', warning: '#d97706', danger: '#dc2626' };
  return `<div class="card" style="text-align:center;border-color:${colors[color]}33;">
    <div style="font-size:22px;">${icon}</div>
    <div style="font-size:24px;font-weight:700;color:${colors[color]};">${count}</div>
    <div style="font-size:12px;color:var(--text-muted);">${label}</div>
  </div>`;
}

function statusBadge(status) {
  const map = { Active: 'active', Completed: 'completed', Blocked: 'blocked', Backlog: 'backlog', Draft: 'draft' };
  return `<span class="badge badge-${map[status] || 'backlog'}">${status || '—'}</span>`;
}

function goalCard(g, showPlan = false) {
  const childCount = g.child_count || '';
  return `<div class="card" onclick="navigate('goal-detail', {id:'${g.id}'})" style="cursor:pointer;">
    <div class="card-row">
      <span class="card-title">${g.name}</span>
      ${statusBadge(g.status)}
    </div>
    <div class="card-meta" style="margin-top:4px;">
      ${g.id} · ${g.horizon || ''} ${g.due_date ? '· Due ' + g.due_date : ''} ${childCount ? `· ${childCount} children` : ''}
    </div>
  </div>`;
}

// ---------------------------------------------------------------------------
// Daily Goals helpers
// ---------------------------------------------------------------------------
function renderDailyChecklist(items, dateStr, compact = false) {
  const addRow = `
    <div style="display:flex;gap:8px;margin-top:8px;">
      <input type="text" id="daily-add-${dateStr}" placeholder="Add item…" style="flex:1;"
        onkeydown="if(event.key==='Enter')addDailyItem('${dateStr}')">
      <button class="btn btn-sm btn-primary" onclick="addDailyItem('${dateStr}')">+</button>
    </div>`;

  if (!items.length) {
    return `<div style="color:var(--text-muted);font-size:14px;padding:4px 0;">No items yet.</div>${addRow}`;
  }

  const rows = items.map((item, idx) => {
    const done = item.status === 'Completed';
    const nameStyle = done ? 'text-decoration:line-through;color:var(--text-muted);' : '';
    const tomorrow = new Date(dateStr + 'T12:00:00'); // noon avoids DST-edge date shift
    tomorrow.setDate(tomorrow.getDate() + 1);
    const tomorrowStr = tomorrow.toLocaleDateString('en-CA');
    const isFirst = idx === 0;
    const isLast = idx === items.length - 1;

    const reorderBtns = !compact ? `
      <div style="display:flex;flex-direction:column;gap:1px;flex-shrink:0;">
        <button class="btn btn-sm btn-ghost" style="padding:0 5px;font-size:10px;line-height:1.4;${isFirst ? 'opacity:0.2;pointer-events:none;' : ''}"
          onclick="dailyReorder('${dateStr}','${item.id}',-1)">▲</button>
        <button class="btn btn-sm btn-ghost" style="padding:0 5px;font-size:10px;line-height:1.4;${isLast ? 'opacity:0.2;pointer-events:none;' : ''}"
          onclick="dailyReorder('${dateStr}','${item.id}',1)">▼</button>
      </div>` : '';

    return `<div class="daily-item" data-item-id="${item.id}" data-date="${dateStr}" style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);">
      <input type="checkbox" ${done ? 'checked' : ''} onchange="toggleDailyItem('${item.id}', this.checked)" style="flex-shrink:0;width:18px;height:18px;cursor:pointer;">
      <span id="daily-name-${item.id}" style="flex:1;font-size:14px;${nameStyle}cursor:text;" onclick="startEditDailyItem('${item.id}')" title="Click to edit">${escHtml(item.name)}</span>
      ${!done && !compact ? `<button class="btn btn-sm btn-ghost" style="font-size:11px;padding:2px 6px;" onclick="moveDailyItem('${item.id}','${tomorrowStr}')">→ Tmrw</button>` : ''}
      ${!compact ? `<button class="btn btn-sm btn-ghost" style="font-size:11px;padding:2px 6px;color:var(--danger);" onclick="removeDailyItem('${item.id}')">✕</button>` : ''}
      ${reorderBtns}
    </div>`;
  }).join('');

  return `<div id="daily-list-${dateStr}">${rows}</div>${addRow}`;
}

window.dailyReorder = async function(dateStr, itemId, direction) {
  const list = document.getElementById(`daily-list-${dateStr}`);
  if (!list) return;
  const items = Array.from(list.querySelectorAll('.daily-item'));
  const idx = items.findIndex(el => el.dataset.itemId === itemId);
  const swapIdx = idx + direction;
  if (swapIdx < 0 || swapIdx >= items.length) return;

  // Swap in DOM
  if (direction === -1) {
    list.insertBefore(items[idx], items[swapIdx]);
  } else {
    list.insertBefore(items[swapIdx], items[idx]);
  }

  // Update arrow visibility
  const allItems = Array.from(list.querySelectorAll('.daily-item'));
  allItems.forEach((el, i) => {
    const btns = el.querySelectorAll('button');
    // up btn is second-to-last, down btn is last
    const upBtn = btns[btns.length - 2];
    const downBtn = btns[btns.length - 1];
    if (upBtn) { upBtn.style.opacity = i === 0 ? '0.2' : '1'; upBtn.style.pointerEvents = i === 0 ? 'none' : ''; }
    if (downBtn) { downBtn.style.opacity = i === allItems.length - 1 ? '0.2' : '1'; downBtn.style.pointerEvents = i === allItems.length - 1 ? 'none' : ''; }
  });

  // Persist order
  const orderedIds = allItems.map(el => el.dataset.itemId);
  try {
    await api('PUT', `/daily/${dateStr}/order`, { item_ids: orderedIds });
  } catch (e) { toast(`Error saving order: ${e.message}`); }
};

let _pendingDailyFocus = null;

function _restoreDailyFocus() {
  if (!_pendingDailyFocus) return;
  const dateStr = _pendingDailyFocus;
  _pendingDailyFocus = null;
  requestAnimationFrame(() => {
    const input = document.getElementById(`daily-add-${dateStr}`);
    if (input) input.focus();
  });
}

window.addDailyItem = async function(dateStr) {
  const input = document.getElementById(`daily-add-${dateStr}`);
  if (!input) return;
  const name = input.value.trim();
  if (!name) return;
  try {
    await api('POST', `/daily/${dateStr}/items`, { name });
    _pendingDailyFocus = dateStr;
    if (currentView === 'dashboard') navigate('dashboard');
    else if (currentView === 'daily') navigate('daily');
  } catch (e) { toast(`Error: ${e.message}`); }
};

window.toggleDailyItem = async function(id, done) {
  try {
    await api('PATCH', `/goals/${id}`, { status: done ? 'Completed' : 'Active' });
    if (currentView === 'dashboard') navigate('dashboard');
    else if (currentView === 'daily') navigate('daily');
  } catch (e) { toast(`Error: ${e.message}`); }
};

window.moveDailyItem = async function(id, toDate) {
  try {
    await api('POST', `/daily/items/${id}/move`, { to_date: toDate });
    toast('Moved!');
    navigate('daily');
  } catch (e) { toast(`Error: ${e.message}`); }
};

window.removeDailyItem = async function(id) {
  try {
    await api('DELETE', `/goals/${id}`);
    navigate('daily');
  } catch (e) { toast(`Error: ${e.message}`); }
};

window.startEditDailyItem = function(id) {
  const span = document.getElementById('daily-name-' + id);
  if (!span) return;

  const originalName = span.textContent;
  const originalStyle = span.getAttribute('style');

  const input = document.createElement('input');
  input.type = 'text';
  input.value = originalName;
  input.style.cssText = 'flex:1;font-size:14px;padding:2px 6px;background:var(--surface2);border:1px solid var(--primary);border-radius:4px;outline:none;color:var(--text);min-width:0;';
  span.parentNode.replaceChild(input, span);
  input.focus();
  input.select();

  let committed = false;

  async function commit() {
    if (committed) return;
    committed = true;
    const newName = input.value.trim();
    if (!newName || newName === originalName) {
      input.parentNode.replaceChild(span, input);
      return;
    }
    try {
      await api('PATCH', `/goals/${id}`, { name: newName });
      span.textContent = newName; // update span before restoring
      input.parentNode.replaceChild(span, input);
    } catch (e) {
      toast(`Error: ${e.message}`);
      committed = false;
      input.parentNode.replaceChild(span, input);
    }
  }

  input.addEventListener('blur', commit);
  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') { e.preventDefault(); commit(); }
    if (e.key === 'Escape') {
      committed = true; // suppress blur→commit
      input.parentNode.replaceChild(span, input);
    }
  });
};

// ---------------------------------------------------------------------------
// Daily view
// ---------------------------------------------------------------------------
register('daily', async () => {
  const el = document.getElementById('main-content');
  el.innerHTML = `<div class="page-header"><span class="page-title">📋 Daily Goals</span></div><div class="spinner"></div>`;

  try {
    const days = await api('GET', '/daily?days=7');

    const today = localDateStr();
    const tomorrow = localDateStr(1);

    // Build HTML newest-first (days array is oldest-first, so reverse)
    const sections = [...days].reverse().map(day => {
      const isToday = day.date === today;
      const isTomorrow = day.date === tomorrow;
      const d = new Date(day.date + 'T12:00:00');
      const label = isToday ? 'Today' : isTomorrow ? 'Tomorrow' : d.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
      const doneCount = (day.items || []).filter(i => i.status === 'Completed').length;
      const total = (day.items || []).length;

      return `
        <div style="margin-bottom:20px;">
          <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:6px;">
            <span style="font-size:15px;font-weight:700;">${label}</span>
            ${total ? `<span style="font-size:12px;color:var(--text-muted);">${doneCount}/${total} done</span>` : ''}
          </div>
          ${renderDailyChecklist(day.items || [], day.date)}
        </div>`;
    }).join('');

    el.innerHTML = `
      <div class="page-header"><span class="page-title">📋 Daily Goals</span></div>
      ${sections}
    `;
    _restoreDailyFocus();
  } catch (e) {
    el.innerHTML = `<p style="color:var(--danger)">Error: ${e.message}</p>`;
  }
});

// ---------------------------------------------------------------------------
// Goals list view
// ---------------------------------------------------------------------------
function buildTree(goals) {
  const map = {};
  const roots = [];
  goals.forEach(g => { map[g.id] = { ...g, _children: [] }; });
  goals.forEach(g => {
    if (g.parent_goal_id && map[g.parent_goal_id]) {
      map[g.parent_goal_id]._children.push(map[g.id]);
    } else {
      roots.push(map[g.id]);
    }
  });
  return roots;
}

function renderTree(nodes, depth = 0) {
  return nodes.map(g => {
    const indent = depth * 18;
    const borderStyle = depth > 0 ? `border-left:2px solid var(--border);padding-left:10px;` : '';
    const hasChildren = g._children.length > 0;

    const toggleBtn = hasChildren
      ? `<button style="background:none;border:none;cursor:pointer;padding:0 4px;font-size:12px;color:var(--text-muted);flex-shrink:0;margin-top:14px;" onclick="event.stopPropagation();toggleGoalNode('${g.id}')" id="toggle-btn-${g.id}">▶</button>`
      : `<div style="width:18px;flex-shrink:0;"></div>`;

    const row = `<div style="margin-left:${indent}px;${borderStyle}display:flex;align-items:flex-start;gap:2px;">
      ${toggleBtn}
      <div style="flex:1;">${goalCard(g)}</div>
    </div>`;

    const children = hasChildren
      ? `<div id="tree-children-${g.id}" style="display:none;">${renderTree(g._children, depth + 1)}</div>`
      : '';

    return row + children;
  }).join('');
}

window.toggleGoalNode = function(id) {
  const children = document.getElementById(`tree-children-${id}`);
  const btn = document.getElementById(`toggle-btn-${id}`);
  if (!children) return;
  const isCollapsed = children.style.display === 'none';
  children.style.display = isCollapsed ? '' : 'none';
  btn.textContent = isCollapsed ? '▼' : '▶';
};

register('goals', async () => {
  const el = document.getElementById('main-content');
  el.innerHTML = `<div class="page-header"><span class="page-title">🎯 Strategic Goals</span></div><div class="spinner"></div>`;

  let filter = { status: null, horizon: null };

  async function render() {
    try {
      const params = new URLSearchParams();
      if (filter.status) params.set('status', filter.status);
      if (filter.horizon) params.set('horizon', filter.horizon);
      const goals = await api('GET', `/goals?${params}`);

      el.innerHTML = `
        <div class="page-header">
          <span class="page-title">🎯 Strategic Goals</span>
          <button class="btn btn-sm btn-primary" onclick="navigate('create-goal')">+ New</button>
        </div>
        <div class="filter-row">
          ${['Active','Backlog','Blocked','Completed'].map(s =>
            `<button class="filter-chip ${filter.status===s?'active':''}" onclick="setGoalFilter('status','${s}')">${s}</button>`
          ).join('')}
        </div>
        <div class="filter-row">
          ${['Daily','Weekly','Monthly','Quarterly','Yearly','Life'].map(h =>
            `<button class="filter-chip ${filter.horizon===h?'active':''}" onclick="setGoalFilter('horizon','${h}')">${h}</button>`
          ).join('')}
        </div>
        ${goals.length ? renderTree(buildTree(goals)) : '<p style="color:var(--text-muted);font-size:14px;margin-top:16px;">No goals found.</p>'}
      `;
    } catch (e) {
      el.innerHTML += `<p style="color:var(--danger)">Error: ${e.message}</p>`;
    }
  }

  window.setGoalFilter = function(key, val) {
    if (filter[key] === val) filter[key] = null; else filter[key] = val;
    render();
  };

  render();
});

// ---------------------------------------------------------------------------
// Goal Detail view
// ---------------------------------------------------------------------------
register('goal-detail', async ({ id }) => {
  const el = document.getElementById('main-content');
  el.innerHTML = `<div class="spinner"></div>`;

  try {
    const goal = await api('GET', `/goals/${id}`);
    const ancestors = goal.ancestors || [];
    const children = goal.children || [];
    const milestones = children.filter(c => c.is_milestone);
    const subGoals = children.filter(c => !c.is_milestone);

    const breadcrumb = [
      ...ancestors.map(a => `<a href="#" onclick="navigate('goal-detail',{id:'${a.id}'})">${a.name}</a>`),
      `<span>${goal.name}</span>`
    ].join('<span class="sep"> › </span>');

    el.innerHTML = `
      <div class="page-header">
        <button class="btn btn-sm btn-ghost" onclick="goBack()">← Back</button>
        <button class="btn btn-sm btn-secondary" onclick="navigate('edit-goal',{id:'${goal.id}'})">Edit</button>
      </div>
      <div class="breadcrumb">${breadcrumb}</div>

      <div class="card">
        <div class="card-row" style="margin-bottom:8px;">
          <h2 style="font-size:18px;font-weight:700;">${goal.name}</h2>
          ${statusBadge(goal.status)}
        </div>
        <table style="font-size:13px;width:100%;border-collapse:collapse;">
          <tr><td style="color:var(--text-muted);padding:3px 8px 3px 0;">ID</td><td>${goal.id}</td></tr>
          <tr><td style="color:var(--text-muted);padding:3px 8px 3px 0;">Horizon</td><td>${goal.horizon || '—'}</td></tr>
          <tr><td style="color:var(--text-muted);padding:3px 8px 3px 0;">Due</td><td>${goal.due_date || '—'}</td></tr>
          <tr><td style="color:var(--text-muted);padding:3px 8px 3px 0;">Category</td><td>${goal.category || '—'}</td></tr>
          <tr><td style="color:var(--text-muted);padding:3px 8px 3px 0;">Type</td><td>${goal.is_milestone ? 'Milestone' : 'Full Goal'}</td></tr>
          <tr><td style="color:var(--text-muted);padding:3px 8px 3px 0;">Depth</td><td>${goal.depth}</td></tr>
        </table>
        ${goal.description ? `<p style="font-size:14px;color:var(--text);margin-top:12px;line-height:1.5;">${escHtml(goal.description)}</p>` : ''}
      </div>

      <div class="section-header">📝 Progress Notes</div>
      <div class="card" style="padding:10px 14px;margin-bottom:16px;">
        <textarea
          id="goal-notes-${goal.id}"
          class="notes-textarea"
          placeholder="Log your progress here… (auto-saves on blur)"
          onblur="saveProgressNotes('${goal.id}')"
        >${escHtml(goal.progress_notes || '')}</textarea>
        <div id="notes-status-${goal.id}" style="font-size:11px;color:var(--text-muted);text-align:right;margin-top:4px;min-height:14px;"></div>
      </div>

      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;">
        <button class="btn btn-sm btn-primary" onclick="planGoal('${goal.id}')">🤖 Plan with AI</button>
        ${goal.is_milestone
          ? `<button class="btn btn-sm btn-ghost" onclick="promoteGoal('${goal.id}')">⬆ Promote</button>`
          : `<button class="btn btn-sm btn-ghost" onclick="demoteGoal('${goal.id}')">⬇ Demote</button>`
        }
        <select onchange="updateStatus('${goal.id}',this.value)" style="flex:1;min-width:120px;">
          ${['Draft','Backlog','Active','Blocked','Completed'].map(s =>
            `<option ${s===goal.status?'selected':''}>${s}</option>`
          ).join('')}
        </select>
      </div>

      ${milestones.length ? `
        <div class="section-header">✅ Milestones</div>
        ${milestones.map(m => `
          <div class="milestone-row">
            <input type="checkbox" ${m.status==='Completed'?'checked':''} onchange="toggleMilestone('${m.id}',this.checked)">
            <span style="flex:1;font-size:14px;">${m.name}</span>
            <button class="btn btn-sm btn-ghost" onclick="promoteGoal('${m.id}')">⬆</button>
          </div>
        `).join('')}
      ` : ''}

      ${subGoals.length ? `
        <div class="section-header">🎯 Strategic Sub-Goals</div>
        ${subGoals.map(g => goalCard(g)).join('')}
      ` : ''}

      ${!children.length ? '<p style="color:var(--text-muted);font-size:13px;margin-top:8px;">No children yet. Use "Plan with AI" to generate them.</p>' : ''}
    `;
  } catch (e) {
    el.innerHTML = `<p style="color:var(--danger)">Error: ${e.message}</p>`;
  }
});

window.planGoal = async function(id) {
  toast('Generating child goals…');
  try {
    const res = await api('POST', `/goals/${id}/plan`);
    toast(`Created ${res.created.length} child goals!`);
    navigate('goal-detail', { id });
  } catch (e) { toast(`Plan failed: ${e.message}`); }
};

window.updateStatus = async function(id, status) {
  try {
    await api('PATCH', `/goals/${id}`, { status });
    toast(`Status → ${status}`);
    navigate('goal-detail', { id });
  } catch (e) { toast(`Error: ${e.message}`); }
};

window.saveProgressNotes = async function(id) {
  const ta = document.getElementById('goal-notes-' + id);
  const statusEl = document.getElementById('notes-status-' + id);
  if (!ta) return;
  try {
    if (statusEl) statusEl.textContent = 'Saving…';
    await api('PATCH', `/goals/${id}`, { progress_notes: ta.value });
    if (statusEl) {
      statusEl.textContent = 'Saved ✓';
      setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 2000);
    }
  } catch (e) {
    if (statusEl) statusEl.textContent = 'Error saving';
    toast(`Error saving notes: ${e.message}`);
  }
};

window.toggleMilestone = async function(id, done) {
  await api('PATCH', `/goals/${id}`, { status: done ? 'Completed' : 'Active' });
};

window.promoteGoal = async function(id) {
  try {
    await api('POST', `/goals/${id}/promote`);
    toast('Promoted to Full Goal');
    navigate('goal-detail', { id });
  } catch (e) { toast(`Error: ${e.message}`); }
};

window.demoteGoal = async function(id) {
  try {
    await api('POST', `/goals/${id}/demote`);
    toast('Demoted to Milestone');
    navigate('goal-detail', { id });
  } catch (e) { toast(`Error: ${e.message}`); }
};

// ---------------------------------------------------------------------------
// Create Goal view
// ---------------------------------------------------------------------------
register('create-goal', ({ parentId } = {}) => {
  const el = document.getElementById('main-content');
  el.innerHTML = `
    <div class="page-header">
      <button class="btn btn-sm btn-ghost" onclick="goBack()">← Back</button>
      <span class="page-title">New Goal</span>
    </div>
    <div class="form-group"><label class="form-label">Name *</label><input type="text" id="g-name" placeholder="What do you want to achieve?"></div>
    <div class="form-group"><label class="form-label">Description</label><textarea id="g-desc" placeholder="Describe your goal…"></textarea></div>
    <div class="form-group"><label class="form-label">Horizon</label>
      <select id="g-horizon">${['Daily','Weekly','Monthly','Quarterly','Yearly','Life'].map(h=>`<option>${h}</option>`).join('')}</select>
    </div>
    <div class="form-group"><label class="form-label">Due Date</label><input type="date" id="g-due"></div>
    <div class="form-group"><label class="form-label">Category</label><input type="text" id="g-cat" placeholder="e.g. Health, Work, Learning"></div>
    ${parentId ? `<input type="hidden" id="g-parent" value="${parentId}">` : ''}
    <button class="btn btn-primary btn-full" onclick="submitCreateGoal()">Create Goal</button>
  `;
});

window.submitCreateGoal = async function() {
  const name = document.getElementById('g-name').value.trim();
  if (!name) { toast('Name is required'); return; }
  const body = {
    name,
    description: document.getElementById('g-desc').value,
    horizon: document.getElementById('g-horizon').value,
    due_date: document.getElementById('g-due').value || null,
    category: document.getElementById('g-cat').value,
  };
  const parentEl = document.getElementById('g-parent');
  if (parentEl) body.parent_goal_id = parentEl.value;

  try {
    const goal = await api('POST', '/goals/create', body);
    toast('Goal created!');
    if (goal && goal.id) {
      navigate('goal-detail', { id: goal.id });
    } else {
      navigate('goals');
    }
  } catch (e) { toast(`Error: ${e.message}`); }
};

// ---------------------------------------------------------------------------
// Edit Goal view
// ---------------------------------------------------------------------------
register('edit-goal', async ({ id }) => {
  const el = document.getElementById('main-content');
  el.innerHTML = `<div class="page-header"><button class="btn btn-sm btn-ghost" onclick="goBack()">← Back</button><span class="page-title">Edit Goal</span></div><div class="spinner"></div>`;

  try {
    const goal = await api('GET', `/goals/${id}`);
    el.innerHTML = `
      <div class="page-header">
        <button class="btn btn-sm btn-ghost" onclick="goBack()">← Back</button>
        <span class="page-title">Edit Goal</span>
      </div>
      <div class="form-group"><label class="form-label">Name *</label><input type="text" id="eg-name" value="${escHtml(goal.name || '')}"></div>
      <div class="form-group"><label class="form-label">Description</label><textarea id="eg-desc" placeholder="Describe your goal…">${escHtml(goal.description || '')}</textarea></div>
      <div class="form-group"><label class="form-label">Horizon</label>
        <select id="eg-horizon">${['Daily','Weekly','Monthly','Quarterly','Yearly','Life'].map(h=>`<option ${goal.horizon===h?'selected':''}>${h}</option>`).join('')}</select>
      </div>
      <div class="form-group"><label class="form-label">Status</label>
        <select id="eg-status">${['Draft','Backlog','Active','Blocked','Completed'].map(s=>`<option ${goal.status===s?'selected':''}>${s}</option>`).join('')}</select>
      </div>
      <div class="form-group"><label class="form-label">Due Date</label><input type="date" id="eg-due" value="${goal.due_date || ''}"></div>
      <div class="form-group"><label class="form-label">Category</label><input type="text" id="eg-cat" value="${escHtml(goal.category || '')}"></div>
      <div class="form-group"><label class="form-label">Notify Before (days)</label><input type="number" id="eg-notify" value="${goal.notify_before_days ?? 3}" min="0"></div>
      <div class="form-group"><label class="form-label">📝 Progress Notes</label><textarea id="eg-notes" placeholder="Log your progress…" style="min-height:140px;">${escHtml(goal.progress_notes || '')}</textarea></div>
      <button class="btn btn-primary btn-full" onclick="submitEditGoal('${goal.id}')">Save Changes</button>
      <button class="btn btn-full" style="margin-top:8px;background:var(--danger);color:#fff;border:none;" onclick="deleteGoal('${goal.id}', '${escHtml(goal.name)}')">Delete Goal</button>
    `;
  } catch (e) {
    el.innerHTML += `<p style="color:var(--danger)">Error: ${e.message}</p>`;
  }
});

window.submitEditGoal = async function(id) {
  const name = document.getElementById('eg-name').value.trim();
  if (!name) { toast('Name is required'); return; }
  const updates = {
    name,
    description: document.getElementById('eg-desc').value,
    horizon: document.getElementById('eg-horizon').value,
    status: document.getElementById('eg-status').value,
    due_date: document.getElementById('eg-due').value || null,
    category: document.getElementById('eg-cat').value,
    notify_before_days: parseInt(document.getElementById('eg-notify').value) || 3,
    progress_notes: document.getElementById('eg-notes').value,
  };
  try {
    await api('PATCH', `/goals/${id}`, updates);
    toast('Goal updated!');
    navigate('goal-detail', { id });
  } catch (e) { toast(`Error: ${e.message}`); }
};

window.deleteGoal = async function(id, name) {
  if (!confirm(`Delete "${name}"?\n\nThis cannot be undone.`)) return;
  try {
    await api('DELETE', `/goals/${id}`);
    toast('Goal deleted');
    // Pop back past the detail view to goals list
    _navStack.length = 0;
    navigate('goals');
  } catch (e) { toast(`Error: ${e.message}`); }
};

// ---------------------------------------------------------------------------
// Quick Capture view
// ---------------------------------------------------------------------------
register('capture', () => {
  const el = document.getElementById('main-content');
  el.innerHTML = `
    <div class="page-header"><span class="page-title">📸 Quick Capture</span></div>
    <div class="form-group"><label class="form-label">Title *</label><input type="text" id="cap-title" placeholder="What's on your mind?"></div>
    <div class="form-group"><label class="form-label">Notes</label><textarea id="cap-desc" placeholder="Add details…"></textarea></div>

    <div class="form-group">
      <label class="form-label">Images</label>
      <div style="display:flex;gap:10px;margin-bottom:8px;">
        <button class="btn btn-secondary btn-sm" onclick="document.getElementById('cam-input').click()">📷 Camera</button>
        <button class="btn btn-secondary btn-sm" onclick="document.getElementById('gal-input').click()">🖼 Gallery</button>
      </div>
      <input type="file" id="cam-input" accept="image/*" capture="environment" style="display:none" onchange="addImages(this.files)">
      <input type="file" id="gal-input" accept="image/*" multiple style="display:none" onchange="addImages(this.files)">
      <div id="image-preview-strip"></div>
    </div>

    <button class="btn btn-primary btn-full" onclick="submitCapture()">Save Capture</button>
  `;
  window._captureImages = [];
});

window.addImages = function(files) {
  for (const f of files) {
    window._captureImages.push(f);
  }
  renderPreviews();
};

function renderPreviews() {
  const strip = document.getElementById('image-preview-strip');
  if (!strip) return;
  strip.innerHTML = window._captureImages.map((f, i) => `
    <div class="preview-thumb">
      <img src="${URL.createObjectURL(f)}" alt="${f.name}">
      <button class="preview-remove" onclick="removeImage(${i})">✕</button>
    </div>
  `).join('');
}

window.removeImage = function(idx) {
  window._captureImages.splice(idx, 1);
  renderPreviews();
};

window.submitCapture = async function() {
  const title = document.getElementById('cap-title').value.trim();
  if (!title) { toast('Title is required'); return; }
  const desc = document.getElementById('cap-desc').value;

  const fd = new FormData();
  fd.append('title', title);
  fd.append('description', desc);
  (window._captureImages || []).forEach(f => fd.append('images', f));

  try {
    const res = await api('POST', '/capture', fd, true);
    const saved = res.images_saved?.length || 0;
    const failed = (res.image_results || []).filter(r => r.status !== 'saved').length;
    if (failed) {
      toast(`Captured (${saved} images saved, ${failed} failed)`);
    } else {
      toast(`Captured! ${saved ? saved + ' image(s) saved.' : ''}`);
    }
    window._captureImages = [];
    navigate('capture');
  } catch (e) { toast(`Error: ${e.message}`); }
};

// ---------------------------------------------------------------------------
// Inbox view
// ---------------------------------------------------------------------------
register('inbox', async () => {
  const el = document.getElementById('main-content');
  el.innerHTML = `<div class="page-header"><span class="page-title">📥 Inbox</span></div><div class="spinner"></div>`;

  try {
    const items = await api('GET', '/inbox');
    if (!items.length) {
      el.innerHTML = `<div class="page-header"><span class="page-title">📥 Inbox</span></div><p style="color:var(--text-muted);margin-top:20px;text-align:center;">Inbox is empty!</p>`;
      return;
    }
    el.innerHTML = `
      <div class="page-header"><span class="page-title">📥 Inbox (${items.length})</span></div>
      ${items.map(g => `
        <div class="card">
          <div class="card-title">${g.name}</div>
          <div class="card-meta">${g.id} · ${g.created_date}</div>
          <div style="display:flex;gap:8px;margin-top:10px;">
            <button class="btn btn-sm btn-primary" onclick="promoteCapture('${g.id}')">→ Promote</button>
            <button class="btn btn-sm btn-ghost" onclick="navigate('goal-detail',{id:'${g.id}'})">View</button>
            <button class="btn btn-sm btn-danger" onclick="discardCapture('${g.id}')">Discard</button>
          </div>
        </div>
      `).join('')}
    `;
  } catch (e) {
    el.innerHTML = `<p style="color:var(--danger)">Error: ${e.message}</p>`;
  }
});

window.promoteCapture = async function(id) {
  await api('PATCH', `/goals/${id}`, { status: 'Backlog' });
  toast('Promoted to Backlog');
  navigate('inbox');
};

window.discardCapture = async function(id) {
  if (!confirm('Discard this capture?')) return;
  // TODO: delete endpoint
  toast('Discarded');
  navigate('inbox');
};

// ---------------------------------------------------------------------------
// Chat view
// ---------------------------------------------------------------------------
register('chat', () => {
  const mc = document.getElementById('main-content');
  // Inline styles own the layout — not subject to CSS caching
  mc.style.cssText = 'padding:0;overflow:hidden;display:flex;flex-direction:column;padding-bottom:0;';
  mc.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;padding:16px 16px 8px;flex-shrink:0;">
      <span class="page-title">💬 Chat</span>
      <button class="btn btn-sm btn-ghost" onclick="clearChat()">Clear</button>
    </div>
    <div id="chat-messages" style="flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:12px;padding:4px 16px 12px;"></div>
    <div id="chat-bottom" style="flex-shrink:0;background:var(--bg);border-top:1px solid var(--border);padding:10px 16px 72px;">
      <div style="display:flex;gap:8px;">
        <input type="text" id="chat-input" style="flex:1;" placeholder="Ask anything about your goals…" onkeydown="if(event.key==='Enter')sendChat()">
        <button class="btn btn-primary btn-sm" onclick="sendChat()">Send</button>
      </div>
      <div id="ctx-bar-wrap" style="display:none;margin-top:8px;">
        <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text-muted);margin-bottom:3px;">
          <span>Context window</span><span id="ctx-pct-label">0%</span>
        </div>
        <div style="background:var(--border);border-radius:4px;height:4px;overflow:hidden;">
          <div id="ctx-bar" style="height:100%;width:0%;background:#22c55e;border-radius:4px;transition:width 0.4s,background 0.4s;"></div>
        </div>
      </div>
    </div>
  `;
  window._chatSessionId = window._chatSessionId || '';
  window._chatMessagesHTML = window._chatMessagesHTML || '';

  // Restore previous conversation if one exists
  if (window._chatSessionId && window._chatMessagesHTML) {
    document.getElementById('chat-messages').innerHTML = window._chatMessagesHTML;
    if (window._chatLastTokens) updateContextBar(window._chatLastTokens, window._chatLastLimit || 32768);
    requestAnimationFrame(() => {
      const msgs = document.getElementById('chat-messages');
      if (msgs) msgs.scrollTop = msgs.scrollHeight;
    });
  }
});

function updateContextBar(tokens, limit) {
  const wrap = document.getElementById('ctx-bar-wrap');
  const bar = document.getElementById('ctx-bar');
  const label = document.getElementById('ctx-pct-label');
  if (!wrap || !bar || !label) return;
  const pct = Math.min(100, Math.round((tokens / limit) * 100));
  wrap.style.display = 'block';
  bar.style.width = pct + '%';
  label.textContent = pct + '%';
  bar.style.background = pct >= 90 ? '#dc2626' : pct >= 80 ? '#f97316' : pct >= 60 ? '#d97706' : '#22c55e';
}

window.clearChat = async function() {
  if (window._chatSessionId) {
    await api('DELETE', `/chat/${window._chatSessionId}`).catch(() => {});
    window._chatSessionId = '';
  }
  window._chatMessagesHTML = '';
  window._chatLastTokens = 0;
  window._chatLastLimit = 32768;
  const msgs = document.getElementById('chat-messages');
  if (msgs) msgs.innerHTML = '';
  const wrap = document.getElementById('ctx-bar-wrap');
  if (wrap) wrap.style.display = 'none';
  const bar = document.getElementById('ctx-bar');
  if (bar) { bar.style.width = '0%'; bar.style.background = '#22c55e'; }
  const label = document.getElementById('ctx-pct-label');
  if (label) label.textContent = '0%';
};

window.sendChat = async function() {
  const input = document.getElementById('chat-input');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';

  const msgs = document.getElementById('chat-messages');
  msgs.innerHTML += `<div class="chat-bubble user">${escHtml(msg)}</div>`;

  const spinner = document.createElement('div');
  spinner.className = 'chat-bubble assistant';
  spinner.innerHTML = '<div class="spinner"></div>';
  msgs.appendChild(spinner);
  msgs.scrollTop = msgs.scrollHeight;

  try {
    const res = await api('POST', '/chat', { session_id: window._chatSessionId || '', message: msg });
    window._chatSessionId = res.session_id;
    spinner.innerHTML = `
      <div>${escHtml(res.reply)}</div>
      ${res.tool_calls?.length ? `<div class="chat-tool-note">🔧 ${res.tool_calls.map(t => t.tool).join(', ')}</div>` : ''}
    `;
    if (res.context_tokens && res.context_limit) {
      updateContextBar(res.context_tokens, res.context_limit);
      window._chatLastTokens = res.context_tokens;
      window._chatLastLimit = res.context_limit;
    }
    if (res.compacted) {
      msgs.innerHTML += `<div style="text-align:center;font-size:11px;color:var(--text-muted);padding:6px 0;">— session compacted —</div>`;
    }
    window._chatMessagesHTML = msgs.innerHTML;
  } catch (e) {
    spinner.innerHTML = `<span style="color:var(--danger)">Error: ${e.message}</span>`;
    window._chatMessagesHTML = msgs.innerHTML;
  }
  msgs.scrollTop = msgs.scrollHeight;
};

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
}

// ---------------------------------------------------------------------------
// More screen (Inbox, Config, Jobs, Logs)
// ---------------------------------------------------------------------------
register('more', () => {
  const el = document.getElementById('main-content');
  el.innerHTML = `
    <div class="page-header"><span class="page-title">⋯ More</span></div>
    ${[
      ['📺', 'TV Dashboard', 'tv'],
      ['📥', 'Inbox', 'inbox'],
      ['📸', 'Capture', 'capture'],
      ['⚙️', 'Config', 'config'],
      ['⏱', 'Jobs', 'jobs'],
      ['📋', 'Logs', 'logs'],
    ].map(([icon, label, view]) => `
      <div class="card" onclick="navigate('${view}')" style="cursor:pointer;display:flex;align-items:center;gap:14px;">
        <span style="font-size:28px;">${icon}</span>
        <span style="font-size:16px;font-weight:600;">${label}</span>
        <span style="margin-left:auto;color:var(--text-muted);">›</span>
      </div>
    `).join('')}
  `;
});

// ---------------------------------------------------------------------------
// Config view
// ---------------------------------------------------------------------------
register('config', async () => {
  const el = document.getElementById('main-content');
  el.innerHTML = `<div class="page-header"><span class="page-title">⚙️ Config</span><button class="btn btn-sm btn-ghost" onclick="navigate('more')">← Back</button></div><div class="spinner"></div>`;

  try {
    const { config: cfg, raw_yaml } = await api('GET', '/config');

    el.innerHTML = `
      <div class="page-header">
        <span class="page-title">⚙️ Config</span>
        <button class="btn btn-sm btn-ghost" onclick="navigate('more')">← Back</button>
      </div>
      <div class="tab-bar">
        <button class="tab-btn active" id="tab-form" onclick="switchTab('form')">Form</button>
        <button class="tab-btn" id="tab-yaml" onclick="switchTab('yaml')">YAML</button>
      </div>
      <div id="cfg-form">${buildConfigForm(cfg)}</div>
      <div id="cfg-yaml" style="display:none">
        <textarea id="raw-yaml-editor" style="height:400px;font-family:monospace;font-size:12px;">${escHtml(raw_yaml)}</textarea>
        <div style="display:flex;gap:8px;margin-top:10px;">
          <button class="btn btn-secondary btn-sm" onclick="validateYaml()">Validate</button>
          <button class="btn btn-primary" onclick="saveRawYaml()">Save YAML</button>
        </div>
      </div>
    `;
  } catch (e) {
    el.innerHTML += `<p style="color:var(--danger)">Error: ${e.message}</p>`;
  }
});

window.switchTab = function(tab) {
  document.getElementById('cfg-form').style.display = tab === 'form' ? 'block' : 'none';
  document.getElementById('cfg-yaml').style.display = tab === 'yaml' ? 'block' : 'none';
  document.getElementById('tab-form').classList.toggle('active', tab === 'form');
  document.getElementById('tab-yaml').classList.toggle('active', tab === 'yaml');
};

function buildConfigForm(cfg) {
  const notifTypes = ['due_soon','goal_overdue','daily_morning_briefing','weekly_digest','end_of_week_summary','inbox_review','beginning_of_month','end_of_month'];
  const notifLabels = {
    due_soon: 'Due Soon', goal_overdue: 'Goal Overdue', daily_morning_briefing: 'Daily Briefing',
    weekly_digest: 'Weekly Digest', end_of_week_summary: 'End of Week Summary',
    inbox_review: 'Inbox Review', beginning_of_month: 'Beginning of Month', end_of_month: 'End of Month',
  };
  const llmProviders = ['anthropic','openrouter','ollama','vllm'];

  return `
  <div class="config-section">
    <div class="config-section-title">General</div>
    ${cfgField('vault_path','Vault Path',cfg.vault_path)}
    ${cfgField('database_path','Database Path',cfg.database_path)}
    ${cfgField('log_path','Log Path',cfg.log_path)}
  </div>

  <div class="config-section">
    <div class="config-section-title">LLM Provider</div>
    <div class="form-group">
      <label class="form-label">Provider</label>
      <select id="cfg-llm-provider" onchange="showLlmFields()">
        ${llmProviders.map(p=>`<option ${cfg.llm?.provider===p?'selected':''}>${p}</option>`).join('')}
      </select>
    </div>
    ${cfgField('llm_model','Model',cfg.llm?.[cfg.llm?.provider]?.model||'')}
    <div id="llm-api-key-row">${cfgPassword('llm_api_key','API Key',cfg.llm?.[cfg.llm?.provider]?.api_key||'')}</div>
    <div id="llm-base-url-row" style="display:none">${cfgField('llm_base_url','Base URL',cfg.llm?.[cfg.llm?.provider]?.base_url||'')}</div>
  </div>

  <div class="config-section">
    <div class="config-section-title">Push (ntfy)</div>
    ${cfgField('ntfy_server','Server URL',cfg.ntfy?.server)}
    ${cfgField('ntfy_topic','Topic',cfg.ntfy?.topic)}
  </div>

  <div class="config-section">
    <div class="config-section-title">Email (SMTP)</div>
    ${cfgField('smtp_host','SMTP Host',cfg.email?.smtp_host)}
    ${cfgField('smtp_port','SMTP Port',cfg.email?.smtp_port)}
    ${cfgField('smtp_user','Username',cfg.email?.smtp_user)}
    ${cfgPassword('smtp_password','Password',cfg.email?.smtp_password)}
    ${cfgField('email_to','Send To',cfg.email?.to_address)}
  </div>

  <div class="config-section">
    <div class="config-section-title">Notifications</div>
    ${notifTypes.map(type => {
      const nc = cfg.notifications?.[type] || {};
      return `<div class="config-row">
        <div>
          <div class="config-row-label">${notifLabels[type]}</div>
          <select id="nchan-${type}" style="margin-top:4px;width:120px;padding:4px 8px;font-size:13px;">
            ${['push','email','both'].map(c=>`<option ${nc.channel===c?'selected':''}>${c}</option>`).join('')}
          </select>
        </div>
        <label class="toggle"><input type="checkbox" id="nen-${type}" ${nc.enabled?'checked':''}><span class="toggle-slider"></span></label>
      </div>`;
    }).join('')}
  </div>

  <div class="config-section">
    <div class="config-section-title">API</div>
    ${cfgField('api_port','Port',cfg.api?.port)}
    ${cfgPassword('api_token','Secret Token',cfg.api?.secret_token)}
  </div>

  <button class="btn btn-primary btn-full" onclick="saveConfigForm()" style="margin-bottom:20px;">Save Config</button>
  `;
}

function cfgField(id, label, value) {
  return `<div class="form-group"><label class="form-label">${label}</label><input type="text" id="cfg-${id}" value="${escHtml(String(value||''))}"></div>`;
}

function cfgPassword(id, label, value) {
  return `<div class="form-group"><label class="form-label">${label}</label>
    <div style="display:flex;gap:6px;">
      <input type="password" id="cfg-${id}" value="${escHtml(String(value||''))}" style="flex:1;">
      <button class="btn btn-sm btn-ghost" onclick="togglePwd('cfg-${id}')">👁</button>
    </div>
  </div>`;
}

window.togglePwd = function(id) {
  const el = document.getElementById(id);
  el.type = el.type === 'password' ? 'text' : 'password';
};

window.showLlmFields = function() {
  const p = document.getElementById('cfg-llm-provider').value;
  const hasUrl = ['ollama','vllm'].includes(p);
  document.getElementById('llm-api-key-row').style.display = hasUrl ? 'none' : 'block';
  document.getElementById('llm-base-url-row').style.display = hasUrl ? 'block' : 'none';
};

window.saveConfigForm = async function() {
  const provider = document.getElementById('cfg-llm-provider').value;
  const hasUrl = ['ollama','vllm'].includes(provider);

  const body = { config: {
    vault_path: document.getElementById('cfg-vault_path').value,
    database_path: document.getElementById('cfg-database_path').value,
    log_path: document.getElementById('cfg-log_path').value,
    llm: { provider, [provider]: {
      model: document.getElementById('cfg-llm_model').value,
      ...(hasUrl
        ? { base_url: document.getElementById('cfg-llm_base_url').value }
        : { api_key: document.getElementById('cfg-llm_api_key').value })
    }},
    ntfy: { server: document.getElementById('cfg-ntfy_server').value, topic: document.getElementById('cfg-ntfy_topic').value },
    email: {
      smtp_host: document.getElementById('cfg-smtp_host').value,
      smtp_port: parseInt(document.getElementById('cfg-smtp_port').value) || 587,
      smtp_user: document.getElementById('cfg-smtp_user').value,
      smtp_password: document.getElementById('cfg-smtp_password').value,
      to_address: document.getElementById('cfg-email_to').value,
      from_address: document.getElementById('cfg-smtp_user').value,
    },
    api: {
      host: '0.0.0.0',
      port: parseInt(document.getElementById('cfg-api_port').value) || 8742,
      secret_token: document.getElementById('cfg-api_token').value,
    },
  }};

  // Notification toggles
  body.config.notifications = {};
  ['due_soon','goal_overdue','daily_morning_briefing','weekly_digest','end_of_week_summary','inbox_review','beginning_of_month','end_of_month'].forEach(t => {
    body.config.notifications[t] = {
      enabled: document.getElementById(`nen-${t}`)?.checked ?? true,
      channel: document.getElementById(`nchan-${t}`)?.value || 'push',
    };
  });

  try {
    await api('PUT', '/config', body);
    toast('Config saved!');
    // Update local token if changed
    const newToken = document.getElementById('cfg-api_token').value;
    if (newToken) saveToken(newToken);
  } catch (e) { toast(`Error: ${e.message}`); }
};

window.validateYaml = function() {
  // Client-side YAML validation via server
  api('PUT', '/config/raw', { yaml: document.getElementById('raw-yaml-editor').value })
    .then(() => toast('Valid YAML! Config saved.'))
    .catch(e => toast(`Invalid: ${e.message}`));
};

window.saveRawYaml = async function() {
  try {
    await api('PUT', '/config/raw', { yaml: document.getElementById('raw-yaml-editor').value });
    toast('Config saved!');
  } catch (e) { toast(`Error: ${e.message}`); }
};

// ---------------------------------------------------------------------------
// Jobs view
// ---------------------------------------------------------------------------
register('jobs', async () => {
  const el = document.getElementById('main-content');
  el.innerHTML = `<div class="page-header"><span class="page-title">⏱ Jobs</span><button class="btn btn-sm btn-ghost" onclick="navigate('more')">← Back</button></div><div class="spinner"></div>`;

  try {
    const jobs = await api('GET', '/jobs');
    const labels = {
      scan: 'Vault Scan', check_due_dates: 'Due Date Check', daily_morning_briefing: 'Daily Briefing',
      weekly_digest: 'Weekly Digest', end_of_week_summary: 'End of Week Summary',
      inbox_review: 'Inbox Review', beginning_of_month: 'Beginning of Month', end_of_month: 'End of Month',
    };

    el.innerHTML = `
      <div class="page-header">
        <span class="page-title">⏱ Jobs</span>
        <button class="btn btn-sm btn-ghost" onclick="navigate('more')">← Back</button>
      </div>
      ${jobs.map(j => `
        <div class="job-row">
          <div class="job-info">
            <div class="job-name">${labels[j.id] || j.id}</div>
            <div class="job-times">
              Last: ${j.last_run ? relTime(j.last_run) : 'Never'} ·
              Next: ${j.next_run ? relTime(j.next_run) : 'N/A'}
            </div>
          </div>
          ${j.status === 'disabled'
            ? `<span style="color:var(--text-muted);font-size:12px;">Disabled</span>`
            : `<button class="btn btn-sm btn-primary" onclick="runJob('${j.id}')">▶ Run</button>`
          }
        </div>
      `).join('')}
    `;
  } catch (e) {
    el.innerHTML += `<p style="color:var(--danger)">Error: ${e.message}</p>`;
  }
});

window.runJob = async function(jobId) {
  toast(`Triggering ${jobId}…`);
  try {
    await api('POST', `/jobs/run/${jobId}`);
    toast(`Job triggered!`);
  } catch (e) { toast(`Error: ${e.message}`); }
};

function relTime(iso) {
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  if (diff < 0) {
    const s = Math.abs(diff) / 1000;
    if (s < 60) return 'in <1m';
    if (s < 3600) return `in ${Math.round(s/60)}m`;
    return `in ${Math.round(s/3600)}h`;
  }
  const s = diff / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s/60)}m ago`;
  if (s < 86400) return `${Math.round(s/3600)}h ago`;
  return d.toLocaleDateString();
}

// ---------------------------------------------------------------------------
// Logs view
// ---------------------------------------------------------------------------
let _tailInterval = null;

register('logs', async () => {
  if (_tailInterval) { clearInterval(_tailInterval); _tailInterval = null; }
  const el = document.getElementById('main-content');
  el.innerHTML = `<div class="page-header"><span class="page-title">📋 Logs</span><button class="btn btn-sm btn-ghost" onclick="navigate('more')">← Back</button></div><div class="spinner"></div>`;

  try {
    const files = await api('GET', '/logs');
    const names = files.map(f => f.name);

    el.innerHTML = `
      <div class="page-header">
        <span class="page-title">📋 Logs</span>
        <button class="btn btn-sm btn-ghost" onclick="navigate('more')">← Back</button>
      </div>
      <div class="form-group">
        <select id="log-file-picker" onchange="loadLog()">
          ${names.map(n=>`<option>${n}</option>`).join('')}
        </select>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;">
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;">
          <input type="checkbox" id="tail-toggle" onchange="toggleTail()"> Live tail (5s)
        </label>
        <input type="text" id="log-search" placeholder="Filter lines…" style="flex:1;min-width:120px;" oninput="filterLog()">
        <div class="filter-row" style="margin:0;">
          ${['ALL','INFO','WARNING','ERROR'].map(l => `<button class="filter-chip ${l==='ALL'?'active':''}" data-level="${l}" onclick="setLogLevel(this,'${l}')">${l}</button>`).join('')}
        </div>
      </div>
      <div id="log-content">Loading…</div>
    `;

    window._logLines = [];
    window._logLevel = 'ALL';
    if (names.length) loadLog();
  } catch (e) {
    el.innerHTML += `<p style="color:var(--danger)">Error: ${e.message}</p>`;
  }
});

window.loadLog = async function() {
  const file = document.getElementById('log-file-picker').value;
  if (!file) return;
  try {
    const res = await api('GET', `/logs/${file}/tail?n=200`);
    window._logLines = res.lines;
    renderLog();
  } catch (e) { document.getElementById('log-content').textContent = `Error: ${e.message}`; }
};

window.filterLog = renderLog;

window.setLogLevel = function(btn, level) {
  document.querySelectorAll('[data-level]').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  window._logLevel = level;
  renderLog();
};

function renderLog() {
  const el = document.getElementById('log-content');
  if (!el) return;
  const search = (document.getElementById('log-search')?.value || '').toLowerCase();
  const level = window._logLevel || 'ALL';
  let lines = window._logLines || [];
  if (level !== 'ALL') lines = lines.filter(l => l.includes(`| ${level} |`));
  if (search) lines = lines.filter(l => l.toLowerCase().includes(search));
  el.textContent = lines.slice().reverse().join('\n') || '(no matching lines)';
}

window.toggleTail = function() {
  const on = document.getElementById('tail-toggle').checked;
  if (on) {
    _tailInterval = setInterval(window.loadLog, 5000);
  } else {
    clearInterval(_tailInterval);
    _tailInterval = null;
  }
};

// ---------------------------------------------------------------------------
// TV Dashboard view (fullscreen information radiator)
// ---------------------------------------------------------------------------

register('tv', async () => {
  if (window._tvInterval) { clearInterval(window._tvInterval); window._tvInterval = null; }
  if (window._tvClockInterval) { clearInterval(window._tvClockInterval); window._tvClockInterval = null; }

  const nav = document.getElementById('nav');
  if (nav) nav.style.display = 'none';

  const mc = document.getElementById('main-content');
  mc.style.cssText = 'padding:0;overflow:hidden;height:100vh;display:flex;flex-direction:column;';
  mc.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;color:var(--text-muted);font-size:18px;">Loading TV dashboard…</div>';

  window._tvLastRefresh = Date.now();

  async function refreshTV() {
    try {
      const [goals, dailyData] = await Promise.all([
        api('GET', '/goals'),
        api('GET', '/daily?days=1'),
      ]);
      window._tvLastRefresh = Date.now();
      mc.innerHTML = buildTvHTML(goals, dailyData);
    } catch (e) {
      mc.innerHTML = `
        <div class="tv-shell">
          <div style="flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:16px;">
            <div style="color:var(--danger);font-size:18px;">Error: ${escHtml(e.message)}</div>
            <button class="btn btn-sm btn-ghost" onclick="navigate('dashboard')">← Back to Dashboard</button>
          </div>
        </div>`;
    }
  }

  await refreshTV();

  // Combined clock + countdown in one 1-second interval
  window._tvClockInterval = setInterval(() => {
    const clockEl = document.getElementById('tv-clock');
    if (clockEl) {
      clockEl.textContent = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }
    const cdEl = document.getElementById('tv-countdown');
    if (cdEl) {
      const elapsed = Math.floor((Date.now() - (window._tvLastRefresh || Date.now())) / 1000);
      cdEl.textContent = Math.max(0, 60 - elapsed) + 's';
    }
  }, 1000);

  // Auto-refresh every 60s
  window._tvInterval = setInterval(refreshTV, 60000);
});

function buildTvHTML(goals, dailyData) {
  const today = localDateStr();
  const in7 = localDateStr(7);

  const todayEntry = dailyData[0] || { items: [] };
  const dailyItems = todayEntry.items || [];
  const doneCount = dailyItems.filter(i => i.status === 'Completed').length;
  const totalCount = dailyItems.length;
  const pct = totalCount ? Math.round(doneCount / totalCount * 100) : 0;

  const activeCount = goals.filter(g => g.depth === 0 && g.status === 'Active' && !g.is_milestone).length;
  const dueThisWeek = goals.filter(g => g.due_date && g.due_date >= today && g.due_date <= in7 && g.status !== 'Completed');
  const overdue = goals.filter(g => g.due_date && g.due_date < today && g.status !== 'Completed');

  const dateStr = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' }).toUpperCase();
  const timeStr = new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  return `
    <div class="tv-shell">
      <header class="tv-header">
        <div class="tv-brand">⚒️ GOAL FORGE</div>
        <div style="text-align:center;">
          <div style="font-size:12px;color:var(--text-muted);letter-spacing:0.06em;margin-bottom:2px;">${dateStr}</div>
          <div id="tv-clock" class="tv-clock">${timeStr}</div>
        </div>
        <div class="tv-stats">
          <div class="tv-stat-pill">
            <span class="tv-stat-num" style="color:#60a5fa">${activeCount}</span>
            <span>Active</span>
          </div>
          <div class="tv-stat-pill">
            <span class="tv-stat-num" style="color:${dueThisWeek.length ? '#fbbf24' : 'var(--text-muted)'}">${dueThisWeek.length}</span>
            <span>Due Soon</span>
          </div>
          <div class="tv-stat-pill">
            <span class="tv-stat-num" style="color:${overdue.length ? '#f87171' : 'var(--text-muted)'}">${overdue.length}</span>
            <span>Overdue</span>
          </div>
          <div class="tv-stat-pill">
            <span class="tv-stat-num" style="color:#4ade80">${pct}%</span>
            <span>Today</span>
          </div>
        </div>
      </header>
      <main class="tv-body">
        ${buildTvTodayCol(dailyItems, doneCount, totalCount, pct)}
        ${buildTvGoalsCol(goals)}
        ${buildTvUpcomingCol(dueThisWeek, overdue, today)}
      </main>
      <footer class="tv-footer">
        <span>Updated ${new Date().toLocaleTimeString()}</span>
        <span>Auto-refresh in <span id="tv-countdown">60</span>s</span>
        <button class="btn btn-sm btn-ghost" onclick="navigate('dashboard')" style="font-size:12px;padding:4px 12px;">Exit TV Mode ×</button>
      </footer>
    </div>`;
}

function buildTvTodayCol(items, doneCount, totalCount, pct) {
  const fillColor = pct >= 67 ? '#4ade80' : pct >= 34 ? '#fbbf24' : '#f87171';
  const progressHtml = totalCount ? `
    <div style="margin-bottom:18px;">
      <div style="background:var(--surface2);border-radius:4px;height:8px;overflow:hidden;margin-bottom:6px;">
        <div style="height:100%;width:${pct}%;background:${fillColor};border-radius:4px;transition:width 0.5s;"></div>
      </div>
      <div style="font-size:13px;color:var(--text-muted);">${doneCount} / ${totalCount} complete</div>
    </div>` : '';

  const listHtml = items.length
    ? items.map(item => {
        const done = item.status === 'Completed';
        return `<div class="tv-daily-item${done ? ' tv-daily-done' : ''}">
          <span class="tv-daily-icon">${done ? '✓' : '○'}</span>
          <span>${escHtml(item.name)}</span>
        </div>`;
      }).join('')
    : '<div style="color:var(--text-muted);font-style:italic;font-size:14px;">No items today</div>';

  return `
    <section class="tv-col">
      <div class="tv-col-header">📋 TODAY</div>
      ${progressHtml}
      <div class="tv-daily-list">${listHtml}</div>
    </section>`;
}

function buildTvGoalsCol(goals) {
  const HORIZONS = ['Life', 'Yearly', 'Quarterly', 'Monthly', 'Weekly'];
  const HORIZON_META = {
    Life:      { icon: '🌍', color: '#a78bfa' },
    Yearly:    { icon: '🏆', color: '#fbbf24' },
    Quarterly: { icon: '📊', color: '#38bdf8' },
    Monthly:   { icon: '🗓', color: '#4ade80' },
    Weekly:    { icon: '📅', color: '#94a3b8' },
  };

  // Build full tree (preserve parent-child links), then filter at render time
  const tree = buildTree(goals);

  // Group depth-0 active/backlog full goals by horizon
  const grouped = {};
  HORIZONS.forEach(h => { grouped[h] = []; });
  tree.filter(n => n.depth === 0 && !n.is_milestone && (n.status === 'Active' || n.status === 'Backlog'))
    .forEach(n => { if (grouped[n.horizon]) grouped[n.horizon].push(n); });

  let html = '';
  for (const h of HORIZONS) {
    if (!grouped[h].length) continue;
    const meta = HORIZON_META[h] || { icon: '•', color: '#94a3b8' };
    html += `
      <div class="tv-horizon-group">
        <div class="tv-horizon-label" style="color:${meta.color}">${meta.icon} ${h.toUpperCase()}</div>
        <div class="tv-horizon-goals">
          ${grouped[h].map(n => renderTvNode(n, 0, meta.color)).join('')}
        </div>
      </div>`;
  }

  return `
    <section class="tv-col tv-col-center">
      <div class="tv-col-header">🌳 STRATEGIC GOALS</div>
      ${html || '<div style="color:var(--text-muted);font-style:italic;">No active goals</div>'}
    </section>`;
}

function renderTvNode(node, depth, accentColor) {
  if (depth > 1) return '';
  const children = (node._children || [])
    .filter(c => !c.is_milestone && (c.status === 'Active' || c.status === 'Backlog'))
    .slice(0, 5);
  const indent = depth * 16;
  const isRoot = depth === 0;

  return `
    <div class="tv-goal-node${isRoot ? ' tv-goal-root' : ' tv-goal-child'}" style="padding-left:${indent}px;">
      <span style="color:${isRoot ? accentColor : 'var(--border)'};flex-shrink:0;">${isRoot ? '●' : '└'}</span>
      <span class="tv-goal-name">${escHtml(node.name)}</span>
      ${node.status === 'Backlog' ? '<span class="tv-badge-backlog">Backlog</span>' : ''}
      ${node.due_date ? `<span class="tv-goal-due">${tvDaysLabel(node.due_date)}</span>` : ''}
    </div>
    ${children.map(c => renderTvNode(c, depth + 1, accentColor)).join('')}`;
}

function buildTvUpcomingCol(dueThisWeek, overdue, today) {
  function urgency(dueDate) {
    const due = new Date(dueDate + 'T12:00:00');
    const now = new Date(today + 'T12:00:00');
    const days = Math.round((due - now) / 86400000);
    if (days < 0)  return { color: '#f87171', label: `${Math.abs(days)}d overdue`, pulse: true };
    if (days === 0) return { color: '#f87171', label: 'Today!', pulse: false };
    if (days <= 2)  return { color: '#f97316', label: `in ${days}d`, pulse: false };
    if (days <= 5)  return { color: '#fbbf24', label: `in ${days}d`, pulse: false };
    return { color: '#4ade80', label: `in ${days}d`, pulse: false };
  }

  const upcomingHtml = dueThisWeek.slice(0, 8).map(g => {
    const u = urgency(g.due_date);
    return `<div class="tv-due-item">
      <div class="tv-due-name">${escHtml(g.name)}</div>
      <div class="tv-due-meta" style="color:${u.color}">${u.label}</div>
    </div>`;
  }).join('');

  const overdueHtml = overdue.slice(0, 6).map(g => {
    const u = urgency(g.due_date);
    return `<div class="tv-due-item tv-overdue-pulse">
      <div class="tv-due-name" style="color:#f87171">${escHtml(g.name)}</div>
      <div class="tv-due-meta" style="color:#f87171">${u.label}</div>
    </div>`;
  }).join('');

  return `
    <section class="tv-col">
      <div class="tv-col-header">📅 COMING UP</div>
      ${dueThisWeek.length
        ? `<div class="tv-section-label">DUE THIS WEEK</div>${upcomingHtml}`
        : '<div style="color:var(--text-muted);font-style:italic;font-size:14px;">Nothing due this week 🎉</div>'
      }
      ${overdue.length ? `
        <div class="tv-section-label" style="color:#f87171;margin-top:20px;">🚨 OVERDUE</div>
        ${overdueHtml}
      ` : ''}
    </section>`;
}

function tvDaysLabel(dueDate) {
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const due = new Date(dueDate + 'T00:00:00');
  const days = Math.round((due - today) / 86400000);
  if (days < 0)  return `<span style="color:#f87171;font-size:11px;">${Math.abs(days)}d over</span>`;
  if (days === 0) return `<span style="color:#f87171;font-size:11px;">Today</span>`;
  if (days <= 3)  return `<span style="color:#f97316;font-size:11px;">in ${days}d</span>`;
  return `<span style="color:var(--text-muted);font-size:11px;">in ${days}d</span>`;
}

// ---------------------------------------------------------------------------
// Goal promote/demote API endpoints (wired up in capture.py)
// ---------------------------------------------------------------------------

// Extra endpoints needed
async function extraEndpointSetup() {
  // These are handled by FastAPI routes in capture.py
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
function initApp() {
  if (!TOKEN) { showLogin(); return; }
  buildShell();
  const hash = window.location.hash.slice(1);
  navigate(hash && routes[hash] ? hash : 'dashboard');
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/service-worker.js');
  }
}

document.addEventListener('DOMContentLoaded', initApp);
