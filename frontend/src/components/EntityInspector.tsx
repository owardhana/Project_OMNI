import { useMemo, useState } from 'react';

import type { FGLink, FGNode, ForceGraphData } from '../types/graph';

interface Props {
  node: FGNode | null;
  graphData: ForceGraphData;
  onClose: () => void;
  onExpand: (node: FGNode) => void;
}

function endId(end: string | FGNode): string {
  return typeof end === 'object' ? end.id : end;
}

function endpointLabel(end: string | FGNode): string {
  if (typeof end === 'object') {
    const sym = 'hgnc_symbol' in end ? end.hgnc_symbol : undefined;
    return sym ?? end.id;
  }
  return end;
}

// Edges are grouped into inspector tabs by rel_type. Backbone links
// (PRODUCES/TRANSLATES_TO/ENCODES/IN_GENE) live in Overview; the rest fan out.
const REL_TO_TAB: Record<string, string> = {
  INTERACTS_WITH: 'Interactions',
  REGULATES: 'Regulation',
  CATALYSES: 'Metabolism',
  ASSOCIATED_WITH: 'Disease',
  IMPLICATED_IN: 'Disease',
  DIFFERENTIALLY_EXPRESSED: 'Disease',
  GENE_DISEASE_ASSOC: 'Disease', // Pillar 1c (ADR-0016) — curated, once loaded
};

// The "other" endpoint of a link relative to the inspected node.
function otherEnd(link: FGLink, nodeId: string): string | FGNode {
  return endId(link.source) === nodeId ? link.target : link.source;
}

function RelRow({ link, nodeId }: { link: FGLink; nodeId: string }) {
  const other = otherEnd(link, nodeId);
  return (
    <div className="rel-row">
      <span className="rel-endpoint">{endpointLabel(other)}</span>
      <span className="rel-meta">
        {link.mode && <span className="rel-tag">{link.mode}</span>}
        {link.confidence != null && (
          <span className="rel-tag">{link.confidence.toFixed(2)}</span>
        )}
        {link.provenance_tier === 'literature' && (
          <span className="rel-tag lit" title="Machine-proposed from literature (ADR-0013)">
            proposed
          </span>
        )}
      </span>
    </div>
  );
}

// Compact per-type identity block shown in the Overview tab.
function Overview({ node }: { node: FGNode }) {
  switch (node.node_type) {
    case 'gene':
      return (
        <>
          <dl className="node-fields">
            <dt>Chromosome</dt><dd>{node.chromosome ?? '—'}</dd>
            <dt>Biotype</dt><dd>{node.biotype ?? '—'}</dd>
            <dt>LoF intolerance</dt>
            <dd>
              {node.pli_score != null ? node.pli_score.toFixed(3) : '—'}
              {node.pli_score != null && node.pli_score > 0.9 && (
                <span className="flag-chip">high</span>
              )}
            </dd>
            {node.cancer_gene && (<><dt>Cancer gene</dt><dd><span className="flag-chip">yes</span></dd></>)}
          </dl>
          {node.description && <p className="node-desc">{node.description}</p>}
        </>
      );
    case 'protein':
      return (
        <>
          {node.summary_text && <p className="node-desc">{node.summary_text}</p>}
          <dl className="node-fields">
            <dt>Subtype</dt><dd>{node.subtype ?? '—'}</dd>
            <dt>Mol. weight</dt>
            <dd>{node.molecular_weight != null ? `${node.molecular_weight} Da` : '—'}</dd>
          </dl>
        </>
      );
    case 'transcript':
      return (
        <dl className="node-fields">
          <dt>Biotype</dt><dd>{node.biotype ?? '—'}</dd>
          <dt>Length</dt><dd>{node.length_bp != null ? `${node.length_bp} bp` : '—'}</dd>
        </dl>
      );
    case 'variant':
      return (
        <dl className="node-fields">
          <dt>Position</dt>
          <dd>{node.chromosome ? `chr${node.chromosome}:${node.position_grch38 ?? '?'}` : '—'}</dd>
          <dt>Clinical significance</dt><dd>{node.clinical_significance ?? '—'}</dd>
          <dt>Consequence</dt><dd>{node.consequence_type ?? '—'}</dd>
          <dt>gnomAD AF</dt><dd>{node.gnomad_af != null ? node.gnomad_af.toExponential(2) : '—'}</dd>
        </dl>
      );
    case 'disease':
      return (
        <>
          {node.description && node.description !== node.name && (
            <p className="node-desc">{node.description}</p>
          )}
          <dl className="node-fields">
            <dt>Category</dt><dd>{node.category ?? '—'}</dd>
          </dl>
        </>
      );
    case 'metabolite':
      return (
        <dl className="node-fields">
          <dt>HMDB</dt><dd>{node.hmdb_id ?? '—'}</dd>
          <dt>ChEBI</dt><dd>{node.chebi_id ?? '—'}</dd>
          <dt>Formula</dt><dd>{node.formula ?? '—'}</dd>
          <dt>Charge</dt><dd>{node.charge != null ? node.charge : '—'}</dd>
        </dl>
      );
    default:
      return null;
  }
}

// Annotations tab: subcellular location + GO/pathway membership (Pillar 1a/1b,
// ADR-0015). Today single-value from UniProt; multi-value + scores once loaded.
function Annotations({ node }: { node: FGNode }) {
  if (node.node_type !== 'protein') {
    return <p className="muted">No annotations for this entity type.</p>;
  }
  return (
    <>
      <dl className="node-fields">
        <dt>Subcellular</dt><dd>{node.subcellular_loc ?? '—'}</dd>
      </dl>
      {node.go_terms && node.go_terms.length > 0 ? (
        <div className="chip-row">
          {node.go_terms.map((go) => (
            <span key={go} className="go-chip">{go}</span>
          ))}
        </div>
      ) : (
        <p className="muted">No GO / pathway annotations.</p>
      )}
    </>
  );
}

function title(node: FGNode): { name: string; sub: string; tf: boolean } {
  switch (node.node_type) {
    case 'gene':
      return { name: node.hgnc_symbol ?? node.ensembl_id, sub: `${node.ensembl_id} · Gene`, tf: !!node.is_tf };
    case 'protein':
      return {
        name: `${node.hgnc_symbol ?? node.uniprot_id} (protein)`,
        sub: `${node.uniprot_id} · UniProt`,
        tf: node.subtype === 'transcription_factor',
      };
    case 'transcript':
      return { name: node.hgnc_symbol ?? node.ensembl_tx_id, sub: `${node.ensembl_tx_id} · Transcript`, tf: false };
    case 'variant':
      return { name: node.rsid ?? node.id, sub: 'Variant · genomics', tf: false };
    case 'disease':
      return { name: node.name ?? node.ontology_id, sub: `${node.ontology_id} · Disease`, tf: false };
    case 'metabolite':
      return { name: node.name ?? node.hmdb_id ?? node.id, sub: `${node.hmdb_id ?? node.chebi_id ?? node.id} · Metabolite`, tf: false };
    default:
      return { name: node.id, sub: '', tf: false };
  }
}

export default function EntityInspector({ node, graphData, onClose, onExpand }: Props) {
  const [active, setActive] = useState('Overview');

  const incident = useMemo(
    () =>
      node
        ? graphData.links.filter(
            (l) => endId(l.source) === node.id || endId(l.target) === node.id,
          )
        : [],
    [node, graphData.links],
  );

  // Group incident edges into their tabs; collect literature-cited edges.
  const { grouped, literature } = useMemo(() => {
    const g: Record<string, FGLink[]> = {};
    const lit: FGLink[] = [];
    for (const l of incident) {
      const tab = REL_TO_TAB[l.rel_type];
      if (tab) (g[tab] ??= []).push(l);
      if (l.pmids.length > 0 || l.provenance_tier === 'literature') lit.push(l);
    }
    return { grouped: g, literature: lit };
  }, [incident]);

  if (!node) return null;
  const t = title(node);
  const isProtein = node.node_type === 'protein';

  // Ordered, dynamic tab list — a relationship tab appears only when populated.
  const tabs: string[] = ['Overview'];
  for (const name of ['Interactions', 'Regulation', 'Metabolism', 'Disease']) {
    if (grouped[name]?.length) tabs.push(name);
  }
  if (isProtein) tabs.push('Annotations');
  if (literature.length) tabs.push('Literature');
  const activeTab = tabs.includes(active) ? active : 'Overview';

  return (
    <aside className="node-panel inspector">
      <button className="node-panel-close" onClick={onClose} aria-label="Close">×</button>
      <h2 className="node-title">
        {t.name}
        {t.tf && <span className="tf-badge">{node.node_type === 'gene' ? 'TF gene' : 'TF'}</span>}
      </h2>
      <div className="node-subtitle">{t.sub}</div>

      <div className="inspector-tabs" role="tablist">
        {tabs.map((tab) => (
          <button
            key={tab}
            role="tab"
            aria-selected={tab === activeTab}
            className={`inspector-tab ${tab === activeTab ? 'active' : ''}`}
            onClick={() => setActive(tab)}
          >
            {tab}
            {grouped[tab]?.length ? <span className="tab-count">{grouped[tab].length}</span> : null}
          </button>
        ))}
      </div>

      <div className="inspector-body">
        {activeTab === 'Overview' && <Overview node={node} />}
        {activeTab === 'Annotations' && <Annotations node={node} />}
        {['Interactions', 'Regulation', 'Metabolism', 'Disease'].includes(activeTab) &&
          (grouped[activeTab] ?? []).map((l, i) => (
            <RelRow key={`${l.rel_type}-${i}`} link={l} nodeId={node.id} />
          ))}
        {activeTab === 'Literature' &&
          literature.map((l, i) => (
            <div key={i} className="lit-row">
              <span className="rel-endpoint">
                {endpointLabel(otherEnd(l, node.id))} · {l.rel_type}
              </span>
              <span className="pmid-inline">
                {l.pmids.length ? l.pmids.map((p) => (
                  <a key={p} href={`https://pubmed.ncbi.nlm.nih.gov/${p}`} target="_blank" rel="noreferrer">
                    {p}
                  </a>
                )) : <span className="muted">proposed</span>}
              </span>
            </div>
          ))}
      </div>

      <button className="expand-btn" onClick={() => onExpand(node)}>
        Expand Neighborhood
      </button>
    </aside>
  );
}
