const $ = (s) => document.querySelector(s);
let data;
const request = async (url, options = {}) => {
  const method = (options.method || 'GET').toUpperCase();
  const headers = new Headers(options.headers || {});
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-Requested-With', 'marketing-agent');
  const response = await fetch(url, {...options, headers});
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.detail || result.error || 'Something went wrong.');
  return result;
};
const toast = (message, error = false) => { const el = $('#toast'); el.textContent = message; el.setAttribute('role', error ? 'alert' : 'status'); el.className = `toast show ${error ? 'error' : ''}`; setTimeout(() => el.className = 'toast', 3500); };
const defaultTimes = { 1: ['10:00'], 2: ['10:00', '18:00'], 3: ['09:00', '14:00', '20:00'] };

function render() {
  $('#knowledge-count').textContent = `${data.knowledge.length} facts`;
  $('#knowledge-list').innerHTML = data.knowledge.slice(0, 12).map(item => `<article><b>${escapeHtml(item.title)}${item.reviewed ? '' : ' · Pending review'}</b><span>${escapeHtml(item.text)}${item.reviewed ? '' : `<button type="button" data-approve-knowledge="${item.id}">Approve for AI use</button>`}</span></article>`).join('');
  $('#assets').innerHTML = data.assets.map(asset => `<article class="asset asset-detail">${asset.mimeType.startsWith('image/') ? `<img src="/api/assets/${asset.id}/content" alt="${escapeHtml(asset.label || asset.originalName)}" loading="lazy">` : '<span class="video-icon" aria-hidden="true">▶</span>'}<span>${escapeHtml(asset.label || asset.originalName)}${asset.description ? ` — ${escapeHtml(asset.description)}` : ''}</span><button type="button" data-edit-asset="${asset.id}">Edit details</button></article>`).join('') || '<p class="muted">No visuals yet.</p>';
  $('#asset-picker').innerHTML = data.assets.map(asset => `<label class="asset"><input type="checkbox" value="${asset.id}"><span>${asset.mimeType.startsWith('video') ? '▶' : '▧'} ${escapeHtml(asset.label || asset.originalName)}</span></label>`).join('') || '<p class="muted">Add a visual above, or create a copy-only post.</p>';
  const setting = data.settings; const form = $('#settings-form'); form.postsPerDay.value = setting.postsPerDay; form.timezone.value = setting.timezone; form.mode.value = setting.mode; form.contactCta.value = setting.contactCta || ''; form.writingExamples.value = setting.writingExamples || ''; form.enabled.checked = setting.enabled;
  showTimes(setting.postingTimes);
  $('#status').textContent = setting.enabled ? `${setting.postsPerDay}× daily · ${setting.mode === 'approval' ? 'Review mode' : 'Auto-publish'}` : 'Schedule paused';
  $('#integration-state').textContent = `DeepSeek ${data.integrations.deepseek ? 'connected' : 'needs key'} · Facebook ${data.integrations.facebook ? 'connected' : 'needs setup'}`;
  $('#publish-now').disabled = !data.integrations.deepseek || !data.integrations.facebook || !data.assets.some(asset => asset.mimeType.startsWith('image/'));
  const factMap = Object.fromEntries(data.knowledge.map(item => [item.id, item.title]));
  $('#posts').innerHTML = data.posts.map(post => {
    const editable = ['draft', 'failed'].includes(post.status);
    const facts = post.factIds.map(id => factMap[id] || id).map(escapeHtml).join(' · ');
    const schedule = post.scheduledFor ? `<small>Prepared for review: ${new Date(post.scheduledFor).toLocaleString()}</small>` : '';
    const caption = editable ? `<textarea class="caption-editor" data-caption-for="${post.id}" aria-label="Edit Facebook caption">${escapeHtml(post.caption)}</textarea>` : `<p>${escapeHtml(post.caption)}</p>`;
    const actions = editable ? `<div class="post-actions"><button type="button" class="secondary" data-save-post="${post.id}">Save changes</button><button type="button" data-publish="${post.id}" ${!data.integrations.facebook ? 'disabled title="Configure Facebook in .env first"' : ''}>Publish to Facebook</button></div>` : post.status === 'publishing' ? '<small>Publishing in progress…</small>' : `<small>Facebook ID: ${escapeHtml(post.facebookPostId || 'sent')}</small>`;
    return `<article class="post"><div class="post-meta"><span class="pill ${post.status}">${escapeHtml(post.status)}</span><time>${new Date(post.createdAt).toLocaleString()}</time></div>${caption}${post.imageNotes ? `<small>Visual direction: ${escapeHtml(post.imageNotes)}</small>` : ''}${facts ? `<small>Grounded in: ${facts}</small>` : ''}${schedule}${actions}${post.error ? `<small class="failure">${escapeHtml(post.error)}</small>` : ''}</article>`;
  }).join('') || '<p class="muted">Your generated drafts will appear here.</p>';
}
function showTimes(values) { $('#times').innerHTML = values.map((value, i) => `<label>Post ${i + 1}<input type="time" value="${value}"></label>`).join(''); }
function escapeHtml(value) { return String(value || '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])); }
async function load() { data = await request('/api/dashboard'); render(); }

$('#knowledge-form').addEventListener('submit', async e => { e.preventDefault(); const f = new FormData(e.target); try { await request('/api/knowledge', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(Object.fromEntries(f)) }); e.target.reset(); await load(); toast('Verified knowledge added.'); } catch (err) { toast(err.message, true); }});
$('#asset-input').addEventListener('change', async e => { const label=$('#asset-label').value; const description=$('#asset-description').value; for (const file of e.target.files) { const f = new FormData(); f.append('file', file); f.append('label', label); f.append('description', description); try { await request('/api/assets', {method:'POST', body:f}); toast(`${file.name} added.`); } catch (err) { toast(err.message, true); }} e.target.value=''; $('#asset-label').value=''; $('#asset-description').value=''; await load(); });
$('#settings-form').addEventListener('submit', async e => { e.preventDefault(); const f=e.target; const postsPerDay=Number(f.postsPerDay.value); const postingTimes=[...$('#times').querySelectorAll('input')].map(i=>i.value); try { await request('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({postsPerDay,timezone:f.timezone.value,mode:f.mode.value,contactCta:f.contactCta.value,writingExamples:f.writingExamples.value,enabled:f.enabled.checked,postingTimes})}); await load(); toast('Cadence and writing voice saved.'); } catch(err){toast(err.message,true)}});
$('#settings-form').postsPerDay.addEventListener('change', e => showTimes(defaultTimes[e.target.value]));
$('#draft-form').addEventListener('submit', async e => {e.preventDefault(); const assetIds=[...$('#asset-picker').querySelectorAll('input:checked')].map(i=>i.value); const form=new FormData(e.target); try { await request('/api/posts/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({assetIds,angle:form.get('angle'),visualContext:form.get('visualContext')})}); await load(); toast('Draft created — review it before posting.'); } catch(err){toast(err.message,true)}});
$('#publish-now').addEventListener('click', async e => {
  e.preventDefault();
  if (!window.confirm('Create and publish a Bangla post to Facebook now? This cannot be undone from this dashboard.')) return;
  const button = e.currentTarget;
  const label = button.textContent;
  button.disabled = true;
  button.textContent = 'Creating Bengali post…';
  toast('Writing the post and choosing the best uploaded image…');
  try {
    await request('/api/posts/publish-now', {method:'POST'});
    await load();
    toast('Bangla post published to Facebook.');
  } catch(err) {
    toast(err.message, true);
  } finally {
    button.textContent = label;
    if (data) render();
  }
});
$('#posts').addEventListener('click', async e => {
  const publishId=e.target.dataset.publish;
  const saveId=e.target.dataset.savePost;
  if (saveId) {
    const caption=$(`[data-caption-for="${saveId}"]`).value;
    const post=data.posts.find(item => item.id === saveId);
    e.target.disabled=true;
    try { await request(`/api/posts/${saveId}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({caption,headline:post.headline || '',assetIds:post.assetIds})}); await load(); toast('Draft changes saved.'); } catch(err){toast(err.message,true); e.target.disabled=false;}
    return;
  }
  if (!publishId) return;
  if (!window.confirm('Publish this exact caption to Facebook now?')) return;
  e.target.disabled=true;
  try {await request(`/api/posts/${publishId}/publish`,{method:'POST'}); await load(); toast('Published to Facebook.');} catch(err){toast(err.message,true); e.target.disabled=false;}
});
$('#assets').addEventListener('click', async e => { const id=e.target.dataset.editAsset; if (!id) return; const asset=data.assets.find(item => item.id === id); const label=window.prompt('Describe this image in a few words:', asset.label || asset.originalName); if (label === null) return; const description=window.prompt('What visible workflow or feature does it show?', asset.description || ''); if (description === null) return; try { await request(`/api/assets/${id}`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({label,description})}); await load(); toast('Image details saved.'); } catch(err){toast(err.message,true)}});
$('#knowledge-list').addEventListener('click', async e => { const id=e.target.dataset.approveKnowledge; if (!id) return; try { await request(`/api/knowledge/${id}/approve`, {method:'POST'}); await load(); toast('Knowledge approved for AI use.'); } catch(err){toast(err.message,true)} });
load().catch(err => toast(err.message, true));
