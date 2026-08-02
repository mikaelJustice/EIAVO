/*
  A small inline SVG icon set — deliberately plain/geometric line icons
  (no external icon font or library) so the control bar reads as a real
  product UI rather than emoji glyphs. Each entry is a full <svg> string;
  colour comes from `currentColor`, so it inherits the button's text color.
*/
window.ICONS = {
  mic: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="12" rx="3"></rect><path d="M5 10a7 7 0 0 0 14 0"></path><line x1="12" y1="19" x2="12" y2="22"></line><line x1="8" y1="22" x2="16" y2="22"></line></svg>`,

  micOff: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5a3 3 0 0 1 6 0v5.5"></path><path d="M15 12.5V13a3 3 0 0 1-4.6 2.5"></path><path d="M5 10a7 7 0 0 0 9.5 6.6"></path><path d="M19 10a7 7 0 0 1-1 3.6"></path><line x1="12" y1="19" x2="12" y2="22"></line><line x1="8" y1="22" x2="16" y2="22"></line><line x1="3" y1="3" x2="21" y2="21"></line></svg>`,

  video: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="6" width="14" height="12" rx="2"></rect><path d="M16 10.5 22 7v10l-6-3.5"></path></svg>`,

  videoOff: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M16 16.5 22 20V7l-4.6 2.7"></path><path d="M2 8v10a2 2 0 0 0 2 2h9.5"></path><path d="M13.5 6H4a2 2 0 0 0-2 2v.3"></path><line x1="2" y1="2" x2="22" y2="22"></line></svg>`,

  screenShare: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="13" rx="2"></rect><path d="M8 21h8"></path><path d="M12 17v4"></path><path d="M12 13V8"></path><path d="m9.2 10.5 2.8-2.8 2.8 2.8"></path></svg>`,

  background: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"></rect><circle cx="9" cy="9" r="2"></circle><path d="m21 15-5-5-9 9"></path></svg>`,

  hand: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 12.5V6.5a1.5 1.5 0 0 1 3 0v4.5"></path><path d="M11 11V4.5a1.5 1.5 0 0 1 3 0V11"></path><path d="M14 10.8V6.8a1.5 1.5 0 0 1 3 0V13"></path><path d="M17 10a1.5 1.5 0 0 1 3 0v5.5A6.5 6.5 0 0 1 13.5 22h-1a6.5 6.5 0 0 1-5.6-3.2L4.6 15A1.4 1.4 0 1 1 7 13.5L8 15"></path></svg>`,

  grid: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7" rx="1.2"></rect><rect x="14" y="3" width="7" height="7" rx="1.2"></rect><rect x="3" y="14" width="7" height="7" rx="1.2"></rect><rect x="14" y="14" width="7" height="7" rx="1.2"></rect></svg>`,

  speakerView: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="12" height="18" rx="2"></rect><rect x="17" y="3" width="4" height="8" rx="1"></rect><rect x="17" y="13" width="4" height="8" rx="1"></rect></svg>`,

  smile: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><path d="M8.2 14.2c1 1.2 2.3 1.8 3.8 1.8s2.8-.6 3.8-1.8"></path><line x1="9" y1="9.3" x2="9.01" y2="9.3"></line><line x1="15" y1="9.3" x2="15.01" y2="9.3"></line></svg>`,

  chat: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 0 1-8.9 8.4 8.6 8.6 0 0 1-4-1L3 20l1.2-4.5A8.4 8.4 0 1 1 21 11.5Z"></path></svg>`,

  users: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.9"></path><path d="M16 3.1a4 4 0 0 1 0 7.8"></path></svg>`,

  notes: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><path d="M14 2v6h6"></path><line x1="8" y1="13" x2="16" y2="13"></line><line x1="8" y1="17" x2="13" y2="17"></line></svg>`,

  record: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"></circle><circle cx="12" cy="12" r="3.6" fill="currentColor" stroke="none"></circle></svg>`,

  stopRecord: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"></circle><rect x="9" y="9" width="6" height="6" rx="1" fill="currentColor" stroke="none"></rect></svg>`,

  close: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>`,

  send: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m22 2-11 11"></path><path d="M22 2 15 22l-4-9-9-4Z"></path></svg>`,
};

// Applies window.ICONS[name] to every element carrying data-icon="name".
window.applyIcons = function (root) {
  (root || document).querySelectorAll("[data-icon]").forEach((el) => {
    const name = el.dataset.icon;
    if (window.ICONS[name]) el.innerHTML = window.ICONS[name];
  });
};

document.addEventListener("DOMContentLoaded", () => window.applyIcons());
