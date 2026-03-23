// CameraDashboard.jsx — Main React application for Imou Portal
// Loaded by index.html via Babel standalone (no build step required)

const { useState, useEffect, useRef, useCallback } = React;

// ─────────────────── API helpers ─────────────────────────────────────────────

async function api(method, path, body) {
  const opts = {
    method: method || 'GET',
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch('/api' + path, opts);
  return res.json();
}

const get  = (path)       => api('GET', path);
const post = (path, body) => api('POST', path, body);
const del  = (path)       => api('DELETE', path);

// ─────────────────── Utility helpers ─────────────────────────────────────────

// alarm_time is stored as UTC (without Z). Add Z so JS parses it as UTC, not local.
function toUtcDate(dateStr) {
  if (!dateStr) return new Date(NaN);
  const s = String(dateStr).replace(' ', 'T');
  return new Date(s.endsWith('Z') || s.includes('+') ? s : s + 'Z');
}

function timeAgo(dateStr) {
  if (!dateStr) return '';
  const diff = (Date.now() - toUtcDate(dateStr).getTime()) / 1000;
  if (diff < 60)   return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return toUtcDate(dateStr).toLocaleDateString('lv-LV', { timeZone: 'Europe/Riga' });
}

function eventLabel(type) {
  const map = {
    AlarmHumanDetection: '🚶 Human Detected',
    AlarmMotion:         '🌀 Motion',
    AlarmSound:          '🔊 Sound',
    AlarmTamper:         '⚠️ Tamper',
    AlarmLine:           '📏 Line Cross',
    AlarmRegion:         '🔲 Region Entry',
    AlarmFace:           '😊 Face',
    AlarmSmoke:          '💨 Smoke',
  };
  return map[type] || type;
}

function eventColor(type) {
  if (type?.includes('Human') || type?.includes('Face')) return 'human';
  if (type?.includes('Motion') || type?.includes('Line') || type?.includes('Region')) return 'motion';
  return '';
}

function hasAbility(device, cap) {
  return (device?.abilities || []).some(a => a.toUpperCase().includes(cap.toUpperCase()));
}

// ─────────────────── Toast component ─────────────────────────────────────────

function Toast({ toast, onClose }) {
  useEffect(() => {
    const t = setTimeout(onClose, 7000);
    return () => clearTimeout(t);
  }, []);

  const cls = `toast toast-${eventColor(toast.event_type)}`;
  return (
    <div className={cls} onClick={onClose}>
      <div className="toast-header">
        <span className="toast-icon">
          {toast.event_type?.includes('Human') ? '🚶' : '🔔'}
        </span>
        <span className="toast-title">{eventLabel(toast.event_type)}</span>
      </div>
      <div className="toast-body">{toast.device_name || toast.device_id}</div>
      {toast.image_url && (
        <img
          className="toast-img"
          src={toast.image_url.startsWith('/') ? toast.image_url + '?v=2' : `/api/proxy/image?url=${encodeURIComponent(toast.image_url)}`}
          alt="Event snapshot"
          onError={e => { e.target.style.display = 'none'; }}
        />
      )}
    </div>
  );
}

// ─────────────────── PTZ Control panel ───────────────────────────────────────

function PTZControl({ device, onClose }) {
  const [lastOp, setLastOp] = useState(null);

  async function move(op) {
    setLastOp(op);
    await post(`/devices/${device.deviceId}/ptz`, {
      operation: op,
      duration: 600,
    });
  }

  const BTNS = [
    ['5', '↖'], ['1', '↑'], ['6', '↗'],
    ['3', '←'], ['0', '⏹'], ['4', '→'],
    ['7', '↙'], ['2', '↓'], ['8', '↘'],
  ];

  return (
    <div className="ptz-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="ptz-panel">
        <div className="modal-close" onClick={onClose}>✕</div>
        <div className="ptz-title">📹 PTZ Control — {device.name}</div>
        <div className="ptz-grid">
          {BTNS.map(([op, label]) => (
            <button
              key={op}
              className={`ptz-btn${op === '0' ? ' stop' : ''}`}
              onMouseDown={() => move(op)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="ptz-zoom-row">
          <button className="ptz-btn" onMouseDown={() => move('9')}>🔍+ Zoom In</button>
          <button className="ptz-btn" onMouseDown={() => move('10')}>🔍− Zoom Out</button>
        </div>
        <div style={{ marginTop: 12 }}>
          <div className="ptz-zoom-row">
            <button className="ptz-btn" onMouseDown={() => move('11')}>🎯 Focus Near</button>
            <button className="ptz-btn" onMouseDown={() => move('12')}>🎯 Focus Far</button>
          </div>
        </div>
        <div style={{ textAlign: 'center', marginTop: 12, fontSize: 11, color: 'var(--text-muted)' }}>
          Click buttons to move. Each click sends a 600ms movement.
        </div>
      </div>
    </div>
  );
}

// ─────────────────── Stream Viewer ───────────────────────────────────────────

function StreamViewer({ device, onClose }) {
  const [streamData, setStreamData] = useState(null);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(null);
  const [streamId, setStreamId] = useState(0); // 0=main, 1=sub
  const videoRef = useRef(null);
  const hlsRef   = useRef(null);

  async function loadStream(sid = streamId) {
    setLoading(true);
    setError(null);
    setStreamData(null);
    try {
      const res = await post(`/devices/${device.deviceId}/stream`, { streamId: sid });
      if (res.ok) {
        setStreamData(res.data);
      } else {
        setError(res.error || 'Failed to get stream');
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadStream();
    return () => {
      // Unbind stream on close to release server resources
      post(`/devices/${device.deviceId}/stream/unbind`, { channel: '0' }).catch(() => {});
      if (hlsRef.current) hlsRef.current.destroy();
    };
  }, []);

  useEffect(() => {
    if (!streamData || !videoRef.current) return;
    const hlsUrl = streamData.hls || streamData.subHls;
    if (!hlsUrl) return;

    if (Hls.isSupported()) {
      if (hlsRef.current) hlsRef.current.destroy();
      const hls = new Hls({ enableWorker: true });
      hls.loadSource(hlsUrl);
      hls.attachMedia(videoRef.current);
      hls.on(Hls.Events.MANIFEST_PARSED, () => videoRef.current?.play().catch(() => {}));
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (data.fatal) setError('Stream error: ' + data.type);
      });
      hlsRef.current = hls;
    } else if (videoRef.current.canPlayType('application/vnd.apple.mpegurl')) {
      // Native HLS on Safari
      videoRef.current.src = hlsUrl;
      videoRef.current.play().catch(() => {});
    } else {
      setError('HLS not supported in this browser. Copy the stream URL to use in VLC.');
    }
  }, [streamData]);

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal stream-modal" style={{ maxWidth: 700 }}>
        <div className="modal-close" onClick={onClose}>✕</div>
        <div className="modal-title">📺 Live Stream — {device.name}</div>

        <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
          {[0, 1].map(sid => (
            <button
              key={sid}
              className={`btn${streamId === sid ? ' active' : ''}`}
              disabled={loading}
              onClick={() => { setStreamId(sid); loadStream(sid); }}
            >
              {sid === 0 ? 'Main Stream (HD)' : 'Sub Stream (SD)'}
            </button>
          ))}
        </div>

        {loading && (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <div className="spinner" style={{ margin: '0 auto' }} />
            <div style={{ marginTop: 12, color: 'var(--text-muted)' }}>Connecting to stream…</div>
          </div>
        )}

        {error && (
          <div style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 8, padding: 16, color: 'var(--danger)', fontSize: 13 }}>
            ⚠️ {error}
          </div>
        )}

        {streamData && !error && (
          <>
            <video ref={videoRef} controls muted playsInline style={{ width: '100%', borderRadius: 8, background: '#000', maxHeight: 400 }} />
            {streamData.hls && (
              <div className="stream-info">
                <div style={{ marginBottom: 4, color: 'var(--text-muted)', fontFamily: 'sans-serif', fontSize: 11 }}>HLS URL (use in VLC or other players):</div>
                {streamData.hls}
              </div>
            )}
            {streamData.rtmp && (
              <div className="stream-info" style={{ marginTop: 8 }}>
                <div style={{ marginBottom: 4, color: 'var(--text-muted)', fontFamily: 'sans-serif', fontSize: 11 }}>RTMP URL:</div>
                {streamData.rtmp}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ─────────────────── Camera Settings Modal ────────────────────────────────────

function CameraSettingsModal({ device, onClose }) {
  const [motion, setMotion]         = useState({ enabled: true, sensitivity: 6 });
  const [nightVision, setNightVision] = useState(2);
  const [privacy, setPrivacy]       = useState(false);
  const [storage, setStorage]       = useState(null);
  const [loading, setLoading]       = useState(true);
  const [saving, setSaving]         = useState(false);
  const [status, setStatus]         = useState(null);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const [m, nv, p, s] = await Promise.allSettled([
          get(`/devices/${device.deviceId}/motion`),
          get(`/devices/${device.deviceId}/nightvision`),
          get(`/devices/${device.deviceId}/privacy`),
          get(`/devices/${device.deviceId}/storage`),
        ]);
        if (m.status === 'fulfilled' && m.value.ok) setMotion(m.value.data);
        if (nv.status === 'fulfilled' && nv.value.ok) setNightVision(nv.value.data?.mode ?? 2);
        if (p.status === 'fulfilled' && p.value.ok) setPrivacy(!!p.value.data?.enable);
        if (s.status === 'fulfilled' && s.value.ok) setStorage(s.value.data);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [device.deviceId]);

  async function save() {
    setSaving(true);
    setStatus(null);
    try {
      await Promise.all([
        post(`/devices/${device.deviceId}/motion`, motion),
        post(`/devices/${device.deviceId}/nightvision`, { mode: nightVision }),
        post(`/devices/${device.deviceId}/privacy`, { enabled: privacy }),
      ]);
      setStatus({ ok: true, msg: 'Settings saved successfully!' });
    } catch (e) {
      setStatus({ ok: false, msg: 'Failed to save: ' + e.message });
    } finally {
      setSaving(false);
    }
  }

  async function restart() {
    if (!confirm(`Restart camera "${device.name}"?`)) return;
    const r = await post(`/devices/${device.deviceId}/restart`);
    setStatus(r.ok ? { ok: true, msg: 'Camera restarting…' } : { ok: false, msg: r.error });
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-close" onClick={onClose}>✕</div>
        <div className="modal-title">⚙️ {device.name}</div>

        {loading && <div style={{ textAlign: 'center', padding: 32 }}><div className="spinner" style={{ margin: '0 auto' }} /></div>}

        {!loading && (
          <>
            {/* Device info */}
            <div className="settings-section">
              <div className="settings-section-title">Device Info</div>
              <div className="detail-grid">
                <div className="detail-card">
                  <div className="detail-label">Device ID</div>
                  <div className="detail-value" style={{ fontSize: 11, fontFamily: 'monospace', wordBreak: 'break-all' }}>{device.deviceId}</div>
                </div>
                <div className="detail-card">
                  <div className="detail-label">Status</div>
                  <div className="detail-value">
                    <span className={`tag tag-${device.status === 'online' ? 'green' : 'red'}`}>{device.status || 'unknown'}</span>
                  </div>
                </div>
                {storage && (
                  <div className="detail-card">
                    <div className="detail-label">Storage</div>
                    <div className="detail-value" style={{ fontSize: 12 }}>
                      {storage.totalSpace ? `${Math.round(storage.usedSpace / 1024)}/${Math.round(storage.totalSpace / 1024)} MB` : 'N/A'}
                    </div>
                  </div>
                )}
                <div className="detail-card">
                  <div className="detail-label">Capabilities</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
                    {(device.abilities || []).slice(0, 5).map(a => (
                      <span key={a} className="tag" style={{ fontSize: 10 }}>{a}</span>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            {/* Motion detection */}
            <div className="settings-section">
              <div className="settings-section-title">Motion Detection</div>
              <div className="toggle-row">
                <div>
                  <div className="toggle-label">Enable Motion Detection</div>
                  <div className="toggle-desc">Camera will detect movement and send alerts</div>
                </div>
                <div
                  className={`toggle${motion.enabled ? ' on' : ''}`}
                  onClick={() => setMotion(m => ({ ...m, enabled: !m.enabled }))}
                />
              </div>
              <div className="form-group" style={{ marginTop: 12 }}>
                <label className="form-label">Sensitivity (1=Low, 8=High) — Current: {motion.sensitivity}</label>
                <input
                  type="range" min="1" max="8"
                  value={motion.sensitivity}
                  onChange={e => setMotion(m => ({ ...m, sensitivity: parseInt(e.target.value) }))}
                  style={{ width: '100%', accentColor: 'var(--blue)' }}
                />
              </div>
            </div>

            {/* Night vision */}
            <div className="settings-section">
              <div className="settings-section-title">Night Vision / IR</div>
              <div className="form-group">
                <label className="form-label">Night Vision Mode</label>
                <select
                  className="form-select"
                  value={nightVision}
                  onChange={e => setNightVision(parseInt(e.target.value))}
                >
                  <option value={1}>Always On (IR)</option>
                  <option value={2}>Auto</option>
                  <option value={3}>Off</option>
                </select>
              </div>
            </div>

            {/* Privacy mask */}
            <div className="settings-section">
              <div className="settings-section-title">Privacy</div>
              <div className="toggle-row">
                <div>
                  <div className="toggle-label">Privacy Mask (Cover Lens)</div>
                  <div className="toggle-desc">When enabled, camera stops recording and streaming</div>
                </div>
                <div
                  className={`toggle${privacy ? ' on' : ''}`}
                  style={privacy ? { background: 'var(--warning)' } : {}}
                  onClick={() => setPrivacy(p => !p)}
                />
              </div>
            </div>

            {status && (
              <div style={{
                background: status.ok ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)',
                border: `1px solid ${status.ok ? 'rgba(16,185,129,0.3)' : 'rgba(239,68,68,0.3)'}`,
                borderRadius: 8, padding: 12, fontSize: 13,
                color: status.ok ? 'var(--success)' : 'var(--danger)',
                marginBottom: 16,
              }}>
                {status.msg}
              </div>
            )}

            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn btn-danger btn-sm" onClick={restart}>🔄 Restart Camera</button>
              <button className="btn" onClick={onClose}>Cancel</button>
              <button className="btn btn-primary" onClick={save} disabled={saving}>
                {saving ? 'Saving…' : '💾 Save Settings'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ─────────────────── Manual Device Manager ───────────────────────────────────

function ManualDeviceManager() {
  const [devices, setDevices] = useState([]);
  const [newId, setNewId]     = useState('');
  const [newName, setNewName] = useState('');
  const [bulkText, setBulkText] = useState('');
  const [status, setStatus]   = useState(null);

  // Known device serial numbers from the screenshot — pre-filled for quick import
  const KNOWN_DEVICES = [
    { device_id: '9E09EF7PBV9757E', name: 'Camera 1 (Offline)' },
    { device_id: '9K05121PCP6B1EB', name: 'Camera 2' },
    { device_id: '9E09EF7PBV73A00', name: 'Camera 3' },
    { device_id: '8C08B92PAZCD296', name: 'Camera 4' },
    { device_id: '8C08B92PAZDFC4B', name: 'Camera 5' },
  ];

  useEffect(() => {
    get('/devices/manual').then(r => r.ok && setDevices(r.data));
  }, []);

  async function addDevice() {
    if (!newId) return;
    const r = await post('/devices/manual', { device_id: newId, name: newName || newId });
    if (r.ok) {
      setDevices(d => [...d.filter(x => x.device_id !== r.data.device_id), { device_id: r.data.device_id, name: r.data.name }]);
      setNewId(''); setNewName('');
      setStatus({ ok: true, msg: `Added ${r.data.device_id}` });
    } else {
      setStatus({ ok: false, msg: r.error });
    }
  }

  async function importKnown() {
    const r = await post('/devices/manual/bulk', { devices: KNOWN_DEVICES });
    if (r.ok) {
      const updated = await get('/devices/manual');
      if (updated.ok) setDevices(updated.data);
      setStatus({ ok: true, msg: `Imported ${r.data.count} devices` });
    }
  }

  async function removeDevice(id) {
    await api('DELETE', `/devices/manual/${id}`);
    setDevices(d => d.filter(x => x.device_id !== id));
  }

  return (
    <div>
      {/* Quick import from known portal devices */}
      {devices.length === 0 && (
        <div style={{ background: 'rgba(59,130,246,0.07)', border: '1px solid var(--border)', borderRadius: 10, padding: 14, marginBottom: 16 }}>
          <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>Quick Import from your Imou portal</div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 10 }}>
            Detected from your Device Access Service screenshot:
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: 12 }}>
            {KNOWN_DEVICES.map(d => (
              <div key={d.device_id} style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--text-dim)' }}>
                {d.device_id} — {d.name}
              </div>
            ))}
          </div>
          <button className="btn btn-primary btn-sm" onClick={importKnown}>⚡ Import All 5 Cameras</button>
        </div>
      )}

      {/* Registered devices */}
      {devices.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          {devices.map(d => (
            <div key={d.device_id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '7px 10px', background: 'rgba(255,255,255,0.03)', borderRadius: 8, border: '1px solid var(--border)', marginBottom: 6 }}>
              <div>
                <span style={{ fontWeight: 600 }}>{d.name}</span>
                <span style={{ marginLeft: 8, fontFamily: 'monospace', fontSize: 11, color: 'var(--text-muted)' }}>{d.device_id}</span>
              </div>
              <button className="btn btn-danger btn-sm" onClick={() => removeDevice(d.device_id)}>Remove</button>
            </div>
          ))}
        </div>
      )}

      {/* Add single device */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
        <input
          className="form-input"
          placeholder="Serial number (e.g. 9K05121PCP6B1EB)"
          value={newId}
          onChange={e => setNewId(e.target.value.toUpperCase())}
          style={{ flex: 2, fontFamily: 'monospace' }}
        />
        <input
          className="form-input"
          placeholder="Camera name"
          value={newName}
          onChange={e => setNewName(e.target.value)}
          style={{ flex: 2 }}
        />
        <button className="btn btn-primary" onClick={addDevice} style={{ whiteSpace: 'nowrap' }}>➕ Add</button>
      </div>

      {status && (
        <div style={{ fontSize: 12, color: status.ok ? 'var(--success)' : 'var(--danger)', marginTop: 6 }}>
          {status.ok ? '✓' : '✗'} {status.msg}
        </div>
      )}
    </div>
  );
}


// ─────────────────── Settings Modal ──────────────────────────────────────────

function SettingsModal({ onClose, user }) {
  const [settings, setSettings] = useState({
    webhook_url: '',
    snapshot_interval: '900',
    notification_sound: '1',
  });
  const [pwForm, setPwForm]     = useState({ current: '', new: '', confirm: '' });
  const [pwStatus, setPwStatus] = useState(null);
  const [users, setUsers]     = useState([]);
  const [newUser, setNewUser] = useState({ username: '', password: '', is_admin: false });
  const [saving, setSaving]   = useState(false);
  const [tab, setTab]         = useState('general');
  const [status, setStatus]   = useState(null);
  const [tokenStatus, setTokenStatus] = useState(null);

  useEffect(() => {
    get('/settings').then(r => r.ok && setSettings(r.data));
    get('/token-status').then(r => r.ok && setTokenStatus(r.data));
    if (user?.is_admin) {
      get('/admin/users').then(r => r.ok && setUsers(r.data));
    }
  }, []);

  async function changePassword() {
    setPwStatus(null);
    if (!pwForm.current || !pwForm.new || !pwForm.confirm) {
      return setPwStatus({ ok: false, msg: 'All fields are required' });
    }
    if (pwForm.new !== pwForm.confirm) {
      return setPwStatus({ ok: false, msg: 'New passwords do not match' });
    }
    if (pwForm.new.length < 8) {
      return setPwStatus({ ok: false, msg: 'Password must be at least 8 characters' });
    }
    const r = await post('/change-password', { current_password: pwForm.current, new_password: pwForm.new });
    if (r.ok) {
      setPwForm({ current: '', new: '', confirm: '' });
      setPwStatus({ ok: true, msg: 'Password changed successfully!' });
    } else {
      setPwStatus({ ok: false, msg: r.error });
    }
  }

  async function saveSettings() {
    setSaving(true);
    setStatus(null);
    const r = await post('/settings', settings);
    setSaving(false);
    setStatus(r.ok ? { ok: true, msg: 'Settings saved!' } : { ok: false, msg: r.error });
  }

  async function createUser() {
    if (!newUser.username || !newUser.password) return;
    const r = await post('/admin/users', newUser);
    if (r.ok) {
      setUsers(u => [...u, r.data]);
      setNewUser({ username: '', password: '', is_admin: false });
    } else {
      alert(r.error);
    }
  }

  async function deleteUser(id) {
    if (!confirm('Delete this user?')) return;
    await del(`/admin/users/${id}`);
    setUsers(u => u.filter(x => x.id !== id));
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={{ maxWidth: 600 }}>
        <div className="modal-close" onClick={onClose}>✕</div>
        <div className="modal-title">⚙️ Portal Settings</div>

        <div style={{ display: 'flex', gap: 8, marginBottom: 24 }}>
          {['general', 'devices', 'webhook', user?.is_admin ? 'users' : null].filter(Boolean).map(t => (
            <button key={t} className={`btn${tab === t ? ' active' : ''}`} onClick={() => setTab(t)}>
              {t.charAt(0).toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>

        {tab === 'general' && (
          <>
            <div className="settings-section">
              <div className="settings-section-title">Imou API Token</div>
              {tokenStatus && (
                <div style={{ display: 'flex', gap: 12, fontSize: 13 }}>
                  <span className={`tag tag-${tokenStatus.valid ? 'green' : 'red'}`}>
                    {tokenStatus.valid ? '✓ Valid' : '✗ Invalid'}
                  </span>
                  {tokenStatus.valid && (
                    <span className="tag">
                      Expires in {Math.floor(tokenStatus.expires_in_seconds / 3600)}h {Math.floor((tokenStatus.expires_in_seconds % 3600) / 60)}m
                    </span>
                  )}
                </div>
              )}
              <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text-muted)' }}>
                Set IMOU_APP_ID and IMOU_APP_SECRET in your .env file. Token refreshes automatically every 2.5 days.
              </div>
            </div>

            <div className="settings-section">
              <div className="settings-section-title">Display Settings</div>
              <div className="form-group">
                <label className="form-label">Snapshot Refresh Interval</label>
                <select
                  className="form-select"
                  value={settings.snapshot_interval}
                  onChange={e => setSettings(s => ({ ...s, snapshot_interval: e.target.value }))}
                >
                  <option value="300">5 minutes — max 2 cameras on free tier</option>
                  <option value="600">10 minutes — max 3 cameras on free tier</option>
                  <option value="900">15 minutes — up to 5 cameras ✓ (recommended)</option>
                  <option value="1800">30 minutes — comfortable for any setup</option>
                  <option value="3600">1 hour — minimum API usage</option>
                </select>
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6 }}>
                  Free tier: 30,000 calls/month (~1,000/day). At 15 min × 5 cameras = 480 calls/day.
                </div>
              </div>
              <div className="toggle-row">
                <div>
                  <div className="toggle-label">Notification Sound</div>
                  <div className="toggle-desc">Play sound on new alerts</div>
                </div>
                <div
                  className={`toggle${settings.notification_sound === '1' ? ' on' : ''}`}
                  onClick={() => setSettings(s => ({ ...s, notification_sound: s.notification_sound === '1' ? '0' : '1' }))}
                />
              </div>
            </div>
            <div className="settings-section">
              <div className="settings-section-title">Change Password</div>
              <div className="form-group">
                <label className="form-label">Current Password</label>
                <input className="form-input" type="password" value={pwForm.current}
                  onChange={e => setPwForm(f => ({ ...f, current: e.target.value }))} />
              </div>
              <div className="form-group">
                <label className="form-label">New Password</label>
                <input className="form-input" type="password" value={pwForm.new}
                  onChange={e => setPwForm(f => ({ ...f, new: e.target.value }))} />
              </div>
              <div className="form-group">
                <label className="form-label">Confirm New Password</label>
                <input className="form-input" type="password" value={pwForm.confirm}
                  onChange={e => setPwForm(f => ({ ...f, confirm: e.target.value }))} />
              </div>
              {pwStatus && (
                <div style={{ marginBottom: 8, fontSize: 13, color: pwStatus.ok ? 'var(--green)' : 'var(--red)' }}>
                  {pwStatus.msg}
                </div>
              )}
              <button className="btn btn-primary" onClick={changePassword}>Change Password</button>
            </div>
          </>
        )}

        {tab === 'devices' && (
          <div className="settings-section">
            <div className="settings-section-title">Manual Device Registration</div>
            <div style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 16, lineHeight: 1.7 }}>
              For <strong>Device Access Service</strong> accounts: add cameras by their serial number
              (found in the Imou developer portal under Device Access Service).
            </div>

            <ManualDeviceManager />
          </div>
        )}

        {tab === 'webhook' && (
          <div className="settings-section">
            <div className="settings-section-title">Imou Webhook (Push Notifications)</div>
            <div style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 16, lineHeight: 1.7 }}>
              Configure the webhook URL in Imou's developer console to receive real-time motion alerts.
              Your server must be publicly accessible (use ngrok, Cloudflare Tunnel, or a public server).
            </div>
            <div className="form-group">
              <label className="form-label">Webhook Callback URL</label>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  className="form-input"
                  placeholder="https://your-domain.com/api/webhook/imou"
                  value={settings.webhook_url}
                  onChange={e => setSettings(s => ({ ...s, webhook_url: e.target.value }))}
                />
              </div>
            </div>
            <div style={{ background: 'rgba(59,130,246,0.05)', border: '1px solid var(--border)', borderRadius: 8, padding: 12, fontSize: 12 }}>
              <div style={{ fontWeight: 600, marginBottom: 8, color: 'var(--cyan)' }}>Your webhook endpoint:</div>
              <div style={{ fontFamily: 'monospace', color: 'var(--text)', wordBreak: 'break-all' }}>
                {window.location.origin}/api/webhook/imou
              </div>
              <div style={{ marginTop: 8, color: 'var(--text-muted)' }}>
                Register this URL in Imou Open Platform → App Management → Callback URL
              </div>
            </div>
          </div>
        )}

        {tab === 'users' && user?.is_admin && (
          <div className="settings-section">
            <div className="settings-section-title">User Management</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
              {users.map(u => (
                <div key={u.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 12px', background: 'rgba(255,255,255,0.03)', borderRadius: 8, border: '1px solid var(--border)' }}>
                  <div>
                    <span style={{ fontWeight: 600 }}>{u.username}</span>
                    {u.is_admin && <span className="tag" style={{ marginLeft: 8, fontSize: 10 }}>Admin</span>}
                  </div>
                  <button className="btn btn-danger btn-sm" onClick={() => deleteUser(u.id)}>Delete</button>
                </div>
              ))}
            </div>
            <div style={{ borderTop: '1px solid var(--border)', paddingTop: 16 }}>
              <div style={{ fontWeight: 600, marginBottom: 12, fontSize: 13 }}>Add New User</div>
              <div className="form-row">
                <div>
                  <label className="form-label">Username</label>
                  <input className="form-input" value={newUser.username} onChange={e => setNewUser(u => ({ ...u, username: e.target.value }))} />
                </div>
                <div>
                  <label className="form-label">Password</label>
                  <input className="form-input" type="password" value={newUser.password} onChange={e => setNewUser(u => ({ ...u, password: e.target.value }))} />
                </div>
              </div>
              <div className="toggle-row" style={{ marginBottom: 12 }}>
                <div className="toggle-label">Admin privileges</div>
                <div className={`toggle${newUser.is_admin ? ' on' : ''}`} onClick={() => setNewUser(u => ({ ...u, is_admin: !u.is_admin }))} />
              </div>
              <button className="btn btn-primary" onClick={createUser}>➕ Create User</button>
            </div>
          </div>
        )}

        {status && (
          <div style={{
            background: status.ok ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)',
            border: `1px solid ${status.ok ? 'rgba(16,185,129,0.3)' : 'rgba(239,68,68,0.3)'}`,
            borderRadius: 8, padding: 12, fontSize: 13,
            color: status.ok ? 'var(--success)' : 'var(--danger)',
            marginBottom: 16,
          }}>
            {status.msg}
          </div>
        )}

        {tab !== 'users' && (
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
            <button className="btn" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" onClick={saveSettings} disabled={saving}>
              {saving ? 'Saving…' : '💾 Save Settings'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ─────────────────── Notification Image viewer ────────────────────────────────

function NotifImageModal({ notif, onClose, devices, onStream }) {
  const device = devices?.find(d => d.deviceId === notif.device_id);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 720, padding: 0, overflow: 'hidden' }} onClick={e => e.stopPropagation()}>

        {/* Header */}
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 3 }}>{eventLabel(notif.event_type)}</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              {notif.device_name} &nbsp;·&nbsp; {new Date(notif.alarm_time || notif.created_at).toLocaleString()}
            </div>
          </div>
          {device && (
            <button className="btn btn-primary btn-sm" onClick={() => { onClose(); onStream(device); }}>
              ▶ Watch Live
            </button>
          )}
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--text-muted)', fontSize: 18, cursor: 'pointer', padding: '2px 6px', lineHeight: 1 }}>✕</button>
        </div>

        {/* Content */}
        {notif.image_url ? (
          <img
            src={notif.image_url.startsWith('/') ? notif.image_url + '?v=4' : `/api/proxy/image?url=${encodeURIComponent(notif.image_url)}`}
            alt="Event snapshot"
            style={{ width: '100%', maxHeight: 520, objectFit: 'contain', background: '#000', display: 'block' }}
          />
        ) : (
          <div style={{ padding: '48px 20px', textAlign: 'center', color: 'var(--text-muted)' }}>
            <div style={{ fontSize: 48, marginBottom: 12, opacity: 0.3 }}>🎥</div>
            <div style={{ fontSize: 14, marginBottom: 6 }}>No snapshot for this alert</div>
            <div style={{ fontSize: 12 }}>New alerts will include a live snapshot automatically</div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─────────────────── Camera Card ─────────────────────────────────────────────

function CameraCard({ device, settings, onSelect, selected, onPTZ, onStream, onCameraSettings }) {
  const [snapshot, setSnapshot] = useState(null);
  const [lastTs, setLastTs]     = useState('');
  const intervalRef             = useRef(null);
  const fetchingRef             = useRef(false);  // prevent concurrent fetches

  const refreshInterval = parseInt(settings?.snapshot_interval || '900') * 1000;
  const rateLimitRef = useRef(0);  // epoch ms until which snapshots are paused

  async function fetchSnapshot() {
    if (fetchingRef.current) return;
    if (Date.now() < rateLimitRef.current) return;
    fetchingRef.current = true;
    try {
      const r = await get(`/devices/${device.deviceId}/snapshot`);
      if (r.ok && r.data?.url) {
        const url = r.data.url;
        // Imou uploads the snapshot to OSS asynchronously — the URL may 404 briefly.
        // Retry up to 4 times with increasing delays (1s, 2s, 4s, 8s) before giving up.
        const loadWithRetry = (url, attempt = 0) => {
          const delay = Math.pow(2, attempt) * 1000;
          setTimeout(() => {
            const img = new Image();
            img.onload = () => { setSnapshot(url); setLastTs(new Date().toLocaleTimeString()); };
            img.onerror = () => { if (attempt < 3) loadWithRetry(url, attempt + 1); };
            img.src = url;
          }, delay);
        };
        loadWithRetry(url);
      } else if (r.status === 429 || r.error?.startsWith('rate_limited:')) {
        const secs = parseInt((r.error || '').split(':')[1] || '86400');
        rateLimitRef.current = Date.now() + secs * 1000;
        const waitStr = secs >= 3600 ? `${Math.ceil(secs/3600)}h` : `${Math.ceil(secs/60)}m`;
        setLastTs(`⏳ Monthly quota hit — retry in ${waitStr}`);
      }
    } catch (e) { /* keep last snapshot */ }
    finally { fetchingRef.current = false; }
  }

  useEffect(() => {
    const staggerMs = (device.deviceId.charCodeAt(0) % 5) * 1800;
    const t = setTimeout(() => {
      fetchSnapshot();
      intervalRef.current = setInterval(fetchSnapshot, refreshInterval);
    }, staggerMs);
    return () => { clearTimeout(t); clearInterval(intervalRef.current); };
  }, [device.deviceId, refreshInterval]);

  const isPTZ = hasAbility(device, 'PT') || hasAbility(device, 'PTZ');

  return (
    <div className={`camera-card${selected ? ' selected' : ''}`} onClick={() => onSelect(device)}>
      {/* Video area */}
      <div className="camera-view">
        {snapshot ? (
          // Direct URL — no proxy. <img> tags load cross-origin without CORS restriction.
          <img src={snapshot} alt={device.name} style={{ transition: 'opacity 0.3s' }} />
        ) : (
          <div className="no-signal">
            <div className="no-signal-icon">📷</div>
            <div>Connecting…</div>
          </div>
        )}

        {/* Overlays */}
        {snapshot && (
          <div className="live-badge">LIVE</div>
        )}

        <div className="camera-timestamp">{lastTs}</div>

        {/* Action overlay on hover */}
        <div className="camera-overlay">
          <button
            className="btn btn-sm"
            style={{ background: 'rgba(0,0,0,0.6)', color: '#fff', border: 'none' }}
            onClick={e => { e.stopPropagation(); onStream(device); }}
          >
            ▶ Stream
          </button>
          {isPTZ && (
            <button
              className="btn btn-sm"
              style={{ background: 'rgba(0,0,0,0.6)', color: '#fff', border: 'none' }}
              onClick={e => { e.stopPropagation(); onPTZ(device); }}
            >
              🕹 PTZ
            </button>
          )}
          <button
            className="btn btn-sm"
            style={{ background: 'rgba(0,0,0,0.6)', color: '#fff', border: 'none' }}
            onClick={e => { e.stopPropagation(); fetchSnapshot(); }}
          >
            📷
          </button>
        </div>
      </div>

      {/* Camera info */}
      <div className="camera-info">
        <div className="camera-header">
          <div className="camera-name">
            <div className={`status-indicator status-${device.status === 'online' ? 'online' : 'offline'}`} />
            {device.name}
          </div>
          <button
            className="icon-btn"
            style={{ width: 28, height: 28, fontSize: 13 }}
            onClick={e => { e.stopPropagation(); onCameraSettings(device); }}
            title="Camera settings"
          >
            ⚙️
          </button>
        </div>

        <div className="camera-meta">
          <span>{device.deviceId?.slice(-8) || 'N/A'}</span>
          {device.channelNum > 1 && <span>{device.channelNum} channels</span>}
          {isPTZ && <span className="tag" style={{ padding: '1px 6px', fontSize: 10 }}>PTZ</span>}
        </div>

        <div className="camera-actions">
          <button className="btn btn-sm" onClick={e => { e.stopPropagation(); onStream(device); }}>
            ▶ Live
          </button>
          {isPTZ && (
            <button className="btn btn-sm" onClick={e => { e.stopPropagation(); onPTZ(device); }}>
              🕹 Control
            </button>
          )}
          <button className="btn btn-sm" onClick={e => { e.stopPropagation(); fetchSnapshot(); }}>
            📷 Snap
          </button>
        </div>
      </div>
    </div>
  );
}

// ─────────────────── Inline Camera Alerts ────────────────────────────────────

function CameraAlerts({ deviceId, notifications, dayFilter, onMarkOne, onImageView }) {
  const items = React.useMemo(() => {
    let cutoff = 0;
    if (dayFilter) {
      const d = new Date();
      d.setHours(0, 0, 0, 0);                        // midnight today
      d.setDate(d.getDate() - (dayFilter - 1));       // go back (dayFilter-1) extra days
      cutoff = d.getTime();
    }
    return notifications
      .filter(n => {
        if (n.device_id !== deviceId && !n.device_id?.startsWith(deviceId)) return false;
        if (cutoff && new Date(n.alarm_time || n.created_at).getTime() < cutoff) return false;
        return true;
      })
      .sort((a, b) => new Date(b.alarm_time || b.created_at) - new Date(a.alarm_time || a.created_at));
  }, [notifications, deviceId, dayFilter]);

  if (items.length === 0) return (
    <div className="cam-alerts-empty">No alerts</div>
  );

  return (
    <div className="cam-alerts-list">
      {items.map(n => (
        <div
          key={n.id}
          className={`cam-alert-row${!n.is_read ? ' unread' : ''} ${eventColor(n.event_type)}`}
          onClick={() => { onMarkOne(n.id); onImageView(n); }}
        >
          {n.image_url ? (
            <img
              className="cam-alert-thumb"
              src={n.image_url.startsWith('/') ? n.image_url + '?v=4' : `/api/proxy/image?url=${encodeURIComponent(n.image_url)}`}
              alt=""
              onError={e => { e.target.style.display = 'none'; }}
            />
          ) : (
            <div className="cam-alert-thumb-placeholder">
              {n.event_type?.includes('Human') ? '🚶' : '🌀'}
            </div>
          )}
          <div className="cam-alert-info">
            <span className="cam-alert-type" style={{
              color: n.event_type?.includes('Human') ? 'var(--danger)' :
                     n.event_type?.includes('Motion') ? 'var(--warning)' : 'var(--text-dim)'
            }}>
              {eventLabel(n.event_type)}
            </span>
            <span className="cam-alert-time" title={(n.alarm_time || n.created_at) + ' UTC'}>
              {toUtcDate(n.alarm_time || n.created_at).toLocaleTimeString('lv-LV', { timeZone: 'Europe/Riga', hour: '2-digit', minute: '2-digit', second: '2-digit' })}
              {' '}<span style={{opacity:0.5, fontSize:'0.85em'}}>{timeAgo(n.alarm_time || n.created_at)}</span>
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

// ─────────────────── Main App ─────────────────────────────────────────────────

function App() {
  const [user, setUser]             = useState(null);
  const [devices, setDevices]       = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [settings, setSettings]    = useState({});
  const [loading, setLoading]       = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [layout, setLayout]         = useState('auto');
  const [alertDayFilter, setAlertDayFilter] = useState(7);
  const [toasts, setToasts]         = useState([]);
  const [selectedCamera, setSelectedCamera] = useState(null);
  const [ptzCamera, setPtzCamera]   = useState(null);
  const [streamCamera, setStreamCamera] = useState(null);
  const [settingsCamera, setSettingsCamera] = useState(null);
  const [showSettings, setShowSettings] = useState(false);
  const [imageViewNotif, setImageViewNotif] = useState(null);
  const audioRef = useRef(null);
  const sseRef   = useRef(null);

  // ── Initial load ──
  useEffect(() => {
    async function init() {
      try {
        const [meRes, devsRes, notifsRes, settingsRes] = await Promise.all([
          get('/me'),
          get('/devices'),
          get('/notifications?limit=200'),
          get('/settings'),
        ]);

        if (!meRes.ok) {
          window.location.href = '/login';
          return;
        }

        setUser(meRes.data);
        if (devsRes.ok) setDevices(devsRes.data || []);
        if (notifsRes.ok) {
          setNotifications(notifsRes.data.notifications || []);
          setUnreadCount(notifsRes.data.unread_count || 0);
        }
        if (settingsRes.ok) setSettings(settingsRes.data);
      } catch (e) {
        window.location.href = '/login';
      } finally {
        setLoading(false);
      }
    }
    init();
  }, []);

  // ── SSE connection for real-time notifications ──
  useEffect(() => {
    function connect() {
      const es = new EventSource('/api/sse');
      sseRef.current = es;

      es.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          if (msg.type === 'notification') {
            const notif = msg.data;
            setNotifications(prev => [notif, ...prev.slice(0, 199)]);
            setUnreadCount(c => c + 1);
            addToast(notif);
            // Play sound if enabled
            if (settings.notification_sound !== '0' && audioRef.current) {
              audioRef.current.play().catch(() => {});
            }
          } else if (msg.type === 'devices_updated') {
            // Re-fetch devices when server signals update
            get('/devices').then(r => r.ok && setDevices(r.data || []));
          }
        } catch (err) {
          console.error('SSE parse error', err);
        }
      };

      es.onerror = () => {
        es.close();
        // Reconnect after 5 seconds
        setTimeout(connect, 5000);
      };
    }

    connect();
    return () => sseRef.current?.close();
  }, []);

  // ── Toast management ──
  function addToast(notif) {
    const id = Date.now();
    setToasts(prev => [...prev, { ...notif, _toastId: id }]);
  }

  function removeToast(id) {
    setToasts(prev => prev.filter(t => t._toastId !== id));
  }

  // ── Actions ──
  async function refreshDevices() {
    setRefreshing(true);
    try {
      const r = await get('/devices');
      if (r.ok) setDevices(r.data || []);
    } finally {
      setRefreshing(false);
    }
  }

  async function markAllRead() {
    await post('/notifications/read-all');
    setNotifications(prev => prev.map(n => ({ ...n, is_read: 1 })));
    setUnreadCount(0);
  }

  const [syncing, setSyncing] = useState(false);
  async function syncAlarms() {
    setSyncing(true);
    try {
      const res = await post('/notifications/sync', { days: 7 });
      if (res.ok) {
        // Reload notifications to show newly imported ones
        const r = await get('/notifications?limit=200');
        if (r.ok) {
          setNotifications(r.data.notifications || []);
          setUnreadCount(r.data.unread_count || 0);
        }
        alert(`Synced: ${res.data.imported} new alert(s) imported from Imou cloud.`);
      } else {
        alert('Sync failed: ' + (res.error || 'unknown error'));
      }
    } finally {
      setSyncing(false);
    }
  }

  async function markOneRead(id) {
    await post(`/notifications/${id}/read`);
    setNotifications(prev => prev.map(n => n.id === id ? { ...n, is_read: 1 } : n));
    setUnreadCount(c => Math.max(0, c - 1));
  }

  async function logout() {
    await post('/logout');
    window.location.href = '/login';
  }

  const onlineCount  = devices.filter(d => d.status === 'online').length;
  const offlineCount = devices.length - onlineCount;

  if (loading) {
    return (
      <div style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 16 }}>
        <div className="spinner" style={{ width: 40, height: 40 }} />
        <div style={{ color: 'var(--text-muted)' }}>Loading Imou Portal…</div>
      </div>
    );
  }

  return (
    <div className="app">
      {/* Notification sound element */}
      <audio ref={audioRef} preload="auto">
        <source src="data:audio/wav;base64,UklGRnoGAABXQVZFZm10IBAAAA..." type="audio/wav" />
      </audio>

      {/* ── Header ── */}
      <header className="header">
        <div className="header-logo">
          <div className="logo-badge">📷</div>
          <span className="logo-name">Imou Portal</span>
        </div>

        <div className="header-stats">
          <div className="stat-pill">
            <div className="stat-dot dot-green" />
            {onlineCount} online
          </div>
          {offlineCount > 0 && (
            <div className="stat-pill">
              <div className="stat-dot dot-red" />
              {offlineCount} offline
            </div>
          )}
          <div className="stat-pill">
            <div className="stat-dot dot-blue" />
            {devices.length} cameras
          </div>
        </div>

        <div className="header-spacer" />

        <div className="header-actions">
          {/* Layout switcher */}
          {['auto', '1', '2', '3', '4'].map(l => (
            <button
              key={l}
              className={`icon-btn${layout === l ? ' active' : ''}`}
              onClick={() => setLayout(l)}
              title={`${l === 'auto' ? 'Auto' : l + ' column'} layout`}
              style={{ fontSize: 12 }}
            >
              {l === 'auto' ? '⊞' : l}
            </button>
          ))}

          <div style={{ width: 1, height: 24, background: 'var(--border)', margin: '0 4px' }} />

          {/* Refresh */}
          <button
            className={`icon-btn${refreshing ? ' active' : ''}`}
            onClick={refreshDevices}
            title="Refresh cameras"
          >
            {refreshing ? '⏳' : '🔄'}
          </button>

          {/* Unread count indicator */}
          <div style={{ position: 'relative', display: 'inline-flex' }}>
            🔔
            {unreadCount > 0 && <span className="badge">{unreadCount > 99 ? '99+' : unreadCount}</span>}
          </div>

          {/* Settings */}
          <button className="icon-btn" onClick={() => setShowSettings(true)} title="Settings">
            ⚙️
          </button>

          {/* User menu */}
          <div className="user-menu" onClick={logout} title="Click to logout">
            👤 {user?.username}
          </div>
        </div>
      </header>

      {/* ── Main body ── */}
      <div className="main-body">
        <main className="camera-section">
          <div className="section-toolbar">
            <span className="section-title">Cameras</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {/* Alert day filter */}
              <div className="notif-filter-bar" style={{ padding: 0, margin: 0 }}>
                {[['All', 0], ['Today', 1], ['3d', 3], ['7d', 7], ['14d', 14]].map(([label, val]) => (
                  <button key={val}
                    className={`notif-filter-btn${alertDayFilter === val ? ' active' : ''}`}
                    onClick={() => setAlertDayFilter(val)}>{label}</button>
                ))}
              </div>
              <button className="btn btn-sm" onClick={syncAlarms} disabled={syncing}
                title="Pull missed alerts from Imou cloud (last 7 days)">
                {syncing ? '⏳' : '⟳'} Sync
              </button>
              {unreadCount > 0 && (
                <button className="btn btn-sm" onClick={markAllRead}>Mark all read</button>
              )}
            </div>
          </div>

          {devices.length > 0 ? (
            <div className={`camera-grid${layout !== 'auto' ? ` layout-${layout}` : ''}`}>
              {devices.map(device => (
                <div key={device.deviceId} className="camera-group-card">
                  <CameraCard
                    device={device}
                    settings={settings}
                    selected={selectedCamera?.deviceId === device.deviceId}
                    onSelect={setSelectedCamera}
                    onPTZ={setPtzCamera}
                    onStream={setStreamCamera}
                    onCameraSettings={setSettingsCamera}
                  />
                  <CameraAlerts
                    deviceId={device.deviceId}
                    notifications={notifications}
                    dayFilter={alertDayFilter}
                    onMarkOne={markOneRead}
                    onImageView={setImageViewNotif}
                  />
                </div>
              ))}
            </div>
          ) : (
            <div style={{
              flex: 1, display: 'flex', flexDirection: 'column',
              alignItems: 'center', justifyContent: 'center',
              color: 'var(--text-muted)', gap: 16,
            }}>
              <div style={{ fontSize: 64, opacity: 0.3 }}>📷</div>
              <div style={{ fontSize: 18, fontWeight: 600 }}>No cameras found</div>
              <div style={{ fontSize: 13, maxWidth: 360, textAlign: 'center' }}>
                Make sure your IMOU_APP_ID and IMOU_APP_SECRET are set in the .env file,
                and that cameras are bound to your Imou developer account.
              </div>
              <button className="btn btn-primary" onClick={refreshDevices}>
                🔄 Retry
              </button>
            </div>
          )}
        </main>
      </div>

      {/* ── Modals ── */}
      {ptzCamera && <PTZControl device={ptzCamera} onClose={() => setPtzCamera(null)} />}
      {streamCamera && <StreamViewer device={streamCamera} onClose={() => setStreamCamera(null)} />}
      {settingsCamera && <CameraSettingsModal device={settingsCamera} onClose={() => setSettingsCamera(null)} />}
      {showSettings && <SettingsModal user={user} onClose={() => setShowSettings(false)} />}
      {imageViewNotif && <NotifImageModal notif={imageViewNotif} onClose={() => setImageViewNotif(null)} devices={devices} onStream={setStreamCamera} />}

      {/* ── Toast container ── */}
      <div className="toast-container">
        {toasts.map(t => (
          <Toast key={t._toastId} toast={t} onClose={() => removeToast(t._toastId)} />
        ))}
      </div>
    </div>
  );
}

// ── Bootstrap ──
ReactDOM.createRoot(document.getElementById('root')).render(<App />);
