<script lang="ts">
  import { Label } from '$lib/components';
  import { nodeFormatter } from '$lib/i18n';
  import type { Field, FlowGraphContext } from '$lib/types';
  import { useSvelteFlow } from '@xyflow/svelte';
  import { getContext, hasContext } from 'svelte';

  let {
    data = '',
    ...field
  }: Field & {
    nodeId: string;
    data?: string;
    placeholder: string | null;
    minlength: number;
    maxlength: number | null;
    rows: number;
  } = $props();

  let textArea: HTMLTextAreaElement;

  const { updateNodeData } = useSvelteFlow();
  const { label, tooltip, placeholder } = nodeFormatter;

  /**
   * Check and report the validity of the text area.
   */
  function reportValidity() {
    return textArea && textArea.reportValidity();
  }

  // register the validator
  if (hasContext('flow/graph')) {
    const context = getContext('flow/graph') as FlowGraphContext;
    context.addValidator(reportValidity);
  }
</script>

<fieldset class="fieldset">
  <Label required={field.required} tip={$tooltip(field.tooltip)}>
    {$label(field.label)}
  </Label>
  <textarea
    class="nodrag nowheel textarea w-full textarea-sm"
    required={field.required}
    minlength={field.minlength}
    maxlength={field.maxlength}
    rows={field.rows}
    placeholder={$placeholder(field.placeholder)}
    bind:value={data}
    bind:this={textArea}
    oninput={() => {
      reportValidity();
      updateNodeData(field.nodeId, { [field.id]: data });
    }}
  ></textarea>
</fieldset>
