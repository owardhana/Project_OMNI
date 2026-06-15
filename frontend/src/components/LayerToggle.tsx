import { LAYERS, type LayerKey } from '../styles/layers';

interface Props {
  visibleLayers: Record<string, boolean>;
  onToggle: (layer: LayerKey) => void;
}

export default function LayerToggle({ visibleLayers, onToggle }: Props) {
  return (
    <div className="layer-toggle">
      {(Object.keys(LAYERS) as LayerKey[]).map((key) => (
        <label key={key} className="layer-toggle-item">
          <input
            type="checkbox"
            checked={visibleLayers[key] ?? true}
            onChange={() => onToggle(key)}
          />
          <span
            className="layer-swatch"
            style={{ backgroundColor: LAYERS[key].color }}
          />
          {LAYERS[key].label}
        </label>
      ))}
    </div>
  );
}
