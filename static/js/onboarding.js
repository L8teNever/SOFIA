async function checkAndRunOnboarding() {
  if (currentUser.display_name) return false;
  await runOnboarding();
  return true;
}

function runOnboarding() {
  return new Promise(resolve => {
    let step = 1;
    let chosenName = '';
    let chosenClass = '';

    const overlay = document.createElement('div');
    overlay.id = 'onboarding-overlay';
    overlay.style.cssText = `
      position:fixed;inset:0;z-index:9999;
      background:#141218;
      display:flex;align-items:center;justify-content:center;
      padding:24px;
    `;

    function render() {
      overlay.innerHTML = '';

      const blob1 = document.createElement('div');
      blob1.className = 'm3-blob';
      blob1.style.cssText = 'width:350px;height:350px;background:#d0bcff;top:-80px;left:-80px;opacity:0.4;';
      const blob2 = document.createElement('div');
      blob2.className = 'm3-blob';
      blob2.style.cssText = 'width:280px;height:280px;background:#ffd8e4;bottom:-60px;right:-60px;opacity:0.3;animation-delay:3s;';
      overlay.appendChild(blob1);
      overlay.appendChild(blob2);

      const card = document.createElement('div');
      card.style.cssText = `
        background:#1c1b1f;border-radius:28px;padding:32px 28px;
        width:100%;max-width:380px;position:relative;z-index:1;
        box-shadow:0 8px 40px rgba(0,0,0,0.5);
      `;

      const dots = document.createElement('div');
      dots.style.cssText = 'display:flex;gap:6px;margin-bottom:28px;';
      for (let i = 1; i <= 3; i++) {
        const d = document.createElement('div');
        d.style.cssText = `height:4px;border-radius:2px;transition:all 0.3s;background:${i <= step ? '#d0bcff' : '#49454f'};flex:${i === step ? 2 : 1};`;
        dots.appendChild(d);
      }
      card.appendChild(dots);

      if (step === 1) {
        card.innerHTML += `
          <div style="width:64px;height:64px;background:#eaddff;border-radius:20px;display:flex;align-items:center;justify-content:center;margin-bottom:20px;">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#21005d" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
          </div>
          <div style="font-size:1.5rem;font-weight:700;color:#e6e1e5;margin-bottom:8px;">Willkommen bei Sofia!</div>
          <div style="color:#cac4d0;font-size:0.95rem;line-height:1.6;margin-bottom:24px;">
            Du bist der erste Nutzer und wirst automatisch als <strong style="color:#d0bcff;">Administrator</strong> eingerichtet.<br><br>
            Eingeloggt als:<br>
            <span style="background:#2b2930;padding:6px 12px;border-radius:10px;font-size:0.85rem;color:#e6e1e5;display:inline-block;margin-top:6px;">${currentUser.email}</span>
          </div>
          <button id="ob-next" style="width:100%;padding:16px;background:#d0bcff;color:#21005d;border:none;border-radius:16px;font-size:1rem;font-weight:700;cursor:pointer;">
            Das bin ich →
          </button>
        `;
        card.querySelector('#ob-next').onclick = () => { step = 2; render(); };

      } else if (step === 2) {
        card.innerHTML += `
          <div style="width:64px;height:64px;background:#ffd8e4;border-radius:20px;display:flex;align-items:center;justify-content:center;margin-bottom:20px;">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#31111d" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          </div>
          <div style="font-size:1.5rem;font-weight:700;color:#e6e1e5;margin-bottom:8px;">Wie heißt du?</div>
          <div style="color:#cac4d0;font-size:0.95rem;margin-bottom:20px;">Dein Name wird anderen Nutzern angezeigt.</div>
          <input id="ob-name" type="text" placeholder="Dein Name" maxlength="60"
            style="width:100%;padding:14px 16px;background:#2b2930;border:2px solid #49454f;border-radius:14px;
                   color:#e6e1e5;font-size:1rem;outline:none;box-sizing:border-box;margin-bottom:16px;"
            value="${chosenName}" />
          <button id="ob-next" style="width:100%;padding:16px;background:#d0bcff;color:#21005d;border:none;border-radius:16px;font-size:1rem;font-weight:700;cursor:pointer;opacity:0.5;">
            Weiter →
          </button>
        `;
        const inp = card.querySelector('#ob-name');
        const btn = card.querySelector('#ob-next');
        inp.focus();
        inp.addEventListener('input', () => {
          const v = inp.value.trim();
          btn.style.opacity = v ? '1' : '0.5';
          btn.disabled = !v;
        });
        if (chosenName) { btn.style.opacity = '1'; btn.disabled = false; }
        btn.onclick = () => {
          const v = inp.value.trim();
          if (!v) return;
          chosenName = v;
          step = 3;
          render();
        };
        inp.addEventListener('keydown', e => { if (e.key === 'Enter') btn.click(); });

      } else if (step === 3) {
        card.innerHTML += `
          <div style="width:64px;height:64px;background:#d3e3fd;border-radius:20px;display:flex;align-items:center;justify-content:center;margin-bottom:20px;">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#041e49" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
          </div>
          <div style="font-size:1.5rem;font-weight:700;color:#e6e1e5;margin-bottom:8px;">Deine Klasse</div>
          <div style="color:#cac4d0;font-size:0.95rem;margin-bottom:20px;">Wie heißt deine Klasse? (z.B. <em>5A</em>, <em>10B</em>)</div>
          <input id="ob-class" type="text" placeholder="Klassenname" maxlength="40"
            style="width:100%;padding:14px 16px;background:#2b2930;border:2px solid #49454f;border-radius:14px;
                   color:#e6e1e5;font-size:1rem;outline:none;box-sizing:border-box;margin-bottom:8px;"
            value="${chosenClass}" />
          <div id="ob-err" style="color:#f2b8b5;font-size:0.82rem;min-height:20px;margin-bottom:12px;"></div>
          <button id="ob-next" style="width:100%;padding:16px;background:#d0bcff;color:#21005d;border:none;border-radius:16px;font-size:1rem;font-weight:700;cursor:pointer;opacity:0.5;">
            Klasse erstellen & loslegen
          </button>
        `;
        const inp = card.querySelector('#ob-class');
        const btn = card.querySelector('#ob-next');
        const err = card.querySelector('#ob-err');
        inp.focus();
        inp.addEventListener('input', () => {
          const v = inp.value.trim();
          btn.style.opacity = v ? '1' : '0.5';
          btn.disabled = !v;
        });
        if (chosenClass) { btn.style.opacity = '1'; btn.disabled = false; }
        btn.onclick = async () => {
          const v = inp.value.trim();
          if (!v) return;
          chosenClass = v;
          btn.disabled = true;
          btn.textContent = 'Wird gespeichert…';
          err.textContent = '';
          try {
            await API.updateMe({ display_name: chosenName });
            const cls = await API.createClass({ name: chosenClass });
            await API.updateUser(currentUser.id, { class_id: cls.id });
            currentUser.display_name = chosenName;
            currentUser.class_id = cls.id;
            overlay.style.opacity = '0';
            overlay.style.transition = 'opacity 0.4s';
            setTimeout(() => { overlay.remove(); resolve(); }, 400);
          } catch (e) {
            err.textContent = e.detail || 'Fehler beim Speichern. Bitte nochmal versuchen.';
            btn.disabled = false;
            btn.textContent = 'Klasse erstellen & loslegen';
          }
        };
        inp.addEventListener('keydown', e => { if (e.key === 'Enter') btn.click(); });
      }

      card.appendChild(dots);
      card.insertBefore(dots, card.firstChild);
      overlay.appendChild(card);
    }

    render();
    document.body.appendChild(overlay);
  });
}
