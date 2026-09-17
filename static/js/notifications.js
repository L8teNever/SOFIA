const Push = {
  async init() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
    try {
      const { public_key } = await API.vapidKey();
      if (!public_key) return;
      Push._vapidKey = public_key;
    } catch {}
  },

  async subscribe() {
    if (!Push._vapidKey) return false;
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') return false;
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: Push._urlBase64ToUint8Array(Push._vapidKey),
    });
    await API.subscribe(sub.toJSON(), navigator.userAgent);
    return true;
  },

  async unsubscribe() {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    const endpoint = sub ? sub.endpoint : null;
    if (sub) await sub.unsubscribe();
    await API.unsubscribe(endpoint);
  },

  async autoSync() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
    if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
    try {
      if (!Push._vapidKey) await Push.init();
      if (!Push._vapidKey) return;
      const reg = await navigator.serviceWorker.ready;
      let sub = await reg.pushManager.getSubscription().catch(() => null);
      if (!sub) {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: Push._urlBase64ToUint8Array(Push._vapidKey),
        }).catch(() => null);
      }
      if (sub) {
        await API.subscribe(sub.toJSON(), navigator.userAgent).catch(() => {});
      }
    } catch (err) {
      console.warn('Push auto-sync error:', err);
    }
  },

  _urlBase64ToUint8Array(base64) {
    const pad = '='.repeat((4 - (base64.length % 4)) % 4);
    const b64 = (base64 + pad).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(b64);
    return new Uint8Array([...raw].map(c => c.charCodeAt(0)));
  },
};
