// app.js - henter data/sources.json og viser kilder, dÃ¸de kilder nederst.
// Forventet format i data/sources.json:
// {
//   "version":"7.0.3",
//   "items":[ ... ]
// }

async function init() {
  const loading = document.getElementById('loading');
  const errorEl = document.getElementById('error');
  const sourcesEl = document.getElementById('sources');
  const deadList = document.getElementById('dead-list');
  const versionEl = document.getElementById('site-version');

  try {
    const res = await fetch('data/sources.json', {cache: "no-store"});
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();

    if (data.version) versionEl.textContent = data.version;

    const items = Array.isArray(data.items) ? data.items : [];
    const seen = new Set();
    const deduped = [];
    for (const it of items) {
      const key = (it.canonical || it.url || '').trim();
      if (!key) continue;
      if (seen.has(key)) continue;
      seen.add(key);
      deduped.push(it);
    }

    const ok = deduped.filter(i => i.status === 'ok' || !i.status);
    const dead = deduped.filter(i => i.status === 'dead');

    if (ok.length === 0) {
      sourcesEl.innerHTML = '<p>Ingen aktive kilder fundet.</p>';
    } else {
      sourcesEl.innerHTML = '';
      ok.forEach(renderSource);
    }

    deadList.innerHTML = '';
    if (dead.length === 0) {
      deadList.innerHTML = '<li>Ingen dÃ¸de kilder.</li>';
    } else {
      dead.forEach(d => {
        const li = document.createElement('li');
        const a = document.createElement('a');
        a.href = d.url;
        a.textContent = d.title || d.url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.className = 'external';
        li.appendChild(a);
        if (d.publisher) li.appendChild(document.createTextNode(' â€” ' + d.publisher));
        deadList.appendChild(li);
      });
    }

  } catch (err) {
    console.error('Hentning fejlede', err);
    errorEl.hidden = false;
    errorEl.textContent = 'Der opstod en fejl ved hentning af kilder.';
  } finally {
    loading.style.display = 'none';
  }

  function renderSource(item) {
    const art = document.createElement('article');
    const h3 = document.createElement('h3');
    const a = document.createElement('a');
    a.href = item.url;
    a.textContent = item.title || item.url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.className = 'external';
    h3.appendChild(a);
    art.appendChild(h3);

    const meta = document.createElement('div');
    meta.className = 'source-meta';
    const parts = [];
    if (item.publisher) parts.push(item.publisher);
    if (item.date) parts.push(item.date);
    meta.textContent = parts.join(' â€” ');
    art.appendChild(meta);

    sourcesEl.appendChild(art);
  }
}

document.addEventListener('DOMContentLoaded', init);
