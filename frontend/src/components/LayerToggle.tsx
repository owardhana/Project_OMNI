import { LAYERS, LAYER_NODE_COLORS, type LayerKey } from '../styles/layers';

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
          {/* One swatch per node-type colour the layer contains, so the toggle
              matches the graph + legend exactly (genomics + proteomics show two). */}
          <span className="layer-swatch-group">
            {LAYER_NODE_COLORS[key].map((color) => (
              <span key={color} className="layer-swatch" style={{ backgroundColor: color }} />
            ))}
          </span>
          {LAYERS[key].label}
        </label>
      ))}
    </div>
  );
}
