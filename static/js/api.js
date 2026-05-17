const API = {
  _base: '/api/v1',

  async _req(method, path, body, isForm = false) {
    const opts = { method, headers: {} };
    if (body && !isForm) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    if (body && isForm) opts.body = body;
    const res = await fetch(this._base + path, opts);
    if (!res.ok) {
      let detail = '';
      try { detail = (await res.json()).detail; } catch {}
      const err = new Error(detail || `${res.status}`);
      err.status = res.status;
      err.detail = detail;
      throw err;
    }
    const ct = res.headers.get('content-type') || '';
    return ct.includes('application/json') ? res.json() : res.text();
  },

  get:    (path)        => API._req('GET',    path),
  post:   (path, body)  => API._req('POST',   path, body),
  patch:  (path, body)  => API._req('PATCH',  path, body),
  delete: (path)        => API._req('DELETE', path),
  upload: (path, form)  => API._req('POST',   path, form, true),

  me:            ()       => API.get('/auth/me'),
  users:         ()       => API.get('/users/'),
  classUsers:    ()       => API.get('/users/class'),
  createUser:    (d)      => API.post('/users/', d),
  updateMe:      (d)      => API.patch('/users/me', d),
  updateUser:    (id, d)  => API.patch(`/users/${id}`, d),
  classes:       ()       => API.get('/classes/'),
  createClass:   (d)      => API.post('/classes/', d),
  deleteClass:   (id)     => API.delete(`/classes/${id}`),
  saveUntis:     (id, d)  => API.post(`/classes/${id}/untis`, d),
  getUntis:      (id)     => API.get(`/classes/${id}/untis`),
  testUntis:      (id, d)  => API.post(`/classes/${id}/untis/test`, d),
  importSubjects: (id)     => API.post(`/classes/${id}/untis/import-subjects`, {}),
  subjects:      ()       => API.get('/subjects/'),
  createSubject: (d)      => API.post('/subjects/', d),
  deleteSubject: (id)     => API.delete(`/subjects/${id}`),
  events:        (month)  => API.get('/calendar/' + (month ? `?month=${month}` : '')),
  createEvent:   (d)      => API.post('/calendar/', d),
  deleteEvent:   (id)     => API.delete(`/calendar/${id}`),
  homework:      ()       => API.get('/homework/'),
  createHomework:(d)      => API.post('/homework/', d),
  checkHomework: (id)     => API.post(`/homework/${id}/check`),
  deleteHomework:(id)     => API.delete(`/homework/${id}`),
  grades:        ()       => API.get('/grades/'),
  createGrade:   (d)      => API.post('/grades/', d),
  deleteGrade:   (id)     => API.delete(`/grades/${id}`),
  timetable:     ()       => API.get('/timetable/'),
  rooms:         ()       => API.get('/chat/rooms'),
  createRoom:    (d)      => API.post('/chat/rooms', d),
  messages:      (id)     => API.get(`/chat/rooms/${id}/messages`),
  files:         ()       => API.get('/files/'),
  uploadFile:    (form)   => API.upload('/files/', form),
  deleteFile:    (id)     => API.delete(`/files/${id}`),
  vapidKey:      ()       => API.get('/push/vapid-public-key'),
  subscribe:     (sub)    => API.post('/push/subscribe', { subscription: sub }),
  unsubscribe:   ()       => API.post('/push/unsubscribe'),
  sendPush:      (d)      => API.post('/push/send', d),
  adminStats:    ()       => API.get('/admin/stats'),
};
