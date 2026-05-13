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
    await API.subscribe(sub.toJSON());
    return true;
  },

  async unsubscribe() {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) await sub.unsubscribe();
    await API.unsubscribe();
  },

  _urlBase64ToUint8Array(base64) {
    const pad = '='.repeat((4 - (base64.length % 4)) % 4);
    const b64 = (base64 + pad).replace(/-/g, '+').replace(/_/g, '/');
    const raw = atob(b64);
    return new Uint8Array([...raw].map(c => c.charCodeAt(0)));
  },
};
