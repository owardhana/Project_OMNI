interface Props {
  active: string;
  onChange: (tissue: string) => void;
}

// Labels map to backend tissue params (resolved server-side:
// blood -> whole_blood, brain -> brain_prefrontal_cortex).
const TISSUES: { label: string; value: string }[] = [
  { label: 'All', value: 'all' },
  { label: 'Blood', value: 'blood' },
  { label: 'Liver', value: 'liver' },
  { label: 'Brain', value: 'brain' },
];

export default function TissueFilter({ active, onChange }: Props) {
  return (
    <div className="tissue-filter">
      {TISSUES.map((t) => (
        <button
          key={t.value}
          className={`tissue-btn${active === t.value ? ' active' : ''}`}
          onClick={() => onChange(t.value)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
