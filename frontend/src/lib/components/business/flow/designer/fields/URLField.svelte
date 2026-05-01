<script lang="ts">
  import { Label, URLWrapper } from '$lib/components';
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
    maxlength: number;
  } = $props();

  // svelte-ignore state_referenced_locally
  // eslint-disable-next-line svelte/prefer-writable-derived
  let url: string = $state(data);
  let urlInput: HTMLInputElement;
  let urlWrapper: URLWrapper;

  const { updateNodeData } = useSvelteFlow();
  const { label, tooltip, placeholder } = nodeFormatter;

  /**
   * Check and report the validity of the URL input.
   */
  function reportValidity() {
    return urlInput && urlInput.reportValidity();
  }

  // register the validator
  if (hasContext('flow/graph')) {
    const context = getContext('flow/graph') as FlowGraphContext;
    context.addValidator(reportValidity);
  }

  /**
   * Update the URL field data.
   */
  function updateFieldData() {
    url = urlWrapper.standardize(url);
    updateNodeData(field.nodeId, {
      [field.id]: urlWrapper.full(url)
    });
  }

  $effect(() => {
    // standardize the URL when the data changes externally
    url = urlWrapper.standardize(data);
  });
</script>

<fieldset class="fieldset">
  <Label required={field.required} tip={$tooltip(field.tooltip)}>
    {$label(field.label)}
  </Label>
  <URLWrapper secure class="input-sm [&_button]:pt-px" bind:this={urlWrapper} onclick={updateFieldData}>
    <input
      type="text"
      class="nodrag grow truncate"
      required={field.required}
      minlength={field.minlength}
      maxlength={field.maxlength}
      placeholder={$placeholder(field.placeholder)}
      bind:value={url}
      bind:this={urlInput}
      oninput={() => {
        reportValidity();
        updateFieldData();
      }}
    />
  </URLWrapper>
</fieldset>
