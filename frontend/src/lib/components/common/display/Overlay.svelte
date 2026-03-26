<script lang="ts" module>
  export type OverlayProps = {
    /** Whether to show the loading overlay. */
    loading?: boolean | null;
    /** Whether to use an opaque black background. */
    black?: boolean;
    /** Whether to use the dynamic viewport height. */
    dvh?: boolean;
    /** Whether to use the fixed positioning. */
    fixed?: boolean;
    /** The loading animation type. */
    animation?: keyof typeof ANIMATIONS;
  };

  // the loading animation class names
  const ANIMATIONS = {
    none: '',
    dots: 'loading-dots',
    ring: 'loading-ring',
    ball: 'loading-ball',
    bars: 'loading-bars',
    spinner: 'loading-spinner',
    infinity: 'loading-infinity'
  };
</script>

<script lang="ts">
  import { fade } from 'svelte/transition';

  const { loading, black = false, dvh = false, fixed = true, animation = 'bars' }: OverlayProps = $props();
  // the overlay class names
  const bgClass = $derived(`absolute flex layer-3 w-full ${dvh ? 'h-(--ks-svh) sm:h-(--ks-lvh)' : 'h-full'}`);
  const loadingClass = $derived(`loading inset-0 m-auto loading-lg ${ANIMATIONS[animation] || 'hidden'}`);
</script>

{#if black}
  {#if loading !== null}
    <div class="bg-black {bgClass}">
      {#if loading}
        <span class="text-white {loadingClass}" class:fixed in:fade={{ duration: 2000 }}></span>
      {/if}
    </div>
  {/if}
{:else if loading}
  <div class="bg-base-100/50 {bgClass}" in:fade={{ duration: 2000 }} out:fade={{ duration: 200 }}>
    <span class={loadingClass} class:fixed></span>
  </div>
{/if}
