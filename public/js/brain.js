import { api } from './api.js';

const STYLE = [
  {
    selector: 'node',
    style: {
      'background-color': '#2f2f36',
      'border-width': 2,
      'border-color': '#3a3a42',
      'label': 'data(label)',
      'color': '#e8e8ec',
      'font-size': 12,
      'text-valign': 'center',
      'text-halign': 'center',
      'text-wrap': 'wrap',
      'text-max-width': 110,
      'width': 'label',
      'height': 'label',
      'padding': '10px',
      'shape': 'round-rectangle',
    },
  },
  { selector: 'node.center', style: {
      'background-color': '#6aa7ff',
      'border-color': '#6aa7ff',
      'color': '#111',
      'font-weight': 'bold',
    } },
  { selector: 'node.far',    style: { 'opacity': 0.7 } },
  { selector: 'edge', style: {
      'width': 1.5,
      'line-color': '#5a5a66',
      'target-arrow-color': '#5a5a66',
      'curve-style': 'bezier',
    } },
  { selector: 'edge[kind = "child"]', style: {
      'target-arrow-shape': 'triangle',
      'line-color': '#6aa7ff',
      'target-arrow-color': '#6aa7ff',
    } },
  { selector: 'edge[kind = "jump"]', style: {
      'line-style': 'dashed',
      'line-color': '#c586ff',
      'target-arrow-shape': 'none',
    } },
];

export function createBrain(containerSelector, { onSelect }) {
  const cy = cytoscape({
    container: document.querySelector(containerSelector),
    style: STYLE,
    wheelSensitivity: 0.2,
    minZoom: 0.3,
    maxZoom: 2.5,
  });

  let centerId = null;

  cy.on('tap', 'node', evt => {
    const id = Number(evt.target.id());
    if (id !== centerId) onSelect(id);
  });

  async function render(id) {
    centerId = id;
    const { nodes, edges } = await api.neighborhood(id, 2);

    cy.elements().remove();
    cy.add(nodes.map(n => ({
      data: { id: String(n.id), label: n.name, distance: n.distance },
      classes: [
        n.id === id ? 'center' : '',
        n.distance >= 2 ? 'far' : '',
      ].filter(Boolean).join(' '),
    })));
    cy.add(edges.map(e => ({
      data: {
        id: `e${e.id}`,
        source: String(e.from),
        target: String(e.to),
        kind: e.kind,
      },
    })));

    const layout = cy.layout({
      name: 'concentric',
      concentric: n => 10 - (n.data('distance') ?? 0),
      levelWidth: () => 1,
      minNodeSpacing: 40,
      spacingFactor: 1.2,
      animate: false,
    });
    layout.run();
    cy.fit(cy.elements(), 40);
  }

  function resize() { cy.resize(); cy.fit(cy.elements(), 40); }

  return { render, resize, get centerId() { return centerId; } };
}
