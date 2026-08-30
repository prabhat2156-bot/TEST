/* webapp/static/admin.js — BASS TG STORE web admin panel */

const $  = (sel, root=document) => root.querySelector(sel);
const $$ = (sel, root=document) => Array.from(root.querySelectorAll(sel));

// ── toast ────────────────────────────────────────────────────────────────
function toast(msg, isErr=false){
  const t = $('#toast');
  t.textContent = msg;
  t.classList.toggle('err', isErr);
  t.classList.add('show');
  clearTimeout(toast._h);
  toast._h = setTimeout(()=>t.classList.remove('show'), 2600);
}

// ── fetch helper ────────────────────────────────────────────────────────
async function api(path, opts={}){
  const res = await fetch('/api' + path, {
    headers: {'Content-Type':'application/json'},
    ...opts,
  });
  if (res.status === 401){ window.location.href = '/login'; return null; }
  const data = await res.json().catch(()=>({ok:false, error:'bad_response'}));
  if (!data.ok) throw new Error(data.error || 'request_failed');
  return data;
}
const apiGet = (path) => api(path);
const apiPost = (path, body) => api(path, {method:'POST', body: JSON.stringify(body||{})});
const apiDelete = (path) => api(path, {method:'DELETE'});

function esc(s){
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function fmtDate(iso){
  if (!iso) return '—';
  try{
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString('en-GB', {day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit'});
  }catch(e){ return iso; }
}
function badge(status){
  const s = (status||'').toLowerCase();
  let cls = 'pending';
  if (['delivered','completed','confirmed','active','approved','closed','visible'].includes(s)) cls = 'ok';
  if (['failed','expired','cancelled','rejected','refunded','banned'].includes(s)) cls = 'fail';
  return `<span class="badge ${cls}">${esc(status||'—')}</span>`;
}

// ── navigation (sidebar + mobile drawer) ───────────────────────────────
const sidebar   = $('.sidebar');
const backdrop   = $('#backdrop');
const hamburger  = $('#hamburgerBtn');
const navItems   = $$('.nav-item');
const views      = $$('.view');

function closeDrawer(){ sidebar.classList.remove('open'); backdrop.classList.remove('open'); }
function openDrawer(){ sidebar.classList.add('open'); backdrop.classList.add('open'); }
if (hamburger) hamburger.addEventListener('click', openDrawer);
backdrop.addEventListener('click', closeDrawer);

const loaders = {}; // view -> function
function showView(name){
  navItems.forEach(i => i.classList.toggle('active', i.dataset.view === name));
  views.forEach(v => v.classList.toggle('active', v.id === 'view-' + name));
  closeDrawer();
  window.scrollTo(0,0);
  if (loaders[name]) loaders[name]();
}
navItems.forEach(item => item.addEventListener('click', () => showView(item.dataset.view)));
$$('.quick-btn[data-goto]').forEach(b => b.addEventListener('click', () => showView(b.dataset.goto)));

// ═══════════════════════════════════════════════════════════════════════
// DASHBOARD
// ═══════════════════════════════════════════════════════════════════════
loaders.dashboard = async function(){
  try{
    const d = await apiGet('/dashboard');
    const s = d.stats, t = d.today;
    $('#dashStats').innerHTML = `
      <div class="stat-card"><div class="label">Today's Orders</div><div class="value">${t.ord_count}</div></div>
      <div class="stat-card"><div class="label">Today's Revenue</div><div class="value">${Number(t.ord_amount).toFixed(2)}</div></div>
      <div class="stat-card"><div class="label">Deposits Today</div><div class="value">${t.dep_count}</div></div>
      <div class="stat-card"><div class="label">Deposit Amount</div><div class="value">${Number(t.dep_amount).toFixed(2)}</div></div>
      <div class="stat-card"><div class="label">Total Users</div><div class="value">${s.users}</div></div>
      <div class="stat-card"><div class="label">All-Time Revenue</div><div class="value">${Number(s.revenue).toFixed(2)}</div></div>
    `;
    $('#dashRecentOrders').innerHTML = d.recent_orders.length ? d.recent_orders.map(o => `
      <tr><td>#${o.order_code}</td><td>@${esc(o.username||o.user_id)}</td><td>${esc(o.product_name)}</td>
      <td>${Number(o.amount).toFixed(2)}</td><td>${badge(o.status)}</td></tr>`).join('')
      : `<tr><td colspan="5" class="empty">No orders yet.</td></tr>`;
  }catch(e){ toast('Failed to load dashboard', true); }
};

// ═══════════════════════════════════════════════════════════════════════
// ORDERS
// ═══════════════════════════════════════════════════════════════════════
let ordersScope = 'today';
async function loadOrders(){
  $('#ordersBody').innerHTML = `<tr><td colspan="7" class="loading">Loading…</td></tr>`;
  try{
    const d = await apiGet('/orders?scope=' + ordersScope);
    $('#ordersBody').innerHTML = d.orders.length ? d.orders.map(o => `
      <tr><td>#${o.order_code}</td><td>@${esc(o.username||o.user_id)}</td><td>${esc(o.product_name)}</td>
      <td>${Number(o.amount).toFixed(2)}</td><td>${badge(o.status)}</td><td>${fmtDate(o.created_at)}</td>
      <td><button class="btn sm" data-order-open="${o.order_code}">View Details</button></td></tr>`).join('')
      : `<tr><td colspan="7" class="empty">No orders found.</td></tr>`;
    $$('[data-order-open]').forEach(b => b.addEventListener('click', () => openOrderModal(b.dataset.orderOpen)));
  }catch(e){ $('#ordersBody').innerHTML = `<tr><td colspan="7" class="empty">Failed to load.</td></tr>`; }
}
loaders.orders = loadOrders;
$$('[data-order-scope]').forEach(b => b.addEventListener('click', () => { ordersScope = b.dataset.orderScope; loadOrders(); }));

const orderModalBackdrop = $('#orderModalBackdrop');
$('#orderModalClose').addEventListener('click', () => orderModalBackdrop.classList.remove('open'));
orderModalBackdrop.addEventListener('click', (e) => { if (e.target === orderModalBackdrop) orderModalBackdrop.classList.remove('open'); });

async function openOrderModal(code){
  orderModalBackdrop.classList.add('open');
  $('#orderModalId').textContent = '#' + code;
  $('#orderModalBody').innerHTML = 'Loading…';
  try{
    const d = await apiGet(`/orders/find?code=${encodeURIComponent(code)}`);
    const o = d.order;
    $('#orderModalId').textContent = '#' + o.order_code;
    $('#orderModalBody').innerHTML = `
      <div class="detail-grid">
        <div class="k">Status</div><div class="v">${badge(o.status)}</div>
        <div class="k">Item</div><div class="v">${esc(o.product_name)}</div>
        <div class="k">Amount</div><div class="v">${Number(o.amount||0).toFixed(2)} USDT</div>
        <div class="k">User</div><div class="v">@${esc(o.username||'')} (ID ${o.user_id})</div>
        <div class="k">Ordered</div><div class="v">${fmtDate(o.created_at)}</div>
      </div>
      <label style="font-size:11px; font-weight:700; text-transform:uppercase; color:#5b5646;">Delivered Item / Credential (ID + password / link)</label>
      <div class="cred-box">${esc(o.credential || '—')}</div>
    `;
  }catch(e){
    $('#orderModalBody').innerHTML = `<div class="empty">Order not found.</div>`;
  }
}

$('#orderFindBtn').addEventListener('click', () => {
  const code = $('#orderFindInput').value.trim();
  if (code) openOrderModal(code);
});
$('#orderFindInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') $('#orderFindBtn').click();
});

// ═══════════════════════════════════════════════════════════════════════
// DEPOSITS
// ═══════════════════════════════════════════════════════════════════════
let depScope = 'today';
async function loadDeposits(){
  $('#depositsBody').innerHTML = `<tr><td colspan="7" class="loading">Loading…</td></tr>`;
  try{
    const d = await apiGet('/deposits?scope=' + depScope);
    $('#depositsBody').innerHTML = d.deposits.length ? d.deposits.map(x => `
      <tr><td>#${x.id}</td><td>@${esc(x.username||x.user_id)}</td><td>${esc(x.network)}</td>
      <td>${Number(x.amount).toFixed(2)}</td><td>${badge(x.status)}</td>
      <td>${fmtDate(x.created_at)}</td>
      <td class="row-actions">${x.status==='pending' ? `
        <button class="btn sm" data-dep-approve="${x.id}">Approve</button>
        <button class="btn sm danger" data-dep-reject="${x.id}">Reject</button>` : '—'}</td></tr>`).join('')
      : `<tr><td colspan="7" class="empty">No deposits found.</td></tr>`;
    $$('[data-dep-approve]').forEach(b => b.addEventListener('click', async () => {
      try{ await apiPost(`/deposits/${b.dataset.depApprove}/approve`); toast('Deposit approved'); loadDeposits(); }
      catch(e){ toast('Failed: '+e.message, true); }
    }));
    $$('[data-dep-reject]').forEach(b => b.addEventListener('click', async () => {
      const reason = prompt('Reason for rejection (shown to user):', 'Could not verify payment') || '';
      try{ await apiPost(`/deposits/${b.dataset.depReject}/reject`, {reason}); toast('Deposit rejected'); loadDeposits(); }
      catch(e){ toast('Failed: '+e.message, true); }
    }));
  }catch(e){ $('#depositsBody').innerHTML = `<tr><td colspan="7" class="empty">Failed to load.</td></tr>`; }
}
loaders.deposits = loadDeposits;
$$('[data-dep-scope]').forEach(b => b.addEventListener('click', () => { depScope = b.dataset.depScope; loadDeposits(); }));

// ═══════════════════════════════════════════════════════════════════════
// MANUAL DEPOSIT / GIFT BALANCE
// ═══════════════════════════════════════════════════════════════════════
$('#manualDepositForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try{
    await apiPost('/manual-deposit', {user_id: f.get('user_id'), amount: f.get('amount'), txid: f.get('txid')});
    toast('Balance credited'); e.target.reset();
  }catch(err){ toast('Failed: '+err.message, true); }
});
$('#giftForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try{
    await apiPost('/gift', {user_id: f.get('user_id'), amount: f.get('amount')});
    toast('Gift sent'); e.target.reset();
  }catch(err){ toast('Failed: '+err.message, true); }
});

// ═══════════════════════════════════════════════════════════════════════
// REFUND REQUESTS  (list + full detail modal with credential + chat)
// ═══════════════════════════════════════════════════════════════════════
let refundScope = 'pending';
async function loadRefunds(){
  $('#refundsBody').innerHTML = `<tr><td colspan="6" class="loading">Loading…</td></tr>`;
  try{
    const d = await apiGet('/refunds?status=' + refundScope);
    $('#refundsBody').innerHTML = d.refunds.length ? d.refunds.map(r => `
      <tr><td>#${r.id}</td><td>@${esc(r.username)}</td><td>#${r.order_code} — ${esc(r.product_name||'')}</td>
      <td>${esc((r.reason||'').slice(0,40))}${(r.reason||'').length>40?'…':''}</td>
      <td>${badge(r.status)}</td>
      <td><button class="btn sm" data-refund-open="${r.id}">Open</button></td></tr>`).join('')
      : `<tr><td colspan="6" class="empty">No refund requests.</td></tr>`;
    $$('[data-refund-open]').forEach(b => b.addEventListener('click', () => openRefundModal(b.dataset.refundOpen)));
  }catch(e){ $('#refundsBody').innerHTML = `<tr><td colspan="6" class="empty">Failed to load.</td></tr>`; }
}
loaders.refunds = loadRefunds;
$$('[data-refund-scope]').forEach(b => b.addEventListener('click', () => { refundScope = b.dataset.refundScope; loadRefunds(); }));

const refundModalBackdrop = $('#refundModalBackdrop');
$('#refundModalClose').addEventListener('click', () => refundModalBackdrop.classList.remove('open'));
refundModalBackdrop.addEventListener('click', (e) => { if (e.target === refundModalBackdrop) refundModalBackdrop.classList.remove('open'); });

async function openRefundModal(rid){
  refundModalBackdrop.classList.add('open');
  $('#refundModalId').textContent = '#' + rid;
  $('#refundModalBody').innerHTML = 'Loading…';
  try{
    const d = await apiGet(`/refunds/${rid}`);
    const r = d.refund;
    const chatHtml = d.messages.length ? d.messages.map(m => `
      <div class="chat-bubble ${m.is_admin ? 'admin':'user'}">${esc(m.message)}
        <div class="meta">${m.is_admin ? 'Admin' : 'User'} · ${fmtDate(m.sent_at)}</div></div>`).join('')
      : `<div class="empty">No messages yet.</div>`;
    $('#refundModalBody').innerHTML = `
      <div class="detail-grid">
        <div class="k">Status</div><div class="v">${badge(r.status)}</div>
        <div class="k">Item</div><div class="v">${esc(r.order.product_name)}</div>
        <div class="k">Order ID</div><div class="v">#${r.order.order_code || r.order.id}</div>
        <div class="k">Amount</div><div class="v">${Number(r.order.amount||0).toFixed(2)} USDT</div>
        <div class="k">Ordered</div><div class="v">${fmtDate(r.order.created_at)}</div>
        <div class="k">User</div><div class="v">@${esc(r.user.username||'')} (ID ${r.user.id})</div>
        <div class="k">Balance</div><div class="v">${Number(r.user.balance||0).toFixed(2)} USDT</div>
        <div class="k">Requested</div><div class="v">${fmtDate(r.created_at)}</div>
      </div>
      <label style="font-size:11px; font-weight:700; text-transform:uppercase; color:#5b5646;">Reason</label>
      <div class="cred-box" style="border-style:solid; border-color:var(--line); margin-top:5px;">${esc(r.reason)}</div>
      <label style="font-size:11px; font-weight:700; text-transform:uppercase; color:#5b5646;">Delivered Item / Credential (ID + password)</label>
      <div class="cred-box">${esc(r.credential)}</div>

      <label style="font-size:11px; font-weight:700; text-transform:uppercase; color:#5b5646;">Chat with user</label>
      <div class="chat-thread" style="margin-top:6px;">${chatHtml}</div>
      <div class="chat-input-row">
        <input id="refundChatInput" placeholder="Type a message to the user…">
        <button class="btn sm" id="refundChatSend">Send</button>
      </div>

      ${r.status === 'pending' ? `
      <div class="modal-actions">
        <button class="btn primary" id="refundApproveBtn">Approve Refund</button>
        <button class="btn danger" id="refundRejectBtn">Reject</button>
      </div>` : `<div class="helptext" style="margin-top:12px;">This request is already ${esc(r.status)}.</div>`}
    `;
    if (r.status === 'pending'){
      $('#refundApproveBtn').addEventListener('click', async () => {
        try{ await apiPost(`/refunds/${rid}/approve`); toast('Refund approved'); refundModalBackdrop.classList.remove('open'); loadRefunds(); }
        catch(e){ toast('Failed: '+e.message, true); }
      });
      $('#refundRejectBtn').addEventListener('click', async () => {
        const note = prompt('Rejection note (shown to user):', 'Not eligible for refund') || '';
        try{ await apiPost(`/refunds/${rid}/reject`, {note}); toast('Refund rejected'); refundModalBackdrop.classList.remove('open'); loadRefunds(); }
        catch(e){ toast('Failed: '+e.message, true); }
      });
    }
    $('#refundChatSend').addEventListener('click', async () => {
      const msg = $('#refundChatInput').value.trim();
      if (!msg) return;
      try{ await apiPost(`/refunds/${rid}/message`, {message: msg}); openRefundModal(rid); }
      catch(e){ toast('Failed: '+e.message, true); }
    });
  }catch(e){ $('#refundModalBody').innerHTML = 'Failed to load request.'; }
}

// ═══════════════════════════════════════════════════════════════════════
// COUPONS
// ═══════════════════════════════════════════════════════════════════════
async function loadCoupons(){
  $('#couponsBody').innerHTML = `<tr><td colspan="5" class="loading">Loading…</td></tr>`;
  try{
    const d = await apiGet('/coupons');
    $('#couponsBody').innerHTML = d.coupons.length ? d.coupons.map(c => `
      <tr><td>${esc(c.code)}</td><td>${c.discount}%</td><td>${c.used_count} / ${c.max_uses}</td>
      <td>${badge(c.is_active ? 'active':'inactive')}</td>
      <td class="row-actions">
        <button class="btn sm" data-coupon-toggle="${c.id}">${c.is_active?'Disable':'Enable'}</button>
        <button class="btn sm danger" data-coupon-delete="${c.id}">Delete</button></td></tr>`).join('')
      : `<tr><td colspan="5" class="empty">No coupons yet.</td></tr>`;
    $$('[data-coupon-toggle]').forEach(b => b.addEventListener('click', async () => {
      await apiPost(`/coupons/${b.dataset.couponToggle}/toggle`); loadCoupons();
    }));
    $$('[data-coupon-delete]').forEach(b => b.addEventListener('click', async () => {
      if (!confirm('Delete this coupon?')) return;
      await apiDelete(`/coupons/${b.dataset.couponDelete}`); loadCoupons();
    }));
  }catch(e){ $('#couponsBody').innerHTML = `<tr><td colspan="5" class="empty">Failed to load.</td></tr>`; }
}
loaders.coupons = loadCoupons;
$('#couponForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try{
    await apiPost('/coupons', {code:f.get('code'), discount:f.get('discount'), max_uses:f.get('max_uses')});
    toast('Coupon created'); e.target.reset(); loadCoupons();
  }catch(err){ toast('Failed: '+err.message, true); }
});

// ═══════════════════════════════════════════════════════════════════════
// CATEGORIES / PRODUCTS
// ═══════════════════════════════════════════════════════════════════════
async function loadCategories(){
  $('#categoriesBody').innerHTML = `<tr><td colspan="4" class="loading">Loading…</td></tr>`;
  try{
    const d = await apiGet('/categories');
    $('#categoriesBody').innerHTML = d.categories.length ? d.categories.map(c => `
      <tr><td>${esc(c.emoji)} ${esc(c.name)}</td><td>${c.product_count}</td>
      <td>${badge(c.is_active ? 'visible':'hidden')}</td>
      <td class="row-actions">
        <button class="btn sm" data-cat-toggle="${c.id}">${c.is_active?'Hide':'Show'}</button>
        <button class="btn sm danger" data-cat-delete="${c.id}">Delete</button></td></tr>`).join('')
      : `<tr><td colspan="4" class="empty">No categories yet.</td></tr>`;
    $$('[data-cat-toggle]').forEach(b => b.addEventListener('click', async () => {
      await apiPost(`/categories/${b.dataset.catToggle}/toggle`); loadCategories();
    }));
    $$('[data-cat-delete]').forEach(b => b.addEventListener('click', async () => {
      if (!confirm('Delete this category? (must have no products)')) return;
      try{ await apiDelete(`/categories/${b.dataset.catDelete}`); loadCategories(); }
      catch(e){ toast('Category has products — remove them first', true); }
    }));
    // keep product-form category dropdown fresh
    const sel = $('#productCategorySelect');
    sel.innerHTML = d.categories.map(c => `<option value="${c.id}">${esc(c.emoji)} ${esc(c.name)}</option>`).join('');
  }catch(e){ $('#categoriesBody').innerHTML = `<tr><td colspan="4" class="empty">Failed to load.</td></tr>`; }
}
loaders.categories = loadCategories;
$('#categoryForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try{
    await apiPost('/categories', {name:f.get('name'), emoji:f.get('emoji')||'🛍️'});
    toast('Category added'); e.target.reset(); loadCategories();
  }catch(err){ toast('Failed: '+err.message, true); }
});

async function loadProducts(){
  $('#productsBody').innerHTML = `<tr><td colspan="5" class="loading">Loading…</td></tr>`;
  try{
    const [prodRes, catRes] = await Promise.all([apiGet('/products'), apiGet('/categories')]);
    const catMap = {}; catRes.categories.forEach(c => catMap[c.id] = c.name);
    $('#productsBody').innerHTML = prodRes.products.length ? prodRes.products.map(p => `
      <tr><td>${esc(p.emoji||'')} ${esc(p.name)}</td><td>${esc(catMap[p.category_id]||'—')}</td>
      <td>${Number(p.price_usdt).toFixed(2)}</td><td>${p.stock_count}</td>
      <td><button class="btn sm danger" data-prod-delete="${p.id}">Delete</button></td></tr>`).join('')
      : `<tr><td colspan="5" class="empty">No products yet.</td></tr>`;
    $$('[data-prod-delete]').forEach(b => b.addEventListener('click', async () => {
      if (!confirm('Delete this product and its stock?')) return;
      await apiDelete(`/products/${b.dataset.prodDelete}`); loadProducts();
    }));
    if (!$('#productCategorySelect').options.length){
      $('#productCategorySelect').innerHTML = catRes.categories.map(c => `<option value="${c.id}">${esc(c.emoji)} ${esc(c.name)}</option>`).join('');
    }
  }catch(e){ $('#productsBody').innerHTML = `<tr><td colspan="5" class="empty">Failed to load.</td></tr>`; }
}
loaders.products = loadProducts;
$('#productForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try{
    await apiPost('/products', {
      category_id: f.get('category_id'), name: f.get('name'), emoji: f.get('emoji')||'📦',
      emoji_id: f.get('emoji_id')||'',
      description: f.get('description')||'', price: f.get('price'), duration: f.get('duration')||''
    });
    toast('Product added'); e.target.reset(); loadProducts();
  }catch(err){ toast('Failed: '+err.message, true); }
});

// ═══════════════════════════════════════════════════════════════════════
// STOCK
// ═══════════════════════════════════════════════════════════════════════
async function loadStock(){
  $('#stockBody').innerHTML = `<tr><td colspan="3" class="loading">Loading…</td></tr>`;
  try{
    const d = await apiGet('/stock');
    $('#stockBody').innerHTML = d.products.length ? d.products.map(p => `
      <tr><td>${esc(p.emoji||'')} ${esc(p.name)}</td>
      <td style="${p.low?'color:#b23;font-weight:700;':''}">${p.stock_count}</td>
      <td class="row-actions">
        <button class="btn sm" data-stock-view="${p.id}">View</button>
        <button class="btn sm danger" data-stock-clear="${p.id}">Clear</button></td></tr>`).join('')
      : `<tr><td colspan="3" class="empty">No products yet.</td></tr>`;
    $$('[data-stock-view]').forEach(b => b.addEventListener('click', () => openStockModal(b.dataset.stockView)));
    $$('[data-stock-clear]').forEach(b => b.addEventListener('click', async () => {
      if (!confirm('Clear all unsold stock for this product?')) return;
      await apiPost(`/stock/${b.dataset.stockClear}/clear`); loadStock();
    }));
    const sel = $('#stockProductSelect');
    sel.innerHTML = d.products.map(p => `<option value="${p.id}">${esc(p.emoji||'')} ${esc(p.name)}</option>`).join('');
  }catch(e){ $('#stockBody').innerHTML = `<tr><td colspan="3" class="empty">Failed to load.</td></tr>`; }
}
loaders.stock = loadStock;

async function openStockModal(pid){
  $('#stockModalId').textContent = '#' + pid;
  $('#stockModalBody').innerHTML = `<div class="loading">Loading…</div>`;
  $('#stockModalBackdrop').classList.add('open');
  try{
    const d = await apiGet(`/stock/${pid}/items`);
    renderStockModal(pid, d.items);
  }catch(e){ $('#stockModalBody').innerHTML = `<div class="empty">Could not load stock.</div>`; }
}
function renderStockModal(pid, items){
  $('#stockModalBody').innerHTML = items.length ? `
    <div class="chat-thread" style="max-height:340px;">
      ${items.map(i => `
        <div class="cred-box" style="display:flex; justify-content:space-between; align-items:center; gap:10px;">
          <span style="word-break:break-all;">#${i.id} · ${esc(i.data)}</span>
          <span class="row-actions" style="flex-shrink:0;">
            <button class="btn sm" data-item-edit="${i.id}" data-item-data="${esc(i.data)}">Edit</button>
            <button class="btn sm danger" data-item-del="${i.id}">Delete</button>
          </span>
        </div>`).join('')}
    </div>` : `<div class="empty">No unsold stock for this product.</div>`;
  $$('[data-item-edit]').forEach(b => b.addEventListener('click', async () => {
    const current = b.dataset.itemData;
    const updated = prompt('Edit this item (email:password or link):', current);
    if (updated === null || !updated.trim()) return;
    try{
      await apiPost(`/stock/item/${b.dataset.itemEdit}`, {data: updated.trim()});
      toast('Item updated'); openStockModal(pid); loadStock();
    }catch(e){ toast('Failed: ' + e.message, true); }
  }));
  $$('[data-item-del]').forEach(b => b.addEventListener('click', async () => {
    if (!confirm('Delete this stock item?')) return;
    await apiDelete(`/stock/item/${b.dataset.itemDel}`);
    toast('Item deleted'); openStockModal(pid); loadStock();
  }));
}
$('#stockModalClose').addEventListener('click', () => $('#stockModalBackdrop').classList.remove('open'));
$('#stockModalBackdrop').addEventListener('click', (e) => { if (e.target.id === 'stockModalBackdrop') $('#stockModalBackdrop').classList.remove('open'); });
$('#stockAddForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try{
    const r = await apiPost('/stock/add', {product_id: f.get('product_id'), data: f.get('data')});
    toast(`Added ${r.added} item(s)${r.duplicates ? `, ${r.duplicates} duplicate(s) skipped`:''}`);
    e.target.reset(); loadStock();
  }catch(err){ toast('Failed: '+err.message, true); }
});

// ═══════════════════════════════════════════════════════════════════════
// FREE ITEMS
// ═══════════════════════════════════════════════════════════════════════
async function loadFreeItems(){
  $('#freeItemsBody').innerHTML = `<tr><td colspan="4" class="loading">Loading…</td></tr>`;
  try{
    const d = await apiGet('/free-items');
    $('#freeItemsBody').innerHTML = d.items.length ? d.items.map(i => `
      <tr><td>${esc(i.emoji)} ${esc(i.name)}</td><td>${i.remaining}</td>
      <td>${badge(i.is_active?'active':'inactive')}</td>
      <td class="row-actions">
        <button class="btn sm" data-free-stock="${i.id}">Add Stock</button>
        <button class="btn sm" data-free-toggle="${i.id}">Toggle</button>
        <button class="btn sm danger" data-free-delete="${i.id}">Delete</button></td></tr>`).join('')
      : `<tr><td colspan="4" class="empty">No free items yet.</td></tr>`;
    $$('[data-free-toggle]').forEach(b => b.addEventListener('click', async () => { await apiPost(`/free-items/${b.dataset.freeToggle}/toggle`); loadFreeItems(); }));
    $$('[data-free-delete]').forEach(b => b.addEventListener('click', async () => {
      if (!confirm('Delete this free item?')) return;
      await apiDelete(`/free-items/${b.dataset.freeDelete}`); loadFreeItems();
    }));
    $$('[data-free-stock]').forEach(b => b.addEventListener('click', async () => {
      const data = prompt('Paste stock, one item per line:');
      if (!data) return;
      const r = await apiPost(`/free-items/${b.dataset.freeStock}/stock`, {data});
      toast(`Added ${r.added} item(s)`); loadFreeItems();
    }));
  }catch(e){ $('#freeItemsBody').innerHTML = `<tr><td colspan="4" class="empty">Failed to load.</td></tr>`; }
}
loaders['free-items'] = loadFreeItems;
$('#freeItemForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try{
    await apiPost('/free-items', {name:f.get('name'), emoji:f.get('emoji')||'🎁'});
    toast('Free item added'); e.target.reset(); loadFreeItems();
  }catch(err){ toast('Failed: '+err.message, true); }
});

// ═══════════════════════════════════════════════════════════════════════
// USERS
// ═══════════════════════════════════════════════════════════════════════
async function loadUsers(){
  const q = $('#userSearchInput').value.trim();
  $('#usersBody').innerHTML = `<tr><td colspan="4" class="loading">Loading…</td></tr>`;
  try{
    const d = await apiGet('/users' + (q ? '?q=' + encodeURIComponent(q) : ''));
    $('#usersBody').innerHTML = d.users.length ? d.users.map(u => `
      <tr><td>@${esc(u.username||u.user_id)}${u.is_banned?' 🚫':''}</td>
      <td>${Number(u.balance||0).toFixed(2)}</td><td>${u.total_orders||0}</td>
      <td class="row-actions">
        <button class="btn sm" data-user-addbal="${u.user_id}">+ Balance</button>
        <button class="btn sm" data-user-rembal="${u.user_id}">− Balance</button>
        <button class="btn sm danger" data-user-ban="${u.user_id}" data-banned="${u.is_banned?1:0}">${u.is_banned?'Unban':'Ban'}</button></td></tr>`).join('')
      : `<tr><td colspan="4" class="empty">No users found.</td></tr>`;
    $$('[data-user-addbal]').forEach(b => b.addEventListener('click', async () => {
      const amt = prompt('Amount to add (USDT):'); if (!amt) return;
      await apiPost(`/users/${b.dataset.userAddbal}/balance`, {delta: parseFloat(amt)}); toast('Balance updated'); loadUsers();
    }));
    $$('[data-user-rembal]').forEach(b => b.addEventListener('click', async () => {
      const amt = prompt('Amount to remove (USDT):'); if (!amt) return;
      await apiPost(`/users/${b.dataset.userRembal}/balance`, {delta: -Math.abs(parseFloat(amt))}); toast('Balance updated'); loadUsers();
    }));
    $$('[data-user-ban]').forEach(b => b.addEventListener('click', async () => {
      const nowBanned = b.dataset.banned === '1';
      await apiPost(`/users/${b.dataset.userBan}/ban`, {ban: !nowBanned}); loadUsers();
    }));
  }catch(e){ $('#usersBody').innerHTML = `<tr><td colspan="4" class="empty">Failed to load.</td></tr>`; }
}
loaders.users = loadUsers;
let userSearchTimer;
$('#userSearchInput').addEventListener('input', () => { clearTimeout(userSearchTimer); userSearchTimer = setTimeout(loadUsers, 350); });

// ── user history ──
async function loadUserHistory(){
  const uid = $('#userHistorySearch').value.trim();
  if (!uid) return;
  $('#userHistoryBody').innerHTML = `<tr><td colspan="4" class="loading">Loading…</td></tr>`;
  try{
    const d = await apiGet(`/users/${uid}/history`);
    $('#userHistoryName').textContent = '@' + (d.user.username || d.user.user_id) + ` — balance ${Number(d.user.balance).toFixed(2)} USDT`;
    $('#userHistoryBody').innerHTML = d.history.length ? d.history.map(h => `
      <tr><td>${fmtDate(h.created_at)}</td><td>${h.type==='order'?'Order':'Deposit'}</td>
      <td>${esc(h.product_name || h.extra || '')}</td>
      <td>${h.type==='order'?'−':'+'}${Number(h.amount).toFixed(2)}</td></tr>`).join('')
      : `<tr><td colspan="4" class="empty">No history for this user.</td></tr>`;
  }catch(e){ $('#userHistoryBody').innerHTML = `<tr><td colspan="4" class="empty">User not found.</td></tr>`; }
}
loaders['user-history'] = () => {};
$('#userHistoryGoBtn').addEventListener('click', loadUserHistory);
$('#userHistorySearch').addEventListener('keydown', (e) => { if (e.key === 'Enter') loadUserHistory(); });

// ═══════════════════════════════════════════════════════════════════════
// TICKETS
// ═══════════════════════════════════════════════════════════════════════
let ticketScope = 'open';
async function loadTickets(){
  $('#ticketsBody').innerHTML = `<tr><td colspan="5" class="loading">Loading…</td></tr>`;
  try{
    const d = await apiGet('/tickets?status=' + ticketScope);
    $('#ticketsBody').innerHTML = d.tickets.length ? d.tickets.map(t => `
      <tr><td>#${t.id}</td><td>@${esc(t.username||t.user_id)}</td><td>${esc(t.subject)}</td>
      <td>${badge(t.status)}</td><td><button class="btn sm" data-ticket-open="${t.id}">Open</button></td></tr>`).join('')
      : `<tr><td colspan="5" class="empty">No tickets.</td></tr>`;
    $$('[data-ticket-open]').forEach(b => b.addEventListener('click', () => openTicketModal(b.dataset.ticketOpen)));
  }catch(e){ $('#ticketsBody').innerHTML = `<tr><td colspan="5" class="empty">Failed to load.</td></tr>`; }
}
loaders.tickets = loadTickets;
$$('[data-ticket-scope]').forEach(b => b.addEventListener('click', () => { ticketScope = b.dataset.ticketScope; loadTickets(); }));

const ticketModalBackdrop = $('#ticketModalBackdrop');
$('#ticketModalClose').addEventListener('click', () => ticketModalBackdrop.classList.remove('open'));
ticketModalBackdrop.addEventListener('click', (e) => { if (e.target === ticketModalBackdrop) ticketModalBackdrop.classList.remove('open'); });

async function openTicketModal(tid){
  ticketModalBackdrop.classList.add('open');
  $('#ticketModalId').textContent = '#' + tid;
  $('#ticketModalBody').innerHTML = 'Loading…';
  try{
    const d = await apiGet(`/tickets/${tid}`);
    const chatHtml = d.messages.length ? d.messages.map(m => `
      <div class="chat-bubble ${m.is_admin?'admin':'user'}">${esc(m.message)}<div class="meta">${m.is_admin?'Admin':'User'} · ${fmtDate(m.sent_at)}</div></div>`).join('')
      : `<div class="empty">No messages yet.</div>`;
    $('#ticketModalBody').innerHTML = `
      <div class="detail-grid">
        <div class="k">Subject</div><div class="v">${esc(d.ticket.subject)}</div>
        <div class="k">Status</div><div class="v">${badge(d.ticket.status)}</div>
        <div class="k">Opened</div><div class="v">${fmtDate(d.ticket.created_at)}</div>
      </div>
      <div class="chat-thread">${chatHtml}</div>
      <div class="chat-input-row">
        <input id="ticketChatInput" placeholder="Type a reply…">
        <button class="btn sm" id="ticketChatSend">Send</button>
      </div>
      ${d.ticket.status === 'open' ? `<div class="modal-actions"><button class="btn danger" id="ticketCloseBtn">Close Ticket</button></div>` : ''}
    `;
    $('#ticketChatSend').addEventListener('click', async () => {
      const msg = $('#ticketChatInput').value.trim();
      if (!msg) return;
      try{ await apiPost(`/tickets/${tid}/reply`, {message: msg}); openTicketModal(tid); }
      catch(e){ toast('Failed: '+e.message, true); }
    });
    if (d.ticket.status === 'open'){
      $('#ticketCloseBtn').addEventListener('click', async () => {
        await apiPost(`/tickets/${tid}/close`); ticketModalBackdrop.classList.remove('open'); loadTickets();
      });
    }
  }catch(e){ $('#ticketModalBody').innerHTML = 'Failed to load ticket.'; }
}

// ═══════════════════════════════════════════════════════════════════════
// ADMINS
// ═══════════════════════════════════════════════════════════════════════
async function loadAdmins(){
  $('#adminsBody').innerHTML = `<tr><td colspan="3" class="loading">Loading…</td></tr>`;
  try{
    const d = await apiGet('/admins');
    const rows = d.owners.map(id => `<tr><td>${id}</td><td>${badge('Owner')}</td><td>—</td></tr>`)
      .concat(d.extra_admins.map(id => `<tr><td>${id}</td><td>${badge('Admin')}</td>
        <td><button class="btn sm danger" data-admin-remove="${id}">Remove</button></td></tr>`));
    $('#adminsBody').innerHTML = rows.join('') || `<tr><td colspan="3" class="empty">No admins.</td></tr>`;
    $$('[data-admin-remove]').forEach(b => b.addEventListener('click', async () => {
      if (!confirm('Remove this admin?')) return;
      await apiDelete(`/admins/${b.dataset.adminRemove}`); loadAdmins();
    }));
  }catch(e){ $('#adminsBody').innerHTML = `<tr><td colspan="3" class="empty">Failed to load.</td></tr>`; }
}
loaders.admins = loadAdmins;
$('#adminAddForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try{ await apiPost('/admins', {user_id: f.get('user_id')}); toast('Admin added'); e.target.reset(); loadAdmins(); }
  catch(err){ toast('Failed: '+err.message, true); }
});

// ═══════════════════════════════════════════════════════════════════════
// BROADCAST
// ═══════════════════════════════════════════════════════════════════════
$('#broadcastForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  if (!confirm('Send this message to ALL users?')) return;
  try{
    const r = await apiPost('/broadcast', {message: f.get('message')});
    toast(`Sent to ${r.sent} users (${r.failed} failed)`); e.target.reset();
  }catch(err){ toast('Failed: '+err.message, true); }
});

// ═══════════════════════════════════════════════════════════════════════
// DAILY HISTORY
// ═══════════════════════════════════════════════════════════════════════
loaders['daily-history'] = async function(){
  $('#dailyHistoryBody').innerHTML = `<tr><td colspan="4" class="loading">Loading…</td></tr>`;
  try{
    const d = await apiGet('/daily-history');
    $('#dailyHistoryBody').innerHTML = d.reports.map(r => `
      <tr><td>${r.date}</td><td>${r.ord_count}</td><td>${Number(r.ord_amount).toFixed(2)}</td><td>${r.new_users}</td></tr>`).join('');
  }catch(e){ $('#dailyHistoryBody').innerHTML = `<tr><td colspan="4" class="empty">Failed to load.</td></tr>`; }
};

// ═══════════════════════════════════════════════════════════════════════
// SETTINGS
// ═══════════════════════════════════════════════════════════════════════
async function loadSettings(){
  try{
    const d = await apiGet('/settings');
    const s = d.settings;
    const form = $('#settingsForm');
    form.bot_name.value = s.bot_name || '';
    form.bot_emoji.value = s.bot_emoji || '';
    form.trc20_address.value = s.trc20_address || '';
    form.bep20_address.value = s.bep20_address || '';
    form.binance_pay_id.value = s.binance_pay_id || '';
    form.log_channel_id.value = s.log_channel_id || '';
    form.deposit_log_channel_id.value = s.deposit_log_channel_id || '';
    form.min_deposit.value = s.min_deposit || '';
    form.low_stock_threshold.value = s.low_stock_threshold || '';
    $$('.toggle[data-setting-key]').forEach(t => {
      t.classList.toggle('on', !!s[t.dataset.settingKey]);
    });
    $('#channelsBody').innerHTML = d.channels.length ? d.channels.map(c => `
      <tr><td>📢 ${esc(c.handle)}</td><td><button class="btn sm danger" data-channel-remove="${c.n}">Remove</button></td></tr>`).join('')
      : `<tr><td colspan="2" class="empty">No force-join channels configured.</td></tr>`;
    $$('[data-channel-remove]').forEach(b => b.addEventListener('click', async () => {
      await apiDelete(`/settings/channel/${b.dataset.channelRemove}`); loadSettings();
    }));
  }catch(e){ toast('Failed to load settings', true); }
}
loaders.settings = loadSettings;
$('#settingsForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = Object.fromEntries(f.entries());
  try{ await apiPost('/settings', body); toast('Settings saved'); }
  catch(err){ toast('Failed: '+err.message, true); }
});
$$('.toggle[data-setting-key]').forEach(t => t.addEventListener('click', async () => {
  try{ const r = await apiPost(`/settings/toggle/${t.dataset.settingKey}`); t.classList.toggle('on', r.value); }
  catch(e){ toast('Failed to toggle', true); }
}));
$('#channelForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try{ await apiPost('/settings/channel', {handle: f.get('handle'), url: f.get('url')||''}); toast('Channel added'); e.target.reset(); loadSettings(); }
  catch(err){ toast('Failed: '+err.message, true); }
});

// ── initial load ────────────────────────────────────────────────────────
loaders.dashboard();
