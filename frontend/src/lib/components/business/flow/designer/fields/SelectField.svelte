<script lang="ts">
  import { Label } from '$lib/components';
  import { nodeFormatter } from '$lib/i18n';
  import type { Field } from '$lib/types';
  import { useSvelteFlow } from '@xyflow/svelte';

  let {
    data = '',
    ...field
  }: Field & {
    nodeId: string;
    data?: string | number | boolean;
    placeholder: string | null;
    options: Record<string, string | number | boolean>;
  } = $props();

  const { updateNodeData } = useSvelteFlow();
  const { label, tooltip, placeholder } = nodeFormatter;
</script>

<fieldset class="fieldset">
  <Label required={field.required} tip={$tooltip(field.tooltip)}>
    {$label(field.label)}
  </Label>
  <select
    class="nodrag select w-full appearance-none select-sm"
    required={field.required}
    bind:value={data}
    onchange={() => updateNodeData(field.nodeId, { [field.id]: data })}
  >
    {#if field.placeholder}
      <option disabled value={field.placeholder}>{$placeholder(field.placeholder)}</option>
    {/if}
    {#each Object.entries(field.options) as [key, value] (key)}
      <option {value}>{$label(key)}</option>
    {/each}
  </select>
</fieldset>
