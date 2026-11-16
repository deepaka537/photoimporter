'use strict';

// ============================================================
// API helpers
// ============================================================

async function apiFetch(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} — ${url}`);
  return res.json();
}

async function apiPost(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

// ============================================================
// Utilities
// ============================================================

const RAW_EXTS     = new Set(['.cr2', '.cr3', '.nef', '.arw', '.dng', '.orf', '.rw2', '.raw']);
const JPEG_EXTS_JS = new Set(['.jpg', '.jpeg', '.heic', '.heif', '.png', '.webp', '.bmp', '.tiff', '.tif', '.gif']);

function basename(p) {
  return p.replace(/.*[/\\]/, '');
}

function fileExt(name) {
  const dot = name.lastIndexOf('.');
  return dot >= 0 ? name.slice(dot).toLowerCase() : '';
}

function isRaw(path) {
  const ext = path.slice(path.lastIndexOf('.')).toLowerCase();
  return RAW_EXTS.has(ext);
}

/** Build a URL to display a photo in the viewer (falls back to lg thumb for RAW). */
function displayUrl(path) {
  return isRaw(path)
    ? '/thumb?p=' + encodeURIComponent(path) + '&s=lg'
    : '/photo?p='  + encodeURIComponent(path);
}

function formatExifDate(d) {
  // "2025:11:14 15:30:00" → "Nov 14, 2025  15:30"
  try {
    const [datePart, timePart] = d.split(' ');
    const [y, m, day] = datePart.split(':').map(Number);
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return `${months[m - 1]} ${day}, ${y}  ${timePart.slice(0, 5)}`;
  } catch (_) { return d; }
}

// ============================================================
// Full-screen viewer
// ============================================================

const viewer = (() => {
  const dialog      = document.getElementById('viewerDialog');
  const imgEl       = document.getElementById('viewerImg');
  const spinner     = document.getElementById('viewerSpinner');
  const fnameEl     = document.getElementById('viewerFilename');
  const metaEl      = document.getElementById('viewerMeta');
  const btnClose    = document.getElementById('viewerClose');
  const btnPrev     = document.getElementById('viewerPrev');
  const btnNext     = document.getElementById('viewerNext');
  const btnExplorer = document.getElementById('viewerOpenExplorer');

  let _photos = [];   // array of photo objects with at least {path, name?}
  let _idx    = 0;

  // ------------------------------------------------------------------

  function open(photos, idx) {
    _photos = photos;
    _idx    = Math.max(0, Math.min(idx, photos.length - 1));
    dialog.showModal();
    _show();
  }

  function _show() {
    const photo = _photos[_idx];
    if (!photo) return;

    // Show spinner while image loads
    spinner.classList.add('active');
    imgEl.src = '';
    imgEl.onload  = () => spinner.classList.remove('active');
    imgEl.onerror = () => spinner.classList.remove('active');
    imgEl.src = displayUrl(photo.path);

    fnameEl.textContent = photo.name || basename(photo.path);
    metaEl.innerHTML = '';
    _loadInfo(photo.path);

    btnPrev.style.visibility = _idx > 0                  ? 'visible' : 'hidden';
    btnNext.style.visibility = _idx < _photos.length - 1 ? 'visible' : 'hidden';
  }

  async function _loadInfo(path) {
    try {
      const info = await apiFetch('/api/photo/info?p=' + encodeURIComponent(path));
      const rows = [];

      const ex = info.exif || {};
      if (ex.date)                    rows.push(['Date',     formatExifDate(ex.date)]);
      if (ex.make || ex.model)        rows.push(['Camera',   [ex.make, ex.model].filter(Boolean).join(' ')]);
      if (ex.iso    != null)          rows.push(['ISO',      ex.iso]);
      if (ex.aperture != null)        rows.push(['Aperture', `f/${ex.aperture}`]);
      if (ex.shutter)                 rows.push(['Shutter',  ex.shutter + '\u202fs']);
      if (ex.width && ex.height)      rows.push(['Size',     `${ex.width}\u00d7${ex.height}`]);
      if (info.face_count)            rows.push(['Faces',    info.face_count]);
      if (info.burst_group)           rows.push(['Burst',    info.burst_group.slice(0, 8)]);

      metaEl.innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
    } catch (_) { /* no EXIF available */ }
  }

  // ------------------------------------------------------------------
  // Events

  btnClose.addEventListener('click', () => dialog.close());

  // Close when clicking on the dark backdrop (outside the inner area)
  dialog.addEventListener('click', e => {
    if (e.target === dialog) dialog.close();
  });

  btnPrev.addEventListener('click', e => {
    e.stopPropagation();
    if (_idx > 0) { _idx--; _show(); }
  });

  btnNext.addEventListener('click', e => {
    e.stopPropagation();
    if (_idx < _photos.length - 1) { _idx++; _show(); }
  });

  document.addEventListener('keydown', e => {
    if (!dialog.open) return;
    if (e.key === 'Escape')      { dialog.close(); }
    if (e.key === 'ArrowLeft')   { e.preventDefault(); if (_idx > 0) { _idx--; _show(); } }
    if (e.key === 'ArrowRight')  { e.preventDefault(); if (_idx < _photos.length - 1) { _idx++; _show(); } }
  });

  if (btnExplorer) {
    btnExplorer.addEventListener('click', async () => {
      const photo = _photos[_idx];
      if (!photo) return;
      try {
        await apiPost('/api/open_in_explorer', { path: photo.path });
      } catch (_) { /* ignore */ }
    });
  }

  return { open };
})();

// ============================================================
// Timeline view
// ============================================================

async function showTimeline() {
  const main = document.getElementById('appMain');
  main.innerHTML = `
    <div class="timeline-view">
      <nav class="tl-sidebar" id="tlSidebar"><div class="loading-msg">Loading…</div></nav>
      <div class="tl-content" id="tlContent"><div class="tl-empty">Select a folder from the sidebar</div></div>
    </div>`;

  const sidebar = document.getElementById('tlSidebar');
  const content = document.getElementById('tlContent');

  let folders;
  try {
    folders = await apiFetch('/api/folders');
  } catch (_) {
    sidebar.innerHTML = '<div class="loading-msg">Error loading folders</div>';
    return;
  }

  if (!folders.length) {
    sidebar.innerHTML = '<div class="loading-msg" style="padding:16px;text-align:center">No date folders found</div>';
    return;
  }

  // Group by year
  const byYear = {};
  for (const f of folders) {
    const yr = f.year ?? 'Other';
    (byYear[yr] = byYear[yr] || []).push(f);
  }

  sidebar.innerHTML = '';
  const years = Object.keys(byYear).sort((a, b) => b - a);

  for (let yi = 0; yi < years.length; yi++) {
    const yr = years[yi];
    const grp = document.createElement('div');
    grp.className = 'tl-year-group';

    // Collapsible year header
    const yearBtn = document.createElement('button');
    yearBtn.className = 'tl-year-btn' + (yi === 0 ? ' open' : '');
    yearBtn.innerHTML = `<span class="tl-year-chevron">&#9654;</span><span>${yr}</span>`;

    const foldersDiv = document.createElement('div');
    foldersDiv.className = 'tl-year-folders' + (yi === 0 ? ' open' : '');

    yearBtn.addEventListener('click', () => {
      const open = yearBtn.classList.toggle('open');
      foldersDiv.classList.toggle('open', open);
    });

    for (const f of byYear[yr]) {
      const btn = document.createElement('button');
      btn.className = 'tl-folder-btn';
      btn.dataset.name = f.name;
      btn.innerHTML = `<span>${f.name}</span><span class="tl-folder-count">${f.count}</span>`;
      btn.addEventListener('click', () => loadFolder(f.path, f.name, btn));
      foldersDiv.appendChild(btn);
    }

    grp.appendChild(yearBtn);
    grp.appendChild(foldersDiv);
    sidebar.appendChild(grp);
  }

  // Auto-select most recent folder
  const firstBtn = sidebar.querySelector('.tl-folder-btn');
  if (firstBtn) firstBtn.click();

  // ------------------------------------------------------------------
  async function loadFolder(folderPath, folderName, btn) {
    sidebar.querySelectorAll('.tl-folder-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    content.innerHTML = '<div class="loading-msg">Loading…</div>';

    let allPhotos;
    try {
      allPhotos = await apiFetch('/api/folder?path=' + encodeURIComponent(folderPath));
    } catch (_) {
      content.innerHTML = '<div class="tl-empty">Error loading folder</div>';
      return;
    }

    if (!allPhotos.length) {
      content.innerHTML = '<div class="tl-empty">No photos in this folder</div>';
      return;
    }

    content.innerHTML = `
      <div class="tl-folder-header">
        ${folderName}
        <span style="color:var(--muted);font-weight:400;font-size:14px">${allPhotos.length} photos</span>
      </div>
      <div class="filter-bar">
        <div class="filter-group">
          <label class="filter-label">Type</label>
          <select class="filter-select" id="filterType">
            <option value="all">All</option>
            <option value="jpeg">JPEG / standard</option>
            <option value="raw">RAW only</option>
          </select>
        </div>
        <div class="filter-group">
          <label class="filter-label">Sort</label>
          <select class="filter-select" id="filterSort">
            <option value="name">Filename</option>
            <option value="date">Date taken</option>
          </select>
        </div>
        <span class="filter-count" id="filterCount"></span>
      </div>
      <div class="tl-grid" id="tlGrid"></div>
      <div id="folderBursts" class="folder-bursts"></div>`;

    function renderGrid() {
      const typeVal = document.getElementById('filterType').value;
      const sortVal = document.getElementById('filterSort').value;

      let photos = [...allPhotos];

      // --- type filter ---
      if (typeVal === 'jpeg') {
        photos = photos.filter(p => JPEG_EXTS_JS.has(fileExt(p.name)));
      } else if (typeVal === 'raw') {
        photos = photos.filter(p => RAW_EXTS.has(fileExt(p.name)));
      }

      // --- sort ---
      if (sortVal === 'date') {
        photos.sort((a, b) => (a.capture_ts ?? Infinity) - (b.capture_ts ?? Infinity));
      }
      // else: already alphabetical from server

      document.getElementById('filterCount').textContent =
        photos.length === allPhotos.length
          ? `${photos.length} photos`
          : `${photos.length} of ${allPhotos.length} photos`;

      const grid = document.getElementById('tlGrid');
      grid.innerHTML = '';

      if (!photos.length) {
        grid.innerHTML = '<p class="tl-empty" style="grid-column:1/-1;padding:32px 0">No photos match the filter</p>';
        return;
      }

      photos.forEach((photo, idx) => {
        const item = document.createElement('div');
        item.className = 'tl-thumb';
        item.innerHTML = `
          <img src="${photo.thumb_url}" loading="lazy" alt="${photo.name}">
          ${photo.burst_group ? '<div class="burst-dot" title="Part of a burst group"></div>' : ''}`;
        item.addEventListener('click', () => viewer.open(photos, idx));
        grid.appendChild(item);
      });
    }

    document.getElementById('filterType').addEventListener('change', renderGrid);
    document.getElementById('filterSort').addEventListener('change', renderGrid);
    renderGrid();

    // ---- Burst groups for this folder (loaded async, shown below grid) ----
    async function loadFolderBursts() {
      let bursts;
      try {
        bursts = await apiFetch('/api/bursts?folder=' + encodeURIComponent(folderPath));
      } catch (_) { return; }
      if (!bursts.length) return;

      const section = document.getElementById('folderBursts');
      if (!section) return;

      section.innerHTML = `
        <button class="folder-bursts-header" id="folderBurstsToggle">
          <span class="burst-chevron">&#9654;</span>
          Burst Groups
          <span class="folder-bursts-count">${bursts.length}</span>
        </button>
        <div class="folder-bursts-content" id="folderBurstsContent"></div>`;

      const toggle = document.getElementById('folderBurstsToggle');
      const bContent = document.getElementById('folderBurstsContent');

      toggle.addEventListener('click', () => {
        const open = toggle.classList.toggle('open');
        bContent.classList.toggle('open', open);
      });

      for (const group of bursts) {
        const groupEl = document.createElement('div');
        groupEl.className = 'burst-group';

        const header = document.createElement('div');
        header.className = 'burst-group-header';
        header.innerHTML = `
          <span class="burst-date">${group.date}</span>
          <span class="burst-count">${group.count} shots</span>
          <span class="burst-chevron">&#9660;</span>`;

        const shotsEl = document.createElement('div');
        shotsEl.className = 'burst-shots';

        for (const shot of group.shots) {
          const barColor = shot.is_best ? 'var(--green)' : 'var(--blue)';
          const shotEl = document.createElement('div');
          shotEl.className = 'burst-shot';
          shotEl.innerHTML = `
            <div class="burst-shot-img-wrap${shot.is_best ? ' best' : ''}">
              <img class="burst-shot-img" src="${shot.thumb_url}" loading="lazy" alt="">
              ${shot.is_best ? '<div class="burst-best-badge">BEST</div>' : ''}
            </div>
            <div class="burst-shot-name">${shot.name || basename(shot.path)}</div>
            <div class="burst-bar-wrap">
              <div class="burst-bar" style="width:${shot.sharpness_pct}%;background:${barColor}"></div>
            </div>
            <div class="burst-pct">${shot.sharpness_pct}%</div>`;
          shotEl.addEventListener('click', () => viewer.open(group.shots, group.shots.indexOf(shot)));
          shotsEl.appendChild(shotEl);
        }

        header.addEventListener('click', () => {
          const open = header.classList.toggle('open');
          shotsEl.classList.toggle('open', open);
        });

        groupEl.appendChild(header);
        groupEl.appendChild(shotsEl);
        bContent.appendChild(groupEl);
      }
    }
    loadFolderBursts();
  }
}

// ============================================================
// People view
// ============================================================

async function showPeople() {
  const main = document.getElementById('appMain');
  main.innerHTML = '<div class="people-view" id="peopleView"><div class="loading-msg">Loading…</div></div>';
  const view = document.getElementById('peopleView');

  let people;
  try {
    people = await apiFetch('/api/people');
  } catch (_) {
    view.innerHTML = '<div class="loading-msg">Error loading people</div>';
    return;
  }

  if (!people.length) {
    view.innerHTML = `
      <h2 class="view-title">People</h2>
      <div class="info-banner">
        No people found in the database.<br><br>
        Run face analysis first:<br>
        <code>python analyzer/analyze.py --dest "..." --faces --cluster</code>
      </div>`;
    return;
  }

  view.innerHTML = `
    <h2 class="view-title">People <span class="view-title-count">${people.length}</span></h2>
    <div class="people-filter-bar">
      <input type="search" class="people-search" id="peopleSearch"
             placeholder="Search people…" autocomplete="off">
      <label class="filter-label" style="margin-left:12px">Min photos</label>
      <select class="filter-select" id="peopleMinPhotos">
        <option value="1">All</option>
        <option value="5">5+</option>
        <option value="10">10+</option>
        <option value="25">25+</option>
        <option value="50">50+</option>
        <option value="100">100+</option>
      </select>
      <button class="merge-toggle-btn" id="mergeToggle" title="Select clusters to merge into one person">Merge Clusters</button>
    </div>
    <div class="people-grid" id="peopleGrid"></div>
    <div class="merge-action-bar" id="mergeActionBar">
      <span class="merge-action-info" id="mergeActionInfo">Select 2 or more clusters to merge</span>
      <button class="merge-confirm-btn" id="mergeConfirmBtn" disabled>Merge</button>
      <button class="back-btn" id="mergeCancelBtn">Cancel</button>
    </div>`;

  // ---- Merge mode state ----
  let mergeMode = false;
  let selectedPeople = []; // [{id, name}, ...] — first entry is the keeper

  const mergeToggle  = document.getElementById('mergeToggle');
  const mergeBar     = document.getElementById('mergeActionBar');
  const mergeInfo    = document.getElementById('mergeActionInfo');
  const mergeConfirm = document.getElementById('mergeConfirmBtn');
  const mergeCancel  = document.getElementById('mergeCancelBtn');
  const grid         = document.getElementById('peopleGrid');

  function updateMergeBar() {
    const n = selectedPeople.length;
    if (n === 0) {
      mergeInfo.textContent = 'Select 2 or more clusters to merge';
      mergeConfirm.disabled = true;
    } else if (n === 1) {
      mergeInfo.textContent = `1 selected — pick another to merge with it`;
      mergeConfirm.disabled = true;
    } else {
      const keepName = selectedPeople[0].name;
      mergeInfo.textContent = `${n} selected — all merged into "${keepName}"`;
      mergeConfirm.disabled = false;
    }
  }

  function enterMergeMode() {
    mergeMode = true;
    selectedPeople = [];
    mergeToggle.classList.add('active');
    grid.classList.add('merge-selectable');
    mergeBar.classList.add('visible');
    updateMergeBar();
  }

  function exitMergeMode() {
    mergeMode = false;
    selectedPeople = [];
    mergeToggle.classList.remove('active');
    grid.classList.remove('merge-selectable');
    grid.querySelectorAll('.person-card.merge-selected').forEach(c => c.classList.remove('merge-selected'));
    mergeBar.classList.remove('visible');
  }

  mergeToggle.addEventListener('click', () => {
    if (mergeMode) exitMergeMode(); else enterMergeMode();
  });
  mergeCancel.addEventListener('click', exitMergeMode);

  mergeConfirm.addEventListener('click', async () => {
    if (selectedPeople.length < 2) return;
    const keep_id  = selectedPeople[0].id;
    const merge_ids = selectedPeople.slice(1).map(s => s.id);
    mergeConfirm.disabled = true;
    mergeConfirm.textContent = 'Merging…';
    try {
      const res = await apiPost('/api/person/merge', { keep_id, merge_ids });
      if (res.ok) {
        showPeople();
      } else {
        alert('Merge failed: ' + (res.error || 'unknown error'));
        mergeConfirm.textContent = 'Merge';
        updateMergeBar();
      }
    } catch (_) {
      alert('Merge request failed');
      mergeConfirm.textContent = 'Merge';
      updateMergeBar();
    }
  });

  // ---- Filter ----
  function filterPeople() {
    const q   = document.getElementById('peopleSearch').value.trim().toLowerCase();
    const min = parseInt(document.getElementById('peopleMinPhotos').value, 10);
    grid.querySelectorAll('.person-card').forEach(card => {
      const name  = (card.querySelector('.person-name')?.textContent || '').toLowerCase();
      const count = parseInt(card.dataset.count || '0', 10);
      card.style.display = ((!q || name.includes(q)) && count >= min) ? '' : 'none';
    });
  }
  document.getElementById('peopleSearch').addEventListener('input', filterPeople);
  document.getElementById('peopleMinPhotos').addEventListener('change', filterPeople);

  // ---- Cards ----
  for (const p of people) {
    const card = document.createElement('div');
    card.className = 'person-card';
    card.dataset.count = p.count;

    const thumbsHtml = [0, 1, 2].map(i => {
      const url = p.sample_thumbs[i];
      return url
        ? `<img class="person-thumb-img" src="${url}" loading="lazy" alt="">`
        : `<div class="person-thumb-img"></div>`;
    }).join('');

    const displayName = p.name || `Person #${p.id}`;

    card.innerHTML = `
      <div class="person-thumbs">${thumbsHtml}</div>
      <div class="person-info">
        <span class="person-name"
              contenteditable="true"
              data-person-id="${p.id}"
              data-original="${displayName}">${displayName}</span>
        <span class="person-count">${p.count} photo${p.count !== 1 ? 's' : ''}</span>
      </div>`;

    card.addEventListener('click', e => {
      if (mergeMode) {
        const idx = selectedPeople.findIndex(s => s.id === p.id);
        if (idx >= 0) {
          selectedPeople.splice(idx, 1);
          card.classList.remove('merge-selected');
        } else {
          selectedPeople.push({ id: p.id, name: p.name || `Person #${p.id}` });
          card.classList.add('merge-selected');
        }
        updateMergeBar();
        return;
      }
      if (e.target.classList.contains('person-name')) return;
      showPersonPhotos(p.id, p.name || `Person #${p.id}`);
    });

    // Inline rename: double-click to focus + select all
    const nameEl = card.querySelector('.person-name');

    nameEl.addEventListener('dblclick', e => {
      if (mergeMode) return;
      e.stopPropagation();
      nameEl.focus();
      const range = document.createRange();
      range.selectNodeContents(nameEl);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    });

    nameEl.addEventListener('keydown', e => {
      if (e.key === 'Enter')  { e.preventDefault(); nameEl.blur(); }
      if (e.key === 'Escape') { nameEl.textContent = nameEl.dataset.original; nameEl.blur(); }
    });

    nameEl.addEventListener('blur', async () => {
      if (mergeMode) return;
      const newName = nameEl.textContent.trim();
      if (!newName || newName === nameEl.dataset.original) {
        nameEl.textContent = nameEl.dataset.original;
        return;
      }
      try {
        const res = await apiPost(`/api/person/${p.id}/rename`, { name: newName });
        if (res.ok) {
          nameEl.dataset.original = newName;
          p.name = newName;
        } else {
          nameEl.textContent = nameEl.dataset.original;
        }
      } catch (_) {
        nameEl.textContent = nameEl.dataset.original;
      }
    });

    grid.appendChild(card);
  }
}

async function showPersonPhotos(personId, personName) {
  const main = document.getElementById('appMain');
  main.innerHTML = '<div class="person-photos-view"><div class="loading-msg">Loading…</div></div>';
  const view = main.querySelector('.person-photos-view');

  let photos;
  try {
    photos = await apiFetch(`/api/person/${personId}`);
  } catch (_) {
    view.innerHTML = '<div class="loading-msg">Error loading photos</div>';
    return;
  }

  view.innerHTML = `
    <div class="back-row">
      <button class="back-btn" id="backToPeople">&#8592; People</button>
      <h2 class="view-title">
        ${personName}
        <span class="view-title-count">${photos.length} photo${photos.length !== 1 ? 's' : ''}</span>
      </h2>
    </div>
    <div class="tl-grid" id="personGrid"></div>`;

  document.getElementById('backToPeople').addEventListener('click', showPeople);

  const grid = document.getElementById('personGrid');
  photos.forEach((photo, idx) => {
    const item = document.createElement('div');
    item.className = 'tl-thumb';
    item.innerHTML = `<img src="${photo.thumb_url}" loading="lazy" alt="${photo.name}">`;
    item.addEventListener('click', () => viewer.open(photos, idx));
    grid.appendChild(item);
  });
}

// ============================================================
// Bursts view
// ============================================================

async function showBursts() {
  const main = document.getElementById('appMain');
  main.innerHTML = '<div class="bursts-view" id="burstsView"><div class="loading-msg">Loading burst groups…</div></div>';
  const view = document.getElementById('burstsView');

  let bursts;
  try {
    bursts = await apiFetch('/api/bursts');
  } catch (_) {
    view.innerHTML = '<div class="bursts-view"><div class="loading-msg">Error loading bursts</div></div>';
    return;
  }

  if (!bursts.length) {
    view.innerHTML = `
      <div class="bursts-view">
        <h2 class="view-title">Burst Groups</h2>
        <div class="info-banner">
          No burst groups found in the database.<br><br>
          Run burst detection first:<br>
          <code>python analyzer/analyze.py --dest "..." --bursts</code>
        </div>
      </div>`;
    return;
  }

  view.innerHTML = `<h2 class="view-title">Burst Groups <span class="view-title-count">${bursts.length}</span></h2>`;

  for (const group of bursts) {
    const groupEl = document.createElement('div');
    groupEl.className = 'burst-group';

    const header = document.createElement('div');
    header.className = 'burst-group-header';
    header.innerHTML = `
      <span class="burst-date">${group.date}</span>
      <span class="burst-count">${group.count} shots</span>
      <span class="burst-chevron">&#9660;</span>`;

    const shotsEl = document.createElement('div');
    shotsEl.className = 'burst-shots';

    for (const shot of group.shots) {
      const barColor = shot.is_best ? 'var(--green)' : 'var(--blue)';
      const shotEl = document.createElement('div');
      shotEl.className = 'burst-shot';
      shotEl.innerHTML = `
        <div class="burst-shot-img-wrap${shot.is_best ? ' best' : ''}">
          <img class="burst-shot-img" src="${shot.thumb_url}" loading="lazy" alt="">
          ${shot.is_best ? '<div class="burst-best-badge">BEST</div>' : ''}
        </div>
        <div class="burst-shot-name">${shot.name || basename(shot.path)}</div>
        <div class="burst-bar-wrap">
          <div class="burst-bar" style="width:${shot.sharpness_pct}%;background:${barColor}"></div>
        </div>
        <div class="burst-pct">${shot.sharpness_pct}%</div>`;

      shotEl.addEventListener('click', () => {
        viewer.open(group.shots, group.shots.indexOf(shot));
      });
      shotsEl.appendChild(shotEl);
    }

    header.addEventListener('click', () => {
      const open = header.classList.toggle('open');
      shotsEl.classList.toggle('open', open);
    });

    groupEl.appendChild(header);
    groupEl.appendChild(shotsEl);
    view.appendChild(groupEl);
  }
}

// ============================================================
// Stats chip + precache progress bar
// ============================================================

async function loadStats() {
  try {
    const s = await apiFetch('/api/stats');
    const chip = document.getElementById('statsChip');
    const parts = [];
    if (s.total_photos != null) parts.push(`${s.total_photos} photos`);
    if (s.total_people != null) parts.push(`${s.total_people} people`);
    if (s.burst_groups != null) parts.push(`${s.burst_groups} bursts`);
    chip.textContent = parts.join(' · ');
  } catch (_) { /* optional */ }
}

function startPrecacheMonitor() {
  const bar  = document.getElementById('precacheBar');
  const fill = document.getElementById('precacheFill');

  async function poll() {
    try {
      const p = await apiFetch('/api/precache_progress');
      if (p.total > 0) {
        bar.classList.add('visible');
        const pct = Math.round(p.done / p.total * 100);
        fill.style.width = pct + '%';
        if (p.done < p.total) {
          setTimeout(poll, 2000);
        } else {
          setTimeout(() => bar.classList.remove('visible'), 3000);
        }
      } else {
        setTimeout(poll, 3000);
      }
    } catch (_) {
      setTimeout(poll, 6000);
    }
  }
  setTimeout(poll, 1500);  // small delay before first poll
}

// ============================================================
// Router
// ============================================================

function getView() {
  return location.hash.slice(1) || 'timeline';
}

function updateNav() {
  const view = getView();
  document.querySelectorAll('.nav-link').forEach(a => {
    a.classList.toggle('active', a.dataset.view === view);
  });
}

function dispatch() {
  updateNav();
  const view = getView();
  if (view === 'people') { showPeople(); return; }
  showTimeline();
}

window.addEventListener('hashchange', dispatch);

// ============================================================
// Boot
// ============================================================

loadStats();
startPrecacheMonitor();
dispatch();
