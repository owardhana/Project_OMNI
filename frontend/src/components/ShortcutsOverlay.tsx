// Keyboard-shortcut help overlay, toggled by '?' (8c). The owning component
// (App) handles the key and the localStorage first-visit hint.

const SHORTCUTS: { keys: string; desc: string }[] = [
  { keys: 'C', desc: 'Toggle camera mode (orbit ⇄ fly)' },
  { keys: 'F', desc: 'Fly mode movement (WASD · R/F up·down)' },
  { keys: '/', desc: 'Focus search' },
  { keys: 'Esc', desc: 'Close panel / overlay · return to orbit' },
  { keys: '← →', desc: 'Previous / next node in detail history' },
  { keys: '?', desc: 'Show / hide this help' },
];

export default function ShortcutsOverlay({ onClose }: { onClose: () => void }) {
  return (
    <div className="shortcuts-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <div className="shortcuts-card" onClick={(e) => e.stopPropagation()}>
        <div className="shortcuts-head">
          <strong>Keyboard shortcuts</strong>
          <button className="node-panel-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        <ul className="shortcuts-list">
          {SHORTCUTS.map((s) => (
            <li key={s.keys}>
              <kbd className="kbd">{s.keys}</kbd>
              <span>{s.desc}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
