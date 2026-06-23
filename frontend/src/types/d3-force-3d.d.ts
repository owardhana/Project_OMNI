// d3-force-3d ships no TypeScript types and has no @types package; declare it as
// an untyped module so `tsc -b` (production build) resolves the import. Only
// forceCollide is used (GraphViewer3D.tsx) and it is called through the typed
// react-force-graph d3Force() accessor, so a precise signature adds little.
declare module 'd3-force-3d';
