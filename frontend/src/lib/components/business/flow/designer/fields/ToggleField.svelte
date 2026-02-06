<script lang="ts">
  import { Label } from '$lib/components';
  import { nodeFormatter } from '$lib/i18n';
  import type { Field } from '$lib/types';
  import { useSvelteFlow } from '@xyflow/svelte';

  let {
    data = false,
    ...field
  }: Field & {
    nodeId: string;
    data?: boolean;
  } = $props();

  const { updateNodeData } = useSvelteFlow();
  const { label, tooltip } = nodeFormatter;
</script>

<fieldset class="mt-2 fieldset grid-cols-2">
  <Label required={field.required} tip={$tooltip(field.tooltip)} tipPlacement="right" class="my-0! justify-start">
    {$label(field.label)}
  </Label>
  <input
    type="checkbox"
    class="toggle self-center justify-self-end"
    bind:checked={data}
    onchange={() => updateNodeData(field.nodeId, { [field.id]: data })}
  />
</fieldset>
