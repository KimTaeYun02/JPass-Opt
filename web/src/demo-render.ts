// Keep the SDK-owned map and all its ancestors connected during UI updates.
export function renderDemoMarkup(app: HTMLElement, markup: string): void {
  const mapArea = app.querySelector<HTMLElement>('.map-area');
  const slot = mapArea?.querySelector<HTMLElement>('#demo-map-slot');
  if (!mapArea || !slot) { app.innerHTML = markup; return; }
  const template = app.ownerDocument.createElement('template');
  template.innerHTML = markup;
  const next = template.content;
  app.querySelector('.site-header')!.replaceWith(next.querySelector('.site-header')!);
  app.querySelector('.planner')!.replaceWith(next.querySelector('.planner')!);
  Array.from(mapArea.children).forEach((child) => { if (child !== slot) child.remove(); });
  Array.from(next.querySelector('.map-area')!.children).forEach((child) => {
    // The live slot is structural; update SDK host state through its controller.
    if (child.id !== 'demo-map-slot') mapArea.append(child);
  });
}
