const $ = (s) => document.querySelector(s);
let data;
const request = async (url, options = {}) => { const response = await fetch(url, options); const result = await response.json(); if (!response.ok) throw new Error(result.detail || result.error || 'Something went wrong.'); return result; };
const toast = (message, error = false) => { const el = $('#toast'); el.textContent = message; el.className = `toast show ${error ? 'error' : ''}`; setTimeout(() => el.className = 'toast', 3500); };
const defaultTimes = { 1: ['10:00'], 2: ['10:00', '18:00'], 3: ['09:00', '14:00', '20:00'] };

function render() {
  $('#knowledge-count').textContent = `${data.knowledge.length} facts`;
  $('#knowledge-list').innerHTML = data.knowledge.slice(0, 6).map(item => `<article><b>${escapeHtml(item.title)}</b><span>${escapeHtml(item.text)}</span></article>`).join('');
  $('#assets').innerHTML = data.assets.map(asset => `<label class="asset"><input type="checkbox" value="${asset.id}"><span>${asset.mimeType.startsWith('video') ? '▶' : '▧'} ${escapeHtml(asset.originalName)}</span></label>`).join('') || '<p class="muted">No visuals yet.</p>';
  $('#asset-picker').innerHTML = data.assets.map(asset => `<label class="asset"><input type="checkbox" value="${asset.id}"><span>${asset.mimeType.startsWith('video') ? '▶' : '▧'} ${escapeHtml(asset.originalName)}</span></label>`).join('') || '<p class="muted">Add a visual above, or create a copy-only post.</p>';
  const setting = data.settings; const form = $('#settings-form'); form.postsPerDay.value = setting.postsPerDay; form.timezone.value = setting.timezone; form.mode.value = setting.mode; form.enabled.checked = setting.enabled;
  showTimes(setting.postingTimes);
  $('#status').textContent = setting.enabled ? `${setting.postsPerDay}× daily · ${setting.mode === 'approval' ? 'Review mode' : 'Auto-publish'}` : 'Schedule paused';
  $('#integration-state').textContent = `DeepSeek ${data.integrations.deepseek ? 'connected' : 'needs key'} · Facebook ${data.integrations.facebook ? 'connected' : 'needs setup'}`;
  $('#posts').innerHTML = data.posts.map(post => `<article class="post"><div class="post-meta"><span class="pill ${post.status}">${post.status}</span><time>${new Date(post.createdAt).toLocaleString()}</time></div><p>${escapeHtml(post.caption)}</p>${post.imageNotes ? `<small>Visual direction: ${escapeHtml(post.imageNotes)}</small>` : ''}${post.status !== 'published' ? `<button data-publish="${post.id}" ${!data.integrations.facebook ? 'disabled title="Configure Facebook in .env first"' : ''}>Publish to Facebook</button>` : `<small>Facebook ID: ${escapeHtml(post.facebookPostId || 'sent')}</small>`}${post.error ? `<small class="failure">${escapeHtml(post.error)}</small>` : ''}</article>`).join('') || '<p class="muted">Your generated drafts will appear here.</p>';
}
function showTimes(values) { $('#times').innerHTML = values.map((value, i) => `<label>Post ${i + 1}<input type="time" value="${value}"></label>`).join(''); }
function escapeHtml(value) { return String(value || '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
async function load() { data = await request('/api/dashboard'); render(); }

$('#knowledge-form').addEventListener('submit', async e => { e.preventDefault(); const f = new FormData(e.target); try { await request('/api/knowledge', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(Object.fromEntries(f)) }); e.target.reset(); await load(); toast('Verified knowledge added.'); } catch (err) { toast(err.message, true); }});
$('#asset-input').addEventListener('change', async e => { for (const file of e.target.files) { const f = new FormData(); f.append('file', file); try { await request('/api/assets', {method:'POST', body:f}); toast(`${file.name} added.`); } catch (err) { toast(err.message, true); }} e.target.value=''; await load(); });
$('#settings-form').addEventListener('submit', async e => { e.preventDefault(); const f=e.target; const postsPerDay=Number(f.postsPerDay.value); const postingTimes=[...$('#times').querySelectorAll('input')].map(i=>i.value); try { await request('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({postsPerDay,timezone:f.timezone.value,mode:f.mode.value,enabled:f.enabled.checked,postingTimes})}); await load(); toast('Posting cadence saved.'); } catch(err){toast(err.message,true)}});
$('#settings-form').postsPerDay.addEventListener('change', e => showTimes(defaultTimes[e.target.value]));
$('#draft-form').addEventListener('submit', async e => {e.preventDefault(); const assetIds=[...$('#asset-picker').querySelectorAll('input:checked')].map(i=>i.value); const form=new FormData(e.target); try { await request('/api/posts/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({assetIds,angle:form.get('angle'),visualContext:form.get('visualContext')})}); await load(); toast('Draft created — review it before posting.'); } catch(err){toast(err.message,true)}});
$('#posts').addEventListener('click', async e => { const id=e.target.dataset.publish; if (!id) return; e.target.disabled=true; try {await request(`/api/posts/${id}/publish`,{method:'POST'}); await load(); toast('Published to Facebook.');} catch(err){toast(err.message,true); e.target.disabled=false;}});
load().catch(err => toast(err.message, true));
