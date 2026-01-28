<script lang="ts" module>
  import type { Snippet } from 'svelte';

  export type ContainerProps = {
    /** The container content snippet. */
    children: Snippet;
    /** Whether to show the loading overlay. */
    loading?: boolean | null;
    /** Whether to use the dynamic viewport height. */
    dvh?: boolean | null;
    /** The class names for the container. */
    class?: string;
    /** The key of the preset styles. */
    type?: keyof typeof PRESETS;
    width?: string;
    minWidth?: string;
    maxWidth?: string;
    rowGap?: string;
    padding?: string;
  };

  /**
   * The preset styles for the container.
   */
  const PRESETS = {
    default: {
      width: '100%',
      minWidth: '0',
      maxWidth: '100%',
      rowGap: '0',
      padding: '0'
    },
    settings: {
      width: '100%',
      minWidth: '24rem',
      maxWidth: '40rem',
      rowGap: '4rem',
      padding: '1rem 1rem 4rem'
    }
  };
</script>

<script lang="ts">
  import { Overlay } from '$lib/components';
  import { fade } from 'svelte/transition';

  let {
    children,
    loading,
    dvh = false,
    class: _class,
    type = 'default',
    width = '',
    minWidth = '',
    maxWidth = '',
    rowGap = '',
    padding = ''
  }: ContainerProps = $props();

  const preset = $derived(PRESETS[type]);
</script>

<div
  class="relative mx-auto {_class}"
  style:width={width || preset.width}
  style:min-width={minWidth || preset.minWidth}
  style:max-width={maxWidth || preset.maxWidth}
>
  <Overlay dvh {loading} />
  {#if loading === null || loading === undefined}
    <div
      class="flex flex-col {dvh ? 'h-(--ks-svh) sm:h-(--ks-lvh)' : ''}"
      style:row-gap={rowGap || preset.rowGap}
      style:padding={padding || preset.padding}
      in:fade
    >
      {@render children()}
    </div>
  {/if}
</div>
