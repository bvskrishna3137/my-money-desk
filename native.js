/* My Money Desk: Android layer.
   Inside the Android app there is no server.py. This file answers the app's /api/* calls itself
   (same rules as server.py: restore points, shrink guard, undo copy) and keeps the files in the app's own storage.
   It also gives the app a way to share files, and handles the Back button. On a normal browser it does nothing. */
(function () {
  'use strict';
  var C = window.Capacitor;
  var NATIVE = !!(C && typeof C.isNativePlatform === 'function' && C.isNativePlatform());
  window.NATIVE = NATIVE;
  var NV = window.NV = { ready: NATIVE };
  if (!NATIVE) return;

  function plug(name) {
    try { if (C.Plugins && C.Plugins[name]) return C.Plugins[name]; } catch (e) {}
    try { return C.registerPlugin(name); } catch (e) { return null; }
  }
  NV.plug = plug;
  var FS = plug('Filesystem'), SH = plug('Share'), AP = plug('App');
  var DIR = 'DATA', ROOT = 'mmd', MAX_BYTES = 5 * 1024 * 1024;
  var LISTS = ['expenses', 'income', 'bills', 'debts', 'cards', 'goals', 'recurring', 'jobs', 'work', 'assets', 'autoexp', 'scores', 'rewards'];
  var REQUIRED = ['expenses', 'income', 'cards', 'debts'];
  var KEEP = { daily: 14, snap: 6, drop: 10, undo: 5 }, SNAP_EVERY = 30 * 60;
  var NAME_OK = /^[A-Za-z0-9._-]+\.json$/;

  /* ---------- small file helpers (all inside the app's private folder) ---------- */
  function wr(path, data) { return FS.writeFile({ path: ROOT + '/' + path, data: data, directory: DIR, encoding: 'utf8', recursive: true }); }
  function rd(path) {
    return FS.readFile({ path: ROOT + '/' + path, directory: DIR, encoding: 'utf8' }).then(function (r) { return typeof r.data === 'string' ? r.data : null; }, function () { return null; });
  }
  function rm(path) { return FS.deleteFile({ path: ROOT + '/' + path, directory: DIR }).then(function () {}, function () {}); }
  function ls(path) { return FS.readdir({ path: ROOT + '/' + path, directory: DIR }).then(function (r) { return r.files || []; }, function () { return []; }); }
  NV.fsWrite = wr; NV.fsRead = rd; NV.fsRemove = rm; NV.fsList = ls;

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function stamp(d) { d = d || new Date(); return d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + '-' + pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds()); }
  function dayStr(d) { d = d || new Date(); return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function total(o) { var n = 0; try { LISTS.forEach(function (k) { n += (o && o[k] || []).length; }); } catch (e) {} return n; }
  function counts(o) { var c = {}; LISTS.forEach(function (k) { c[k] = (o && o[k] || []).length; }); return c; }
  function kindOf(fn) { return fn.indexOf('snap-') === 0 ? 'snap' : fn.indexOf('before-drop-') === 0 ? 'drop' : fn.indexOf('before-restore-') === 0 ? 'undo' : 'daily'; }
  function resp(code, body, extra) {
    var h = { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }; if (extra) for (var k in extra) h[k] = extra[k];
    return new Response(body == null ? '{}' : body, { status: code, headers: h });
  }

  /* ---------- state: revision number and the list of restore points ---------- */
  var st = null, chain = Promise.resolve();
  function serial(fn) { var p = chain.then(fn, fn); chain = p.then(function () {}, function () {}); return p; }
  function init() {
    if (st) return Promise.resolve(st);
    return Promise.all([rd('rev.txt'), rd('backups/index.json'), ls('backups')]).then(function (a) {
      var idx = {}; try { idx = JSON.parse(a[1] || '{}') || {}; } catch (e) { idx = {}; }
      var on = {}; a[2].forEach(function (f) { if (NAME_OK.test(f.name) && f.name !== 'index.json') on[f.name] = f; });
      Object.keys(idx).forEach(function (n) { if (!on[n]) delete idx[n]; });
      Object.keys(on).forEach(function (n) { if (!idx[n]) { var mt = on[n].mtime || Date.now(); idx[n] = { mtime: Math.floor(mt > 1e12 ? mt / 1000 : mt), size: on[n].size || 0, counts: null }; } });
      st = { rev: parseInt(a[0] || '0', 10) || 0, idx: idx };
      return st;
    });
  }
  function saveIdx() { return wr('backups/index.json', JSON.stringify(st.idx)); }
  function prune(re, keep) {
    var names = Object.keys(st.idx).filter(function (n) { return re.test(n); }).sort(), drop = names.slice(0, Math.max(0, names.length - keep));
    return Promise.all(drop.map(function (n) { delete st.idx[n]; return rm('backups/' + n); }));
  }
  function putBackup(name, raw, cnt) {
    return wr('backups/' + name, raw).then(function () { st.idx[name] = { mtime: Math.floor(Date.now() / 1000), size: raw.length, counts: cnt }; });
  }
  function lastSnapSec() { var m = 0; Object.keys(st.idx).forEach(function (n) { if (n.indexOf('snap-') === 0 && st.idx[n].mtime > m) m = st.idx[n].mtime; }); return m; }

  /* ---------- the four calls the app makes ---------- */
  function apiLoad() {
    return init().then(function () { return rd('money-data.json'); }).then(function (raw) { return resp(200, raw || '{}', { 'X-Rev': String(st.rev) }); });
  }
  function headerOf(init0, name) {
    var h = init0 && init0.headers; if (!h) return null; name = name.toLowerCase();
    if (typeof h.get === 'function') return h.get(name);
    for (var k in h) if (k.toLowerCase() === name) return h[k];
    return null;
  }
  function apiSave(init0) {
    return init().then(function () {
      var raw = init0 && init0.body;
      if (typeof raw !== 'string' || !raw.length || raw.length > MAX_BYTES) return resp(413, '{}');
      var obj; try { obj = JSON.parse(raw); if (!obj || typeof obj !== 'object' || !REQUIRED.every(function (k) { return Array.isArray(obj[k]); })) throw 0; } catch (e) { return resp(400, '{"error":"bad data"}'); }
      var restore = headerOf(init0, 'X-Restore') === '1', base = headerOf(init0, 'X-Base-Rev');
      if (!restore && base != null) { var b = parseInt(base, 10); if (!(b === st.rev)) return resp(409, JSON.stringify({ conflict: true, rev: st.rev })); }
      return rd('money-data.json').then(function (old) {
        var shrink = false, steps = Promise.resolve();
        if (old) {
          var oo = null; try { oo = JSON.parse(old); } catch (e) {}
          var ot = total(oo), nt = total(obj);
          if (restore) steps = steps.then(function () { return putBackup('before-restore-' + stamp() + '.json', old, counts(oo)).then(function () { return prune(/^before-restore-/, KEEP.undo); }); });
          else if (ot - nt >= 10 && nt < ot * 0.6) { shrink = true; steps = steps.then(function () { return putBackup('before-drop-' + stamp() + '.json', old, counts(oo)).then(function () { return prune(/^before-drop-/, KEEP.drop); }); }); }
          steps = steps.then(function () { return wr('money-data.json.bak', old); });
        }
        return steps.then(function () { return wr('money-data.json', raw); }).then(function () {
          st.rev += 1; var ok = wr('rev.txt', String(st.rev)).catch(function () {}), cnt = counts(obj), now = Date.now();
          return ok.then(function () {
            var more = Promise.resolve();
            if (!shrink) more = more.then(function () { return putBackup('money-data-' + dayStr() + '.json', raw, cnt).then(function () { return prune(/^money-data-/, KEEP.daily); }); });
            if (now / 1000 - lastSnapSec() >= SNAP_EVERY) more = more.then(function () { return putBackup('snap-' + stamp() + '.json', raw, cnt).then(function () { return prune(/^snap-/, KEEP.snap); }); });
            return more.then(saveIdx).catch(function () {});
          }).then(function () { return resp(200, JSON.stringify({ ok: true, rev: st.rev, shrink: shrink })); });
        });
      });
    });
  }
  function apiBackups() {
    return init().then(function () {
      var items = Object.keys(st.idx).map(function (n) { var x = st.idx[n]; return { file: n, kind: kindOf(n), mtime: x.mtime, size: x.size, counts: x.counts || null }; });
      items.sort(function (a, b) { return b.mtime - a.mtime; });
      return resp(200, JSON.stringify({ rev: st.rev, items: items }));
    });
  }
  function apiBackup(url) {
    var m = /[?&]f=([^&]*)/.exec(url), fn = m ? decodeURIComponent(m[1]) : '';
    if (!NAME_OK.test(fn) || fn === 'index.json') return Promise.resolve(resp(404, '{}'));
    return rd('backups/' + fn).then(function (raw) { return raw == null ? resp(404, '{}') : resp(200, raw); });
  }

  var realFetch = window.fetch.bind(window);
  window.fetch = function (input, init0) {
    var url = typeof input === 'string' ? input : (input && input.url) || '', path = url.replace(/^https?:\/\/[^/]+/, '');
    if (/^\/api\/load(\?|$)/.test(path)) return serial(apiLoad);
    if (/^\/api\/save(\?|$)/.test(path)) return serial(function () { return apiSave(init0); });
    if (/^\/api\/backups(\?|$)/.test(path)) return serial(apiBackups);
    if (/^\/api\/backup(\?|$)/.test(path)) return serial(function () { return apiBackup(path); });
    if (/^\/?index\.html\?v=/.test(path)) return Promise.resolve(new Response('', { status: 200 })); /* no web updates inside the app */
    return realFetch(input, init0);
  };

  /* ---------- sharing files (backups, CSV, PDF) ---------- */
  function shareFile(name, data, isBase64) {
    var path = 'share/' + name.replace(/[^A-Za-z0-9._-]+/g, '_');
    var args = { path: path, data: data, directory: 'CACHE', recursive: true }; if (!isBase64) args.encoding = 'utf8';
    return FS.writeFile(args).then(function () { return FS.getUri({ path: path, directory: 'CACHE' }); })
      .then(function (r) { return SH.share({ title: name, url: r.uri, dialogTitle: 'Save or share' }); });
  }
  NV.shareText = function (name, text) { return shareFile(name, text, false); };
  NV.shareBytes = function (name, bytes) {
    var s = '', i, CH = 0x8000; for (i = 0; i < bytes.length; i += CH) s += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
    return shareFile(name, btoa(s), true);
  };

  /* ---------- Back button ---------- */
  if (AP && AP.addListener) {
    AP.addListener('backButton', function () {
      var handled = false;
      try { if (typeof app !== 'undefined' && app && app.nativeBack) handled = app.nativeBack(); } catch (e) {}
      if (!handled) { try { if (AP.minimizeApp) AP.minimizeApp(); else if (AP.exitApp) AP.exitApp(); } catch (e) {} }
    });
  }
  NV.api = { load: apiLoad, save: apiSave, backups: apiBackups };
})();
