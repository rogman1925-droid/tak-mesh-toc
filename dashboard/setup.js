/* TAK MESH TOC first-run setup (served by Ops at /setup, the deck's own screen only). Nothing is written until Finish. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); };
  var TEAM_HEX = { 'White': '#ffffff', 'Yellow': '#ffff00', 'Orange': '#ff7f00', 'Magenta': '#ff00ff', 'Red': '#ff0000', 'Maroon': '#7f0000',
    'Purple': '#7f007f', 'Dark Blue': '#00007f', 'Blue': '#0000ff', 'Cyan': '#00ffff', 'Teal': '#007f7f', 'Green': '#00ff00',
    'Dark Green': '#007f00', 'Brown': '#a0714f' };
  var TAGS = { ok: '[ OK ]', warn: '[WARN]', bad: '[FAIL]', info: '[info]' };
  var step = 1, state = null, unlocked = false, tested = null, choices = {}, finished = false;

  function api(method, path, body, raw) {
    var o = { method: method, headers: { 'X-Deck': '1' } };
    if (raw) { o.headers['Content-Type'] = 'application/zip'; o.headers['X-File-Name'] = raw.name; o.body = raw.data; }
    else if (body) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(body); }
    return fetch(path, o).then(function (r) { return r.json().catch(function () { return { ok: false, error: 'no answer from Ops' }; }); });
  }
  function rows(list) {
    return list.map(function (c) {
      return '<div class="row"><span class="tag lv-' + c.level + '">' + TAGS[c.level] + '</span><span>' + esc(c.title) + '</span><span class="' +
        (c.level === 'ok' ? '' : 'lv-' + c.level) + '">' + esc(c.detail) + '</span></div>';
    }).join('');
  }
  function show(msgEl, text, cls) { msgEl.textContent = text; msgEl.className = 'msg ' + (cls || ''); msgEl.classList.toggle('hidden', !text); }

  /* ---- step 1 */
  function loadState() {
    return api('GET', '/api/setup/state').then(function (s) {
      state = s;
      $('facts').innerHTML = rows(s.facts);
      var c = s.current;
      choices = { callsign: c.callsign, team: c.team, role: c.role, gps: c.gps, return_path: c.return_path, dm_relay: c.dm_relay,
        ops_local: c.ops_local || s.needed, mesh_iface: null };
      if (s.package) { unlocked = true; fillPackage(s.package); }
      buildMesh(); buildIdentity(); buildFeatures(); render();
    });
  }

  /* ---- step 2: the data package */
  $('pkgfile').addEventListener('change', function () {
    var f = this.files[0]; if (!f) return;
    unlocked = false; tested = null; $('pkginfo').classList.add('hidden'); $('pwbox').classList.add('hidden'); show($('pkgerr'), '');
    $('pkgname').textContent = f.name; $('pkgnote').textContent = 'reading it…';
    f.arrayBuffer().then(function (buf) { return api('POST', '/api/setup/package', null, { name: f.name, data: buf }); }).then(gotPackage);
  });
  $('unlock').addEventListener('click', function () {
    api('POST', '/api/setup/unlock', { password: $('pw').value }).then(gotPackage);
  });
  $('pw').addEventListener('keydown', function (e) { if (e.key === 'Enter') $('unlock').click(); });
  function gotPackage(r) {
    if (r.need_password) {
      $('pkgnote').textContent = 'read OK. The certificate needs its password (the one set when it was made).';
      $('pwbox').classList.remove('hidden'); $('pw').value = ''; $('pw').focus();
      show($('pkgerr'), r.error && r.error !== 'the certificate needs its password' ? r.error : '', 'bad');
    } else if (!r.ok) {
      $('pkgnote').textContent = ''; show($('pkgerr'), r.error || 'that package could not be read', 'bad');
    } else {
      unlocked = true; $('pwbox').classList.add('hidden'); show($('pkgerr'), '');
      fillPackage(r);
    }
    render();
  }
  function fillPackage(p) {
    $('pkgname').textContent = p.file || 'data package';
    $('pkgnote').textContent = 'read OK' + (p.password_from_package ? ' · the password came with the package' : '') + ' · nothing saved yet';
    $('pkgrows').innerHTML =
      '<div class="kv"><span>This deck\'s certificate</span><span>' + esc(p.name || p.client) + (p.until ? ', valid until ' + esc(p.until) : '') + '</span></div>' +
      '<div class="kv"><span>Server\'s CA</span><span>' + (p.ca ? esc(p.ca) + ' (the deck will check it is really your server)' : '<span class="lv-warn">not in the package: the server will not be checked</span>') + '</span></div>' +
      (p.description ? '<div class="kv"><span>Server name</span><span>' + esc(p.description) + '</span></div>' : '');
    if (!$('host').value) $('host').value = p.host || '';
    if (!$('port').value) $('port').value = p.port || 8089;
    $('pkginfo').classList.remove('hidden');
  }
  $('test').addEventListener('click', function () {
    var btn = this; btn.disabled = true; show($('testres'), 'testing…');
    api('POST', '/api/setup/test', { host: $('host').value.trim(), port: Number($('port').value) }).then(function (r) {
      btn.disabled = false; tested = r.ok ? r : null;
      show($('testres'), (r.ok ? '[ OK ] ' : '[FAIL] ') + (r.detail || r.error), r.ok ? 'ok' : 'bad'); render();
    });
  });
  ['host', 'port'].forEach(function (id) { $(id).addEventListener('input', function () { tested = null; show($('testres'), ''); render(); }); });

  /* ---- step 3: mesh port */
  function buildMesh() {
    var ifs = state.interfaces, html = '';
    ifs.forEach(function (i) {
      var d = i.addr + '/' + i.prefix + (i.internet ? ' · internet' : '') + (i.wireless ? ' · Wi-Fi' : '') + (i.carrier === false ? ' · no cable' : '');
      var rec = i.name === 'eth0' ? 'cable to the mesh node' : i.internet ? 'keep for internet' : '';
      html += '<label class="opt"><input type="radio" name="mesh" value="' + esc(i.name) + '"><span class="mono">' + esc(i.name) + '</span><span class="d">' + esc(d) + (rec ? ' · ' + rec : '') + '</span></label>';
    });
    html += '<label class="opt"><input type="radio" name="mesh" value=""><span>Later</span><span class="d">no node right now: server only, run setup again when one is plugged in</span></label>';
    $('ifaces').innerHTML = '<legend class="hidden">Mesh port</legend>' + html;
    var pick = '';
    ifs.forEach(function (i) { if (!pick && i.name === 'eth0' && /^10\.41\./.test(i.addr)) pick = 'eth0'; });
    choices.mesh_iface = pick || null;
    Array.prototype.forEach.call(document.querySelectorAll('input[name=mesh]'), function (r) {
      r.checked = r.value === pick;
      r.addEventListener('change', function () { choices.mesh_iface = this.value || null; markSel(); });
    });
    markSel();
  }
  function markSel() {
    Array.prototype.forEach.call(document.querySelectorAll('.opt'), function (o) { var i = o.querySelector('input[type=radio]'); if (i) o.classList.toggle('sel', i.checked); });
  }

  /* ---- step 4: identity */
  function buildIdentity() {
    $('cs').value = choices.callsign;
    $('cs').addEventListener('input', function () { choices.callsign = this.value; render(); });
    $('swatches').innerHTML = state.teams.map(function (t) {
      return '<button type="button" aria-label="' + esc(t) + '" aria-pressed="' + (t === choices.team) + '" data-team="' + esc(t) + '" style="background:' + TEAM_HEX[t] + '"></button>';
    }).join('');
    $('teamname').textContent = choices.team;
    Array.prototype.forEach.call($('swatches').querySelectorAll('button'), function (b) {
      b.addEventListener('click', function () {
        choices.team = this.getAttribute('data-team'); $('teamname').textContent = choices.team;
        Array.prototype.forEach.call($('swatches').querySelectorAll('button'), function (x) { x.setAttribute('aria-pressed', String(x === b)); });
      });
    });
    $('role').innerHTML = state.roles.map(function (r) { return '<option' + (r === choices.role ? ' selected' : '') + '>' + esc(r) + '</option>'; }).join('');
    $('role').addEventListener('change', function () { choices.role = this.value; });
  }

  /* ---- step 5: features */
  function buildFeatures() {
    var gps = state.facts.filter(function (f) { return f.title === 'GPS (gpsd)'; })[0];
    $('f-return').checked = choices.return_path; $('f-dm').checked = choices.dm_relay; $('f-gps').checked = choices.gps || (gps && gps.level === 'ok');
    choices.gps = $('f-gps').checked;
    $('gpsnote').textContent = gps ? gps.detail : 'uses gpsd';
    $('ops-local').checked = choices.ops_local; $('ops-mesh').checked = !choices.ops_local;
    $('f-return').addEventListener('change', function () { choices.return_path = this.checked; });
    $('f-dm').addEventListener('change', function () { choices.dm_relay = this.checked; });
    $('f-gps').addEventListener('change', function () { choices.gps = this.checked; });
    $('ops-local').addEventListener('change', function () { choices.ops_local = true; markSel(); });
    $('ops-mesh').addEventListener('change', function () { choices.ops_local = false; markSel(); });
  }

  /* ---- step 6: helper */
  function buildHelper() {
    $('helper-on').classList.toggle('hidden', !choices.dm_relay); $('helper-off').classList.toggle('hidden', !!choices.dm_relay);
    if (choices.dm_relay) api('GET', '/api/setup/helper').then(function (h) { $('h-uid').textContent = h.uid; $('h-cmd').textContent = h.command; });
  }

  /* ---- step 7: finish */
  function allChoices() {
    var m = (state.interfaces.filter(function (i) { return i.name === choices.mesh_iface; })[0] || {});
    return { host: $('host').value.trim(), port: Number($('port').value), callsign: choices.callsign.trim(), team: choices.team, role: choices.role,
      gps: choices.gps, return_path: choices.return_path, dm_relay: choices.dm_relay, ops_local: choices.ops_local,
      mesh_iface: choices.mesh_iface, mesh_addr: m.addr || '' };
  }
  function buildPlan() {
    show($('finerr'), '');
    api('POST', '/api/setup/plan', { choices: allChoices() }).then(function (r) {
      if (!r.ok) { show($('finerr'), r.error, 'bad'); $('plan').innerHTML = ''; render(); return; }
      var html = '';
      Object.keys(r.changes).forEach(function (sec) {
        Object.keys(r.changes[sec]).forEach(function (k) {
          var v = r.changes[sec][k];
          html += '<div class="kv"><span class="mono">[' + esc(sec) + '] ' + esc(k) + '</span><span class="mono">' + (v === null ? '<span class="lv-info">(off)</span>' : esc(v)) + '</span></div>';
        });
      });
      $('plan').innerHTML = html; render();
    });
  }
  function save() {
    $('next').disabled = true; $('back').disabled = true;
    $('fin-title').textContent = 'Saving…';
    api('POST', '/api/setup/finish', { choices: allChoices() }).then(function (r) {
      if (!r.ok) { show($('finerr'), r.error, 'bad'); $('fin-title').textContent = 'Ready to save'; $('next').disabled = false; $('back').disabled = false; return; }
      finished = true; $('plan').classList.add('hidden'); render();
      $('fin-lead').textContent = 'Saved. The old deck.ini is kept as ' + r.backup.split('/').pop() + '.';
      if (r.restart === 'auto') { $('fin-title').textContent = 'Restarting…'; waitAndCheck(Date.now()); }
      else { $('fin-title').textContent = 'Saved: one command left'; $('restart').classList.remove('hidden'); render(); }
    });
  }
  function waitAndCheck(t0) {
    setTimeout(function () {
      fetch('/healthz', { cache: 'no-store' }).then(function (r) { if (!r.ok) throw 0; return new Promise(function (ok) { setTimeout(ok, 8000); }); })
        .then(runCheck).catch(function () { if (Date.now() - t0 < 90000) waitAndCheck(t0); else { $('restart').classList.remove('hidden'); } });
    }, 3000);
  }
  function runCheck() {
    $('fin-title').textContent = 'Checking…';
    api('GET', '/api/setup/check').then(function (r) {
      var bad = r.rows.filter(function (x) { return x.level === 'bad'; }).length;
      $('checkrows').innerHTML = rows(r.rows); $('checkrows').classList.remove('hidden'); $('fin-note').classList.remove('hidden');
      $('fin-title').textContent = bad ? 'Saved, with something to fix' : ($('cs').value.trim() || 'The deck') + ' is on the air';
      $('fin-lead').textContent = r.summary || '';
      $('restart').classList.add('hidden'); render();
    });
  }
  $('recheck').addEventListener('click', runCheck);

  /* ---- moving between steps */
  function canNext() {
    if (step === 2) return unlocked && $('host').value.trim() !== '' && Number($('port').value) > 0;
    if (step === 4) return /^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,31}$/.test(($('cs').value || '').trim());
    if (step === 7) return !finished && $('plan').innerHTML !== '';
    return true;
  }
  function render() {
    Array.prototype.forEach.call(document.querySelectorAll('section[data-step]'), function (s) { s.classList.toggle('hidden', Number(s.getAttribute('data-step')) !== step); });
    Array.prototype.forEach.call($('nav').children, function (d, i) { d.className = i + 1 === step ? 'on' : i + 1 < step ? 'done' : ''; });
    $('stepof').textContent = 'STEP ' + step + ' OF 7';
    $('back').disabled = step === 1 || finished;
    $('next').textContent = step === 7 ? 'SAVE AND RESTART' : step === 2 && unlocked && !tested ? 'NEXT WITHOUT TEST >' : 'NEXT >';
    $('next').classList.toggle('hidden', finished);
    $('toops').classList.toggle('hidden', !finished);
    $('next').disabled = !canNext();
  }
  $('next').addEventListener('click', function () {
    if (step === 7) return save();
    step += 1;
    if (step === 6) buildHelper();
    if (step === 7) buildPlan();
    render(); $('main').scrollTop = 0;
  });
  $('back').addEventListener('click', function () { if (step > 1) { step -= 1; render(); } });
  $('skip').addEventListener('click', function () { try { sessionStorage.setItem('deck.setup-later', '1'); } catch (e) { /* private window */ } });
  $('toops').addEventListener('click', function () { try { sessionStorage.removeItem('deck.setup-later'); } catch (e) { /* ignore */ } });
  loadState().catch(function () { $('facts').innerHTML = rows([{ level: 'bad', title: 'Ops', detail: 'not answering: is manet-dashboard running?' }]); });
  render();
})();
