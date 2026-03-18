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
    maxlength: number;
  } = $props();

  const HTTP = 'http://';
  const HTTPS = 'https://';
  let secure: boolean = $state(true);

  // svelte-ignore state_referenced_locally
  // eslint-disable-next-line svelte/prefer-writable-derived
  let url: string = $state(standardize(data));
  let urlInput: HTMLInputElement;

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
   * Standardize the URL.
   *
   * @param url - The URL to standardize.
   * @returns The standardized URL.
   */
  function standardize(url: string): string {
    url = url.trim();
    if (url.toLowerCase().startsWith(HTTP)) {
      secure = false;
      return url.slice(7);
    } else if (url.toLowerCase().startsWith(HTTPS)) {
      secure = true;
      return url.slice(8);
    }
    return url;
  }

  /**
   * Update the URL field data.
   */
  function updateFieldData() {
    url = standardize(url);
    updateNodeData(field.nodeId, {
      [field.id]: `${secure ? HTTPS : HTTP}${url}`
    });
  }

  $effect(() => {
    // standardize the URL when the data changes externally
    url = standardize(data);
  });
</script>

<fieldset class="fieldset">
  <Label required={field.required} tip={$tooltip(field.tooltip)}>
    {$label(field.label)}
  </Label>
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <label class="input input-sm w-full gap-0" onclick={(event) => event.preventDefault()}>
    <button
      class="cursor-pointer pt-px opacity-80"
      onclick={() => {
        secure = !secure;
        updateFieldData();
      }}
    >
      {secure ? HTTPS : HTTP}
    </button>
    <input
      type="text"
      class="nodrag grow text-ellipsis"
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
  </label>
</fieldset>
