// Which dialog is open. At most one at a time, so there is a single value rather than a flag for
// each: `null` for none, or { name, payload } (the payload is for dialogs that show something,
// like the document viewer showing a source).

export const MODAL_NAMES = ['newChat', 'apiKey', 'library', 'agent', 'merge', 'search', 'context', 'source'];

export const openModal = (name, payload = null) => {
  if (!MODAL_NAMES.includes(name)) throw new Error(`unknown dialog: ${name}`);
  return { name, payload };
};

// Closes the open dialog, or - when names are given - only if it is one of those. Closing a dialog
// that isn't open must not close another one that is.
export const closeModal = (current, names = []) => (
  current && (names.length === 0 || names.includes(current.name)) ? null : current
);

// What the search shortcut (ctrl/cmd + K) does. It opens search from nowhere, but never replaces a
// dialog that is already open: that could throw away something half-filled in, like a merge.
export const afterSearchShortcut = (current) => current ?? openModal('search');
