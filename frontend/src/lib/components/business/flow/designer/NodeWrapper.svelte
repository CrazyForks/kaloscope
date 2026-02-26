<script lang="ts">
  import { _ } from '$lib/i18n';
  import { icons } from '$lib/icons';
  import type { NodeSchema } from '$lib/types';
  import { useEdges, useNodes, useStore, type NodeProps } from '@xyflow/svelte';
  import NodeHandle from './NodeHandle.svelte';
  import * as _fields from './fields';

  // field components
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fields: Record<string, any> = _fields;

  // get node props
  let { id, data }: NodeProps = $props();
  const schema: NodeSchema = $derived(data.$schema as NodeSchema);

  // get all nodes and edges
  const nodes = useNodes();
  const edges = useEdges();

  // check if the node is selected
  const selected = $derived(nodes.current.find((node) => node.id === id)?.selected ?? false);

  // check if the node is interactive
  const store = useStore();
  let interactive = $derived(store.nodesDraggable || store.nodesConnectable || store.elementsSelectable);

  // dynamic class names
  const shadowClass = $derived(selected ? 'shadow-xl' : 'shadow-lg hover:shadow-xl');
  const borderClass = $derived(
    `border-2 ${selected ? 'border-base-content/30' : 'border-border hover:border-base-content/30'}`
  );
  const titleClass = $derived(
    `backdrop-blur-lg transition-colors ${selected ? 'bg-base-300' : 'bg-base-300/50 group-hover:bg-base-300'}`
  );
  const bodyClass = $derived(
    `backdrop-blur-sm transition-colors ${selected ? 'bg-base-100' : 'bg-base-100/70 group-hover:bg-base-100'}`
  );

  /**
   * Delete the node and all connected edges.
   *
   * @param event - The click event.
   */
  function deleteNode(event: MouseEvent) {
    event.stopPropagation();
    nodes.current = nodes.current.filter((node) => node.id !== id);
    edges.current = edges.current.filter((edge) => edge.source !== id && edge.target !== id);
  }
</script>

<div class="group relative w-max min-w-80 rounded-box transition-all {shadowClass} {borderClass}">
  <div class="absolute z-1 size-full bg-transparent {interactive ? 'hidden' : ''}"></div>
  <div class="flex items-center gap-2 rounded-t-box p-2 text-lg font-medium {titleClass}">
    <iconify-icon icon={schema.icon in icons ? icons[schema.icon] : icons.box3d} width="1.5rem"></iconify-icon>
    {$_(`flow.node.name.${schema.node_type}`, { default: schema.name })}
    <button class="btn ml-auto btn-square btn-subtle btn-sm" aria-label="Delete" onclick={(event) => deleteNode(event)}>
      <iconify-icon icon={icons.delete} width="1.25rem"></iconify-icon>
    </button>
  </div>
  <div class="relative -mt-px flex flex-col rounded-b-box p-3 {bodyClass}">
    {#each schema.fields as field (field.id)}
      {@const Field = fields[field.field_type]}
      <Field nodeId={id} {...field} data={data[field.id] ?? field.default} />
    {/each}
    {#each schema.handles as handle (handle.id)}
      <NodeHandle nodeId={id} {...handle} />
    {/each}
  </div>
</div>
