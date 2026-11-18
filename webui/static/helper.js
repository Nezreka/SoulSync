// ===============================
// THE ? BUTTON
// ===============================
//
// The ? opens the help sheet (webui/src/features/help, window.SoulSyncHelp).
// Everything it used to open (Element Info, the mode menu, helper search,
// Setup Progress, Troubleshoot, the old tours) is gone: search, Health,
// What's new, shortcuts and tours all live in the sheet now. What is left
// here is what the vanilla button needs before and around the React bundle:
//
//   toggleHelperMode()  the button's onclick (index.html #helper-float-btn)
//   _helperPageId()     which page is showing, for anything still asking
//   the idle glow until the button is first used, and a one-time pointer
//   to it on an install that hasn't finished setup
//
// ?, F1, Ctrl/Cmd K and Esc belong to the sheet (features/help/host.tsx).

let _helpOpenPending = false;

function _helperMarkDiscovered() {
    const floatBtn = document.getElementById('helper-float-btn');
    if (floatBtn) floatBtn.classList.remove('undiscovered', 'first-launch-pulse');
    document.querySelectorAll('.helper-first-launch-tip').forEach((tip) => tip.remove());
    try { localStorage.setItem('soulsync_helper_discovered', '1'); } catch (e) { /* storage blocked */ }
}

function toggleHelperMode() {
    _helperMarkDiscovered();
    const help = window.SoulSyncHelp;
    if (help) { help.toggle(); return; }
    // the React bundle hasn't run yet: open once it says it is ready
    _helpOpenPending = !_helpOpenPending;
}

window.addEventListener('ss:help-ready', () => {
    if (_helpOpenPending && window.SoulSyncHelp) window.SoulSyncHelp.open();
    _helpOpenPending = false;
});

// the page the user is on: a vanilla page's id, or the shell's current page
function _helperPageId() {
    return document.querySelector('.page.active:not(#webui-react-root)')?.id?.replace('-page', '')
        || (typeof window.currentPage === 'string' ? window.currentPage : '');
}

// ── first launch ─────────────────────────────────────────────────────────
// an install that hasn't finished setup gets one quiet pointer at the ?,
// which opens the sheet (Home, and for an admin Health's unfinished setup
// items). the server knows whether setup is done, not this browser (#1909)

function _helperShowFirstLaunchTip() {
    const floatBtn = document.getElementById('helper-float-btn');
    if (!floatBtn || document.querySelector('.helper-first-launch-tip')) return;
    floatBtn.classList.add('first-launch-pulse');
    const tip = document.createElement('button');
    tip.type = 'button';
    tip.className = 'helper-first-launch-tip';
    tip.textContent = 'New here? Help lives behind the ?';
    tip.addEventListener('click', () => {
        try { localStorage.setItem('soulsync_setup_welcome_dismissed', '1'); } catch (e) { /* storage blocked */ }
        _helperMarkDiscovered();
        if (window.SoulSyncHelp) window.SoulSyncHelp.open();
    });
    document.body.appendChild(tip);
    setTimeout(() => {
        if (!tip.parentElement) return;
        tip.classList.add('fading');
        setTimeout(() => tip.remove(), 500);
        floatBtn.classList.remove('first-launch-pulse');
    }, 12000);
}

document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        let discovered = false;
        let dismissed = false;
        try {
            discovered = !!localStorage.getItem('soulsync_helper_discovered');
            dismissed = !!(localStorage.getItem('soulsync_setup') || localStorage.getItem('soulsync_setup_welcome_dismissed'));
        } catch (e) { /* storage blocked: no glow, no tip */ return; }

        // idle glow until the button is first used
        if (!discovered) document.getElementById('helper-float-btn')?.classList.add('undiscovered');

        if (!dismissed) {
            fetch('/api/setup/status')
                .then((r) => (r.ok ? r.json() : null))
                .then((d) => { if (d && d.setup_complete === false) _helperShowFirstLaunchTip(); })
                .catch(() => { /* no answer, no nag */ });
        }
    }, 2500);
});
